"""Stratégie Opus Omnibus V11 — FollowSetup.

Variante de V11 dont l'unique signal de sortie est le **changement de direction
du setup actif**. Les positions ne sont plus bornées par un TP, un trailing,
un SL serré ou une sortie temporelle : tant que le setup courant pointe dans
la même direction que la position ouverte, la position reste vivante et
maximise son exposition. Dès qu'un setup de direction opposée s'active, la
position est clôturée (et l'engine ouvrira la nouvelle position au tick
suivant via son flux standard).

Différences avec V11 :
  * Pas de filtre horaire / jours actifs.
  * Pas de TP, pas de trailing, pas d'``exit_after_bars``.
  * SL "safety net" très large (``safety_sl_atr_mult``, défaut 10 × ATR),
    utilisé uniquement pour le dimensionnement de la position côté backtest.
  * Pas de ``signal_up_dynamic_risk`` (taille fixe par setup).
  * Pas d'``exit_td_window`` ; le setup ``LONG_EXIT_TD`` est retiré.
  * Pas de calcul d'importance des features ni de pruning (le cache
    feature_store reste partagé pour la performance).
  * ``check_early_exit`` rejoue le routing de setups sur la dernière bougie
    et retourne ``setup_flip_to_<NAME>`` dès que la direction diverge.

Fichier autonome — duplication assumée du pipeline V4 et du routing V10
pour qu'une suppression de la branche V11 ne casse jamais cette stratégie.
"""

import datetime as _dt
import gc
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import (
    pre_val,
)
from app.core.indicators import (
    safe_num as _safe_num,
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

# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline V4 dupliqué (FeatureBuilder polars + régime + setups V10 sans EXIT_TD).
# ─────────────────────────────────────────────────────────────────────────────
_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")

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


# ── Setups OMNIBUS — variante FollowSetup ────────────────────────────────────
# Identique au routing V10/V11 mais LONG_EXIT_TD est retiré (plus de notion
# d'exit_td_window dans cette variante) et les size_factor sont uniformisés
# à 1.0 (pas d'adaptation par régime ni de boost SHORT_TD_HIGH).
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "SIGNAL_UP", "priority": -1, "direction": 1, "enabled": True,
        "regime": None, "needs_bearish_excess": True,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.60,
        "size_factor": 1.0,
    },
    {
        "name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_bearish_excess": False,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
        "size_factor": 1.0,
    },
    {
        "name": "LONG_CHOPPY", "priority": 2, "direction": 1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_bearish_excess": False,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
        "size_factor": 1.0,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_bearish_excess": False,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
        "size_factor": 1.0,
    },
    {
        "name": "LONG_TU", "priority": 3, "direction": 1, "enabled": True,
        "regime": REGIME_TREND_UP, "needs_bearish_excess": False,
        "needs_rsi_below": None, "needs_adx_above": 25.0,
        "amp_min": 0.55, "dir_max": None, "dir_min": 0.62,
        "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 5, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_bearish_excess": False,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_LIGHT", "priority": 6, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_bearish_excess": False,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.55,
        "size_factor": 1.0,
    },
)


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    setups: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "enabled", "size_factor",
                      "needs_bearish_excess", "needs_rsi_below", "needs_adx_above"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def _evaluate_setup(setup: Dict[str, Any],
                    regime: int, p_event: float, p_up: float,
                    bearish_excess: bool = False,
                    rsi: float = 50.0,
                    adx: float = 0.0) -> bool:
    if not setup.get("enabled", True):
        return False
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    if p_event < float(setup["amp_min"]):
        return False
    if setup["dir_max"] is not None and p_up >= float(setup["dir_max"]):
        return False
    if setup["dir_min"] is not None and p_up <= float(setup["dir_min"]):
        return False
    if setup.get("needs_bearish_excess", False) and not bearish_excess:
        return False
    if setup.get("needs_rsi_below") is not None and rsi >= float(setup["needs_rsi_below"]):
        return False
    if setup.get("needs_adx_above") is not None and adx < float(setup["needs_adx_above"]):
        return False
    return True


def _select_setup(setups: List[Dict[str, Any]],
                  regime: int, p_event: float, p_up: float,
                  bearish_excess: bool = False,
                  rsi: float = 50.0,
                  adx: float = 0.0) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up,
                                bearish_excess, rsi, adx)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


logger = logging.getLogger(__name__)

_TRAIN_LOG_PATH = os.path.join("logs", "opus_omnibus_v11_followsetup_train.jsonl")
_FLIP_LOG_PATH  = os.path.join("logs", "opus_omnibus_v11_followsetup_flips.jsonl")


# ─────────────────────────────────────────────────────────────────────────────
#  Détection de régime enrichie (identique V11).
# ─────────────────────────────────────────────────────────────────────────────
def _classify_regime(adx: float, bull: int, bear: int,
                     di_diff: float, slope20: float, bb_rank: float,
                     adx_threshold: float = 20.0,
                     di_rescue: float = 10.0) -> Tuple[int, str]:
    if adx < adx_threshold:
        return REGIME_RANGE, ("Range-Squeeze" if (bb_rank is not None and bb_rank < 0.2) else "Range-Open")
    if bull == 1:
        return REGIME_TREND_UP, "TrendUp-aligned"
    if bear == 1:
        return REGIME_TREND_DN, "TrendDown-aligned"
    if di_diff > di_rescue and slope20 > 0:
        return REGIME_TREND_UP, "TrendUp-DI"
    if di_diff < -di_rescue and slope20 < 0:
        return REGIME_TREND_DN, "TrendDown-DI"
    return REGIME_CHOPPY, "Choppy"


def _last_regime(features_df: pl.DataFrame,
                 adx_threshold: float, di_rescue: float
                 ) -> Tuple[int, str]:
    row = features_df.tail(1).row(-1, named=True)
    return _classify_regime(
        _safe_num(row.get("ADX"), 0.0),
        int(_safe_num(row.get("MM_bullish_align"), 0.0)),
        int(_safe_num(row.get("MM_bearish_align"), 0.0)),
        _safe_num(row.get("DI_diff"), 0.0),
        _safe_num(row.get("slope_SMA20"), 0.0),
        row.get("BB_width_rank100"),
        adx_threshold, di_rescue,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Labellisation multi-horizon (identique V11).
# ─────────────────────────────────────────────────────────────────────────────
def _multi_horizon_labels(close: np.ndarray, horizons: List[int],
                          amp_top_pct: float) -> Tuple[np.ndarray, np.ndarray, int, float, dict]:
    hs = sorted(set(int(h) for h in horizons if int(h) >= 1)) or [1]
    maxh = max(hs)
    N = len(close)
    n = N - maxh
    if n <= 0:
        return np.zeros(0, np.int8), np.zeros(0, np.int8), 0, 0.0, {}

    base = close[:n]
    base_safe = np.maximum(base, 1e-9)
    rets = np.empty((n, len(hs)), dtype=np.float64)
    weights = np.empty(len(hs), dtype=np.float64)
    for j, h in enumerate(hs):
        rets[:, j] = (close[h:h + n] - base) / base_safe
        weights[j] = 1.0 / h
    weights /= weights.sum()

    abs_max = np.max(np.abs(rets), axis=1)
    amp_thr = float(np.quantile(abs_max, 1.0 - amp_top_pct))
    y_amp = (abs_max >= amp_thr).astype(np.int8)

    mean_ret = rets @ weights
    y_dir = (mean_ret > 0).astype(np.int8)

    stats = {
        "horizons":      hs,
        "n_labels":      int(n),
        "amp_thr_pct":   round(amp_thr * 100, 4),
        "amp_pos_rate":  round(float(y_amp.mean()), 4),
        "dir_pos_rate":  round(float(y_dir.mean()), 4),
    }
    return y_amp, y_dir, n, amp_thr, stats


# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """OMNIBUS V11 FollowSetup — pas de TP/SL/trailing actifs, sortie pilotée
    par le flip de direction du setup courant."""

    name      = "opus_omnibus_v11_followsetup"
    # Recette(s) consommée(s) — surchargeable par le bloc `models:`
    # du YAML (cf. app.ml.recipe.strategy_models).
    models: Dict[str, str] = {"signal": "omnibus_v4_multi_nopruning"}
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        # ── Setups (sans LONG_EXIT_TD) ──
        "setup_signal_up_amp_min":          [0.45, 0.50, 0.55],
        "setup_signal_up_dir_min":          [0.55, 0.60, 0.65],
        "setup_short_td_high_amp_min":      [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":      [0.25, 0.30, 0.35],
        "setup_long_choppy_amp_min":        [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":        [0.55, 0.58, 0.62],
        "setup_short_choppy_amp_min":       [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":       [0.38, 0.42, 0.46],
        "setup_long_tu_amp_min":            [0.50, 0.55, 0.60],
        "setup_long_tu_dir_min":            [0.58, 0.62, 0.66],
        "setup_long_range_strict_amp_min":  [0.55, 0.60, 0.65],
        "setup_long_range_light_amp_min":   [0.45, 0.50, 0.55],
        # ── ML (décision uniquement) ──
        "di_rescue":       [8.0, 10.0, 14.0],
        # ── Filet de sécurité SL (pour le sizing uniquement) ──
        "safety_sl_atr_mult": [6.0, 8.0, 10.0, 15.0],
        # ── Anti-whipsaw sur les flips de setup ──
        "flip_confirm_bars":     [1, 2, 3, 4],
        "flip_cooldown_bars":    [0, 3, 5, 8],
        "flip_min_score":        [0.0, 0.50, 0.55, 0.60],
        "flip_hysteresis_margin": [0.0, 0.03, 0.05, 0.08],
        # ── Garde-fou temps maxi ouvert (borrow cost) ──
        "max_bars_safety":       [100, 200, 400, 800],
    }
    # Hyperparamètres d'entraînement figés (hors espace de recherche) : ils
    # font tous partie de _TRAIN_PARAM_KEYS — les échantillonner invalide le
    # cache d'entraînement process-wide entre les trials de l'optimiseur, et
    # chaque trial repaye l'intégralité des retrains LightGBM walk-forward
    # (rédhibitoire sur 50k bougies). Valeurs effectives : _DEFAULTS ;
    # surchargables via le YAML stratégie.
    fixed_params: Dict[str, Any] = {
        "label_horizons":  [1, 3, 6],
        "calibrate":       True,
        "amp_top_pct":     0.30,
        "warmup_bars":     750,
        "retrain_every":   800,
        "n_estimators":    500,
        "num_leaves":      31,
        "learning_rate":   0.03,
    }

    _DEFAULTS = {
        # Pas de filtre horaire/jours par défaut.
        "enable_hour_filter":  False,
        "active_hours_utc":    list(range(0, 24)),
        "active_days":         [0, 1, 2, 3, 4, 5, 6],
        "adx_threshold":       20.0,
        # Sécurité sizing : SL très large, jamais touché en théorie.
        "safety_sl_atr_mult":  10.0,
        "disable_trailing":    True,
        "use_fixed_tp":        False,
        "signal_up_dynamic_risk": False,
        "bearish_excess_rsi_threshold": 38.0,
        "bearish_excess_sma_pct":        1.5,
        # ML
        "label_horizons":   [1, 3, 6],
        "calibrate":        True,
        "prune_features":   False,
        "di_rescue":        10.0,
        "log_training":     True,
        # Entraînement
        "amp_top_pct":      0.30,
        "warmup_bars":      750,
        "retrain_every":    800,
        "n_estimators":     500,
        "num_leaves":       31,
        "learning_rate":    0.03,
        # Anti-whipsaw : K bougies consécutives avec setup opposé requis,
        # cooldown post-flip, score minimum du setup cible, marge d'hystérésis
        # sur dir_max/dir_min, et timeout dur en bougies (borrow cost guard).
        "flip_confirm_bars":      2,
        "flip_cooldown_bars":     5,
        "flip_min_score":         0.55,
        "flip_hysteresis_margin": 0.05,
        "max_bars_safety":        200,
    }

    retrain_interval_h: int = 6

    def __init__(self):
        self._lock = threading.Lock()
        self._amp_models:   Dict[str, Any]              = {}
        self._dir_models:   Dict[str, Any]              = {}
        self._amp_cal:      Dict[str, Any]              = {}
        self._dir_cal:      Dict[str, Any]              = {}
        self._feature_cols: Dict[str, List[str]]        = {}
        self._medians:      Dict[str, Dict[str, float]] = {}
        self._trained_tfs:  set                         = set()
        self._managed_externally: bool                  = False
        self._call_cnt:     Dict[str, int]              = {}
        self._last_retrain: Dict[str, int]              = {}
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        self._cancel_event = None
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0
        # Cooldown post-flip : compteur de bougies (cnt) au moment du dernier
        # flip par TF, pour gel temporaire de l'ouverture côté score().
        self._last_flip_cnt: Dict[str, int] = {}

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            # Cache partagé "v4_polars" : identique au _build_features de
            # v7/v10_retrained/v11/opus_stat_retrained_v4 → calcul mutualisé.
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="v4_polars", version="1",
                builder=lambda w: _build_features(_window_polars(w, n=len(w))),
                in_kind="polars", out_kind="polars")
            self._bt_features = feats
            self._bt_features_len = len(df) if feats is not None else 0
            logger.info(
                f"[OmnibusV11-FollowSetup] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(len(feats.columns) if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OmnibusV11-FollowSetup] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    # ── Cycle de vie ML ──────────────────────────────────────────────────────
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
            self._amp_cal.clear()
            self._dir_cal.clear()
            self._feature_cols.clear()
            self._medians.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._train_meta.clear()
            self._last_retrain.clear()
            self._last_flip_cnt.clear()
            self._managed_externally = False
            self._best_auc = 0.0
        self._bt_features = None
        self._bt_features_len = 0
        gc.collect()

    # ── Persistance par TF ────────────────────────────────────────────────────
    @staticmethod
    def _tf_from_path(path: str) -> str:
        base = os.path.splitext(os.path.basename(path))[0]
        return base.rsplit("_", 1)[-1]

    def save_model(self, path: str, extra_meta: dict = None) -> None:
        from app.ml.backend.persistence import save_amp_dir_bundle
        tf = self._tf_from_path(path)
        with self._lock:
            amp_m = self._amp_models.get(tf)
            dir_m = self._dir_models.get(tf)
            amp_c = self._amp_cal.get(tf)
            dir_c = self._dir_cal.get(tf)
            feats = self._feature_cols.get(tf)
            meds  = self._medians.get(tf)
            auc   = self._best_auc_per_tf.get(tf, 0.0)
            meta  = self._train_meta.get(tf, {})
        if amp_m is None or dir_m is None:
            return
        if save_amp_dir_bundle(path, tf, amp_m, dir_m, feats, meds, auc, meta,
                               amp_cal=amp_c, dir_cal=dir_c, extra_meta=extra_meta):
            logger.info(f"[OmnibusV11-FollowSetup] Modèles sauvegardés → {path} "
                        f"(AUC={auc:.3f})")

    def load_model(self, path: str) -> bool:
        from app.ml.backend.persistence import load_amp_dir_bundle
        data = load_amp_dir_bundle(path)
        if data is None or data.get("amp_model") is None or data.get("dir_model") is None:
            return False
        tf = self._tf_from_path(path)
        with self._lock:
            self._amp_models[tf]   = data["amp_model"]
            self._dir_models[tf]   = data["dir_model"]
            self._amp_cal[tf]      = data.get("amp_cal")
            self._dir_cal[tf]      = data.get("dir_cal")
            self._feature_cols[tf] = list(data.get("features") or [])
            self._medians[tf]      = dict(data.get("medians") or {})
            self._best_auc_per_tf[tf] = float(data.get("best_auc", 0.0))
            self._train_meta[tf]   = dict(data.get("train_meta") or {})
            self._trained_tfs.add(tf)
            self._best_auc = max(self._best_auc, self._best_auc_per_tf[tf])
        logger.info(f"[OmnibusV11-FollowSetup] Modèle {tf} chargé depuis {path}")
        return True

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        tf = _detect_timeframe(df) or "default"
        p  = (params or {}).get(self.name, {})
        self._train(df, tf_key=tf, params=p)

    # ── Entraînement (sans importance/pruning) ───────────────────────────────
    # Entraînement avec cache process-wide (cf. app/core/train_cache.py) :
    # les retrains identiques (même fenêtre, mêmes hyperparams d'entraînement)
    # sont réutilisés entre les trials de l'optimiseur au lieu d'être relancés.
    _TRAIN_STATE_ATTRS = ('_amp_models', '_dir_models', '_amp_cal', '_dir_cal', '_feature_cols',
                         '_medians', '_best_auc_per_tf', '_train_meta')
    _TRAIN_PARAM_KEYS  = ('amp_top_pct', 'n_estimators', 'num_leaves', 'learning_rate',
                         'label_horizons', 'calibrate')

    def _train(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        from app.core.train_cache import cached_train
        return cached_train(self, df, tf_key, params, self._train_impl,
                            self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS)

    def _train_impl(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        try:
            import lightgbm as lgb
        except ImportError:
            logger.error("[OmnibusV11-FollowSetup] lightgbm requis : pip install lightgbm")
            return False

        amp_top_pct   = float(params.get("amp_top_pct",   self._DEFAULTS["amp_top_pct"]))
        n_estimators  = int(params.get("n_estimators",    self._DEFAULTS["n_estimators"]))
        num_leaves    = int(params.get("num_leaves",      self._DEFAULTS["num_leaves"]))
        learning_rate = float(params.get("learning_rate", self._DEFAULTS["learning_rate"]))
        horizons      = list(params.get("label_horizons", self._DEFAULTS["label_horizons"]))
        calibrate     = bool(params.get("calibrate",      self._DEFAULTS["calibrate"]))
        log_training  = bool(params.get("log_training",   self._DEFAULTS["log_training"]))

        n_keep = max(2200, len(df))
        # Cache backtest si dispo (alimenté par prepare_for_backtest).
        # ``_bt_train_offset`` (posé par score() avant _train) repère la
        # position de la fenêtre d'entraînement dans la fenêtre complète :
        # sans lui, head(len(df)) lisait les PREMIÈRES lignes des features
        # alors que train_df est une tranche de FIN.
        _off = int(getattr(self, "_bt_train_offset", None) or 0)
        if (self._bt_features is not None and
                self._bt_features_len > 0 and
                _off + len(df) <= self._bt_features_len):
            feats = self._bt_features.slice(_off, len(df))
        else:
            feats = _build_features(_window_polars(df, n=n_keep))
        if feats is None or len(feats) < 250:
            logger.warning(f"[OmnibusV11-FollowSetup] {tf_key} : données insuffisantes")
            return False

        feature_cols = _select_feature_columns(feats)
        if not feature_cols:
            logger.warning(f"[OmnibusV11-FollowSetup] {tf_key} : aucune feature exploitable")
            return False

        close = feats["close"].to_numpy().astype(np.float64)
        y_amp, y_dir, n, amp_thr, lbl_stats = _multi_horizon_labels(
            close, horizons, amp_top_pct,
        )
        if n < 200:
            logger.warning(f"[OmnibusV11-FollowSetup] {tf_key} : pas assez de barres labélisables (n={n})")
            return False

        X_full = feats.head(n).select(feature_cols).to_numpy().astype(np.float32)
        split = max(int(n * 0.8), 100)
        split = min(split, n - 50)
        if split < 100 or n - split < 50:
            logger.warning(f"[OmnibusV11-FollowSetup] {tf_key} : split impossible (n={n})")
            return False

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

        if len(np.unique(y_amp[:split])) < 2 or len(np.unique(y_dir[:split])) < 2:
            from app.core.log_throttle import log_throttled
            log_throttled(logger, f"omnibusv11fs:monoclass:{tf_key}",
                          f"[OmnibusV11-FollowSetup] {tf_key} : labels mono-classe, fit ignoré")
            return False

        common = dict(
            objective="binary", metric="auc",
            num_leaves=num_leaves, learning_rate=learning_rate,
            min_child_samples=20, subsample=0.8, subsample_freq=5,
            colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
            max_bin=63, force_col_wise=True, verbosity=-1, n_jobs=1,
        )

        boosters: Dict[str, Any] = {}
        aucs: Dict[str, float] = {}
        cal_err: Dict[str, float] = {}
        calibrators: Dict[str, Any] = {}

        for target, y in (("amp", y_amp), ("dir", y_dir)):
            spw = (y[:split] == 0).sum() / max((y[:split] == 1).sum(), 1)
            ds_tr = lgb.Dataset(X_train, label=y[:split], feature_name=feature_cols,
                                free_raw_data=False)
            ds_va = lgb.Dataset(X_valid, label=y[split:n], reference=ds_tr,
                                feature_name=feature_cols, free_raw_data=False)
            try:
                booster = lgb.train(
                    {**common, "scale_pos_weight": spw},
                    ds_tr, num_boost_round=n_estimators, valid_sets=[ds_va],
                    callbacks=[lgb.early_stopping(20, verbose=False),
                               lgb.log_evaluation(-1)],
                )
            except Exception as e:
                logger.warning(f"[OmnibusV11-FollowSetup] {tf_key} : entraînement {target} KO ({e})")
                del ds_tr, ds_va
                gc.collect()
                return False
            aucs[target] = float(booster.best_score.get("valid_0", {}).get("auc", 0.0))
            boosters[target] = booster

            if calibrate:
                try:
                    from app.ml.backend.isotonic import IsotonicRegression
                    raw_va = booster.predict(X_valid)
                    y_va = y[split:n]
                    if len(np.unique(y_va)) >= 2:
                        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                        iso.fit(raw_va, y_va)
                        cal_va = iso.predict(raw_va)
                        cal_err[target] = round(float(np.mean(np.abs(cal_va - y_va))), 4)
                        calibrators[target] = iso
                except Exception as ce:
                    logger.debug(f"[OmnibusV11-FollowSetup] {tf_key} calibration {target} KO : {ce}")

            del ds_tr, ds_va
            gc.collect()

        del X_train, X_valid

        auc_combined = (aucs.get("amp", 0.0) + aucs.get("dir", 0.0)) / 2.0
        meta = {
            "n_train":      int(split),
            "n_valid":      int(n - split),
            "n_features":   len(feature_cols),
            "auc_amp":      round(aucs.get("amp", 0.0), 4),
            "auc_dir":      round(aucs.get("dir", 0.0), 4),
            "amp_thr_pct":  round(float(amp_thr) * 100, 4),
            "amp_top_pct":  amp_top_pct,
            "horizons":     lbl_stats.get("horizons"),
            "label_stats":  lbl_stats,
            "calibrated":   bool(calibrators),
            "cal_err":      cal_err,
        }

        with self._lock:
            self._amp_models[tf_key] = boosters["amp"]
            self._dir_models[tf_key] = boosters["dir"]
            self._amp_cal[tf_key]    = calibrators.get("amp")
            self._dir_cal[tf_key]    = calibrators.get("dir")
            self._feature_cols[tf_key] = feature_cols
            self._medians[tf_key]    = medians
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key] = auc_combined
            self._best_auc = max(self._best_auc, auc_combined)
            self._train_meta[tf_key] = meta
        gc.collect()

        logger.info(
            f"[OmnibusV11-FollowSetup] {tf_key} entraîné : {split} train / {n - split} val | "
            f"{len(feature_cols)} feats | "
            f"AUC amp={aucs.get('amp', 0):.3f} dir={aucs.get('dir', 0):.3f} | "
            f"horizons={meta['horizons']} | calib={meta['calibrated']} cal_err={cal_err}"
        )
        if log_training:
            self._append_train_log(tf_key, meta)
        return True

    def _append_train_log(self, tf_key: str, meta: dict) -> None:
        try:
            os.makedirs(os.path.dirname(_TRAIN_LOG_PATH) or ".", exist_ok=True)
            record = {"ts": _dt.datetime.utcnow().isoformat(), "strategy": self.name,
                      "tf": tf_key, **meta}
            with open(_TRAIN_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[OmnibusV11-FollowSetup] log entraînement KO : {e}")

    # ── Prédictions (avec calibration) ────────────────────────────────────────
    def _predict(self, features_df: pl.DataFrame, tf: str, target: str) -> Optional[float]:
        with self._lock:
            booster   = (self._amp_models if target == "amp" else self._dir_models).get(tf)
            cal       = (self._amp_cal if target == "amp" else self._dir_cal).get(tf)
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
            raw = float(booster.predict(vals.reshape(1, -1))[0])
            if cal is not None:
                return float(cal.predict([raw])[0])
            return raw
        except Exception as e:
            logger.warning(f"[OmnibusV11-FollowSetup] Prédiction {tf}/{target} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "dir")

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Helpers : régime + bearish excess + features sur la dernière bougie ──
    def _compute_features(self, df: pl.DataFrame, params) -> Optional[pl.DataFrame]:
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            return self._bt_features.head(len(df))
        return _build_features(
            _window_polars(df, n=max(260, self.min_bars_required(params)))
        )

    def _bearish_excess(self, df: pl.DataFrame, last_row: dict,
                        c_now: float, p: dict) -> bool:
        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold",
                                 self._DEFAULTS["bearish_excess_rsi_threshold"]))
        be_sma_pct = float(p.get("bearish_excess_sma_pct",
                                 self._DEFAULTS["bearish_excess_sma_pct"]))
        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False
        rsi_v = _safe_num(last_row.get("RSI_14"), 50.0)
        rsi_excess = rsi_v < be_rsi_thr
        sma20_v = _safe_num(last_row.get("SMA_20"), 0.0)
        price_below_sma20 = (
            c_now < sma20_v * (1.0 - be_sma_pct / 100.0)
        ) if sma20_v > 0 else False
        return consec_red or rsi_excess or price_below_sma20

    # ── Score : entrée selon le setup actif ──────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        adx_threshold     = float(p.get("adx_threshold",     self._DEFAULTS["adx_threshold"]))
        di_rescue         = float(p.get("di_rescue",         self._DEFAULTS["di_rescue"]))
        safety_sl_mult    = float(p.get("safety_sl_atr_mult", self._DEFAULTS["safety_sl_atr_mult"]))
        disable_trailing  = bool(p.get("disable_trailing",   self._DEFAULTS["disable_trailing"]))
        cooldown_bars     = int(p.get("flip_cooldown_bars",  self._DEFAULTS["flip_cooldown_bars"]))
        max_bars_safety   = int(p.get("max_bars_safety",     self._DEFAULTS["max_bars_safety"]))

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(f"Timeframe non supporté (détecté={tf})")

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

        # Cooldown post-flip : gel des entrées pendant N bougies après un flip
        # pour éviter les aller-retours rapides (anti-thrash).
        last_flip = self._last_flip_cnt.get(tf, -10**9)
        bars_since_flip = cnt - last_flip
        if cooldown_bars > 0 and bars_since_flip < cooldown_bars:
            return self._none(
                f"Cooldown post-flip ({bars_since_flip}/{cooldown_bars} bougies)"
            )

        features = self._compute_features(df, params)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v = _safe_num(last_row.get("ATR_14"), 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime, regime_sub = _last_regime(features, adx_threshold, di_rescue)
        regime_lbl = REGIME_LABELS[regime]

        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")

        rsi_v = _safe_num(last_row.get("RSI_14"), 50.0)
        adx_v = _safe_num(last_row.get("ADX"), 0.0)
        bearish_excess = self._bearish_excess(df, last_row, c_now, p)

        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up,
                               bearish_excess, rsi_v, adx_v)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl}({regime_sub}) "
                f"p_event={p_event:.2f} p_up={p_up:.2f} rsi={rsi_v:.1f} adx={adx_v:.1f}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side        = "long" if setup["direction"] == 1 else "short"
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self._train_meta.get(tf, {})
        # SL safety net (uniquement pour le sizing) ; pas de TP ; pas de timeout.
        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      safety_sl_mult,
            "disable_trailing": disable_trailing,
            "size_factor":      size_factor,
            # Timeout dur (garde-fou contre l'accumulation de borrow_cost si
            # aucun flip ne se déclenche pendant des centaines de bougies).
            "exit_after_bars":  max_bars_safety,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "regime_sub":       regime_sub,
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "setup_direction":  int(setup["direction"]),
            "bearish_excess":   bool(bearish_excess),
            "rsi":              round(rsi_v, 1),
            "adx":              round(adx_v, 1),
        }

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(rsi_v, 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "regime_sub":       regime_sub,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "setup_direction":  int(setup["direction"]),
            "sl_mult":          safety_sl_mult,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
            "calibrated":       meta.get("calibrated", False),
            "horizons":         meta.get("horizons"),
            "n_features":       meta.get("n_features", 0),
        }
        sig["conditions"] = [
            f"Setup FollowSetup retenu : {setup['name']} (priorité {setup['priority']}, dir={side})",
            f"Régime : {regime_lbl} / {regime_sub} | ADX={adx_v:.1f}",
            f"P(événement)={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓"
            + (" (calibrée)" if meta.get("calibrated") else ""),
            (f"P(hausse)={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse)={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse)={p_up:.2f}"),
            f"Pas de TP/trailing — sortie sur flip de setup (confirm "
            f"{int(p.get('flip_confirm_bars', self._DEFAULTS['flip_confirm_bars']))} "
            f"bougies, cooldown {cooldown_bars}) | "
            f"SL safety {safety_sl_mult:.1f}×ATR | timeout {max_bars_safety} bougies",
        ]
        sig["reason"] = (
            f"OmnibusV11-FollowSetup {setup['name']} {side.upper()} | "
            f"{regime_lbl}/{regime_sub} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}"
        )
        return sig

    # ── Sortie pilotée par le flip de direction du setup ─────────────────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        """Ferme la position si un setup opposé est confirmé sur K bougies
        consécutives, avec score suffisant et marge d'hystérésis sur les seuils
        directionnels. État d'attente stocké directement sur la position via
        ``_fs_opp_count`` / ``_fs_opp_setup``.

        Tant qu'aucun setup opposé n'est validé, la position reste ouverte pour
        maximiser l'exposition au gain.
        """
        side = position.get("side")
        if side not in ("long", "short"):
            return None
        if df is None or len(df) < self.min_bars_required(params):
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold        = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        di_rescue            = float(p.get("di_rescue",     self._DEFAULTS["di_rescue"]))
        confirm_bars         = int(p.get("flip_confirm_bars",
                                          self._DEFAULTS["flip_confirm_bars"]))
        flip_min_score       = float(p.get("flip_min_score",
                                            self._DEFAULTS["flip_min_score"]))
        hysteresis_margin    = float(p.get("flip_hysteresis_margin",
                                            self._DEFAULTS["flip_hysteresis_margin"]))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS or tf not in self._trained_tfs:
            return None

        try:
            features = self._compute_features(df, params)
            if features is None or len(features) == 0:
                return None

            last_row = features.row(-1, named=True)
            c_now    = float(df["close"][-1] or 0.0)
            if c_now <= 0:
                return None

            regime, regime_sub = _last_regime(features, adx_threshold, di_rescue)
            p_event   = self.predict_amplitude(features, tf)
            p_up      = self.predict_direction(features, tf)
            if p_event is None or p_up is None:
                return None

            rsi_v = _safe_num(last_row.get("RSI_14"), 50.0)
            adx_v = _safe_num(last_row.get("ADX"), 0.0)
            bearish_excess = self._bearish_excess(df, last_row, c_now, p)

            setups = _apply_setup_overrides(p)
            current = _select_setup(setups, regime, p_event, p_up,
                                    bearish_excess, rsi_v, adx_v)
        except Exception as e:
            logger.warning(f"[OmnibusV11-FollowSetup] check_early_exit recompute KO : {e}")
            return None

        held_dir = 1 if side == "long" else -1

        # Pas de setup actif OU même direction → on garde l'exposition et on
        # remet à zéro le compteur d'attente d'un setup opposé.
        if current is None or int(current["direction"]) == held_dir:
            position["_fs_opp_count"] = 0
            position["_fs_opp_setup"] = None
            return None

        # Hystérésis sur les seuils directionnels du setup opposé : on exige
        # une marge en plus de la condition stricte du setup pour éviter les
        # flips sur signal limite.
        if hysteresis_margin > 0.0:
            d_max = current.get("dir_max")
            d_min = current.get("dir_min")
            if d_max is not None and p_up >= (float(d_max) - hysteresis_margin):
                position["_fs_opp_count"] = 0
                position["_fs_opp_setup"] = None
                return None
            if d_min is not None and p_up <= (float(d_min) + hysteresis_margin):
                position["_fs_opp_count"] = 0
                position["_fs_opp_setup"] = None
                return None

        # Score du setup cible (même formule que score()).
        priority_bonus = max(0, 6 - int(current["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        new_score      = round(
            min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3
        )
        if new_score < flip_min_score:
            position["_fs_opp_count"] = 0
            position["_fs_opp_setup"] = None
            return None

        # Confirmation sur K bougies consécutives : on incrémente le compteur
        # uniquement si c'est le même setup opposé qu'à la bougie précédente.
        prev_setup = position.get("_fs_opp_setup")
        if prev_setup == current["name"]:
            cnt_opp = int(position.get("_fs_opp_count", 0)) + 1
        else:
            cnt_opp = 1
        position["_fs_opp_count"] = cnt_opp
        position["_fs_opp_setup"] = current["name"]

        if cnt_opp < max(1, confirm_bars):
            return None

        # Flip validé : on logge, on déclenche le cooldown et on rend la
        # raison de sortie au moteur.
        try:
            self._last_flip_cnt[tf] = self._call_cnt.get(tf, 0)
        except Exception:
            pass
        self._log_flip(
            tf=tf,
            position=position,
            new_setup=current,
            new_score=new_score,
            p_event=float(p_event),
            p_up=float(p_up),
            regime=regime,
            regime_sub=regime_sub,
            adx=adx_v,
            rsi=rsi_v,
            confirm_bars=int(cnt_opp),
        )
        return f"setup_flip_to_{current['name']}"

    def _log_flip(self, tf: str, position: dict, new_setup: dict,
                  new_score: float, p_event: float, p_up: float,
                  regime: int, regime_sub: str, adx: float, rsi: float,
                  confirm_bars: int) -> None:
        try:
            os.makedirs(os.path.dirname(_FLIP_LOG_PATH) or ".", exist_ok=True)
            record = {
                "ts":             _dt.datetime.utcnow().isoformat(),
                "strategy":       self.name,
                "tf":             tf,
                "symbol":         position.get("symbol"),
                "position_id":    position.get("id"),
                "from_side":      position.get("side"),
                "from_setup":     position.get("setup"),
                "from_entry":     position.get("entry"),
                "from_entry_bar": position.get("bar"),
                "to_setup":       new_setup.get("name"),
                "to_direction":   int(new_setup.get("direction", 0)),
                "new_score":      round(float(new_score), 4),
                "p_event":        round(float(p_event), 4),
                "p_up":           round(float(p_up), 4),
                "regime":         regime,
                "regime_lbl":     REGIME_LABELS.get(regime, "?"),
                "regime_sub":     regime_sub,
                "adx":            round(float(adx), 2),
                "rsi":            round(float(rsi), 2),
                "confirm_bars":   int(confirm_bars),
            }
            with open(_FLIP_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[OmnibusV11-FollowSetup] log flip KO : {e}")

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
