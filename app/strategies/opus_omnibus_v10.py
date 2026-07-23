"""Stratégie Opus Omnibus V10 — corrections data-driven sur la base du backtest V9.

V10 = V9 corrigé. Analyse du backtest BTC/USDC 1h (5000 bougies, 122 trades V9
vs 117 V8, PnL +415 vs +427) a révélé que :

1. **LONG_TU et LONG_PULLBACK_TU étaient nets négatifs** (-3.15 USDC sur
   12 trades, WR 50%). Cause racine : AUC_dir du modèle V4 ≈ 0.53 sur 1h
   (quasi-aléatoire), donc le seuil ``p_dir > 0.55`` ne filtre quasi rien.
   Les setups V8 gagnants exigeaient p_dir aux extrêmes (>0.60 ou <0.30)
   ou s'appuyaient sur l'amplitude (AUC ~0.68-0.75, fiable).

2. **3 stop_loss SIGNAL_UP ont coûté -41 USDC** (×1.5 bonus amplifiait).
   Tous en régime trending (Trend Up #105, Choppy mais en transition).
   SIGNAL_UP est conçu pour rebond mean-reverting → mal adapté aux trends.

3. **LONG_EXIT_TD resserrement contre-productif** : V9 amp_min 0.40→0.50
   a éliminé des winners → -3.4 USDC vs +3.5 V8.

4. **17 jours muets en fin de backtest (range 75-78k)** : aucun setup
   permissif pour les ranges où p_amp/p_dir restent dans la zone tiède.

### Changements V10

* **LONG_TU resserré drastiquement** : amp_min 0.45→0.55, dir_min 0.55→0.62,
  ADX>=25 requis (tendance vraiment établie, pas marginal 20-25), sl_mult
  1.4→1.1 (couper court si l'entrée est mauvaise).
* **LONG_PULLBACK_TU supprimé** (signal-to-noise insuffisant).
* **SIGNAL_UP size_factor dynamique** par régime :
    - Choppy/Range : ×1.5 (terrain d'origine, marche)
    - Trend Down : ×1.2
    - Trend Up    : ×1.0 (pas de boost en trending)
* **SIGNAL_UP sl_mult dynamique** : 1.3 en Choppy/Range, 1.0 en trending
  (sortie rapide si l'excès baissier n'était qu'un faux signal).
* **LONG_EXIT_TD restauré V8** : amp_min 0.40.
* **SHORT_TD supprimé** : 6 trades V9 WR 50% +1.9, SHORT_TD_HIGH couvre
  déjà excellemment la zone TREND_DN (WR 95.8%).
* **LONG_RANGE_LIGHT (nouveau)** : couvre les ranges faibles que LONG_RANGE_STRICT
  laisse passer (p_amp≥0.50, p_dir>0.55, size×0.6, max_bars 4).

### Setups V10 (par priorité)

  Priority -1  SIGNAL_UP            tout régime, p_amp≥0.50, p_dir>0.60, excès baissier
                                    (size dynamique selon régime)
  Priority  0  SHORT_TD_HIGH        reg=Trend Down,  p_amp≥0.60, p_dir<0.30   size×1.5
  Priority  2  LONG_CHOPPY          reg=Choppy,      p_amp≥0.50, p_dir>0.58
  Priority  2  SHORT_CHOPPY         reg=Choppy,      p_amp≥0.50, p_dir<0.42
  Priority  3  LONG_TU (V10)        reg=Trend Up,    p_amp≥0.55, p_dir>0.62, ADX≥25
  Priority  4  LONG_EXIT_TD         exit_td_window,  reg≠TD,     p_amp≥0.40
  Priority  5  LONG_RANGE_STRICT    reg=Range,       p_amp≥0.60, p_dir>0.60
  Priority  6  LONG_RANGE_LIGHT     reg=Range,       p_amp≥0.50, p_dir>0.55, size×0.6 (NEW)

Réutilise les modèles V4 pré-entraînés.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import pre_val
from app.core.indicators import safe_num as _safe_num
from app.engine.engine import BaseStrategyML
from app.strategies.opus_stat_pretrained_v4 import (
    REGIME_CHOPPY,
    REGIME_LABELS,
    REGIME_RANGE,
    REGIME_TREND_DN,
    REGIME_TREND_UP,
    _detect_timeframe,
    _FeatureBuilder,
    _last_bar_hour_dow,
    _load_pretrained,
    _prepare_row,
    _to_pandas_window,
)

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")
_EXIT_TD_WINDOW_BARS = 3


# ─────────────────────────────────────────────────────────────────────────────
# Setups OMNIBUS V10
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    # SIGNAL_UP — size/sl dynamiques calculés runtime (cf. score())
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
    # SHORT_TD V9 supprimé : WR 50% sur 6 trades, redondant avec SHORT_TD_HIGH (WR 96%).
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
    # LONG_TU V10 — beaucoup plus sélectif que V9 (qui était net négatif)
    {
        "name": "LONG_TU", "priority": 3, "direction": 1, "enabled": True,
        "regime": REGIME_TREND_UP, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None,
        "needs_adx_above": 25.0,          # V10 : exige tendance forte
        "amp_min": 0.55, "dir_max": None, "dir_min": 0.62,   # V9 : 0.45 / 0.55
        "tp_mult": 1.4, "sl_mult": 1.1, "max_bars": 10, "size_factor": 1.0,
    },
    # LONG_PULLBACK_TU V9 supprimé (2 trades, breakeven, signal trop faible).
    {
        "name": "LONG_EXIT_TD", "priority": 4, "direction": 1, "enabled": True,
        "regime": None, "needs_exit_td_window": True,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.40, "dir_max": None, "dir_min": None,    # restauré V8 (V9 : 0.50)
        "tp_mult": 1.2, "sl_mult": 1.5, "max_bars": 8, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 5, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8, "sl_mult": 1.2, "max_bars": 6, "size_factor": 1.0,
    },
    # NEW V10 : couvre les ranges faibles, taille réduite pour limiter le risque.
    {
        "name": "LONG_RANGE_LIGHT", "priority": 6, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.55,
        "tp_mult": 0.7, "sl_mult": 1.0, "max_bars": 4, "size_factor": 0.6,
    },
)
_SETUP_NAMES = tuple(s["name"] for s in _DEFAULT_SETUPS)


def _classify_regime(adx_val: float, bull: int, bear: int,
                     adx_threshold: float = 20.0) -> int:
    if adx_val < adx_threshold:
        return REGIME_RANGE
    if bull == 1:
        return REGIME_TREND_UP
    if bear == 1:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _regime_history_from_features(features_df: pl.DataFrame, n_last: int = 5,
                                  adx_threshold: float = 20.0) -> List[int]:
    sub = features_df.tail(n_last)
    out: List[int] = []
    for row in sub.rows(named=True):
        adx_v = _safe_num(row.get("ADX") or 0.0, 0.0)
        bull  = int(_safe_num(row.get("MM_bullish_align") or 0, 0.0))
        bear  = int(_safe_num(row.get("MM_bearish_align") or 0, 0.0))
        out.append(_classify_regime(adx_v, bull, bear, adx_threshold))
    return out


def _exit_td_window_active(regimes: List[int],
                           window_bars: int = _EXIT_TD_WINDOW_BARS) -> bool:
    n = len(regimes)
    if n < 2:
        return False
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


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


def _signal_up_dynamic_risk(regime: int) -> Tuple[float, float]:
    """V10 : size_factor et sl_mult adaptés au régime pour SIGNAL_UP.

    Backtest V9 a montré que SIGNAL_UP ×1.5 amplifiait 3 stops à -13 USDC
    en régime trending. La stratégie est conçue pour mean-reversion
    (excès baissier suivi de rebond), donc en trending on capte mal :
        - Choppy/Range  : ×1.5 / sl 1.3  (terrain d'origine, WR>85%)
        - Trend Down    : ×1.2 / sl 1.1  (catch falling knife → prudence)
        - Trend Up      : ×1.0 / sl 1.0  (faux pullback dans uptrend → exit vite)

    Retourne (size_multiplier, sl_mult_override).
    """
    if regime == REGIME_CHOPPY or regime == REGIME_RANGE:
        return 1.5, 1.3
    if regime == REGIME_TREND_DN:
        return 1.2, 1.1
    if regime == REGIME_TREND_UP:
        return 1.0, 1.0
    return 1.0, 1.3


# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """V10 OMNIBUS — V9 corrigé : LONG_TU sélectif, SIGNAL_UP dynamique, LONG_RANGE_LIGHT."""

    name      = "opus_omnibus_v10"
    model_dir = os.path.join(os.path.dirname(__file__), "opus_stat_pretrained_v4_data")

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
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
        "setup_long_tu_sl_mult":            [1.0, 1.1, 1.3],
        "setup_long_tu_max_bars":           [8, 10, 12],
        "setup_long_range_strict_amp_min":  [0.55, 0.60, 0.65],
        "setup_long_range_strict_dir_min":  [0.55, 0.60, 0.65],
        "setup_long_range_light_amp_min":   [0.45, 0.50, 0.55],
        "setup_long_range_light_dir_min":   [0.52, 0.55, 0.58],
        "setup_long_range_light_size_factor": [0.5, 0.6, 0.7],
        "exit_td_window_bars":              [2, 3, 4],
    }
    fixed_params: Dict[str, Any] = {}

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
        # V10 : autorise OFF du sizing dynamique (debug / A/B)
        "signal_up_dynamic_risk":      True,
    }

    _FEATURE_BUILDER = _FeatureBuilder()
    retrain_interval_h: int = 24 * 365

    def __init__(self):
        self._lock = threading.Lock()
        self._models:  Dict[tuple, Any] = {}
        self._medians: Dict[tuple, Dict[str, float]] = {}
        self._loaded = False
        self._managed_externally = True
        self._best_auc            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        self._bt_features = None
        self._bt_features_len = 0
        self._ensure_loaded()

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            from app.core.feature_store import cached_strategy_features
            _ohlcv = ("time", "open", "high", "low", "close", "volume")
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="opus_v4_polars", version="1",
                builder=lambda w: self._FEATURE_BUILDER.build(
                    w.select([c for c in _ohlcv if c in w.columns])),
                in_kind="polars", out_kind="polars")
            self._bt_features = feats
            self._bt_features_len = len(df) if feats is not None else 0
            logger.info(
                f"[OmnibusV10] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(len(feats.columns) if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OmnibusV10] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        try:
            models, medians = _load_pretrained()
            with self._lock:
                self._models  = dict(models)
                self._medians = dict(medians)
                self._loaded  = True
                self._best_auc_per_tf = {"15m": 0.626, "30m": 0.597, "1h": 0.603}
                self._best_auc        = max(self._best_auc_per_tf.values())
                for tf in _SUPPORTED_TFS:
                    self._train_meta[tf] = {
                        "auc_amp": {"15m": 0.749, "30m": 0.690, "1h": 0.676}.get(tf, 0.0),
                        "auc_dir": {"15m": 0.503, "30m": 0.504, "1h": 0.530}.get(tf, 0.0),
                        "source":  "v4_models.pkl (pré-entraîné V4, partagé V7-V10)",
                    }
            return True
        except Exception as e:
            logger.error(f"[OmnibusV10] Chargement modèles V4 KO : {e}")
            return False

    @property
    def is_trained(self) -> bool:
        return self._loaded

    @property
    def managed_externally(self) -> bool:
        return True

    @managed_externally.setter
    def managed_externally(self, _v: bool) -> None:
        return

    def min_bars_required(self, params: dict = None) -> int:
        return 230

    def reset_model(self) -> None:
        self._bt_features = None
        self._bt_features_len = 0

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        self._ensure_loaded()

    def save_model(self, path: str) -> None:
        return

    def load_model(self, path: str = None) -> bool:
        return self._ensure_loaded()

    def _predict(self, features_df: pl.DataFrame, tf: str,
                 target: str) -> Optional[float]:
        key = (tf, target, "single")
        entry = self._models.get(key)
        if entry is None:
            return None
        try:
            feat_names = entry["features"]
            medians    = self._medians.get((tf, target), {})
            X          = _prepare_row(features_df, feat_names, medians)
            # phase6 : booster natif (predict) au lieu de clf.predict_proba (sklearn).
            return float(entry["model"].predict(X)[0])
        except Exception as e:
            logger.warning(f"[OmnibusV10] Prédiction {key} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "dir")

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if not self._ensure_loaded():
            return self._none("Modèles V4 indisponibles")

        if df is None or len(df) < self.min_bars_required():
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        enable_hour_filter = bool(p.get("enable_hour_filter", self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc   = list(p.get("active_hours_utc",   self._DEFAULTS["active_hours_utc"]))
        active_days        = list(p.get("active_days",        self._DEFAULTS["active_days"]))
        adx_threshold      = float(p.get("adx_threshold",     self._DEFAULTS["adx_threshold"]))
        exit_td_window_bars = int(p.get("exit_td_window_bars", self._DEFAULTS["exit_td_window_bars"]))
        disable_trailing   = bool(p.get("disable_trailing",   self._DEFAULTS["disable_trailing"]))
        use_fixed_tp       = bool(p.get("use_fixed_tp",       self._DEFAULTS["use_fixed_tp"]))
        signal_up_dyn      = bool(p.get("signal_up_dynamic_risk", self._DEFAULTS["signal_up_dynamic_risk"]))

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

        if self._bt_features is not None and len(df) <= self._bt_features_len:
            features = self._bt_features.head(len(df))
        else:
            window   = _to_pandas_window(df, n=max(260, self.min_bars_required() + 20))
            features = self._FEATURE_BUILDER.build(window)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v    = _safe_num(last_row.get("ATR_14") or 0.0, 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now    = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime_history = _regime_history_from_features(
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

        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma_pct = float(p.get("bearish_excess_sma_pct", 1.5))

        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False

        rsi_v = _safe_num(last_row.get("RSI_14", 50.0), 50.0)
        adx_v = _safe_num(last_row.get("ADX", 0.0), 0.0)
        rsi_excess = rsi_v < be_rsi_thr

        sma20_v = float(pre_val(df, "_pre_sma20") or 0.0)
        if sma20_v <= 0 and len(df) >= 20:
            sma20_v = float(df["close"].rolling_mean(20)[-1] or 0.0)
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

        # V10 : SIGNAL_UP — size_factor et sl_mult dynamiques selon régime
        signal_up_dyn_applied = False
        if setup["name"] == "SIGNAL_UP" and signal_up_dyn:
            setup = dict(setup)
            size_mult, sl_override = _signal_up_dynamic_risk(regime)
            setup["size_factor"] = float(setup.get("size_factor", 1.0)) * size_mult
            setup["sl_mult"]     = sl_override
            signal_up_dyn_applied = True

        side = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])

        # Score multiplicatif conservé pour V10 (V10.1 utilisera l'additif).
        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self._train_meta.get(tf, {})
        size_factor = float(setup.get("size_factor", 1.0))

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
            "consec_red":       bool(consec_red),
            "rsi_excess":       bool(rsi_excess),
            "price_below_sma20": bool(price_below_sma20),
            "rsi":              round(rsi_v, 1),
            "adx":              round(adx_v, 1),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(rsi_v, 1),
            "sma20":            round(sma20_v, 4) if sma20_v > 0 else None,
            "bearish_excess":   bool(bearish_excess),
            "signal_up_dyn":    bool(signal_up_dyn_applied),
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
        }

        conditions = [
            f"Setup V10 retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime : {regime_lbl} | ADX={adx_v:.1f} | exit_td_window={exit_td_active}",
            f"P(événement)={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse)={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse)={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse)={p_up:.2f} (pas de seuil dir)"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | "
            f"max {max_bars} bougies",
        ]
        if setup["name"] == "SIGNAL_UP":
            conditions.append(
                f"Excès baissier : rouge×2={consec_red} | RSI<{be_rsi_thr:.0f}={rsi_excess} | "
                f"Prix<SMA20-{be_sma_pct:.1f}%={price_below_sma20}"
            )
            if signal_up_dyn_applied:
                conditions.append(
                    f"V10 SIGNAL_UP dynamique : size×{size_factor:.2f} SL {sl_atr_mult:.2f}×ATR "
                    f"(adapté régime {regime_lbl})"
                )
        if setup["name"] == "LONG_TU":
            conditions.append(
                f"V10 LONG_TU strict : ADX={adx_v:.1f} ≥ {setup.get('needs_adx_above', 25):.0f} ✓ "
                f"(p_dir hors zone aveugle du modèle V4)"
            )
        if setup["name"] == "LONG_RANGE_LIGHT":
            conditions.append(
                f"V10 LONG_RANGE_LIGHT (size réduit ×{size_factor:.2f}, max {max_bars} bougies)"
            )

        sig["conditions"] = conditions
        sig["reason"] = (
            f"OmnibusV10 {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}"
        )
        return sig

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        setup_name = position.get("setup")
        if not setup_name:
            ind = position.get("indicators") or {}
            setup_name = ind.get("setup")
        if not setup_name:
            return None

        if not self._ensure_loaded():
            return None
        if df is None or len(df) < self.min_bars_required():
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        dir_inv_short  = float(p.get("early_exit_dir_inv_short",  0.55))
        dir_inv_long   = float(p.get("early_exit_dir_inv_long",   0.42))
        dir_drop_range = float(p.get("early_exit_dir_drop_range", 0.40))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return None

        try:
            if self._bt_features is not None and len(df) <= self._bt_features_len:
                features = self._bt_features.head(len(df))
            else:
                window   = _to_pandas_window(df, n=max(260, self.min_bars_required() + 20))
                features = self._FEATURE_BUILDER.build(window)
            if features is None or len(features) == 0:
                return None
            last_row = features.row(-1, named=True)
            regime = _classify_regime(
                _safe_num(last_row.get("ADX") or 0.0, 0.0),
                int(_safe_num(last_row.get("MM_bullish_align") or 0, 0.0)),
                int(_safe_num(last_row.get("MM_bearish_align") or 0, 0.0)),
                adx_threshold,
            )
            p_up = self.predict_direction(features, tf)
            if p_up is None:
                return None
        except Exception as e:
            logger.warning(f"[OmnibusV10] check_early_exit recompute KO : {e}")
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
