"""Helpers polars-only partagés entre les stratégies opus_v4 / opus_omnibus_v6.

Extraits ici pour permettre aux variantes ``retrained_v4`` et ``omnibus_v6``
d'éviter d'importer (même indirectement) le module ``opus_stat_pretrained_v4``
qui charge ``pandas`` au niveau module.

Contenu :
  - Codes / labels de régimes (REGIME_*).
  - ``detect_timeframe`` / ``last_bar_hour_dow`` : helpers temporels sur polars.
  - ``classify_regime`` + ``regime_history_from_features`` (polars) +
    ``exit_td_window_active``.
  - Setups OMNIBUS V7 : ``DEFAULT_SETUPS``, ``apply_setup_overrides``,
    ``evaluate_setup``, ``select_setup``, ``check_early_exit_v7``.
"""
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl


# Codes des régimes (alignés sur risk.py V4)
REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3
REGIME_LABELS   = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
    -1: "?",
}


def detect_timeframe(df: pl.DataFrame) -> Optional[str]:
    """Détecte le timeframe via la médiane des écarts ``df['time'].diff()``.

    Retourne ``"15m" | "30m" | "1h"`` ou ``None`` si indéterminable.
    """
    if "time" not in df.columns or len(df) < 3:
        return None
    times = df["time"]
    try:
        deltas = times.diff().drop_nulls()
        if len(deltas) == 0:
            return None
        try:
            med_us = deltas.dt.total_microseconds().median()
            med_s  = float(med_us) / 1_000_000.0
        except Exception:
            med_s = float(deltas.median().total_seconds())
    except Exception:
        arr = times.to_numpy()
        try:
            diffs = np.diff(arr.astype("float64"))
            med_s = float(np.median(diffs))
            if med_s > 1e6:
                med_s /= 1000.0
        except Exception:
            return None
    if med_s <= 0:
        return None
    if abs(med_s - 900)  < 60:
        return "15m"
    if abs(med_s - 1800) < 120:
        return "30m"
    if abs(med_s - 3600) < 240:
        return "1h"
    return None


def last_bar_hour_dow(df: pl.DataFrame) -> tuple:
    """``(hour_utc, weekday)`` de la dernière barre, ou ``(None, None)``."""
    if "time" not in df.columns or len(df) == 0:
        return None, None
    ts = df["time"][-1]
    try:
        if hasattr(ts, "hour") and hasattr(ts, "weekday"):
            return int(ts.hour), int(ts.weekday())
    except Exception:
        pass
    try:
        import datetime as _dt
        raw = float(ts)
        if raw > 1e12:
            raw /= 1000.0
        d = _dt.datetime.utcfromtimestamp(raw)
        return d.hour, d.weekday()
    except Exception:
        return None, None


# ═════════════════════════════════════════════════════════════════════════════
#  Régimes + sélection de setup OMNIBUS V7
#  (copie polars-compatible des helpers de ``opus_omnibus_v6_pretrained``)
# ═════════════════════════════════════════════════════════════════════════════

EXIT_TD_WINDOW_BARS = 3   # fenêtre LONG_EXIT_TD (bougies après sortie de TD)


def classify_regime(adx_val: float, bull: int, bear: int,
                    adx_threshold: float = 20.0) -> int:
    if adx_val < adx_threshold:
        return REGIME_RANGE
    if bull == 1:
        return REGIME_TREND_UP
    if bear == 1:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def regime_history_from_features(features_df: pl.DataFrame, n_last: int = 5,
                                  adx_threshold: float = 20.0) -> List[int]:
    """Séquence des régimes sur les ``n_last`` dernières bougies (polars)."""
    sub = features_df.tail(n_last)
    rows = sub.select(["ADX", "MM_bullish_align", "MM_bearish_align"]).rows()
    out: List[int] = []
    for adx_v, bull, bear in rows:
        out.append(classify_regime(
            float(adx_v) if adx_v is not None else 0.0,
            int(bull)    if bull  is not None else 0,
            int(bear)    if bear  is not None else 0,
            adx_threshold,
        ))
    return out


def exit_td_window_active(regimes: List[int],
                          window_bars: int = EXIT_TD_WINDOW_BARS) -> bool:
    """True si on est sorti d'un Trend Down dans les ``window_bars`` dernières bougies."""
    n = len(regimes)
    if n < 2:
        return False
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


# ─── Setups V7 ──────────────────────────────────────────────────────────────
DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
        "tp_mult": 1.4,  "sl_mult": 1.6,  "max_bars": 8,  "size_factor": 1.5,
    },
    {
        "name": "SHORT_TD",     "priority": 1, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": 0.40, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.6,  "max_bars": 8,  "size_factor": 1.0,
    },
    {
        "name": "LONG_CHOPPY",  "priority": 2, "direction":  1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
        "tp_mult": 0.9,  "sl_mult": 1.2,  "max_bars": 5,  "size_factor": 1.0,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.4,  "max_bars": 6,  "size_factor": 1.0,
    },
    {
        "name": "LONG_EXIT_TD", "priority": 3, "direction":  1, "enabled": True,
        "regime": None,  "needs_exit_td_window": True,
        "amp_min": 0.40, "dir_max": None, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.5,  "max_bars": 8,  "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 4, "direction":  1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8,  "sl_mult": 1.2,  "max_bars": 6,  "size_factor": 1.0,
    },
)
SETUP_NAMES = tuple(s["name"] for s in DEFAULT_SETUPS)


def apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Superpose les surcharges YAML sur ``DEFAULT_SETUPS``.

    Convention de clés : ``setup_<name_lower>_<field>``.
    """
    setups: List[Dict[str, Any]] = []
    for src in DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "tp_mult", "sl_mult", "max_bars", "enabled", "size_factor"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def evaluate_setup(setup: Dict[str, Any], regime: int, p_event: float, p_up: float,
                   exit_td_active: bool) -> bool:
    if not setup.get("enabled", True):
        return False
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    if setup["needs_exit_td_window"]:
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
    return True


def select_setup(setups: List[Dict[str, Any]], regime: int, p_event: float,
                 p_up: float, exit_td_active: bool) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if evaluate_setup(s, regime, p_event, p_up, exit_td_active)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


def check_early_exit_v7(setup_name: str, regime: int, p_up: float,
                        dir_inv_short: float = 0.55,
                        dir_inv_long: float = 0.40,
                        dir_drop_range: float = 0.40) -> Optional[str]:
    """Sorties anticipées V7 (cf. docstring de ``opus_omnibus_v6_pretrained``).

      SHORT_TD_HIGH / SHORT_TD :
        régime ≠ TD              → 'regime_exit_TD'
        p_dir > dir_inv_short     → 'p_dir_inversion'
      SHORT_CHOPPY :
        régime ≠ Choppy           → 'regime_exit_choppy'
        p_dir > 0.58              → 'p_dir_inversion'  (V7 : durci)
      LONG_CHOPPY (V7 assoupli) :
        p_dir < dir_inv_long      → 'p_dir_drop'
        régime = TD               → 'to_TD'
      LONG_EXIT_TD :
        régime = TD               → 'back_to_TD'
      LONG_RANGE_STRICT :
        régime = TD               → 'regime_to_TD'
        p_dir < dir_drop_range    → 'p_dir_drop'
    """
    if setup_name in ("SHORT_TD_HIGH", "SHORT_TD"):
        if regime != REGIME_TREND_DN:    return "regime_exit_TD"
        if p_up > dir_inv_short:         return "p_dir_inversion"
    elif setup_name == "SHORT_CHOPPY":
        if regime != REGIME_CHOPPY:      return "regime_exit_choppy"
        if p_up > 0.58:                  return "p_dir_inversion"
    elif setup_name == "LONG_CHOPPY":
        if p_up < dir_inv_long:          return "p_dir_drop"
        if regime == REGIME_TREND_DN:    return "to_TD"
    elif setup_name == "LONG_EXIT_TD":
        if regime == REGIME_TREND_DN:    return "back_to_TD"
    elif setup_name == "LONG_RANGE_STRICT":
        if regime == REGIME_TREND_DN:    return "regime_to_TD"
        if p_up < dir_drop_range:        return "p_dir_drop"
    return None
