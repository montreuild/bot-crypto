"""MLBackend — backend ML générique pour stratégies LightGBM.

Façade de coordination qui combine :
- `features.build_features` : construction des ~462 features V4 (polars).
- `trainer.train`           : entraînement LightGBM (amp + dir) + calibration + pruning.
- `predictor.predict_*`     : inférence single/batch avec calibration.
- `persistence.save/load_model` : format natif LGB + JSON (sans RCE).

Usage typique (composition dans une stratégie BaseStrategyML) :

    from app.ml.backend import MLBackend

    class Strategy(BaseStrategyML):
        def __init__(self):
            self.ml = MLBackend(name="my_strategy", model_dir="models")
            ...

        def fit(self, df, params=None):
            self.ml.fit(df, params, defaults=self._DEFAULTS)
        def save_model(self, path):
            self.ml.save_model(path)
        def load_model(self, path):
            return self.ml.load_model(path)
        def predict_amplitude(self, features_df, tf):
            return self.ml.predict_amplitude(features_df, tf)

Le backend préserve le cache process-wide `cached_train` en passant la
`strategy` au trainer — la clé de cache reste basée sur
`type(strategy).__module__` pour ne pas invalider les caches entre trials
de l'optimiseur.
"""
from __future__ import annotations

import gc
import logging
import os
import threading
from typing import Any, Dict, List, Optional

import polars as pl

from app.ml.backend.features import (
    FEATURES_CATALOG_NAME,
    FEATURES_CATALOG_VERSION,
    REGIME_CHOPPY,
    REGIME_LABELS,
    REGIME_RANGE,
    REGIME_TREND_DN,
    REGIME_TREND_UP,
    SUPPORTED_TFS,
    build_features,
    classify_regime,
    detect_timeframe,
    exit_td_window_active,
    last_bar_hour_dow,
    multi_horizon_labels,
    regime_history,
    single_horizon_labels,
    window_polars,
)
from app.ml.backend.persistence import (
    load_model as _load_model_impl,
)
from app.ml.backend.persistence import (
    save_model as _save_model_impl,
)
from app.ml.backend.predictor import (
    predict_amplitude as _predict_amp,
)
from app.ml.backend.predictor import (
    predict_direction as _predict_dir,
)
from app.ml.backend.predictor import (
    predict_series as _predict_series,
)
from app.ml.backend.predictor import (
    predict_single as _predict_single,
)
from app.ml.backend.trainer import (
    TrainConfig,
    TrainState,
)
from app.ml.backend.trainer import (
    train as _train_impl,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MLBackend",
    "TrainConfig",
    "TrainState",
    # Features (ré-exportés pour les stratégies qui en ont besoin)
    "build_features",
    "window_polars",
    "detect_timeframe",
    "last_bar_hour_dow",
    "select_feature_columns",
    "impute_inplace",
    "multi_horizon_labels",
    "single_horizon_labels",
    "classify_regime",
    "regime_history",
    "exit_td_window_active",
    "SUPPORTED_TFS",
    "REGIME_RANGE", "REGIME_TREND_UP", "REGIME_TREND_DN", "REGIME_CHOPPY",
    "REGIME_LABELS",
    "FEATURES_CATALOG_NAME", "FEATURES_CATALOG_VERSION",
]


def select_feature_columns(features_df: pl.DataFrame) -> List[str]:
    """Ré-export de `features.select_feature_columns` (homogénéité API)."""
    from app.ml.backend.features import select_feature_columns as _sfc
    return _sfc(features_df)


def impute_inplace(arr, feature_cols, medians) -> None:
    """Ré-export de `features.impute_inplace`."""
    from app.ml.backend.features import impute_inplace as _ii
    _ii(arr, feature_cols, medians)


class MLBackend:
    """Backend ML générique — composition recommandée dans les stratégies.

    Centralise l'état d'entraînement (TrainState), le lock, et délègue aux
    modules trainer/predictor/persistence. La stratégie qui l'utilise
    n'a plus qu'à porter son routing (setups, score, early_exit) — toute
    la mécanique ML est encapsulée ici.

    Le backend est thread-safe (lock interne). Il peut être partagé entre
    le thread de trading (inférence) et le thread de retrain (MLStrategyTrainer).
    """

    def __init__(self, name: str, model_dir: str = "models",
                 calibrate: bool = True,
                 prune_features: bool = True,
                 multi_horizon: bool = True):
        self.name        = name
        self.model_dir   = model_dir
        self._calibrate  = bool(calibrate)
        self._prune      = bool(prune_features)
        self._multi_h    = bool(multi_horizon)

        self._lock  = threading.Lock()
        self._state = TrainState()

        # Cache backtest (features pré-calculées sur toute la fenêtre).
        # Peuplé par `prepare_for_backtest`, lu par `_train` et `score`.
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0
        # Offset de la fenêtre d'entraînement dans le cache backtest
        # (posé par la stratégie avant d'appeler fit/train, depuis
        # `aligned_train_window`).
        self._bt_train_offset: Optional[int] = None

    # ── État ML (accès thread-safe) ─────────────────────────────────────────
    @property
    def state(self) -> TrainState:
        """Accès direct à l'état (à utiliser sous `self._lock` si mutation)."""
        return self._state

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    @property
    def is_trained(self) -> bool:
        return bool(self._state.trained_tfs)

    @property
    def managed_externally(self) -> bool:
        return self._state.managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self._state.managed_externally = bool(v)

    @property
    def trained_tfs(self) -> set:
        return set(self._state.trained_tfs)

    @property
    def best_auc_per_tf(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._state.best_auc_per_tf)

    @property
    def best_auc(self) -> float:
        return self._state.best_auc

    @property
    def train_meta(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._state.train_meta)

    # Attributs attendus par `cached_train` (compat stratégie).
    _TRAIN_STATE_ATTRS = TrainState.STATE_ATTRS
    _TRAIN_PARAM_KEYS  = TrainState.PARAM_KEYS

    def reset_model(self) -> None:
        """Réinitialise tout l'état ML (modèles, caches, compteurs)."""
        with self._lock:
            self._state.reset()
        self._bt_features = None
        self._bt_features_len = 0
        self._bt_train_offset = None
        gc.collect()

    # ── Backtest cache ──────────────────────────────────────────────────────
    def prepare_for_backtest(self, df: pl.DataFrame,
                             symbol: Optional[str] = None,
                             tf: Optional[str] = None) -> None:
        """Pré-calcule les features V4 sur toute la fenêtre de backtest.

        Utilise le FeatureStore (catalogue partagé ``v4_polars`` version ``1``)
        pour mutualiser le calcul entre stratégies au cours d'un même backtest.
        """
        try:
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                symbol, tf, df,
                name=FEATURES_CATALOG_NAME, version=FEATURES_CATALOG_VERSION,
                builder=lambda w: build_features(window_polars(w, n=len(w))),
                in_kind="polars", out_kind="polars",
            )
            self._bt_features = feats
            self._bt_features_len = len(df) if feats is not None else 0
            n_cols = len(feats.columns) if feats is not None else 0
            logger.info(
                f"[MLBackend:{self.name}] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies ({n_cols} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[MLBackend:{self.name}] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    def get_bt_features(self, n: Optional[int] = None) -> Optional[pl.DataFrame]:
        """Retourne les features backtest (head(n) ou tout si n=None)."""
        if self._bt_features is None or n is None:
            return self._bt_features
        if n <= self._bt_features_len:
            return self._bt_features.head(n)
        return None

    # ── Train ───────────────────────────────────────────────────────────────
    def fit(self, df: pl.DataFrame, params: Optional[Dict[str, Any]] = None,
            defaults: Optional[Dict[str, Any]] = None,
            strategy: Any = None) -> None:
        """API BaseStrategyML.fit — détecte le TF et délègue à `train`.

        Args:
            df:       DataFrame OHLCV polars.
            params:   paramètres de la stratégie (avec clé = self.name).
            defaults: defaults de la stratégie (_DEFAULTS).
            strategy: instance de la stratégie (pour préserver la clé de cache
                      process-wide `type(strategy).__module__`). Si None, le
                      cache est désactivé (chaque fit retrain depuis zéro).
        """
        tf = detect_timeframe(df) or "default"
        p  = (params or {}).get(self.name, {})
        self.train(df, tf_key=tf, params=p,
                   defaults=defaults or {},
                   strategy=strategy or self)

    def train(self, df: pl.DataFrame, tf_key: str,
              params: Dict[str, Any], defaults: Dict[str, Any],
              strategy: Any = None) -> bool:
        """Entraîne les 2 boosters (amp + dir) pour un TF.

        Si `strategy` est fourni, le cache process-wide `cached_train` est
        utilisé : la clé inclut `type(strategy).__module__` + fenêtre +
        hyperparams — les retrains identiques sont réutilisés entre les
        trials de l'optimiseur.

        Sans `strategy`, le train est direct (pas de cache).
        """
        if strategy is not None and hasattr(strategy, "_train_impl"):
            # Cache process-wide via app.core.train_cache.
            from app.core.train_cache import cached_train
            return cached_train(
                strategy, df, tf_key, params, self._train_impl_wrapper,
                self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS,
            )
        return self._train_impl_wrapper(df, tf_key, params)

    def _train_impl_wrapper(self, df: pl.DataFrame, tf_key: str,
                            params: Dict[str, Any]) -> bool:
        """Wrapper appelé par `cached_train` — délègue au trainer."""
        # Injecte la config calibrate/prune/multi_horizon dans params si absente.
        p = dict(params)
        p.setdefault("calibrate",      self._calibrate)
        p.setdefault("prune_features", self._prune)
        if self._multi_h:
            p.setdefault("label_horizons", [1, 3, 6])
        else:
            p.setdefault("label_horizons", [1])
        return _train_impl(
            self._state, self._lock, df, tf_key, p,
            defaults=p,  # params contient déjà les valeurs résolues
            bt_features=self._bt_features,
            bt_features_len=self._bt_features_len,
            bt_train_offset=self._bt_train_offset,
        )

    # ── Predict ─────────────────────────────────────────────────────────────
    def predict_single(self, features_df: pl.DataFrame, tf: str,
                       target: str) -> Optional[float]:
        return _predict_single(self._state, self._lock, features_df, tf, target)

    def predict_series(self, features_df: pl.DataFrame, tf: str,
                       target: str) -> Optional[Any]:
        return _predict_series(self._state, self._lock, features_df, tf, target)

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return _predict_amp(self._state, self._lock, features_df, tf)

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return _predict_dir(self._state, self._lock, features_df, tf)

    # ── Persistance ─────────────────────────────────────────────────────────
    @staticmethod
    def _tf_from_path(path: str) -> str:
        """Extrait le TF depuis le nom de fichier (ex: `foo_1h` → `1h`)."""
        base = os.path.splitext(os.path.basename(path))[0]
        return base.rsplit("_", 1)[-1]

    def save_model(self, path: str, extra_meta: Optional[dict] = None) -> None:
        """Sauvegarde au format natif (3 fichiers : .amp.lgb, .dir.lgb, .meta.json).

        ``extra_meta`` : bloc de provenance ML-02 optionnel
        (``{"provenance": {...}, "gate": {...}}``), fusionné dans meta.json.
        """
        tf = self._tf_from_path(path)
        _save_model_impl(self._state, self._lock, path, tf, extra_meta=extra_meta)

    def load_model(self, path: str) -> bool:
        """Charge les modèles depuis le format natif LightGBM (.lgb + .meta.json)."""
        tf = self._tf_from_path(path)
        return _load_model_impl(self._state, self._lock, path, tf)

    # ── Helpers statiques ───────────────────────────────────────────────────
    @staticmethod
    def build_features(raw_df: pl.DataFrame) -> Optional[pl.DataFrame]:
        """Construit les features V4 (alias de `features.build_features`)."""
        return build_features(raw_df)

    @staticmethod
    def detect_timeframe(df: pl.DataFrame) -> Optional[str]:
        return detect_timeframe(df)

    @staticmethod
    def last_bar_hour_dow(df: pl.DataFrame):
        return last_bar_hour_dow(df)
