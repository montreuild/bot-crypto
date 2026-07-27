"""Stratégie Scoring Statistique Opus V4 — reproduction exacte du rapport V4.

Méthodologie alignée sur le rapport BTC/USDC V4 (§4-§6) :

  1. **Deux modèles LightGBM séparés** (§6.1, §6.2)
     - `amp_model` : P(événement à t+1) — AUC OOS cible 0.69-0.75
       Label : |return_t+1| > p70 historique → top 30% des amplitudes
     - `dir_model` : P(hausse à t+1) — AUC OOS cible 0.51 global, 0.87 en Trend Down
       Label : return_t+1 > 0

  2. **Features avec lags 1, 3, 6, 12** (§4.1)
     Pour chaque proxy de volatilité (ATR_pct, vol_std_20, range_size, BB_width)
     on calcule sa valeur aux lags 1, 3, 6, 12 → 4 × 4 = 16 features amplitude.
     Direction : RSI, body, wicks, MACD aux mêmes lags.

  3. **Règle de décision conditionnée régime** (§6.4)
       SI Trend_Up    : NE PAS TRADER (AUC ≈ 0.50, pas d'edge)
       SI Trend_Down  : trade si P(event) ≥ 0.50 ET |P(up)-0.5| ≥ 0.10
       SI Range/Choppy: trade si P(event) ≥ 0.60 ET |P(up)-0.5| ≥ 0.15 (taille ×0.5)

  4. **Sortie à la clôture de la barre suivante** (§5)
     Pas de SL/TP : `exit_after_bars=1` → l'engine clôture à la prochaine close.
     C'est la mesure exacte du rapport (WR 68-75%).

  5. **Multiplicateur horaire** (§6.5)
     13-17h UTC ×1.0 | 8-12h ×0.7 | 18-23h ×0.5 | 0-5h ×0.3

Note importante : la qualité du modèle dépend du volume d'entraînement.
Pour atteindre l'AUC du rapport (50k bougies), entraîner via Optimiseur ML
sur au moins 20 000-50 000 barres. Sur 2000 barres inline, l'AUC sera plus faible.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import pre_val
from app.engine.engine import BaseStrategyML

logger = logging.getLogger(__name__)

REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3

REGIME_LABELS = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
}

# Lags utilisés (rapport §4.1)
_LAGS = (1, 3, 6, 12)

# Features de base pour amplitude (proxies de volatilité)
# Rapport §4.1 / 6.1 — ces colonnes proviennent de precompute_df
_AMP_BASE_COLS = [
    "_pre_atr_pct_r",     # ATR_pct (normalisé)
    "_pre_volstd20_r",    # vol_std_20
    "_pre_range_r",       # range_size
    "_pre_body_abs_r",    # body absolu
]

# Features de base pour direction
_DIR_BASE_COLS = [
    "_pre_rsi14",
    "_pre_body",
    "_pre_upper_wick",
    "_pre_lower_wick",
    "_pre_macd_hist",
    "_pre_volratio20",
    "_pre_range_pos20",
]


def _detect_regime(adx: float, sma20: float, sma50: float,
                   sma100: float, sma200: float,
                   pdi: float = 0.0, ndi: float = 0.0,
                   adx_threshold: float = 20.0) -> int:
    """Régime selon §6.2 du rapport — ADX + alignement SMA + confirmation DI."""
    if adx < adx_threshold:
        return REGIME_RANGE
    if sma20 > sma50 > sma100 > sma200 and pdi > ndi:
        return REGIME_TREND_UP
    if sma20 < sma50 < sma100 < sma200 and ndi > pdi:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _bb_width(close: np.ndarray, period: int = 20) -> np.ndarray:
    """Largeur Bollinger normalisée : (upper - lower) / sma."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(close)
    out = np.zeros(n, dtype=np.float32)
    if n < period:
        return out
    windows = sliding_window_view(close.astype(np.float64), period)
    sma = windows.mean(axis=1)
    std = windows.std(axis=1)
    out[period - 1:] = (4.0 * std / np.maximum(sma, 1e-9)).astype(np.float32)
    return out


def _build_features(df: pl.DataFrame, adx_threshold: float = 20.0) -> Optional[np.ndarray]:
    """Construit features amplitude + direction + régime, avec lags 1/3/6/12.

    Total = 4 amp × 4 lags + 7 dir × 4 lags + 4 régime = 16 + 28 + 4 = 48 features
    """
    missing = [c for c in _AMP_BASE_COLS + _DIR_BASE_COLS if c not in df.columns]
    if missing:
        return None

    n = len(df)
    cols: List[np.ndarray] = []

    # Features amplitude (4 colonnes × 4 lags = 16)
    for col in _AMP_BASE_COLS:
        arr = df[col].cast(pl.Float32).to_numpy()
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        for lag in _LAGS:
            lagged = np.concatenate([np.full(lag, arr[0] if n > 0 else 0.0), arr[:-lag]])
            cols.append(lagged.astype(np.float32))

    # BB width + rang percentile sur 100 barres (rapport §6.1)
    close = df["close"].cast(pl.Float32).to_numpy()
    bb_w  = _bb_width(close, 20)
    # rang sur 100 barres (proxy BB_width_rank100) — vectorisé numpy
    from numpy.lib.stride_tricks import sliding_window_view
    bb_rank = np.zeros(n, dtype=np.float32)
    if n >= 101:
        windows = sliding_window_view(bb_w, 100)          # (n-99, 100)
        bb_rank[100:] = (bb_w[100:, np.newaxis] > windows[:n - 100]).sum(axis=1) / 100.0
    for lag in _LAGS:
        cols.append(np.concatenate([np.zeros(lag, dtype=np.float32), bb_w[:-lag]]))
        cols.append(np.concatenate([np.zeros(lag, dtype=np.float32), bb_rank[:-lag]]))

    # Features direction (7 × 4 = 28)
    for col in _DIR_BASE_COLS:
        arr = df[col].cast(pl.Float32).to_numpy()
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        for lag in _LAGS:
            cols.append(np.concatenate([np.full(lag, arr[0] if n > 0 else 0.0), arr[:-lag]]).astype(np.float32))

    # Features régime (4 colonnes)
    has_regime = all(c in df.columns for c in
                     ("_pre_adx14", "_pre_sma20", "_pre_sma50",
                      "_pre_sma100", "_pre_sma200", "_pre_pdi14", "_pre_ndi14"))
    if has_regime:
        adx_a = df["_pre_adx14"].to_numpy().astype(np.float32)
        s20   = df["_pre_sma20"].to_numpy().astype(np.float32)
        s50   = df["_pre_sma50"].to_numpy().astype(np.float32)
        s100  = df["_pre_sma100"].to_numpy().astype(np.float32)
        s200  = df["_pre_sma200"].to_numpy().astype(np.float32)
        pdi_a = df["_pre_pdi14"].to_numpy().astype(np.float32)
        ndi_a = df["_pre_ndi14"].to_numpy().astype(np.float32)

        is_range = adx_a < adx_threshold
        is_tu    = (~is_range) & (s20 > s50) & (s50 > s100) & (s100 > s200) & (pdi_a > ndi_a)
        is_td    = (~is_range) & (~is_tu) & (s20 < s50) & (s50 < s100) & (s100 < s200) & (ndi_a > pdi_a)
        regimes  = np.where(is_range, REGIME_RANGE,
                   np.where(is_tu, REGIME_TREND_UP,
                   np.where(is_td, REGIME_TREND_DN, REGIME_CHOPPY))).astype(np.float32)
        s200_safe = np.where(s200 > 0, s200, 1.0)
        cols.append(regimes / 3.0)                       # régime normalisé
        cols.append((pdi_a - ndi_a) / 50.0)              # biais DI
        cols.append((s20 - s200) / s200_safe)            # force tendance
        cols.append(adx_a / 50.0)                        # ADX normalisé
    else:
        for _ in range(4):
            cols.append(np.zeros(n, dtype=np.float32))

    X = np.column_stack(cols)
    return np.nan_to_num(X, nan=0.0, posinf=1.0, neginf=-1.0)


def _hour_multiplier(hour: int) -> float:
    """Multiplicateur horaire §6.5 du rapport."""
    if 13 <= hour <= 17:    # session US, lift ×2
        return 1.0
    if 8 <= hour <= 12:     # session EU
        return 0.7
    if 0 <= hour <= 5:      # nuit
        return 0.3
    return 0.5


def _get_hour(df: pl.DataFrame) -> int:
    """Extrait l'heure UTC de la dernière barre."""
    try:
        ts = df["time"][-1]
        if hasattr(ts, "hour"):
            return ts.hour
        import datetime
        return datetime.datetime.utcfromtimestamp(int(ts) / 1_000_000).hour
    except Exception:
        return 12


class Strategy(BaseStrategyML):
    name      = "scoring_statistique_opus_v4"
    # Recette(s) consommée(s) — surchargeable par le bloc `models:`
    # du YAML (cf. app.ml.recipe.strategy_models).
    models: Dict[str, str] = {"signal": "stat48_v4"}
    model_dir = "models"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":     [15, 20, 25],
        "amp_thresh_td":     [0.45, 0.50, 0.55],
        "dir_dist_td":       [0.08, 0.10, 0.12, 0.15],
        "amp_thresh_other":  [0.55, 0.60, 0.65],
        "dir_dist_other":    [0.12, 0.15, 0.18],
    }

    # Hyperparamètres d'entraînement figés (hors espace de recherche) pour
    # borner le coût des trials de l'optimiseur sur de longues fenêtres.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":   0.30,
        "warmup_bars":   2000,
        "retrain_every": 800,
    }

    def __init__(self):
        self._amp_models:  Dict[str, Any] = {}
        self._dir_models:  Dict[str, Any] = {}
        self._trained_tfs: set            = set()
        self._lock         = threading.Lock()
        self._call_cnt:    Dict[str, int] = {}
        self._last_retrain:Dict[str, int] = {}
        self._managed_externally          = False
        # Compatibilité API ML
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        # Cache backtest : voir prepare_for_backtest. Clé = ``adx_threshold``
        # car ``_build_features`` dépend de ce paramètre pour le régime.
        self._bt_features_cache: Dict[float, np.ndarray] = {}
        self._bt_features_len: int = 0
        self._bt_full_df: Optional[pl.DataFrame] = None  # df complet du backtest (FeatureStore)
        # Diagnostic : compteurs et accumulateurs pour comprendre pourquoi
        # 0 trade peut survenir en backtest (échec training silencieux vs
        # seuils trop stricts). Loggés périodiquement dans score().
        self._diag_predictions: List[Tuple[float, float, int]] = []   # (p_event, p_up, regime)
        self._diag_rejects:     Dict[str, int]                 = {}
        self._diag_signals:     int                            = 0
        self._diag_last_dump:   int                            = 0

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les features sur toute la fenêtre du backtest.

        ``_build_features`` est appelé à chaque ``score()`` sur un slice de
        250 bougies — soit ~5000 rebuilds redondants sur un backtest long.
        On cache le tableau X complet par valeur de ``adx_threshold`` (seul
        paramètre qui influence le calcul des features de régime).
        Pré-peuplement : pour les jobs d'optimisation, on construit aussi
        tout de suite chaque valeur d'``adx_threshold`` du ``param_space``
        afin que ``_train`` puisse lire le cache au lieu de rebuilder.
        """
        try:
            self._bt_features_cache.clear()
            self._bt_features_len = len(df)
            self._bt_full_df = df  # conservé pour des (re)builds alignés sur la fenêtre complète
            # Pré-peuplement : couvre toutes les valeurs d'adx_threshold du
            # param_space + la valeur "par défaut" (20.0). Coût ~N×build mais
            # amorti sur tous les trials du job, et persisté via le FeatureStore.
            seeds = list(self.param_space.get("adx_threshold", [])) + [20.0]
            seen = set()
            for raw in seeds:
                key = round(float(raw), 6)
                if key in seen:
                    continue
                seen.add(key)
                X = self._build_full_features(key)
                if X is not None:
                    self._bt_features_cache[key] = X
            logger.info(
                f"[OpusV4-Stat] backtest : cache features prêt sur {len(df)} bougies "
                f"({len(self._bt_features_cache)} seuil(s) adx_threshold pré-calculés)"
            )
        except Exception as e:
            logger.warning(f"[OpusV4-Stat] prepare_for_backtest KO : {e}")
            self._bt_features_cache.clear()
            self._bt_features_len = 0
            self._bt_full_df = None

    def _build_full_features(self, adx_threshold: float) -> Optional[np.ndarray]:
        """Construit X (n, 48) sur TOUTE la fenêtre de backtest via le catalogue
        FeatureStore (un provider par ``adx_threshold``). ``_build_features`` exige
        les colonnes ``_pre_*`` : on les précalcule dans le builder
        (``precompute_df`` est idempotent). Retourne ``None`` hors backtest."""
        df = self._bt_full_df
        if df is None:
            return None
        from app.core.feature_store import cached_strategy_features
        from app.core.indicators import precompute_df as _pc
        key = round(float(adx_threshold), 6)
        return cached_strategy_features(
            getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
            name=f"{self.name}_adx{key}", version="1",
            builder=lambda w, t=adx_threshold: _build_features(_pc(w), t),
            in_kind="polars", out_kind="numpy")

    def _features_for_training(self, df: pl.DataFrame,
                               adx_threshold: float) -> Optional[np.ndarray]:
        """Features sur TOUTE la fenêtre reçue — chemin d'ENTRAÎNEMENT.

        ``_get_or_build_features`` retombe, hors backtest, sur un slice des
        **250 dernières barres**. C'est le bon compromis pour ``score()``, qui
        ne lit que la dernière ligne. Pour ``_train`` c'était un défaut
        silencieux et coûteux : quelle que soit la fenêtre passée à ``fit()``
        — 3 400 barres depuis le gate, 8 000 depuis le runner — le modèle
        n'apprenait que sur 250 lignes, soit 200 en entraînement et 50 en
        validation, alors que la recette annonce ``min_bars: 2000``. Aucun
        message ne le signalait.

        Le cache de backtest reste utilisé quand il couvre la fenêtre : il est
        semé par ``prepare_for_backtest`` sur le frame complet, et
        ``X[:len(df)]`` l'aligne sur le préfixe courant.
        """
        if self._bt_features_len and len(df) <= self._bt_features_len:
            key = round(float(adx_threshold), 6)
            X = self._bt_features_cache.get(key)
            if X is not None and len(X) >= len(df):
                return X[: len(df)]
        return _build_features(df, adx_threshold)


    def _get_or_build_features(self, df: pl.DataFrame,
                               adx_threshold: float) -> Optional[np.ndarray]:
        """Retourne ``X[:len(df)]`` aligné sur ``df``.

        En backtest, ``X`` couvre TOUTE la fenêtre (semé par
        ``prepare_for_backtest``) : l'index ``len(df)-1`` correspond donc bien à
        la barre courante. Correctif d'alignement : on (re)construit si le cache
        est absent OU plus court que ``len(df)`` — auparavant un X figé à la
        longueur du premier accès gelait les features de toutes les barres
        suivantes."""
        if self._bt_features_len == 0 or len(df) > self._bt_features_len:
            # Pas en mode backtest cached ou DataFrame plus long que prévu :
            # fallback au build classique (sur le slice de 250 barres).
            feat_window = df.slice(max(0, len(df) - 250), min(250, len(df)))
            return _build_features(feat_window, adx_threshold)
        key = round(float(adx_threshold), 6)
        X = self._bt_features_cache.get(key)
        if X is None or len(X) < len(df):
            X = self._build_full_features(adx_threshold)
            if X is None:
                return None
            self._bt_features_cache[key] = X
        return X[: len(df)]

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("warmup_bars", 2000)) + max(_LAGS) + 20

    @property
    def is_trained(self) -> bool:
        return bool(self._trained_tfs)

    @property
    def managed_externally(self) -> bool:
        return self._managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool):
        self._managed_externally = v

    def reset_model(self) -> None:
        with self._lock:
            self._amp_models.clear()
            self._dir_models.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._train_meta.clear()
            self._last_retrain.clear()
            self._managed_externally = False
            self._best_auc = 0.0
        self._bt_features_cache.clear()
        self._bt_features_len = 0
        self._bt_full_df = None
        self._diag_predictions.clear()
        self._diag_rejects.clear()
        self._diag_signals   = 0
        self._diag_last_dump = 0

    def _diag_record_reject(self, reason: str) -> None:
        self._diag_rejects[reason] = self._diag_rejects.get(reason, 0) + 1

    def _diag_dump_if_due(self, cnt: int, interval: int = 1000) -> None:
        """Logge périodiquement la distribution p_event/p_up + rejets."""
        if cnt - self._diag_last_dump < interval:
            return
        self._diag_last_dump = cnt
        if not self._diag_predictions:
            logger.info(
                f"[V4-diag] {cnt} appels — 0 prédiction (rejets : {dict(self._diag_rejects)})"
            )
            return
        arr     = np.asarray(self._diag_predictions, dtype=np.float64)
        p_event = arr[:, 0]
        p_up    = arr[:, 1]
        dist_up = np.abs(p_up - 0.5)
        logger.info(
            f"[V4-diag] {cnt} appels | {len(arr)} prédictions | signaux={self._diag_signals} | "
            f"p_event μ={p_event.mean():.3f} med={np.median(p_event):.3f} "
            f"p90={np.quantile(p_event, 0.9):.3f} max={p_event.max():.3f} | "
            f"|p_up-0.5| μ={dist_up.mean():.3f} p90={np.quantile(dist_up, 0.9):.3f} "
            f"max={dist_up.max():.3f} | rejets={dict(self._diag_rejects)}"
        )

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        p             = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold", 20.0))
        amp_top_pct   = float(p.get("amp_top_pct",   0.30))
        self._fit(df, adx_threshold=adx_threshold, amp_top_pct=amp_top_pct)

    def _fit(self, df: pl.DataFrame, timeframe: str = "",
             adx_threshold: float = 20.0, amp_top_pct: float = 0.30) -> None:
        tf_key = timeframe or "default"
        self._train(df, tf_key, adx_threshold, amp_top_pct)

    # ── Cœur ML : deux modèles séparés (amplitude + direction) ───────────────

    def _train(self, df: pl.DataFrame, tf_key: str,
               adx_threshold: float = 20.0,
               amp_top_pct: float = 0.30) -> bool:
        """Entraîne amplitude + direction — délègue à ``app.ml.recipe_trainer``.

        Étape C : ~141 lignes de boucle LightGBM (Dataset, scale_pos_weight,
        early stopping, calibration) vivaient ici, dupliquées d'une stratégie à
        l'autre. Elles appartiennent à la RECETTE, pas à la stratégie — c'est
        le diagnostic §2 de la conception, « la classe de stratégie est
        propriétaire de son modèle ».

        Ce qui RESTE ici est ce qui appartient légitimement à la stratégie :
        le cache de features du backtest (``_features_for_training``) et l'état
        ML en mémoire, indexé par ``tf_key`` — le TF hors ligne, le SYMBOLE
        depuis ``score()``.

        Équivalence vérifiée avant bascule : prédictions identiques à
        l'ancienne implémentation (``scripts/check_recipe_trainer_equivalence.py``).
        """
        from app.ml import features_catalog as _fc
        from app.ml import recipe_trainer as _rt

        X = self._features_for_training(df, adx_threshold)
        if X is None:
            return False
        recipe = (self.models or {}).get("signal")
        if not recipe:
            logger.error("[V4] aucune recette déclarée (models['signal'])")
            return False
        try:
            fs = _fc.from_matrix("stat48@1", X, df)
        except Exception as e:
            logger.warning("[V4] %s : emballage des features KO : %s" % (tf_key, e))
            return False

        out = _rt.train(recipe, df, tf_key, features=fs,
                        params={"adx_threshold": adx_threshold,
                                "amp_top_pct": amp_top_pct})
        if out is None or "amp" not in out.boosters or "dir" not in out.boosters:
            return False

        auc_amp = float(out.train_meta.get("auc_amp", 0.0))
        auc_dir = float(out.train_meta.get("auc_dir", 0.0))
        with self._lock:
            self._amp_models[tf_key]      = out.boosters["amp"]
            self._dir_models[tf_key]      = out.boosters["dir"]
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key] = (auc_amp + auc_dir) / 2.0
            self._train_meta[tf_key]      = dict(out.train_meta)
            self._best_auc = (auc_amp + auc_dir) / 2.0
        logger.info(
            "[V4] %s entraîné : %s train / %s val | AUC amp=%.3f dir=%.3f"
            % (tf_key, out.train_meta.get("n_train"), out.train_meta.get("n_valid"),
               auc_amp, auc_dir)
        )
        return True

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    def _save_key(self, tf_key: str) -> Optional[str]:
        """Clé du magasin de modèles à persister pour ``tf_key``.

        Le magasin est indexé selon l'appelant : ``fit()`` (chemin registre/
        gate) écrit sous ``"default"`` faute de TF, ``score()`` sous le
        SYMBOLE. ``save_model`` ne connaît, lui, que le TF déduit du nom de
        fichier — sans repli il ne trouvait rien et sortait en silence, ce qui
        se manifestait beaucoup plus loin par un « artefacts absents » du
        registre. Repli explicite : TF demandé, puis ``"default"``, puis
        l'unique modèle entraîné s'il n'y en a qu'un.
        """
        with self._lock:
            if tf_key in self._amp_models and tf_key in self._dir_models:
                return tf_key
            if "default" in self._amp_models and "default" in self._dir_models:
                return "default"
            keys = [k for k in self._amp_models if k in self._dir_models]
        return keys[0] if len(keys) == 1 else None

    def save_model(self, path: str) -> None:
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        key    = self._save_key(tf_key)
        if key is None:
            logger.warning(
                f"[V4] save_model({path}) : aucun modèle entraîné à persister "
                f"(TF demandé={tf_key!r}, clés disponibles={sorted(self._amp_models)})"
            )
            return
        with self._lock:
            amp  = self._amp_models.get(key)
            dir_ = self._dir_models.get(key)
            auc  = self._best_auc_per_tf.get(key, 0.0)
            meta = self._train_meta.get(key, {})
        if amp is None or dir_ is None:
            return
        # phase6 : plus de StandardScaler — on utilise save_lgb_with_scaler
        # avec scaler=None (rétro-compat avec le format existant).
        from app.ml.backend.persistence import save_lgb_with_scaler
        # Le meta porte le TF DEMANDÉ : c'est celui sous lequel l'artefact est
        # publié et rechargé, pas la clé interne dont il a été tiré.
        if save_lgb_with_scaler(amp, dir_, None, path, tf_key, auc, meta):
            logger.info(f"[V4] Modèles sauvegardés → {path} (AUC combiné={auc:.3f})")

    def load_model(self, path: str) -> bool:
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        from app.ml.backend.persistence import load_lgb_with_scaler
        data = load_lgb_with_scaler(path)
        if data is None:
            return False
        try:
            with self._lock:
                for key in (tf_key, "default"):
                    self._amp_models[key]      = data["amp_model"]
                    self._dir_models[key]      = data["dir_model"]
                    self._best_auc_per_tf[key] = float(data.get("best_auc", 0.0))
                    self._train_meta[key]      = data.get("train_meta", {})
                    self._trained_tfs.add(key)
                self._best_auc = float(data.get("best_auc", 0.0))
            logger.info(f"[V4] Modèles chargés depuis {path}")
            return True
        except Exception as e:
            logger.warning(f"[V4] Chargement échoué {path}: {e}")
            return False

    # ── score() — règle de décision §6.4 du rapport ───────────────────────────

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p              = (params or {}).get(self.name, {})
        adx_threshold  = float(p.get("adx_threshold",    20.0))
        amp_thresh_td  = float(p.get("amp_thresh_td",    0.50))
        dir_dist_td    = float(p.get("dir_dist_td",      0.10))
        amp_thresh_oth = float(p.get("amp_thresh_other", 0.60))
        dir_dist_oth   = float(p.get("dir_dist_other",   0.15))
        amp_top_pct    = float(p.get("amp_top_pct",      0.30))
        warmup_bars    = int(p.get("warmup_bars",        2000))
        retrain_every  = int(p.get("retrain_every",      800))

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        sym  = symbol or "default"
        cnt  = self._call_cnt.get(sym, 0) + 1
        self._call_cnt[sym] = cnt
        tf_key = sym

        # Walk-forward
        last       = self._last_retrain.get(tf_key, 0)
        need_train = (tf_key not in self._trained_tfs) or (cnt - last >= retrain_every)

        if need_train and not self._managed_externally:
            n_train  = min(len(df) - 1, warmup_bars * 2)
            train_df = df.slice(len(df) - n_train - 1, n_train)
            if self._train(train_df, tf_key, adx_threshold, amp_top_pct):
                self._last_retrain[tf_key] = cnt

        if tf_key not in self._trained_tfs:
            self._diag_record_reject("non_entraine")
            self._diag_dump_if_due(cnt)
            return self._none("Modèle non encore entraîné (warmup en cours)")

        # ── État courant ───────────────────────────────────────────────────
        c_now  = float(df["close"][-1] or 0.0)
        atr_v  = pre_val(df, "_pre_atr14")  or 0.0
        adx_v  = pre_val(df, "_pre_adx14")  or 0.0
        sma20  = pre_val(df, "_pre_sma20")  or c_now
        sma50  = pre_val(df, "_pre_sma50")  or c_now
        sma100 = pre_val(df, "_pre_sma100") or c_now
        sma200 = pre_val(df, "_pre_sma200") or c_now
        pdi    = pre_val(df, "_pre_pdi14")  or 0.0
        ndi    = pre_val(df, "_pre_ndi14")  or 0.0

        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime     = _detect_regime(adx_v, sma20, sma50, sma100, sma200, pdi, ndi, adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        # ── Filtre régime (§6.4) : Trend Up = NE PAS TRADER ────────────────
        if regime == REGIME_TREND_UP:
            self._diag_record_reject("regime_trend_up")
            self._diag_dump_if_due(cnt)
            return self._none(
                "Trend Up — AUC dir ≈ 0.50, pas d'edge (§6.4)",
                regime=regime,
            )

        # ── Prédictions LightGBM ───────────────────────────────────────────
        # Construction des features — utilise le cache pré-calculé en backtest
        # (gain ~50×) ou rebuild local sur 250 barres en live.
        X_full = self._get_or_build_features(df, adx_threshold)
        if X_full is None or len(X_full) == 0:
            self._diag_record_reject("features_manquantes")
            self._diag_dump_if_due(cnt)
            return self._none("Features manquantes")
        X_last = X_full[-1:]  # dernière ligne uniquement

        with self._lock:
            amp_m   = self._amp_models.get(tf_key)
            dir_m   = self._dir_models.get(tf_key)

        if amp_m is None or dir_m is None:
            self._diag_record_reject("modele_indispo")
            self._diag_dump_if_due(cnt)
            return self._none("Modèle indisponible")

        try:
            # phase6 : plus de StandardScaler — LightGBM est invariant aux
            # transformations monotones des features.
            p_event = float(amp_m.predict(X_last)[0])
            p_up    = float(dir_m.predict(X_last)[0])
        except Exception as e:
            logger.warning(f"[V4] Prédiction : {e}")
            self._diag_record_reject("erreur_predict")
            self._diag_dump_if_due(cnt)
            return self._none("Erreur prédiction")

        # Prédiction réussie : on l'enregistre pour la distribution.
        self._diag_predictions.append((p_event, p_up, regime))
        dir_dist = abs(p_up - 0.5)

        # ── Règle de décision (§6.4) ───────────────────────────────────────
        if regime == REGIME_TREND_DN:
            amp_thresh = amp_thresh_td   # 0.50
            dir_thresh = dir_dist_td     # 0.10
            size_fac   = 1.0
        else:  # Range ou Choppy (Trend Up déjà filtré)
            amp_thresh = amp_thresh_oth  # 0.60
            dir_thresh = dir_dist_oth    # 0.15
            size_fac   = 0.5

        if p_event < amp_thresh:
            self._diag_record_reject(f"amp_lt_{amp_thresh:.2f}_{regime_lbl}")
            self._diag_dump_if_due(cnt)
            return self._none(
                f"P(événement)={p_event:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        if dir_dist < dir_thresh:
            self._diag_record_reject(f"dir_lt_{dir_thresh:.2f}_{regime_lbl}")
            self._diag_dump_if_due(cnt)
            return self._none(
                f"|P(hausse)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        # Signal valide : on incrémente le compteur de signaux.
        self._diag_signals += 1
        self._diag_dump_if_due(cnt)

        side = "long" if p_up > 0.5 else "short"

        # ── Multiplicateur horaire (§6.5) ──────────────────────────────────
        hour     = _get_hour(df)
        hour_fac = _hour_multiplier(hour)
        size_fac *= hour_fac

        confidence = dir_dist * 2.0
        score      = round(min(0.55 + p_event * confidence * 0.39, 0.94), 3)

        meta    = self._train_meta.get(tf_key, {})
        auc_amp = meta.get("auc_amp", 0.0)
        auc_dir = meta.get("auc_dir", 0.0)

        return {
            "score":           score,
            "side":            side,
            "name":            self.name,
            "atr":             atr_v,
            "stop_hint":       round(c_now - 1.5 * atr_v if side == "long"
                                     else c_now + 1.5 * atr_v, 2),
            # ★ CLÉ DU RAPPORT : sortie à la clôture de la prochaine bougie (§5)
            "exit_after_bars": 1,
            "p_event":         round(p_event, 4),
            "p_up":            round(p_up, 4),
            "regime":          regime,
            "regime_lbl":      regime_lbl,
            "indicators": {
                "adx":         round(adx_v, 1),
                "rsi":         round(pre_val(df, "_pre_rsi14") or 50.0, 1),
                "p_event":     round(p_event, 4),
                "p_up":        round(p_up, 4),
                "dir_dist":    round(dir_dist, 4),
                "hour":        hour,
                "hour_mult":   hour_fac,
                "size_factor": size_fac,
                "auc_amp":     auc_amp,
                "auc_dir":     auc_dir,
            },
            "conditions": [
                f"Régime : {regime_lbl} (ADX={adx_v:.0f}) — autorisé ✓",
                f"P(événement) = {p_event:.2f} ≥ seuil {amp_thresh:.2f} ✓",
                f"P(hausse) = {p_up:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
                f"Heure {hour}h UTC ×{hour_fac:.1f} | Taille {size_fac:.0%}",
                f"AUC modèle : amp={auc_amp:.2f} dir={auc_dir:.2f}",
                "Sortie : clôture barre suivante (next-bar return, rapport §5)",
            ],
            "reason": (
                f"OpusV4 {side.upper()} | {regime_lbl} | "
                f"P(event)={p_event:.2f} P(up)={p_up:.2f} | "
                f"AUC dir={auc_dir:.2f}"
            ),
        }

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
