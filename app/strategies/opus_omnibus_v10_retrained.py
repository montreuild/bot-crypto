"""Stratégie Opus Omnibus V10 RETRAINED — V10 (routing setups) + entraînement inline.

V10 (``opus_omnibus_v10``) utilise les modèles V4 PRÉ-ENTRAÎNÉS figés (pkl).
Cette variante conserve la logique de routing V10 (8 setups, SIGNAL_UP à risque
dynamique, LONG_TU strict, LONG_RANGE_LIGHT, excès baissier) mais **entraîne ses
propres modèles LightGBM amp/dir inline** (réentraînement walk-forward toutes les
``retrain_every`` barres ; en live, ``managed_externally=True`` délègue au
``MLStrategyTrainer``).

Fichier **autonome** : tout le pipeline (FeatureBuilder V4 polars, moteur
d'entraînement LightGBM, helpers de régime, définitions des setups) est dupliqué
ici plutôt qu'importé d'autres stratégies. Supprimer une autre stratégie ne peut
donc pas casser celle-ci.
"""

import gc
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

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")

# Codes de régime
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

_EXIT_TD_WINDOW_BARS = 3

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


# ─────────────────────────────────────────────────────────────────────────────
#  Régime
# ─────────────────────────────────────────────────────────────────────────────
def _classify_regime(adx_val: float, bull: int, bear: int,
                     adx_threshold: float = 20.0) -> int:
    if adx_val < adx_threshold:
        return REGIME_RANGE
    if bull == 1:
        return REGIME_TREND_UP
    if bear == 1:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _regime_history(features_df: pl.DataFrame, n_last: int = 5,
                    adx_threshold: float = 20.0) -> List[int]:
    sub = features_df.tail(n_last)
    rows = sub.select(["ADX", "MM_bullish_align", "MM_bearish_align"]).rows()
    out: List[int] = []
    for adx_v, bull, bear in rows:
        out.append(_classify_regime(
            float(adx_v) if adx_v is not None else 0.0,
            int(bull)    if bull  is not None else 0,
            int(bear)    if bear  is not None else 0,
            adx_threshold,
        ))
    return out


def _exit_td_window_active(regimes: List[int], window_bars: int) -> bool:
    n = len(regimes)
    if n < 2:
        return False
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Setups OMNIBUS V10
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "SIGNAL_UP", "priority": -1, "direction": 1, "enabled": True,
        "regime": None,
        "needs_exit_td_window": False,
        "needs_bearish_excess": True,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 1.0, "sl_mult": 1.3, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
        "tp_mult": 1.4, "sl_mult": 1.6, "max_bars": 8, "size_factor": 1.5,
    },
    {
        "name": "LONG_CHOPPY", "priority": 2, "direction": 1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
        "tp_mult": 0.9, "sl_mult": 1.2, "max_bars": 10, "size_factor": 1.0,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.4, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "LONG_TU", "priority": 3, "direction": 1, "enabled": True,
        "regime": REGIME_TREND_UP, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None,
        "needs_adx_above": 25.0,
        "amp_min": 0.55, "dir_max": None, "dir_min": 0.62,
        "tp_mult": 1.4, "sl_mult": 1.1, "max_bars": 10, "size_factor": 1.0,
    },
    {
        "name": "LONG_EXIT_TD", "priority": 4, "direction": 1, "enabled": True,
        "regime": None, "needs_exit_td_window": True,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.40, "dir_max": None, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.5, "max_bars": 8, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 5, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8, "sl_mult": 1.2, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_LIGHT", "priority": 6, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.55,
        "tp_mult": 0.7, "sl_mult": 1.0, "max_bars": 4, "size_factor": 0.6,
    },
)


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    setups: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "tp_mult", "sl_mult", "max_bars", "enabled", "size_factor",
                      "needs_bearish_excess", "needs_rsi_below", "needs_adx_above"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def _evaluate_setup(setup: Dict[str, Any],
                    regime: int, p_event: float, p_up: float,
                    exit_td_active: bool,
                    bearish_excess: bool = False,
                    rsi: float = 50.0,
                    adx: float = 0.0) -> bool:
    if not setup.get("enabled", True):
        return False
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    if setup.get("needs_exit_td_window", False):
        if not exit_td_active:
            return False
        if regime == REGIME_TREND_DN:
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
                  exit_td_active: bool,
                  bearish_excess: bool = False,
                  rsi: float = 50.0,
                  adx: float = 0.0) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, exit_td_active,
                                bearish_excess, rsi, adx)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


def _check_early_exit(setup_name: str, regime: int, p_up: float,
                      dir_inv_short: float = 0.55,
                      dir_inv_long: float = 0.42,
                      dir_drop_range: float = 0.40) -> Optional[str]:
    if setup_name == "SIGNAL_UP":
        if p_up < dir_inv_long:
            return "p_dir_drop"
        if regime == REGIME_TREND_DN:
            return "to_TD"
    elif setup_name == "SHORT_TD_HIGH":
        if regime != REGIME_TREND_DN:
            return "regime_exit_TD"
        if p_up > dir_inv_short:
            return "p_dir_inversion"
    elif setup_name == "LONG_CHOPPY":
        if p_up < dir_inv_long:
            return "p_dir_drop"
        if regime == REGIME_TREND_DN:
            return "to_TD"
    elif setup_name == "SHORT_CHOPPY":
        if regime != REGIME_CHOPPY:
            return "regime_exit_choppy"
        if p_up > 0.58:
            return "p_dir_inversion"
    elif setup_name == "LONG_TU":
        if regime == REGIME_TREND_DN:
            return "to_TD"
        if p_up < dir_inv_long:
            return "p_dir_drop"
    elif setup_name == "LONG_EXIT_TD":
        if regime == REGIME_TREND_DN:
            return "back_to_TD"
    elif setup_name in ("LONG_RANGE_STRICT", "LONG_RANGE_LIGHT"):
        if regime == REGIME_TREND_DN:
            return "regime_to_TD"
        if p_up < dir_drop_range:
            return "p_dir_drop"
    return None


def _signal_up_dynamic_risk(regime: int) -> Tuple[float, float]:
    """size_factor et sl_mult adaptés au régime pour SIGNAL_UP (mean-reversion)."""
    if regime == REGIME_CHOPPY or regime == REGIME_RANGE:
        return 1.5, 1.3
    if regime == REGIME_TREND_DN:
        return 1.2, 1.1
    if regime == REGIME_TREND_UP:
        return 1.0, 1.0
    return 1.0, 1.3


# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """OMNIBUS V10 — routing V10 sur modèles LightGBM entraînés inline."""

    name      = "opus_omnibus_v10_retrained"
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        # ── Setups V10 ──
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
        "setup_long_tu_needs_adx_above":    [22.0, 25.0, 28.0],
        "setup_long_range_strict_amp_min":  [0.55, 0.60, 0.65],
        "setup_long_range_light_amp_min":   [0.45, 0.50, 0.55],
        "exit_td_window_bars":              [2, 3, 4],
    }
    # Hyperparamètres d'entraînement figés (hors espace de recherche) : les
    # échantillonner invalide le cache d'entraînement process-wide entre les
    # trials de l'optimiseur — chaque trial repaye alors l'intégralité des
    # retrains LightGBM walk-forward (rédhibitoire sur 50k bougies). Valeurs
    # effectives : _DEFAULTS ; surchargables via le YAML stratégie.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":     0.30,
        "warmup_bars":     750,
        "retrain_every":   800,
        "n_estimators":    500,
        "num_leaves":      31,
        "learning_rate":   0.03,
    }

    # Contrat de gate (ML-02) : labellisation single-horizon t+1 câblée en dur
    # dans _train_impl (ret_t1), ce n'est pas un paramètre d'entraînement.
    # Sans cette déclaration, le gate évaluait les candidats sur le défaut
    # multi-horizon [1,3,6] hérité de V11 — une cible jamais apprise ici.
    gate_spec: Dict[str, Any] = {
        "label_horizons": [1],
        "amp_top_pct":    fixed_params["amp_top_pct"],
    }

    _DEFAULTS = {
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),
        "active_days":         [0, 1, 2, 3, 4],
        "adx_threshold":       20.0,
        "exit_td_window_bars": _EXIT_TD_WINDOW_BARS,
        "disable_trailing":    True,
        "use_fixed_tp":        True,
        "bearish_excess_rsi_threshold": 38.0,
        "bearish_excess_sma_pct":        1.5,
        "signal_up_dynamic_risk":        True,
        # Entraînement
        "amp_top_pct":         0.30,
        "warmup_bars":         750,
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
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
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
                f"[OmnibusV10-RT] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(len(feats.columns) if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] prepare_for_backtest KO : {e}")
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

    # ── Persistance par TF ────────────────────────────────────────────────────
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
            logger.debug(f"[OmnibusV10-RT] save_model({path}) : pas de modèle pour {tf_key}")
            return
        if save_amp_dir_bundle(path, tf_key, amp_m, dir_m, feats, meds, auc, meta,
                               extra_meta=extra_meta):
            logger.info(f"[OmnibusV10-RT] Modèles sauvegardés → {path} (AUC={auc:.3f})")

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
            f"[OmnibusV10-RT] Modèle {tf_key} chargé depuis {path} "
            f"(AUC={self._best_auc_per_tf[tf_key]:.3f})"
        )
        return True

    # ── Entraînement ──────────────────────────────────────────────────────────
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
            logger.error("[OmnibusV10-RT] lightgbm requis : pip install lightgbm")
            return False

        amp_top_pct   = float(params.get("amp_top_pct",   self._DEFAULTS["amp_top_pct"]))
        n_estimators  = int(params.get("n_estimators",    self._DEFAULTS["n_estimators"]))
        num_leaves    = int(params.get("num_leaves",      self._DEFAULTS["num_leaves"]))
        learning_rate = float(params.get("learning_rate", self._DEFAULTS["learning_rate"]))

        n_keep = max(2200, len(df))
        # Réutilise le cache backtest si dispo (features causales déterministes).
        # ``_bt_train_offset`` (posé par score() avant _train) repère la position
        # de la fenêtre d'entraînement dans la fenêtre complète : sans lui,
        # head(len(df)) lisait les PREMIÈRES lignes des features alors que
        # train_df est une tranche de FIN.
        _off = int(getattr(self, "_bt_train_offset", None) or 0)
        if (self._bt_features is not None and
                self._bt_features_len > 0 and
                _off + len(df) <= self._bt_features_len):
            feats = self._bt_features.slice(_off, len(df))
        else:
            feats = _build_features(_window_polars(df, n=n_keep))
        if feats is None or len(feats) < 250:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : données insuffisantes")
            return False

        feature_cols = _select_feature_columns(feats)
        if not feature_cols:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : aucune feature exploitable")
            return False

        close = feats["close"].to_numpy().astype(np.float64)
        n     = len(close) - 1
        if n < 200:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : pas assez de barres labélisables")
            return False
        ret_t1  = (close[1:] - close[:n]) / np.maximum(close[:n], 1e-9)
        abs_ret = np.abs(ret_t1)
        amp_thr = float(np.quantile(abs_ret, 1.0 - amp_top_pct))
        y_amp   = (abs_ret >= amp_thr).astype(np.int8)
        y_dir   = (ret_t1 > 0).astype(np.int8)

        X_full = feats.head(n).select(feature_cols).to_numpy().astype(np.float32)

        split = max(int(n * 0.8), 100)
        split = min(split, n - 50)
        if split < 100 or n - split < 50:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : split impossible (n={n})")
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

        common = dict(
            objective="binary", metric="auc",
            num_leaves=num_leaves, learning_rate=learning_rate,
            min_child_samples=20, subsample=0.8, subsample_freq=5,
            colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
            max_bin=63, force_col_wise=True,
            verbosity=-1, n_jobs=1,
        )

        with self._lock:
            self._amp_models.pop(tf_key, None)
            self._dir_models.pop(tf_key, None)
        gc.collect()

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
            logger.warning(f"[OmnibusV10-RT] {tf_key} : entraînement amp KO ({e})")
            del ds_train_amp, ds_valid_amp
            gc.collect()
            return False
        auc_amp = booster_amp.best_score.get("valid_0", {}).get("auc", 0.0)
        del ds_train_amp, ds_valid_amp

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
            logger.warning(f"[OmnibusV10-RT] {tf_key} : entraînement dir KO ({e})")
            del ds_train_dir, ds_valid_dir
            gc.collect()
            return False
        auc_dir = booster_dir.best_score.get("valid_0", {}).get("auc", 0.0)
        del ds_train_dir, ds_valid_dir, X_train, X_valid

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
            }
        gc.collect()
        logger.info(
            f"[OmnibusV10-RT] {tf_key} entraîné : {split} train / {n - split} val | "
            f"{len(feature_cols)} features | AUC amp={auc_amp:.3f} dir={auc_dir:.3f} | "
            f"amp_thr={amp_thr * 100:.2f}%"
        )
        return True

    # ── Prédictions ────────────────────────────────────────────────────────────
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
            logger.warning(f"[OmnibusV10-RT] Prédiction {tf}/{target} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "dir")

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Score V10 ───────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        enable_hour_filter  = bool(p.get("enable_hour_filter",  self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",    self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",         self._DEFAULTS["active_days"]))
        adx_threshold       = float(p.get("adx_threshold",      self._DEFAULTS["adx_threshold"]))
        exit_td_window_bars = int(p.get("exit_td_window_bars",  self._DEFAULTS["exit_td_window_bars"]))
        disable_trailing    = bool(p.get("disable_trailing",    self._DEFAULTS["disable_trailing"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",        self._DEFAULTS["use_fixed_tp"]))
        signal_up_dyn       = bool(p.get("signal_up_dynamic_risk", self._DEFAULTS["signal_up_dynamic_risk"]))

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        if enable_hour_filter:
            hour, dow = _last_bar_hour_dow(df)
            if hour is not None and dow is not None:
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

        # ── Réentraînement inline (walk-forward) ─────────────────────────────
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

        if self._bt_features is not None and len(df) <= self._bt_features_len:
            features = self._bt_features.head(len(df))
        else:
            features = _build_features(_window_polars(df, n=max(260, self.min_bars_required(params))))
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v = _safe_num(last_row.get("ATR_14"), 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime_history = _regime_history(
            features, n_last=max(exit_td_window_bars + 2, 5),
            adx_threshold=adx_threshold,
        )
        regime         = regime_history[-1]
        regime_lbl     = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regime_history, exit_td_window_bars)

        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")

        # ── Détection d'excès baissier ───────────────────────────────────────
        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma_pct = float(p.get("bearish_excess_sma_pct", 1.5))
        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False
        rsi_v = _safe_num(last_row.get("RSI_14"), 50.0)
        adx_v = _safe_num(last_row.get("ADX"), 0.0)
        rsi_excess = rsi_v < be_rsi_thr
        sma20_v = _safe_num(last_row.get("SMA_20"), 0.0)
        price_below_sma20 = (c_now < sma20_v * (1.0 - be_sma_pct / 100.0)) if sma20_v > 0 else False
        bearish_excess = consec_red or rsi_excess or price_below_sma20

        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up, exit_td_active,
                               bearish_excess, rsi_v, adx_v)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} rsi={rsi_v:.1f} adx={adx_v:.1f} "
                f"exit_td={exit_td_active} bearish_excess={bearish_excess}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        signal_up_dyn_applied = False
        if setup["name"] == "SIGNAL_UP" and signal_up_dyn:
            setup = dict(setup)
            size_mult, sl_override = _signal_up_dynamic_risk(regime)
            setup["size_factor"] = float(setup.get("size_factor", 1.0)) * size_mult
            setup["sl_mult"]     = sl_override
            signal_up_dyn_applied = True

        side        = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self._train_meta.get(tf, {})

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "disable_trailing": disable_trailing,
            "size_factor":      size_factor,
            "exit_after_bars":  max_bars,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "bearish_excess":   bool(bearish_excess),
            "signal_up_dyn":    bool(signal_up_dyn_applied),
            "rsi":              round(rsi_v, 1),
            "adx":              round(adx_v, 1),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(rsi_v, 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "max_bars":         max_bars,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
            "n_features":       meta.get("n_features", 0),
        }
        sig["conditions"] = [
            f"Setup V10-RT retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime : {regime_lbl} | ADX={adx_v:.1f} | exit_td_window={exit_td_active}",
            f"P(événement)={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse)={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse)={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse)={p_up:.2f} (pas de seuil dir)"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | max {max_bars} bougies",
            f"Modèle V4 entraîné inline / {tf} ({meta.get('n_features', 0)} features, "
            f"AUC amp={meta.get('auc_amp', 0):.2f} dir={meta.get('auc_dir', 0):.2f})",
        ]
        if setup["name"] == "SIGNAL_UP" and signal_up_dyn_applied:
            sig["conditions"].append(
                f"V10 SIGNAL_UP dynamique : size×{size_factor:.2f} SL {sl_atr_mult:.2f}×ATR "
                f"(adapté régime {regime_lbl})"
            )
        sig["reason"] = (
            f"OmnibusV10-RT {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}"
        )
        return sig

    # ── Sortie anticipée V10 ─────────────────────────────────────────────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        setup_name = position.get("setup")
        if not setup_name:
            ind = position.get("indicators") or {}
            setup_name = ind.get("setup")
        if not setup_name:
            return None
        if df is None or len(df) < self.min_bars_required(params):
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold  = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        dir_inv_short  = float(p.get("early_exit_dir_inv_short",  0.55))
        dir_inv_long   = float(p.get("early_exit_dir_inv_long",   0.42))
        dir_drop_range = float(p.get("early_exit_dir_drop_range", 0.40))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS or tf not in self._trained_tfs:
            return None

        try:
            if self._bt_features is not None and len(df) <= self._bt_features_len:
                features = self._bt_features.head(len(df))
            else:
                features = _build_features(
                    _window_polars(df, n=max(260, self.min_bars_required(params)))
                )
            if features is None or len(features) == 0:
                return None
            last_row = features.row(-1, named=True)
            regime = _classify_regime(
                _safe_num(last_row.get("ADX"), 0.0),
                int(_safe_num(last_row.get("MM_bullish_align"), 0.0)),
                int(_safe_num(last_row.get("MM_bearish_align"), 0.0)),
                adx_threshold,
            )
            p_up = self.predict_direction(features, tf)
            if p_up is None:
                return None
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] check_early_exit recompute KO : {e}")
            return None

        return _check_early_exit(
            setup_name, regime, p_up,
            dir_inv_short=dir_inv_short,
            dir_inv_long=dir_inv_long,
            dir_drop_range=dir_drop_range,
        )

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
