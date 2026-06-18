"""Stratégie ML à seuil dynamique — labels adaptatifs à la volatilité, filtre régime ADX."""

import logging
import math
import os
import random
import threading
import warnings
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import polars as pl
from sklearn.ensemble        import RandomForestClassifier
from sklearn.linear_model    import LogisticRegression
from sklearn.preprocessing   import StandardScaler
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
from sklearn.pipeline        import Pipeline
from sklearn.exceptions      import UndefinedMetricWarning

from app.engine.engine import BaseStrategyML
from app.core.indicators import rsi, atr_series, adx as _adx

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Feature Engineering — identique à ml_strategy (avancé)
# ═══════════════════════════════════════════════════════════════
def compute_features(df: pl.DataFrame) -> pl.DataFrame:
    """
    Construit 30+ features techniques depuis OHLCV.
    Inclut log-returns, VWAP rolling, micro-structure des bougies,
    divergence RSI/prix — plus robustes que les simples pct_change.
    """
    if len(df) < 100:
        return pl.DataFrame()

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]
    open_p = df["open"]

    # ── Log-Returns (normalisation statistique) ────────────────
    log_ret_1  = (close / close.shift(1).clip(lower_bound=1e-9)).log(math.e)
    log_ret_3  = (close / close.shift(3).clip(lower_bound=1e-9)).log(math.e)
    log_ret_5  = (close / close.shift(5).clip(lower_bound=1e-9)).log(math.e)
    log_ret_10 = (close / close.shift(10).clip(lower_bound=1e-9)).log(math.e)
    log_ret_20 = (close / close.shift(20).clip(lower_bound=1e-9)).log(math.e)

    # ── Rolling VWAP (Volume Weighted Average Price) ───────────
    typical_price = (high + low + close) / 3
    vwap_20 = ((typical_price * volume).rolling_mean(20) /
               volume.rolling_mean(20).clip(lower_bound=1e-9))
    vwap_50 = ((typical_price * volume).rolling_mean(50) /
               volume.rolling_mean(50).clip(lower_bound=1e-9))
    vwap_dist_20 = (close - vwap_20) / vwap_20.clip(lower_bound=1e-9)
    vwap_dist_50 = (close - vwap_50) / vwap_50.clip(lower_bound=1e-9)

    # ── Micro-structure : pression intra-bougie ────────────────
    body_size   = (close - open_p).abs()
    upper_wick  = high - pl.Series(np.maximum(open_p.to_numpy(), close.to_numpy()))
    lower_wick  = pl.Series(np.minimum(open_p.to_numpy(), close.to_numpy())) - low
    total_range = (high - low).clip(lower_bound=1e-9)
    body_to_range    = body_size   / total_range
    upper_wick_ratio = upper_wick  / total_range
    lower_wick_ratio = lower_wick  / total_range

    # ── RSI & divergence approximative ────────────────────────
    rsi_14_s    = rsi(close, 14)
    rsi_14      = rsi_14_s / 100.0
    price_slope = close.diff(5)
    rsi_slope   = rsi_14_s.diff(5)
    rsi_divergence = price_slope.sign() * rsi_slope.sign()

    # ── EMAs relatifs au prix ──────────────────────────────────
    ema_rels = {}
    for s in [8, 13, 21, 50, 100]:
        ema = close.ewm_mean(span=s, adjust=False)
        ema_rels[f"ema{s}_rel"] = (close - ema) / close.clip(lower_bound=1e-9)

    # ── Croisements EMA ───────────────────────────────────────
    ema8  = close.ewm_mean(span=8,  adjust=False)
    ema21 = close.ewm_mean(span=21, adjust=False)
    ema50 = close.ewm_mean(span=50, adjust=False)
    cross_8_21  = (ema8  - ema21) / close.clip(lower_bound=1e-9)
    cross_21_50 = (ema21 - ema50) / close.clip(lower_bound=1e-9)

    # ── MACD ──────────────────────────────────────────────────
    ema12 = close.ewm_mean(span=12, adjust=False)
    ema26 = close.ewm_mean(span=26, adjust=False)
    macd  = ema12 - ema26
    sig_  = macd.ewm_mean(span=9, adjust=False)
    macd_hist_norm = (macd - sig_) / close.clip(lower_bound=1e-9)
    macd_norm      = macd / close.clip(lower_bound=1e-9)

    # ── ATR relatif ───────────────────────────────────────────
    atr_rels = {}
    for period in [7, 14]:
        atr_s = atr_series(df, period)
        atr_rels[f"atr{period}_rel"] = atr_s / close.clip(lower_bound=1e-9)

    # ── Bollinger %B ──────────────────────────────────────────
    bb_feats = {}
    for period in [10, 20]:
        sma = close.rolling_mean(period)
        std = close.rolling_std(period).clip(lower_bound=1e-9)
        bb_feats[f"bb_pct_{period}"]   = (close - (sma - 2*std)) / (4 * std)
        bb_feats[f"bb_width_{period}"] = (4 * std) / sma.clip(lower_bound=1e-9)

    # ── Momentum ──────────────────────────────────────────────
    mom_5  = close / close.shift(5).clip(lower_bound=1e-9) - 1
    mom_20 = close / close.shift(20).clip(lower_bound=1e-9) - 1

    # ── Volume ────────────────────────────────────────────────
    vol_ma        = volume.rolling_mean(20).clip(lower_bound=1e-9)
    vol_ratio_s   = volume / vol_ma
    vol_trend_5   = vol_ma.pct_change(5)
    # Rolling price-volume correlation via vectorized sliding window
    c_arr = close.to_numpy()
    v_arr = volume.to_numpy()
    win   = 10
    n     = len(c_arr)
    corr_arr = np.full(n, np.nan)
    if n >= win:
        from numpy.lib.stride_tricks import sliding_window_view
        c_wins = sliding_window_view(c_arr, win)
        v_wins = sliding_window_view(v_arr, win)
        c_mean = c_wins.mean(axis=1)
        v_mean = v_wins.mean(axis=1)
        cov    = ((c_wins - c_mean[:, None]) * (v_wins - v_mean[:, None])).mean(axis=1)
        std_c  = c_wins.std(axis=1)
        std_v  = v_wins.std(axis=1)
        valid  = (std_c > 0) & (std_v > 0)
        corr_valid = np.where(valid, cov / (std_c * std_v + 1e-9), 0.0)
        corr_arr[win - 1:] = corr_valid
    vol_price_corr = pl.Series(corr_arr)

    # ── Volatilité réalisée ───────────────────────────────────
    ret_pct   = close.pct_change(1)
    vol_real_5  = ret_pct.rolling_std(5)
    vol_real_20 = ret_pct.rolling_std(20)
    vol_ratio_rv = vol_real_5 / vol_real_20.clip(lower_bound=1e-9)

    # ── ADX ───────────────────────────────────────────────────
    adx_14 = _adx(df, 14)[0]

    # ── High/Low range normalisé ──────────────────────────────
    hl_range  = (high - low) / close.clip(lower_bound=1e-9)
    close_pos = (close - low) / (high - low + 1e-9)

    # ── Assemble feature dict ─────────────────────────────────
    feat_dict: Dict[str, pl.Series] = {
        "log_ret_1":        log_ret_1,
        "log_ret_3":        log_ret_3,
        "log_ret_5":        log_ret_5,
        "log_ret_10":       log_ret_10,
        "log_ret_20":       log_ret_20,
        "vwap_dist_20":     vwap_dist_20,
        "vwap_dist_50":     vwap_dist_50,
        "body_to_range":    body_to_range,
        "upper_wick_ratio": upper_wick_ratio,
        "lower_wick_ratio": lower_wick_ratio,
        "rsi_14":           rsi_14,
        "rsi_divergence":   rsi_divergence,
        **ema_rels,
        "cross_8_21":       cross_8_21,
        "cross_21_50":      cross_21_50,
        "macd_hist_norm":   macd_hist_norm,
        "macd_norm":        macd_norm,
        **atr_rels,
        **bb_feats,
        "mom_5":            mom_5,
        "mom_20":           mom_20,
        "vol_ratio":        vol_ratio_s,
        "vol_trend_5":      vol_trend_5,
        "vol_price_corr":   vol_price_corr,
        "vol_real_5":       vol_real_5,
        "vol_real_20":      vol_real_20,
        "vol_ratio_rv":     vol_ratio_rv,
        "adx_14":           adx_14,
        "hl_range":         hl_range,
        "close_pos":        close_pos,
    }

    result = pl.DataFrame(feat_dict)
    # Replace NaN and inf with null, then fill all nulls with 0
    result = result.with_columns([
        pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite()).then(None).otherwise(pl.col(c)).alias(c)
        for c in result.columns
    ]).fill_null(0.0)
    return result


# ═══════════════════════════════════════════════════════════════
#  Labeling DYNAMIQUE — seuil adaptatif basé sur la volatilité
# ═══════════════════════════════════════════════════════════════
def compute_labels(df: pl.DataFrame, lookahead: int = 3,
                   vol_multiplier: float = 0.6) -> pl.Series:
    """
    Le seuil de hausse n'est pas fixe (ex: +0.2%) mais adaptatif :
      seuil = volatilité_20p * sqrt(lookahead) * vol_multiplier

    Le seuil est décalé d'une barre (shift(1)) pour éviter le lookahead bias :
    le seuil à la barre t n'utilise que la volatilité calculée jusqu'à t-1.

    Avantage : en range serré le seuil est bas (→ plus de signaux valides),
    en tendance forte il est plus élevé (→ filtre les micro-retours).
    """
    close    = df["close"]
    log_ret  = (close / close.shift(1).clip(lower_bound=1e-9)).log(math.e)
    volatility         = log_ret.rolling_std(20)
    # Shift threshold by 1 bar to prevent current-bar information leaking into labels
    dynamic_threshold  = volatility.shift(1) * math.sqrt(lookahead) * vol_multiplier
    future = (close.shift(-lookahead) / close.clip(lower_bound=1e-9)).log(math.e)
    return (future > dynamic_threshold).cast(pl.Int32)


# ═══════════════════════════════════════════════════════════════
#  Espaces de paramètres
# ═══════════════════════════════════════════════════════════════
PARAM_SPACE_RF = {
    "clf__n_estimators":      [100, 200, 300],
    "clf__max_depth":         [3, 5, 8, None],
    "clf__min_samples_leaf":  [5, 10, 20],
    "clf__min_samples_split": [10, 20],
    "clf__class_weight":      ["balanced", None],
}

PARAM_SPACE_LR = {
    "clf__C":            [0.1, 1.0, 10.0],
    "clf__solver":       ["lbfgs", "saga"],
    "clf__class_weight": [None, "balanced"],
}


def random_search_hyperparams(
    X: np.ndarray, y: np.ndarray,
    model_type: str = "random_forest",
    n_trials: int = 20,
    cv_folds: int = 5,
    seed: int = 42,
    cancel_event=None,
) -> Tuple[dict, float, List[dict]]:
    """
    Random Search avec TimeSeriesSplit.
    Correction UndefinedMetricWarning : utilise np.nanmean pour ignorer
    les folds mono-classe sans crasher.
    """
    rng          = random.Random(seed)
    space        = PARAM_SPACE_RF if model_type == "random_forest" else PARAM_SPACE_LR

    # Adapt folds to data: with TimeSeriesSplit the smallest train fold is n/(n_splits+1).
    # If that fold lacks minority class samples, cross_val_score returns all-NaN scores.
    # Limit folds so each fold has at least 3 minority-class samples in training.
    min_class_count = int(np.bincount(y.astype(int)).min()) if len(y) > 0 else 0
    max_folds_by_class = max(2, min_class_count // 3)
    max_folds_by_size  = max(2, len(y) // 20)
    actual_folds = min(cv_folds, 5, max_folds_by_class, max_folds_by_size)
    if actual_folds < 2:
        return {}, 0.0, []

    tscv        = TimeSeriesSplit(n_splits=actual_folds)
    best_params = {}
    best_score  = -1.0
    all_results: List[dict] = []

    logger.info(f"[ml_dynamic_threshold] Random Search — {n_trials} essais, modèle={model_type}")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UndefinedMetricWarning)
        warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
        for trial in range(n_trials):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("[ml_dynamic_threshold] Entraînement interrompu (annulation backtest)")
                break
            params = {k: rng.choice(v) for k, v in space.items()}
            try:
                pipeline = _build_pipeline(model_type, params)
                # np.errstate : une colonne de features entièrement NaN fait
                # diviser 0/0 dans StandardScaler (sklearn extmath) → bruit de
                # RuntimeWarning « invalid value encountered in divide ». Bénin.
                with np.errstate(invalid="ignore", divide="ignore"):
                    scores   = cross_val_score(
                        pipeline, X, y, cv=tscv,
                        scoring="roc_auc", n_jobs=1,  # n_jobs=1 pour ne pas bloquer l'annulation
                        error_score=np.nan,   # NaN sur fold mono-classe au lieu de crash
                    )
                if np.isnan(scores).all():
                    continue  # tous les folds invalides → skip
                mean_auc = float(np.nanmean(scores))
                std_auc  = float(np.nanstd(scores))
                all_results.append({"params": params, "auc": mean_auc, "std": std_auc})
                if mean_auc > best_score:
                    best_score, best_params = mean_auc, params
                    logger.info(
                        f"  [ml_dynamic_threshold][Trial {trial+1:02d}] Nouveau meilleur AUC={mean_auc:.4f}"
                        f"±{std_auc:.4f} | {params}"
                    )
            except Exception as e:
                logger.debug(f"  [Trial {trial+1:02d}] KO : {e}")

    logger.info(f"[ml_dynamic_threshold] Meilleur AUC={best_score:.4f} | {best_params}")
    all_results.sort(key=lambda x: -x["auc"])
    return best_params, best_score, all_results


def _build_pipeline(model_type: str, params: dict) -> Pipeline:
    clf_params = {k.replace("clf__", ""): v for k, v in params.items() if k.startswith("clf__")}
    if model_type == "random_forest":
        clf = RandomForestClassifier(random_state=42, **clf_params)
    else:
        clf = LogisticRegression(random_state=42, max_iter=2000, solver="saga", **clf_params)
    return Pipeline([("scaler", StandardScaler()), ("clf", clf)])


# ═══════════════════════════════════════════════════════════════
#  Détection automatique du timeframe depuis un DataFrame OHLCV
# ═══════════════════════════════════════════════════════════════
_SECS_TO_TF: Dict[int, str] = {
    60: "1m", 180: "3m", 300: "5m", 900: "15m",
    1800: "30m", 3600: "1h", 14400: "4h", 86400: "1d",
}

def _detect_tf(df: pl.DataFrame) -> str:
    """Infère le timeframe depuis l'intervalle médian entre les barres."""
    if "time" not in df.columns or len(df) < 2:
        return "unknown"
    try:
        t_int = df["time"].tail(64).cast(pl.Int64).to_numpy()  # TF constant: tail O(1)
        median_diff = int(np.median(np.diff(t_int)))
        # Polars Datetime est en microsecondes ; millisecondes ou secondes sinon
        if median_diff > 1_000_000_000:
            secs = median_diff // 1_000_000_000   # nanosecondes
        elif median_diff > 1_000_000:
            secs = median_diff // 1_000_000        # microsecondes (polars défaut)
        elif median_diff > 1_000:
            secs = median_diff // 1_000            # millisecondes
        else:
            secs = median_diff                     # déjà en secondes
        closest = min(_SECS_TO_TF, key=lambda k: abs(k - secs))
        if abs(closest - secs) <= max(closest * 0.15, 5):
            return _SECS_TO_TF[closest]
        return f"custom_{secs}s"
    except Exception as e:
        logger.debug(f"[MLDynThreshold] infer timeframe KO : {e}")
        return "unknown"


# ═══════════════════════════════════════════════════════════════
#  Classe Strategy — interface compatible moteur v7
# ═══════════════════════════════════════════════════════════════
class MLDynamicThresholdStrategy(BaseStrategyML):
    """
    Stratégie ML à seuil dynamique.
    Interface identique aux autres stratégies v6 : méthode score() retourne
    un dict {score, side, name, reason, conditions, indicators}.

    Hérite de BaseStrategyML → exclue automatiquement de l'auto_optimizer
    (elle gère son propre random search interne à chaque fit()).
    Les outer-params ci-dessous sont utilisables via strategy_params dans config.yaml.

    Modes de réentraînement :
    - managed_externally=False (défaut, backtest) : réentraînement inline toutes les
      `retrain_every` itérations, dans le thread courant.
    - managed_externally=True (live) : le LiveTrader planifie fit() en arrière-plan ;
      score() utilise le modèle existant sans bloquer la boucle de trading.
    """
    name = "ml_dynamic_threshold"
    retrain_interval_h: int = 6
    model_dir: str = "models"

    timeframes: List[str] = ["5m", "15m", "1h"]

    param_space: Dict[str, List] = {
        "model_type":     ["random_forest", "logistic_regression"],
        "lookahead":      [2, 3, 5],
        "vol_multiplier": [0.4, 0.6, 0.8],
        "adx_min":        [15.0, 20.0, 25.0],
        "proba_long":     [0.55, 0.60, 0.65],
        "proba_short":    [0.35, 0.40, 0.45],
    }

    fixed_params: Dict[str, Any] = {
        # 8 trials suffisent : le random search interne converge typiquement
        # avant et la marge marginale au-delà ne justifie pas le coût
        # (chaque trial = 1 fit complet avec TimeSeriesSplit). Réduction 15→8.
        "n_trials":      8,
        "min_train":     150,
        "retrain_every": 50,
        # Le random search interne (n_trials × TimeSeriesSplit ≈ 40 fits) ne
        # tourne qu'au 1er fit puis tous les `hyper_search_every` refits — les
        # refits intermédiaires réutilisent les meilleurs hyperparams et ne
        # coûtent qu'UN fit. Sans cela, un backtest 50k bougies = ~1000 random
        # searches complets (heures de calcul).
        "hyper_search_every": 20,
        # Fenêtre d'entraînement glissante (walk-forward) : borne le coût de
        # chaque fit, qui croissait avec la taille de la fenêtre du backtest.
        "max_train_window": 6000,
    }

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        min_train = int(p.get("min_train", self.fixed_params["min_train"]))
        return min_train + 50

    def __init__(self,
                 model_type:     str   = "random_forest",
                 lookahead:      int   = 3,
                 vol_multiplier: float = 0.6,
                 adx_min:        float = 20.0,
                 proba_long:     float = 0.60,
                 proba_short:    float = 0.40,
                 n_trials:       int   = 15,
                 min_train:      int   = 150,
                 retrain_every:  int   = 50,
                 hyper_search_every: int = 20,
                 max_train_window:   int = 6000):

        self.model_type     = model_type
        self.lookahead      = lookahead
        self.vol_multiplier = vol_multiplier
        self.adx_min        = adx_min
        self.proba_long     = proba_long
        self.proba_short    = proba_short
        self.n_trials       = n_trials
        self.min_train      = min_train
        self.retrain_every  = retrain_every
        self.hyper_search_every = hyper_search_every
        self.max_train_window   = max_train_window
        self._fit_count_per_tf: Dict[str, int] = {}
        # TFs désactivés manuellement (ex: ["1h"] pour désactiver seulement le 1h).
        self.disabled_timeframes: List[str] = []

        # ── Stockage multi-TF : un pipeline distinct par timeframe ──────────
        # Clé : TF détecté (ex: "5m", "1h"). Valeur : sklearn Pipeline entraîné.
        self._pipelines:        Dict[str, Any]       = {}   # tf → Pipeline
        self._best_auc_per_tf:  Dict[str, float]     = {}   # tf → AUC
        self._best_params_per_tf: Dict[str, dict]    = {}   # tf → hyperparams
        self._feature_cols_per_tf: Dict[str, List]   = {}   # tf → feature names
        self._call_count_per_tf: Dict[str, int]      = {}   # tf → appels
        self._trained_tfs:      set                  = set()

        # Compatibilité rétrograde : _best_auc reflète le dernier TF entraîné
        self._best_auc:     float    = 0.0
        self._best_params:  dict     = {}
        self._feature_cols: List[str] = []

        # Thread-safety : protège les lectures/écritures du modèle lors du
        # réentraînement en arrière-plan (live managed_externally=True).
        self._model_lock:   threading.RLock = threading.RLock()
        # Mis à True par le LiveTrader pour désactiver le réentraînement inline.
        self.managed_externally: bool = False
        # Signal d'annulation (positionné par le backtest runner pour arrêter proprement).
        self._cancel_event: Optional[threading.Event] = None
        # Cache backtest : voir prepare_for_backtest.
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les ~30 features pour toute la fenêtre du backtest.

        Sans ce cache, ``compute_features`` est appelé à chaque ``score()``,
        soit ~5000 rebuilds redondants sur un backtest long (rolling VWAP,
        log-returns, divergences RSI/prix, micro-structure des bougies…).
        """
        try:
            # Catalogue FeatureStore : provider 'ml_dyn_threshold' (consommation
            # positionnelle via to_numpy → include_time=False pour préserver le
            # schéma exact de compute_features).
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="ml_dyn_threshold", version="1",
                builder=lambda w: compute_features(w),
                in_kind="polars", out_kind="polars", include_time=False)
            if feats is not None and len(feats) > 0:
                self._bt_features = feats
                self._bt_features_len = len(df)
                logger.info(
                    f"[MLDynThr] backtest : features pré-calculées sur "
                    f"{self._bt_features_len} bougies ({len(feats.columns)} colonnes)"
                )
        except Exception as e:
            logger.warning(f"[MLDynThr] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    def _get_or_build_features(self, df: pl.DataFrame) -> pl.DataFrame:
        """Cache-aware wrapper autour de ``compute_features``."""
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            return self._bt_features.head(len(df))
        return compute_features(df)

    # ── Interface principale ───────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None, df_htf=None, symbol: str = "") -> Dict[str, Any]:
        """
        Interface compatible Engine v7 (multi-TF).
        Applique les params de config si aucun TF n'est encore entraîné.
        disabled_timeframes est relu à chaque appel pour refléter les changements à chaud.
        """
        if params:
            p = params.get(self.name, {})
            if p:
                # disabled_timeframes est appliqué à chaque appel (hot-reload).
                if "disabled_timeframes" in p:
                    self.disabled_timeframes = list(p["disabled_timeframes"])
                if not self._trained_tfs:
                    for k, v in p.items():
                        if k != "disabled_timeframes" and hasattr(self, k):
                            setattr(self, k, v)
        return self._signal(df)

    def _signal(self, df: pl.DataFrame) -> Dict[str, Any]:
        if self._cancel_event is not None and self._cancel_event.is_set():
            return self._no_signal()
        if len(df) < self.min_train + 20:
            return self._no_signal()

        tf = _detect_tf(df)
        if self.disabled_timeframes and tf in self.disabled_timeframes:
            return self._no_signal()
        cnt = self._call_count_per_tf.get(tf, 0) + 1
        self._call_count_per_tf[tf] = cnt

        if tf not in self._trained_tfs:
            if self.managed_externally:
                # En live : le trainer gère l'entraînement en arrière-plan.
                # Retourner no_signal tant que le modèle n'est pas prêt pour ce TF.
                return self._no_signal()
            # Mode backtest / fallback : premier entraînement synchrone pour ce TF.
            self._fit(df, tf)
        elif not self.managed_externally and cnt % self.retrain_every == 0:
            # Backtest walk-forward : réentraînement périodique par TF.
            self._fit(df, tf)

        if tf not in self._trained_tfs:
            return self._no_signal()

        return self._predict(df, tf)

    # ── Entraînement ──────────────────────────────────────────
    def _fit(self, df: pl.DataFrame, tf: str = "") -> None:
        try:
            # Réutilise le cache backtest si dispo (peuplé par
            # ``prepare_for_backtest``) au lieu de rebuilder les ~30 features
            # à chaque fit (random search interne × n_trials × N retrains).
            feats  = self._get_or_build_features(df)
            labels = compute_labels(df, self.lookahead, self.vol_multiplier)

            # Position-based alignment:
            # feats has same length as df (no dropna — filled with 0)
            # labels has null at last lookahead positions
            # valid range: [0 : n - 2*lookahead] (extra margin for safety)
            n       = len(df)
            n_valid = max(0, n - 2 * self.lookahead)
            X       = feats[:n_valid].to_numpy()
            # fill_null(0) évite les NaN qui transforment Int32→float64 en numpy
            y       = labels[:n_valid].fill_null(0).to_numpy().astype(np.int64)

            # Fenêtre glissante : borne le coût d'entraînement sur les longues
            # fenêtres de backtest (le fit croissait en O(n) avec la fenêtre).
            max_win = int(self.max_train_window or 0)
            if max_win > 0 and len(X) > max_win:
                X = X[-max_win:]
                y = y[-max_win:]

            # Sanitisation NaN/inf — indispensable avant StandardScaler.
            # compute_features nettoie déjà côté polars, mais certaines fenêtres
            # de marché réelles laissent passer des NaN/inf (colonne entièrement
            # nulle sur une fenêtre courte, division par variance nulle…). Sans
            # ce garde-fou, une colonne all-NaN fait diverger StandardScaler
            # (sample_count=0 → moyenne/échelle NaN), les prédictions deviennent
            # NaN, tous les roc_auc sont NaN → « Aucun essai valide ». Même
            # protection que scoring_statistique_opus_v4/v5.
            X = np.nan_to_num(X.astype(np.float64), nan=0.0, posinf=1.0, neginf=-1.0)

            # Sécurité : deux classes minimum
            if len(np.unique(y)) < 2:
                logger.warning(
                    f"[{self.name}] Fit annulé : une seule classe dans les labels. "
                    "Augmentez min_train ou réduisez vol_multiplier."
                )
                return

            # Sécurité : effectif minimum par classe
            class_counts = np.bincount(y)
            if class_counts.min() < 10:
                logger.warning(
                    f"[{self.name}] Fit annulé : classe minoritaire trop petite "
                    f"({class_counts.min()} exemples)."
                )
                return

            # Random search complet seulement au 1er fit du TF puis tous les
            # `hyper_search_every` refits ; entre-temps, réutilisation des
            # meilleurs hyperparams (le refit ne coûte alors qu'UN fit).
            tf_key = tf or _detect_tf(df)
            fit_no = self._fit_count_per_tf.get(tf_key, 0)
            self._fit_count_per_tf[tf_key] = fit_no + 1
            hyper_every = max(int(self.hyper_search_every or 1), 1)
            prev_p = self._best_params_per_tf.get(tf_key)
            ran_search = not (prev_p and fit_no % hyper_every != 0)
            if not ran_search:
                best_p, best_auc = prev_p, self._best_auc_per_tf.get(tf_key, 0.0)
            else:
                # La recherche d'hyperparams (n_trials × CV) tourne sur la fin
                # de fenêtre (3000 barres max) : la sélection d'hyperparams n'a
                # pas besoin de tout l'historique, le fit final si (max_train_window).
                _hs_cap = 3000
                X_hs, y_hs = (X[-_hs_cap:], y[-_hs_cap:]) if len(X) > _hs_cap else (X, y)
                best_p, best_auc, _ = random_search_hyperparams(
                    X_hs, y_hs, self.model_type, self.n_trials,
                    cancel_event=self._cancel_event,
                )

            # Ne pas continuer si annulé après le random search
            if self._cancel_event is not None and self._cancel_event.is_set():
                logger.info(f"[{self.name}] Entraînement annulé après random search")
                return

            if not best_p:
                # Repli sur les hyperparamètres sklearn par défaut plutôt que
                # d'abandonner l'entraînement : la recherche peut ne retenir
                # aucun essai sur une fenêtre difficile (CV dégénérée, AUC NaN),
                # mais un modèle par défaut reste exploitable et évite de laisser
                # le TF sans modèle pour tout le reste du backtest.
                logger.warning(
                    f"[{self.name}] Random search sans essai valide — "
                    f"repli sur hyperparamètres par défaut ({self.model_type})"
                )
                best_p, best_auc = {}, 0.0

            pipeline = _build_pipeline(self.model_type, best_p)
            with np.errstate(invalid="ignore", divide="ignore"):  # cf. note CV ci-dessus
                pipeline.fit(X, y)
            tf = tf or _detect_tf(df)

            with self._model_lock:
                self._pipelines[tf]          = pipeline
                self._best_params_per_tf[tf] = best_p
                self._best_auc_per_tf[tf]    = best_auc
                self._feature_cols_per_tf[tf] = list(feats.columns)
                self._trained_tfs.add(tf)
                # Compat rétrograde (logs du trainer, etc.)
                self._best_auc     = best_auc
                self._best_params  = best_p
                self._feature_cols = list(feats.columns)

            # ── Validation IS/OOS — détection sur-apprentissage ───────────
            # Diagnostic coûteux (un fit supplémentaire) : uniquement quand le
            # random search a tourné, pas sur les refits à hyperparams réutilisés.
            try:
                split = int(len(X) * 0.8)
                if ran_search and split > 20 and len(X) - split > 10:
                    from sklearn.metrics import roc_auc_score as _auc
                    pipe_oos = _build_pipeline(self.model_type, best_p)
                    with np.errstate(invalid="ignore", divide="ignore"):  # cf. note CV
                        pipe_oos.fit(X[:split], y[:split])
                    proba_oos = pipe_oos.predict_proba(X[split:])[:, 1]
                    oos_auc   = float(_auc(y[split:], proba_oos))
                    ratio     = best_auc / max(oos_auc, 1e-9)
                    status    = "✅ robuste" if ratio < 1.3 else "⚠️ surapprentissage probable"
                    logger.info(
                        f"[{self.name}/{tf}] IS AUC={best_auc:.4f} | OOS AUC={oos_auc:.4f} "
                        f"| Ratio IS/OOS={ratio:.2f} — {status}"
                    )
            except Exception as oe:
                logger.debug(f"[{self.name}/{tf}] IS/OOS check KO : {oe}")

            logger.info(
                f"[{self.name}/{tf}] Modèle entraîné — AUC={best_auc:.4f} | n={len(X)}"
            )
        except Exception as e:
            logger.error(f"[{self.name}] Erreur entraînement : {e}")

    # ── Prédiction ────────────────────────────────────────────
    def _predict(self, df: pl.DataFrame, tf: str = "") -> Dict[str, Any]:
        tf = tf or _detect_tf(df)
        try:
            feats = self._get_or_build_features(df)
            if len(feats) == 0:
                return self._no_signal()

            # Filtre ADX — régime de marché. Borne la fenêtre : adx() fait diff()
            # + 4 ewm_mean() sur tout le df et on ne lit que la dernière valeur →
            # O(n)/barre (O(n²) en backtest). Le lissage Wilder converge bien
            # avant 300 barres → dernière valeur identique.
            adx_val = float(_adx(df.tail(300), 14)[0][-1])
            auc_tf  = self._best_auc_per_tf.get(tf, self._best_auc)
            if adx_val < self.adx_min:
                return {
                    "score":      0.0,
                    "side":       "none",
                    "name":       self.name,
                    "reason":     f"Filtre régime : ADX={adx_val:.1f} < {self.adx_min} [{tf}]",
                    "conditions": [f"ADX insuffisant ({adx_val:.1f})"],
                    "indicators": {"adx": round(adx_val, 2), "auc": round(auc_tf, 4), "tf": tf},
                }

            # Même sanitisation qu'à l'entraînement : le StandardScaler embarqué
            # propagerait des NaN en sortie sinon (et predict_proba renverrait NaN).
            X_last = np.nan_to_num(
                feats[-1:].to_numpy().astype(np.float64),
                nan=0.0, posinf=1.0, neginf=-1.0,
            )
            with self._model_lock:
                pipeline = self._pipelines.get(tf)
            if pipeline is None:
                return self._no_signal()
            proba  = float(pipeline.predict_proba(X_last)[0, 1])
            close  = float(df["close"][-1])

            if proba >= self.proba_long:
                side  = "long"
                score = 0.5 + (proba - self.proba_long) / max(1.0 - self.proba_long, 1e-9) * 0.5
            elif proba <= self.proba_short:
                side  = "short"
                score = 0.5 + (self.proba_short - proba) / max(self.proba_short, 1e-9) * 0.5
            else:
                return self._no_signal()

            score = round(min(score, 0.99), 3)

            indicators = {
                "proba_up":      round(proba, 4),
                "model":         self.model_type,
                "auc":           round(auc_tf, 4),
                "adx":           round(adx_val, 2),
                "tf":            tf,
                "n_features":    len(self._feature_cols_per_tf.get(tf, self._feature_cols)),
                "lookahead":     self.lookahead,
                "vol_multiplier":self.vol_multiplier,
                "close":         close,
            }

            return {
                "score":      score,
                "side":       side,
                "name":       self.name,
                "reason":     (
                    f"ML-DynThreshold {self.model_type}/{tf} — proba_up={proba:.3f} "
                    f"(long≥{self.proba_long}, short≤{self.proba_short}, ADX={adx_val:.1f})"
                ),
                "conditions": [
                    f"Proba hausse : {proba:.3f}",
                    f"Modèle : {self.model_type} [{tf}]",
                    f"AUC cross-val : {auc_tf:.4f}",
                    f"Lookahead : {self.lookahead} bougies",
                    f"Seuil dynamique (vol_mult={self.vol_multiplier})",
                    f"ADX : {adx_val:.1f}",
                ],
                "indicators": indicators,
            }

        except Exception as e:
            logger.error(f"[{self.name}] Erreur prédiction : {e}")
            return self._no_signal()

    @staticmethod
    def _no_signal() -> Dict[str, Any]:
        return {
            "score":      0.0,
            "side":       "none",
            "name":       "ml_dynamic_threshold",
            "reason":     "Pas de signal ML",
            "conditions": [],
            "indicators": {},
        }

    # ── Contrat BaseStrategyML ─────────────────────────────────────────────

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        """Entraîne le modèle pour le TF détecté depuis df (appelé par MLStrategyTrainer)."""
        if params:
            p = params.get(self.name, {})
            for k, v in p.items():
                if hasattr(self, k):
                    setattr(self, k, v)
        tf = _detect_tf(df)
        self._fit(df, tf)

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        """Génère un signal sans réentraîner (modèle déjà chargé en mémoire)."""
        return self._predict(df)

    def save_model(self, path: str) -> None:
        """Persiste le modèle du TF encodé dans le chemin (ex: models/{name}_{tf}.pkl)."""
        # Extrait le TF depuis le nom de fichier : "ml_dynamic_threshold_1h.pkl" → "1h"
        tf = os.path.basename(path).rsplit("_", 1)[-1].split(".")[0]
        with self._model_lock:
            pipeline = self._pipelines.get(tf)
        if pipeline is None:
            logger.debug(f"[{self.name}] save_model: aucun pipeline pour {tf}, skip")
            return
        try:
            import joblib
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = {
                "pipeline":     pipeline,
                "best_params":  self._best_params_per_tf.get(tf, {}),
                "best_auc":     self._best_auc_per_tf.get(tf, 0.0),
                "feature_cols": self._feature_cols_per_tf.get(tf, []),
                "model_type":   self.model_type,
            }
            joblib.dump(payload, path)
            logger.info(f"[{self.name}/{tf}] Modèle sauvegardé → {path}")
        except Exception as e:
            logger.warning(f"[{self.name}/{tf}] Sauvegarde modèle KO : {e}")

    def load_model(self, path: str) -> bool:
        """Charge le modèle et l'enregistre sous le TF encodé dans le chemin."""
        if not os.path.exists(path):
            return False
        tf = os.path.basename(path).rsplit("_", 1)[-1].split(".")[0]
        try:
            import joblib
            data = joblib.load(path)
            auc  = data.get("best_auc", 0.0)
            with self._model_lock:
                self._pipelines[tf]           = data["pipeline"]
                self._best_params_per_tf[tf]  = data.get("best_params", {})
                self._best_auc_per_tf[tf]     = auc
                self._feature_cols_per_tf[tf] = data.get("feature_cols", [])
                self._trained_tfs.add(tf)
                # Compat rétrograde
                self._best_auc     = auc
                self._best_params  = data.get("best_params", {})
                self._feature_cols = data.get("feature_cols", [])
            logger.info(f"[{self.name}/{tf}] Modèle chargé depuis {path} (AUC={auc:.4f})")
            return True
        except Exception as e:
            logger.warning(f"[{self.name}/{tf}] Chargement modèle KO : {e}")
            return False

    def reset_model(self) -> None:
        """Réinitialise tous les modèles (walk-forward backtest, tests)."""
        with self._model_lock:
            self._pipelines.clear()
            self._best_auc_per_tf.clear()
            self._best_params_per_tf.clear()
            self._feature_cols_per_tf.clear()
            self._call_count_per_tf.clear()
            self._trained_tfs.clear()
            self._best_auc     = 0.0
            self._best_params  = {}
            self._feature_cols = []
        # Invalide le cache de features (un nouveau backtest peut suivre).
        self._bt_features = None
        self._bt_features_len = 0

    @property
    def is_trained(self) -> bool:
        return len(self._trained_tfs) > 0


# Alias Engine
class Strategy(MLDynamicThresholdStrategy):
    pass
