"""Stratégie Opus Stat Retrained V4 — pipeline V4 entraîné inline (polars/numpy).

Variante de ``opus_stat_pretrained_v4`` qui **entraîne son propre modèle** au
lieu de charger le pkl embarqué. Méthodologie V4 :

  1. Features V4 (~100 indicateurs × lags 1/3/6/12) construites par
     ``app.ml.backend.features.build_features`` (polars/numpy).
  2. Labellisation amplitude (``|ret_t+1| > quantile``) + direction (``ret > 0``).
  3. Split chronologique 80/20.
  4. Deux LightGBM (amp + dir) entraînés en mode ``binary`` avec early-stopping.
  5. Imputation des NaN par les médianes du **train**.
  6. Persistance par TF via ``save_model`` / ``load_model``.
  7. Walk-forward : réentraînement périodique inline.

Optimisations mémoire :
  - Features castées en ``float32`` avant passage à LightGBM (~2× moins de RAM).
  - LightGBM ``max_bin=63`` + ``force_col_wise=True`` (~4× moins d'histogrammes).
  - ``gc.collect()`` explicite entre trainings pour éviter l'accumulation des
    boosters précédents en backtest (cause typique du ``bad allocation``).

Helpers V4 partagés : le pipeline de features (``_build_features`` et ses
satellites) vivait en copie locale ici — ~390 lignes identiques à celles de
trois stratégies soeurres ET à ``app.ml.backend.features``, l'implémentation
de référence. Les copies sont désormais des alias du backend (équivalence des
sorties vérifiée sur données réelles avant factorisation, verrouillée par
``tests/test_ml_helpers_shared.py``). Le ROUTING (setups, seuils, gestion du
risque) reste entièrement local : c'est lui qui différencie la stratégie.
"""

import gc
import logging
import os
import threading
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.indicators import (
    pre_val,
)
from app.engine.engine import BaseStrategyML
from app.ml.backend.features import _ewm_alpha_np as _bk_ewm_alpha_np
from app.ml.backend.features import build_features as _bk_build_features
from app.ml.backend.features import detect_timeframe as _bk_detect_timeframe
from app.ml.backend.features import impute_inplace as _bk_impute_inplace
from app.ml.backend.features import last_bar_hour_dow as _bk_last_bar_hour_dow
from app.ml.backend.features import select_feature_columns as _bk_select_feature_columns
from app.ml.backend.features import window_polars as _bk_window_polars

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers V4 partagés — implémentation unique dans app/ml/backend/features.py.
#  Ces fonctions étaient dupliquées à l'identique dans 4 stratégies (~300 lignes
#  chacune pour le seul ``_build_features``). Équivalence des sorties vérifiée
#  sur données réelles et cas dégénérés avant factorisation, et verrouillée par
#  tests/test_ml_helpers_shared.py (qui interdit une nouvelle copie locale).
#  Les noms préfixés restent exposés au niveau module : plusieurs consommateurs
#  historiques les importent directement depuis la stratégie.
# ─────────────────────────────────────────────────────────────────────────────
_build_features = _bk_build_features
_detect_timeframe = _bk_detect_timeframe
_window_polars = _bk_window_polars
_last_bar_hour_dow = _bk_last_bar_hour_dow
_select_feature_columns = _bk_select_feature_columns
_impute_inplace = _bk_impute_inplace
_ewm_alpha_np = _bk_ewm_alpha_np

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")

# Codes / labels de régime (alignés sur ``app.engine.risk`` V4)
REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3
REGIME_LABELS   = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
    -1:              "?",
}

# Multiplicateur de taille par heure UTC — dérivé du lift empirique horaire
# mesuré sur ~50k bougies 15m (heatmap jour×heure de l'analyse V4 recouvrée) :
# mult(h) = lift(h) / lift_max(=2.43 à 14h), plancher 0.2. Cf.
# opus_stat_pretrained_v4.py (même constante, dupliquée — stratégies
# auto-portantes par convention de ce module, cf. docstring).
_HOUR_LIFT_15M = {
    0: 0.44, 1: 1.09, 2: 0.66, 3: 0.62, 4: 0.47, 5: 0.54, 6: 0.64, 7: 0.65,
    8: 0.82, 9: 0.90, 10: 0.91, 11: 0.89, 12: 0.87, 13: 1.49, 14: 2.43,
    15: 2.28, 16: 1.80, 17: 1.62, 18: 1.30, 19: 1.10, 20: 0.94, 21: 0.79,
    22: 0.67, 23: 0.56,
}
_HOUR_SIZE_MULT_FLOOR = 0.20
_HOUR_LIFT_MAX = max(_HOUR_LIFT_15M.values())
_HOUR_SIZE_MULT = {
    h: max(_HOUR_SIZE_MULT_FLOOR, lift / _HOUR_LIFT_MAX)
    for h, lift in _HOUR_LIFT_15M.items()
}

# Colonnes exclues du jeu de features (raw OHLCV + MM brutes non-stationnaires)
_EXCLUDED_COLS = frozenset({
    "time", "open", "high", "low", "close", "volume",
    "log_ret", "OBV",
    "SMA_20", "SMA_50", "SMA_100", "SMA_200",
    "EMA_20", "EMA_50", "EMA_100", "EMA_200",
    "EMA_9", "EMA_21",
    "high_20", "low_20", "high_50", "low_50", "high_100", "low_100",
    "ATR_14",
})

_NUMERIC_DTYPES = (
    pl.Float32, pl.Float64,
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
)


class Strategy(BaseStrategyML):
    """Stratégie ML — pipeline V4 entraîné inline (modèles persistés par TF)."""

    name      = "opus_stat_retrained_v4"
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        "thresh_amp_td":    [0.40, 0.45, 0.50, 0.55, 0.60],
        "thresh_dir_td":    [0.05, 0.08, 0.10, 0.12, 0.15],
        "thresh_amp_other": [0.50, 0.55, 0.60, 0.65, 0.70],
        "thresh_dir_other": [0.10, 0.13, 0.15, 0.18, 0.20],
        "sl_atr_mult_td":    [1.5, 1.75, 1.8, 2.0, 2.25],
        "sl_atr_mult_other": [1.0, 1.25, 1.5, 1.75],
        "tp_atr_mult_td":    [1.0, 1.2, 1.4, 1.6],
        "tp_atr_mult_other": [0.8, 1.0, 1.2, 1.4],
        "max_hold_bars":     [1, 2, 4, 6, 8],
    }
    # Hyperparamètres d'entraînement figés (hors espace de recherche) : les
    # échantillonner invalide le cache d'entraînement process-wide entre les
    # trials de l'optimiseur — chaque trial repaye alors l'intégralité des
    # retrains LightGBM walk-forward (rédhibitoire sur 50k bougies). Valeurs
    # effectives : _DEFAULTS ; surchargables via le YAML stratégie.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":     0.30,
        "warmup_bars":     2000,
        "retrain_every":   800,
        "n_estimators":    500,
        "num_leaves":      31,
        "learning_rate":   0.03,
    }

    # Contrat de gate (ML-02) : _train_impl labellise en t+1 EN DUR (ret_t1),
    # ce n'est pas un paramètre d'entraînement — d'où la déclaration explicite
    # ici. Sans elle, le gate évaluait les candidats sur le défaut multi-horizon
    # [1,3,6] hérité de V11, soit une cible que ce modèle n'a jamais apprise.
    gate_spec: Dict[str, Any] = {
        "label_horizons": [1],
        "amp_top_pct":    fixed_params["amp_top_pct"],
    }

    _DEFAULTS = {
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),
        "active_days":         [0, 1, 2, 3, 4],
        # Sizing gradué par heure UTC (indépendant du filtre binaire ci-dessus,
        # cf. _HOUR_SIZE_MULT) — désactivable pour retrouver un sizing plat.
        "enable_hour_sizing":  True,
        "use_fixed_tp":        True,
        "disable_trailing":    True,
        "use_exit_after_bars": False,
        "sl_atr_mult_td":      1.8,
        "sl_atr_mult_other":   1.5,
        "tp_atr_mult_td":      1.2,
        "tp_atr_mult_other":   1.0,
        "use_kelly_sizing":    True,
        "kelly_size_other":    0.5,
        "min_confidence":      0.2,
        "amp_top_pct":         0.30,
        "warmup_bars":         2000,
        "retrain_every":       800,
        "n_estimators":        500,
        "num_leaves":          31,
        "learning_rate":       0.03,
    }

    retrain_interval_h: int = 6

    def __init__(self):
        self._lock = threading.Lock()
        self._amp_models:    Dict[str, Any]              = {}
        self._dir_models:    Dict[str, Any]              = {}
        self._feature_cols:  Dict[str, List[str]]        = {}
        self._medians:       Dict[str, Dict[str, float]] = {}
        self._trained_tfs:   set                         = set()
        self._managed_externally: bool                   = False
        self._call_cnt:      Dict[str, int]              = {}
        self._last_retrain:  Dict[str, int]              = {}
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        self._cancel_event = None
        # Cache backtest : voir prepare_for_backtest.
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les features V4 (polars) pour toute la fenêtre.

        Le scoring n'a besoin que de la dernière ligne, mais ``_build_features``
        produit ~462 colonnes sur toute la fenêtre. Sans cache, on rebuild à
        chaque barre du backtest. Avec cache, un seul build pour toute la
        fenêtre, puis ``features.head(len(df))`` dans la boucle.
        """
        try:
            # Catalogue partagé "v4_polars" (build identique entre v7/v10_rt/v11/stat_rt).
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="v4_polars", version="1",
                builder=lambda w: _build_features(_window_polars(w, n=len(w))),
                in_kind="polars", out_kind="polars")
            self._bt_features = feats
            self._bt_features_len = len(df) if feats is not None else 0
            logger.info(
                f"[OpusV4-RT] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(len(feats.columns) if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OpusV4-RT] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    # ── Cycle de vie ML ────────────────────────────────────────────────────
    @property
    def is_trained(self) -> bool:
        return bool(self._trained_tfs)

    @property
    def managed_externally(self) -> bool:
        return self._managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self._managed_externally = bool(v)

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        warmup = int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))
        return max(230, warmup + 30)

    def reset_model(self) -> None:
        with self._lock:
            self._amp_models.clear()
            self._dir_models.clear()
            self._feature_cols.clear()
            self._medians.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._train_meta.clear()
            self._last_retrain.clear()
            self._managed_externally = False
            self._best_auc = 0.0
        self._bt_features = None
        self._bt_features_len = 0
        gc.collect()

    # ── Persistance par TF ─────────────────────────────────────────────────
    @staticmethod
    def _tf_from_path(path: str) -> str:
        base = os.path.splitext(os.path.basename(path))[0]
        return base.rsplit("_", 1)[-1]

    def save_model(self, path: str, extra_meta: dict = None) -> None:
        from app.ml.backend.persistence import save_amp_dir_bundle
        tf_key = self._tf_from_path(path)
        with self._lock:
            amp_m = self._amp_models.get(tf_key)
            dir_m = self._dir_models.get(tf_key)
            feats = self._feature_cols.get(tf_key)
            meds  = self._medians.get(tf_key)
            auc   = self._best_auc_per_tf.get(tf_key, 0.0)
            meta  = self._train_meta.get(tf_key, {})
        if amp_m is None or dir_m is None:
            logger.debug(f"[OpusV4-RT] save_model({path}) : pas de modèle pour {tf_key}")
            return
        if save_amp_dir_bundle(path, tf_key, amp_m, dir_m, feats, meds, auc, meta,
                               extra_meta=extra_meta):
            logger.info(f"[OpusV4-RT] Modèles sauvegardés → {path} (AUC={auc:.3f})")

    def load_model(self, path: str) -> bool:
        from app.ml.backend.persistence import load_amp_dir_bundle
        data = load_amp_dir_bundle(path)
        if data is None or data.get("amp_model") is None or data.get("dir_model") is None:
            return False
        tf_key = self._tf_from_path(path)
        with self._lock:
            self._amp_models[tf_key]      = data["amp_model"]
            self._dir_models[tf_key]      = data["dir_model"]
            self._feature_cols[tf_key]    = list(data.get("features") or [])
            self._medians[tf_key]         = dict(data.get("medians") or {})
            self._best_auc_per_tf[tf_key] = float(data.get("best_auc", 0.0))
            self._train_meta[tf_key]      = dict(data.get("train_meta") or {})
            self._trained_tfs.add(tf_key)
            self._best_auc = max(self._best_auc, self._best_auc_per_tf[tf_key])
        logger.info(
            f"[OpusV4-RT] Modèle {tf_key} chargé depuis {path} "
            f"(AUC={self._best_auc_per_tf[tf_key]:.3f})"
        )
        return True

    # ── Entraînement (reproduit la méthodologie V4) ────────────────────────
    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        tf = _detect_timeframe(df) or "default"
        p  = (params or {}).get(self.name, {})
        self._train(df, tf_key=tf, params=p)

    # Entraînement avec cache process-wide (cf. app/core/train_cache.py) :
    # les retrains identiques (même fenêtre, mêmes hyperparams d'entraînement)
    # sont réutilisés entre les trials de l'optimiseur au lieu d'être relancés.
    _TRAIN_STATE_ATTRS = ('_amp_models', '_dir_models', '_feature_cols', '_medians', '_best_auc_per_tf', '_train_meta')
    _TRAIN_PARAM_KEYS  = ('amp_top_pct', 'n_estimators', 'num_leaves', 'learning_rate')

    def _train(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        from app.core.train_cache import cached_train
        return cached_train(self, df, tf_key, params, self._train_impl,
                            self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS)

    def _train_impl(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        try:
            import lightgbm as lgb
        except ImportError:
            logger.error("[OpusV4-RT] lightgbm requis : pip install lightgbm")
            return False

        amp_top_pct   = float(params.get("amp_top_pct",   self._DEFAULTS["amp_top_pct"]))
        n_estimators  = int(params.get("n_estimators",    self._DEFAULTS["n_estimators"]))
        num_leaves    = int(params.get("num_leaves",      self._DEFAULTS["num_leaves"]))
        learning_rate = float(params.get("learning_rate", self._DEFAULTS["learning_rate"]))

        # 1. Features V4 — réutilise le cache backtest si dispo.
        # ``_bt_train_offset`` (posé par score() avant _train) repère la
        # position de la fenêtre d'entraînement dans la fenêtre complète :
        # sans lui, head(len(df)) lisait les PREMIÈRES lignes des features
        # alors que train_df est une tranche de FIN.
        n_keep = max(2200, len(df))
        _off = int(getattr(self, "_bt_train_offset", None) or 0)
        if (self._bt_features is not None and
                self._bt_features_len > 0 and
                _off + len(df) <= self._bt_features_len):
            feats = self._bt_features.slice(_off, len(df))
        else:
            feats = _build_features(_window_polars(df, n=n_keep))
        if feats is None or len(feats) < 250:
            logger.warning(f"[OpusV4-RT] {tf_key} : données insuffisantes pour entraîner")
            return False

        feature_cols = _select_feature_columns(feats)
        if not feature_cols:
            logger.warning(f"[OpusV4-RT] {tf_key} : aucune feature exploitable")
            return False

        # 2. Labels
        close = feats["close"].to_numpy().astype(np.float64)
        n     = len(close) - 1
        if n < 200:
            logger.warning(f"[OpusV4-RT] {tf_key} : pas assez de barres labélisables")
            return False
        ret_t1  = (close[1:] - close[:n]) / np.maximum(close[:n], 1e-9)
        abs_ret = np.abs(ret_t1)
        amp_thr = float(np.quantile(abs_ret, 1.0 - amp_top_pct))
        y_amp   = (abs_ret >= amp_thr).astype(np.int8)
        y_dir   = (ret_t1 > 0).astype(np.int8)

        # 3. Matrice de features alignée (float32 pour LightGBM — moitié moins de RAM)
        X_full = feats.head(n).select(feature_cols).to_numpy().astype(np.float32)

        split = max(int(n * 0.8), 100)
        split = min(split, n - 50)
        if split < 100 or n - split < 50:
            logger.warning(f"[OpusV4-RT] {tf_key} : split impossible (n={n})")
            return False

        # 4. Médianes du TRAIN (float64 pour précision)
        medians: Dict[str, float] = {}
        for j, col in enumerate(feature_cols):
            col_train = X_full[:split, j]
            mask = np.isfinite(col_train)
            medians[col] = float(np.median(col_train[mask])) if mask.any() else 0.0

        X_train = X_full[:split].copy()
        X_valid = X_full[split:n].copy()
        del X_full
        _impute_inplace(X_train, feature_cols, medians)
        _impute_inplace(X_valid, feature_cols, medians)

        # 5. LightGBM — paramètres tunés pour mémoire
        common = dict(
            objective="binary", metric="auc",
            num_leaves=num_leaves, learning_rate=learning_rate,
            min_child_samples=20, subsample=0.8, subsample_freq=5,
            colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
            max_bin=63,                  # ~4× moins de RAM histogramme (default 255)
            force_col_wise=True,         # moins de RAM pour many-features
            verbosity=-1, n_jobs=1,
        )
        # Fix : callbacks instanciés frais dans chaque lgb.train (early_stopping
        # est stateful, sa réutilisation entre amp et dir cassait le 2e modèle).

        # Libère les anciens boosters AVANT de réentraîner (évite double pic mémoire)
        with self._lock:
            self._amp_models.pop(tf_key, None)
            self._dir_models.pop(tf_key, None)
        gc.collect()

        # ── Modèle amplitude ───────────────────────────────────────────────
        ds_train_amp = lgb.Dataset(X_train, label=y_amp[:split], feature_name=feature_cols,
                                   free_raw_data=False)
        ds_valid_amp = lgb.Dataset(X_valid, label=y_amp[split:n], reference=ds_train_amp,
                                   feature_name=feature_cols, free_raw_data=False)
        try:
            booster_amp = lgb.train(
                {**common, "scale_pos_weight":
                    (y_amp[:split] == 0).sum() / max((y_amp[:split] == 1).sum(), 1)},
                ds_train_amp, num_boost_round=n_estimators,
                valid_sets=[ds_valid_amp],
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(-1)],
            )
        except Exception as e:
            logger.warning(f"[OpusV4-RT] {tf_key} : entraînement amp KO ({e})")
            del ds_train_amp, ds_valid_amp
            gc.collect()
            return False
        auc_amp = booster_amp.best_score.get("valid_0", {}).get("auc", 0.0)
        del ds_train_amp, ds_valid_amp

        # ── Modèle direction ───────────────────────────────────────────────
        ds_train_dir = lgb.Dataset(X_train, label=y_dir[:split], feature_name=feature_cols,
                                   free_raw_data=False)
        ds_valid_dir = lgb.Dataset(X_valid, label=y_dir[split:n], reference=ds_train_dir,
                                   feature_name=feature_cols, free_raw_data=False)
        try:
            booster_dir = lgb.train(
                {**common, "scale_pos_weight":
                    (y_dir[:split] == 0).sum() / max((y_dir[:split] == 1).sum(), 1)},
                ds_train_dir, num_boost_round=n_estimators,
                valid_sets=[ds_valid_dir],
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(-1)],
            )
        except Exception as e:
            logger.warning(f"[OpusV4-RT] {tf_key} : entraînement dir KO ({e})")
            del ds_train_dir, ds_valid_dir
            gc.collect()
            return False
        auc_dir = booster_dir.best_score.get("valid_0", {}).get("auc", 0.0)
        del ds_train_dir, ds_valid_dir, X_train, X_valid

        # Top features par gain — pour l'affichage "Top features avec
        # importance" de la page Modèles (E7), même format que MLBackend
        # (app/ml/backend/trainer.py) pour un rendu UI générique.
        def _top_features(booster, top_n: int = 15):
            try:
                gains = booster.feature_importance(importance_type="gain")
                pairs = sorted(zip(feature_cols, gains), key=lambda kv: -kv[1])[:top_n]
                return [{"feature": c, "gain": round(float(g), 2)} for c, g in pairs]
            except Exception:
                return []

        auc_combined = (auc_amp + auc_dir) / 2.0
        with self._lock:
            self._amp_models[tf_key]      = booster_amp
            self._dir_models[tf_key]      = booster_dir
            self._feature_cols[tf_key]    = feature_cols
            self._medians[tf_key]         = medians
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key] = auc_combined
            self._best_auc                = max(self._best_auc, auc_combined)
            self._train_meta[tf_key] = {
                "n_train":     int(split),
                "n_valid":     int(n - split),
                "n_features":  len(feature_cols),
                "auc_amp":     round(float(auc_amp), 4),
                "auc_dir":     round(float(auc_dir), 4),
                "amp_thr_pct": round(float(amp_thr) * 100, 3),
                "amp_top_pct": amp_top_pct,
                "feature_importance_amp": _top_features(booster_amp),
                "feature_importance_dir": _top_features(booster_dir),
            }
        gc.collect()
        logger.info(
            f"[OpusV4-RT] {tf_key} entraîné : {split} train / {n - split} val | "
            f"{len(feature_cols)} features | AUC amp={auc_amp:.3f} dir={auc_dir:.3f} | "
            f"amp_thr={amp_thr * 100:.2f}%"
        )
        return True

    # ── Prédictions ────────────────────────────────────────────────────────
    def _predict(self, features_df: pl.DataFrame, tf: str, target: str) -> Optional[float]:
        with self._lock:
            booster   = (self._amp_models if target == "amp" else self._dir_models).get(tf)
            feat_cols = self._feature_cols.get(tf)
            medians   = self._medians.get(tf, {})
        if booster is None or not feat_cols:
            return None
        try:
            # Extraction de la derniere ligne en UN appel (dict col->valeur) au
            # lieu d'un acces colonne polars par feature (~440 get_column par
            # prediction, x2/barre). row.get(c)=None pour les colonnes absentes
            # -> repli sur la mediane d'entrainement.
            row  = features_df.tail(1).row(0, named=True)
            vals = np.empty(len(feat_cols), dtype=np.float32)
            for j, c in enumerate(feat_cols):
                v = row.get(c)
                vals[j] = v if (v is not None and np.isfinite(v)) else medians.get(c, 0.0)
            return float(booster.predict(vals.reshape(1, -1))[0])
        except Exception as e:
            logger.warning(f"[OpusV4-RT] Prédiction {tf}/{target} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "dir")

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Score ──────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        thresh_amp_td    = float(p.get("thresh_amp_td",    0.50))
        thresh_dir_td    = float(p.get("thresh_dir_td",    0.10))
        thresh_amp_other = float(p.get("thresh_amp_other", 0.55))
        thresh_dir_other = float(p.get("thresh_dir_other", 0.13))
        adx_threshold    = float(p.get("adx_threshold",    20.0))
        max_hold_bars    = int(p.get("max_hold_bars",      4))

        sl_atr_mult_td    = float(p.get("sl_atr_mult_td",    self._DEFAULTS["sl_atr_mult_td"]))
        sl_atr_mult_other = float(p.get("sl_atr_mult_other", self._DEFAULTS["sl_atr_mult_other"]))
        tp_atr_mult_td    = float(p.get("tp_atr_mult_td",    self._DEFAULTS["tp_atr_mult_td"]))
        tp_atr_mult_other = float(p.get("tp_atr_mult_other", self._DEFAULTS["tp_atr_mult_other"]))
        if "sl_atr_mult" in p:
            sl_atr_mult_td = sl_atr_mult_other = float(p["sl_atr_mult"])
        if "tp_atr_mult" in p:
            tp_atr_mult_td = tp_atr_mult_other = float(p["tp_atr_mult"])

        use_kelly_sizing = bool(p.get("use_kelly_sizing", self._DEFAULTS["use_kelly_sizing"]))
        kelly_size_other = float(p.get("kelly_size_other", self._DEFAULTS["kelly_size_other"]))
        min_confidence   = float(p.get("min_confidence",   self._DEFAULTS["min_confidence"]))

        enable_hour_filter  = bool(p.get("enable_hour_filter",  self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",    self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",         self._DEFAULTS["active_days"]))
        enable_hour_sizing  = bool(p.get("enable_hour_sizing",  self._DEFAULTS["enable_hour_sizing"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",        self._DEFAULTS["use_fixed_tp"]))
        disable_trailing    = bool(p.get("disable_trailing",    self._DEFAULTS["disable_trailing"]))
        use_exit_after_bars = bool(p.get("use_exit_after_bars", self._DEFAULTS["use_exit_after_bars"]))

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        # Heure/jour de la dernière bougie — calculés inconditionnellement :
        # le filtre binaire (skip total hors session) et le sizing gradué
        # (_HOUR_SIZE_MULT, appliqué plus bas) sont deux leviers indépendants.
        hour, dow = _last_bar_hour_dow(df)
        if enable_hour_filter and hour is not None and dow is not None:
            if dow not in active_days:
                return self._none(
                    f"Hors jours actifs (weekday={dow}, autorisés={active_days})"
                )
            if hour not in active_hours_utc:
                return self._none(
                    f"Hors session ({hour}h UTC, autorisées={active_hours_utc})"
                )

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(
                f"Timeframe non supporté (détecté={tf}, attendus={_SUPPORTED_TFS})"
            )

        cnt = self._call_cnt.get(tf, 0) + 1
        self._call_cnt[tf] = cnt
        last       = self._last_retrain.get(tf, 0)
        need_train = (tf not in self._trained_tfs) or (cnt - last >= retrain_every)
        if need_train and not self._managed_externally:
            from app.core.train_cache import aligned_train_window
            n_train = min(len(df) - 1, warmup_bars * 2)
            train_df, self._bt_train_offset = aligned_train_window(
                df, retrain_every, n_train)
            ok = self._train(train_df, tf, p)
            self._bt_train_offset = None
            if ok:
                self._last_retrain[tf] = cnt

        if tf not in self._trained_tfs:
            return self._none("Modèle pas encore entraîné (warmup en cours)")

        # Fast-path backtest : features pré-calculées une fois.
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            features = self._bt_features.head(len(df))
        else:
            features = _build_features(_window_polars(df, n=max(260, self.min_bars_required(params))))
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v = float(last_row.get("ATR_14") or 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        adx_v = float(last_row.get("ADX") or 0.0)
        bull  = int(last_row.get("MM_bullish_align") or 0)
        bear  = int(last_row.get("MM_bearish_align") or 0)
        if adx_v < adx_threshold:
            regime = REGIME_RANGE
        elif bull == 1:
            regime = REGIME_TREND_UP
        elif bear == 1:
            regime = REGIME_TREND_DN
        else:
            regime = REGIME_CHOPPY
        regime_lbl = REGIME_LABELS[regime]

        if regime == REGIME_TREND_UP:
            return self._none("Trend Up : aucun edge (AUC dir ≈ 0.50)", regime=regime)

        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")
        dir_dist = abs(p_up - 0.5)

        if regime == REGIME_TREND_DN:
            amp_thresh, dir_thresh = thresh_amp_td, thresh_dir_td
            sl_atr_mult, tp_atr_mult = sl_atr_mult_td, tp_atr_mult_td
            regime_size_fac = 1.0
        else:
            amp_thresh, dir_thresh = thresh_amp_other, thresh_dir_other
            sl_atr_mult, tp_atr_mult = sl_atr_mult_other, tp_atr_mult_other
            regime_size_fac = kelly_size_other

        if p_event < amp_thresh:
            return self._none(
                f"P(event)={p_event:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )
        if dir_dist < dir_thresh:
            return self._none(
                f"|P(up)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side = "long" if p_up > 0.5 else "short"

        confidence = dir_dist * 2.0
        if use_kelly_sizing:
            size_factor = min(1.0, max(0.0, regime_size_fac * max(confidence, min_confidence)))
        else:
            size_factor = regime_size_fac

        # Sizing gradué par heure UTC (indépendant du filtre binaire) — un
        # signal à 14h (lift ×2.43, mult=1.0) garde sa taille pleine ; le même
        # signal à 19h (lift ×1.10, mult≈0.45) ou hors fenêtre si le filtre
        # est désactivé est réduit en proportion du lift empirique mesuré.
        hour_size_mult = 1.0
        if enable_hour_sizing and hour is not None:
            hour_size_mult = _HOUR_SIZE_MULT.get(hour, 1.0)
            size_factor = min(1.0, max(0.0, size_factor * hour_size_mult))

        score_val = round(min(0.55 + p_event * confidence * 0.39, 0.94), 3)
        meta      = self._train_meta.get(tf, {})

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "size_factor":      round(size_factor, 4),
            "disable_trailing": disable_trailing,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult
        if use_exit_after_bars:
            sig["exit_after_bars"] = max_hold_bars
        if not disable_trailing:
            sig["trail_override"] = {
                "trail_wide":  max(1.0, sl_atr_mult),
                "trail_tight": max(0.5, tp_atr_mult * 0.5),
                "breakeven_r": 0.8,
                "lock_r":      max(1.0, tp_atr_mult),
                "tight_r":     max(1.5, tp_atr_mult * 1.5),
                "grace_bars":  1,
            }

        exit_desc = [f"SL fixe = entry ∓ {sl_atr_mult:.2f}×ATR"]
        if use_fixed_tp:
            exit_desc.append(f"TP fixe = entry ± {tp_atr_mult:.2f}×ATR")
        exit_desc.append("trailing désactivé" if disable_trailing else "trailing actif")
        if use_exit_after_bars:
            exit_desc.append(f"sortie après {max_hold_bars} barres max")

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(float(last_row.get("RSI_14") or 50.0), 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "dir_dist":         round(dir_dist, 4),
            "confidence":       round(confidence, 4),
            "size_factor":      round(size_factor, 4),
            "regime_size_fac":  regime_size_fac,
            "hour_size_mult":   round(hour_size_mult, 3),
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "use_fixed_tp":     use_fixed_tp,
            "use_kelly_sizing": use_kelly_sizing,
            "disable_trailing": disable_trailing,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
            "n_features":       meta.get("n_features", 0),
            "amp_thr_pct":      meta.get("amp_thr_pct", 0.0),
        }
        sig["conditions"] = [
            f"Modèle V4 entraîné inline / {tf} ({meta.get('n_features', 0)} features, "
            f"AUC amp={meta.get('auc_amp', 0):.2f} dir={meta.get('auc_dir', 0):.2f})",
            f"Régime : {regime_lbl} (ADX={adx_v:.0f}) — autorisé ✓",
            f"P(événement)={p_event:.2f} ≥ {amp_thresh:.2f} ✓",
            f"P(hausse)={p_up:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR (régime {regime_lbl})",
            f"Sizing : régime ×{regime_size_fac:.2f} × confidence {confidence:.2f} "
            f"= size_factor {size_factor:.2f}" if use_kelly_sizing
            else f"Sizing : ×{regime_size_fac:.2f} (Kelly désactivé)",
            f"Sizing horaire : {hour}h UTC → ×{hour_size_mult:.2f} "
            f"(lift empirique {_HOUR_LIFT_15M.get(hour, 1.0):.2f}× la moyenne)"
            if enable_hour_sizing and hour is not None else "Sizing horaire désactivé",
            f"Sortie : {' + '.join(exit_desc)}",
        ]
        sig["reason"] = (
            f"OpusV4-RT {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f}"
        )
        return sig

    def _none(self, reason: str = "", p_event: float = 0.0, p_up: float = 0.5,
              regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "p_event":    round(p_event, 4),
            "p_up":       round(p_up, 4),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
