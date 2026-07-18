"""Opus Omnibus V11 — variante *sans ML* (``opus_omnibus_v11_no_ml``).

Jumeau déterministe et **autonome** de ``opus_omnibus_v11`` : même esprit de
routing (régime enrichi à 4 classes avec requalification DI/pente, 8 setups,
SIGNAL_UP à risque dynamique, fenêtre exit_td, excès baissier, sorties
anticipées) mais les deux sorties ML ``p_event`` (amplitude) et ``p_up``
(direction) sont remplacées par des **proxys d'indicateurs**.

Aucune dépendance à un autre module de stratégie ni à un modèle entraîné. Tous
les indicateurs proviennent de ``app.core.indicators`` : ils sont lus en O(1)
depuis les colonnes ``_pre_*`` pré-calculées par ``precompute_df`` (appliqué par
le backtest et le live), avec repli idempotent si elles sont absentes. Aucun
DataFrame de features lourd n'est construit → coût CPU minimal.

Proxys
------
* ``p_up`` ∈ [0,1] (0.5 neutre) — sigmoïde d'une moyenne pondérée de signaux
  directionnels normalisés : DI_diff, RSI, MACD/ATR, ROC, distance SMA50,
  vélocité RSI, position dans la range 20, direction du corps de bougie.
* ``p_event`` ∈ [0,1] — sigmoïde recentrée d'une moyenne de signaux d'amplitude
  déjà normalisés par leur moyenne 100 barres (donc TF-indépendants) :
  ATR%, range, écart-type des log-returns, ratio de volume, ADX, corps absolu.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import precompute_df, pre_val

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")
_EXIT_TD_WINDOW_BARS = 3

REGIME_RANGE, REGIME_TREND_UP, REGIME_TREND_DN, REGIME_CHOPPY = 0, 1, 2, 3
REGIME_LABELS = {
    REGIME_RANGE: "Range", REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down", REGIME_CHOPPY: "Choppy", -1: "?",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Outils numériques
# ─────────────────────────────────────────────────────────────────────────────
def _sig(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _tanh(x: float) -> float:
    return math.tanh(_clip(x, -30.0, 30.0))


def _detect_timeframe(df: pl.DataFrame) -> Optional[str]:
    if "time" not in df.columns or len(df) < 3:
        return None
    try:
        med_us = df["time"].tail(64).diff().drop_nulls().dt.total_microseconds().median()
        med_s = float(med_us) / 1_000_000.0
    except Exception:
        return None
    if med_s <= 0:
        return None
    if abs(med_s - 900) < 60:   return "15m"
    if abs(med_s - 1800) < 120: return "30m"
    if abs(med_s - 3600) < 240: return "1h"
    if abs(med_s - 14400) < 960:  return "4h"
    if abs(med_s - 86400) < 5760: return "1d"
    return None


def _last_bar_hour_dow(df: pl.DataFrame) -> Tuple[Optional[int], Optional[int]]:
    if "time" not in df.columns or len(df) == 0:
        return None, None
    ts = df["time"][-1]
    try:
        return int(ts.hour), int(ts.weekday())
    except Exception:
        return None, None


# ─────────────────────────────────────────────────────────────────────────────
#  Proxys p_up / p_event (à partir de scalaires d'indicateurs)
# ─────────────────────────────────────────────────────────────────────────────
def _proxy_p_up(*, pdi, ndi, rsi, macd_hist, atr, roc, c, sma50,
                rsi_vel, range_pos, body, gain: float) -> float:
    di    = _tanh((pdi - ndi) / 20.0)
    r     = _clip((rsi - 50.0) / 30.0, -1.0, 1.0)
    macd  = _tanh(macd_hist / (0.5 * atr + 1e-9))
    rocs  = _tanh(roc / 5.0)
    dist  = _tanh(((c - sma50) / sma50 * 15.0) if sma50 > 0 else 0.0)
    rvel  = _tanh(rsi_vel / 15.0)
    rpos  = _clip((range_pos - 0.5) * 2.0, -1.0, 1.0)
    bdy   = _tanh(body * 200.0)
    w = (1.0, 0.7, 0.8, 0.8, 0.8, 0.6, 0.6, 0.4)
    s = (di, r, macd, rocs, dist, rvel, rpos, bdy)
    return _sig(gain * (sum(wi * si for wi, si in zip(w, s)) / sum(w)))


def _proxy_p_event(*, atr_pct_r, range_r, volstd_r, volr, adx, body_abs_r,
                   center: float, gain: float) -> float:
    def _norm(x):  # ratio centré ~1.0 → [0,1] (0.7→0, 2.0→1)
        return _clip((x - 0.7) / 1.3, 0.0, 1.0)
    a = (_norm(atr_pct_r), _norm(range_r), _norm(volstd_r),
         _clip((volr - 1.0) / 1.5, 0.0, 1.0), _clip(adx / 40.0, 0.0, 1.0),
         _norm(body_abs_r))
    w = (1.0, 0.7, 0.7, 0.7, 0.6, 0.5)
    raw = sum(wi * ai for wi, ai in zip(w, a)) / sum(w)
    return _sig(gain * (raw - center))


# ─────────────────────────────────────────────────────────────────────────────
#  Régime enrichi V11
# ─────────────────────────────────────────────────────────────────────────────
def _regime(adx, bull, bear, di_diff, slope20, adx_thr, di_rescue) -> int:
    if adx < adx_thr:
        return REGIME_RANGE
    if bull:
        return REGIME_TREND_UP
    if bear:
        return REGIME_TREND_DN
    if di_diff > di_rescue and slope20 > 0:
        return REGIME_TREND_UP
    if di_diff < -di_rescue and slope20 < 0:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _exit_td_window_active(regimes: List[int], window_bars: int) -> bool:
    n = len(regimes)
    if n < 2:
        return False
    for k in range(max(1, n - window_bars), n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Setups V10/V11
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {"name": "SIGNAL_UP", "priority": -1, "direction": 1, "regime": None,
     "needs_exit_td_window": False, "needs_bearish_excess": True, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": None, "dir_min": 0.60,
     "tp_mult": 1.0, "sl_mult": 1.3, "max_bars": 6, "size_factor": 1.0},
    {"name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "regime": REGIME_TREND_DN,
     "needs_exit_td_window": False, "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
     "tp_mult": 1.4, "sl_mult": 1.6, "max_bars": 8, "size_factor": 1.5},
    {"name": "LONG_CHOPPY", "priority": 2, "direction": 1, "regime": REGIME_CHOPPY,
     "needs_exit_td_window": False, "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
     "tp_mult": 0.9, "sl_mult": 1.2, "max_bars": 10, "size_factor": 1.0},
    {"name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "regime": REGIME_CHOPPY,
     "needs_exit_td_window": False, "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
     "tp_mult": 1.2, "sl_mult": 1.4, "max_bars": 6, "size_factor": 1.0},
    {"name": "LONG_TU", "priority": 3, "direction": 1, "regime": REGIME_TREND_UP,
     "needs_exit_td_window": False, "needs_bearish_excess": False, "needs_adx_above": 25.0,
     "amp_min": 0.55, "dir_max": None, "dir_min": 0.62,
     "tp_mult": 1.4, "sl_mult": 1.1, "max_bars": 10, "size_factor": 1.0},
    {"name": "LONG_EXIT_TD", "priority": 4, "direction": 1, "regime": None,
     "needs_exit_td_window": True, "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.40, "dir_max": None, "dir_min": None,
     "tp_mult": 1.2, "sl_mult": 1.5, "max_bars": 8, "size_factor": 1.0},
    {"name": "LONG_RANGE_STRICT", "priority": 5, "direction": 1, "regime": REGIME_RANGE,
     "needs_exit_td_window": False, "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
     "tp_mult": 0.8, "sl_mult": 1.2, "max_bars": 6, "size_factor": 1.0},
    {"name": "LONG_RANGE_LIGHT", "priority": 6, "direction": 1, "regime": REGIME_RANGE,
     "needs_exit_td_window": False, "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": None, "dir_min": 0.55,
     "tp_mult": 0.7, "sl_mult": 1.0, "max_bars": 4, "size_factor": 0.6},
)

_OVERRIDE_FIELDS = ("priority", "direction", "amp_min", "dir_max", "dir_min",
                    "tp_mult", "sl_mult", "max_bars", "enabled", "size_factor",
                    "needs_bearish_excess", "needs_adx_above")


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in _OVERRIDE_FIELDS:
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        out.append(s)
    return out


def _evaluate_setup(s, regime, p_event, p_up, exit_td, bearish_excess, adx) -> bool:
    if not s.get("enabled", True):
        return False
    if s["regime"] is not None and regime != s["regime"]:
        return False
    if s.get("needs_exit_td_window", False):
        if not exit_td or regime == REGIME_TREND_DN:
            return False
    if p_event < float(s["amp_min"]):
        return False
    if s["dir_max"] is not None and p_up >= float(s["dir_max"]):
        return False
    if s["dir_min"] is not None and p_up <= float(s["dir_min"]):
        return False
    if s.get("needs_bearish_excess", False) and not bearish_excess:
        return False
    if s.get("needs_adx_above") is not None and adx < float(s["needs_adx_above"]):
        return False
    return True


def _select_setup(setups, regime, p_event, p_up, exit_td, bearish_excess, adx):
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, exit_td, bearish_excess, adx)]
    return min(cands, key=lambda s: s["priority"]) if cands else None


def _check_early_exit(setup_name, regime, p_up,
                      dir_inv_short=0.55, dir_inv_long=0.42, dir_drop_range=0.40):
    if setup_name == "SIGNAL_UP":
        if p_up < dir_inv_long:       return "p_dir_drop"
        if regime == REGIME_TREND_DN: return "to_TD"
    elif setup_name == "SHORT_TD_HIGH":
        if regime != REGIME_TREND_DN: return "regime_exit_TD"
        if p_up > dir_inv_short:      return "p_dir_inversion"
    elif setup_name == "LONG_CHOPPY":
        if p_up < dir_inv_long:       return "p_dir_drop"
        if regime == REGIME_TREND_DN: return "to_TD"
    elif setup_name == "SHORT_CHOPPY":
        if regime != REGIME_CHOPPY:   return "regime_exit_choppy"
        if p_up > 0.58:               return "p_dir_inversion"
    elif setup_name == "LONG_TU":
        if regime == REGIME_TREND_DN: return "to_TD"
        if p_up < dir_inv_long:       return "p_dir_drop"
    elif setup_name == "LONG_EXIT_TD":
        if regime == REGIME_TREND_DN: return "back_to_TD"
    elif setup_name in ("LONG_RANGE_STRICT", "LONG_RANGE_LIGHT"):
        if regime == REGIME_TREND_DN: return "regime_to_TD"
        if p_up < dir_drop_range:     return "p_dir_drop"
    return None


def _signal_up_dynamic_risk(regime: int) -> Tuple[float, float]:
    if regime in (REGIME_CHOPPY, REGIME_RANGE):
        return 1.5, 1.3
    if regime == REGIME_TREND_DN:
        return 1.2, 1.1
    if regime == REGIME_TREND_UP:
        return 1.0, 1.0
    return 1.0, 1.3


# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategy):
    """V11 OMNIBUS sans ML — régime enrichi + proxys d'indicateurs (O(1))."""

    name = "opus_omnibus_v11_no_ml"
    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        "setup_signal_up_amp_min":         [0.45, 0.50, 0.55],
        "setup_signal_up_dir_min":         [0.55, 0.60, 0.65],
        "setup_short_td_high_amp_min":     [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":     [0.25, 0.30, 0.35],
        "setup_long_choppy_amp_min":       [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":       [0.55, 0.58, 0.62],
        "setup_short_choppy_amp_min":      [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":      [0.38, 0.42, 0.46],
        "setup_long_tu_amp_min":           [0.50, 0.55, 0.60],
        "setup_long_tu_dir_min":           [0.58, 0.62, 0.66],
        "setup_long_range_strict_amp_min": [0.55, 0.60, 0.65],
        "setup_long_range_light_amp_min":  [0.45, 0.50, 0.55],
        "exit_td_window_bars":             [2, 3, 4],
        "di_rescue":                       [8.0, 10.0, 14.0],
        "p_up_gain":                       [1.5, 2.0, 2.5],
        "p_event_gain":                    [2.5, 3.0, 4.0],
        "p_event_center":                  [0.38, 0.42, 0.46],
    }
    fixed_params: Dict[str, Any] = {}

    _DEFAULTS = {
        "enable_hour_filter": True,
        "active_hours_utc": list(range(13, 21)),
        "active_days": [0, 1, 2, 3, 4],
        "adx_threshold": 20.0,
        "exit_td_window_bars": _EXIT_TD_WINDOW_BARS,
        "disable_trailing": True,
        "use_fixed_tp": True,
        "bearish_excess_rsi_threshold": 38.0,
        "bearish_excess_sma_pct": 1.5,
        "signal_up_dynamic_risk": True,
        "di_rescue": 10.0,
        "p_up_gain": 2.0,
        "p_event_gain": 3.0,
        "p_event_center": 0.42,
    }

    def min_bars_required(self, params: dict = None) -> int:
        return 230

    # ── Lecture O(1) des indicateurs pré-calculés ──────────────────────────────
    @staticmethod
    def _tail(df: pl.DataFrame, col: str, k: int) -> np.ndarray:
        return df[col][-k:].to_numpy().astype(np.float64)

    def _regimes(self, df: pl.DataFrame, k: int,
                 adx_thr: float, di_rescue: float) -> List[int]:
        kp = k + 5
        adx = self._tail(df, "_pre_adx14", kp)
        s20 = self._tail(df, "_pre_sma20", kp)
        s50 = self._tail(df, "_pre_sma50", kp)
        s100 = self._tail(df, "_pre_sma100", kp)
        s200 = self._tail(df, "_pre_sma200", kp)
        pdi = self._tail(df, "_pre_pdi14", kp)
        ndi = self._tail(df, "_pre_ndi14", kp)
        c = self._tail(df, "close", kp)
        n = len(adx)
        out: List[int] = []
        for i in range(max(0, n - k), n):
            bull = int(s20[i] > s50[i] > s100[i] > s200[i])
            bear = int(s20[i] < s50[i] < s100[i] < s200[i])
            slope20 = (s20[i] - s20[i - 4]) / (4.0 * c[i]) if i >= 4 and c[i] > 0 else 0.0
            out.append(_regime(adx[i], bull, bear, pdi[i] - ndi[i], slope20,
                               adx_thr, di_rescue))
        return out

    def _proxies(self, df: pl.DataFrame, p: dict) -> Tuple[float, float, dict]:
        c   = float(df["close"][-1] or 0.0)
        rsi = float(pre_val(df, "_pre_rsi14") or 50.0)
        adx = float(pre_val(df, "_pre_adx14") or 0.0)
        pdi = float(pre_val(df, "_pre_pdi14") or 0.0)
        ndi = float(pre_val(df, "_pre_ndi14") or 0.0)
        atr = float(pre_val(df, "_pre_atr14") or 0.0)
        sma50 = float(pre_val(df, "_pre_sma50") or 0.0)
        macd_hist = float(pre_val(df, "_pre_macd_hist") or 0.0)
        rsi_vel = float(pre_val(df, "_pre_rsi_vel6") or 0.0)
        range_pos = float(pre_val(df, "_pre_range_pos20") or 0.5)
        body = float(pre_val(df, "_pre_body") or 0.0)
        atr_pct_r = float(pre_val(df, "_pre_atr_pct_r") or 1.0)
        range_r = float(pre_val(df, "_pre_range_r") or 1.0)
        volstd_r = float(pre_val(df, "_pre_volstd20_r") or 1.0)
        volr = float(pre_val(df, "_pre_volratio20") or 1.0)
        body_abs_r = float(pre_val(df, "_pre_body_abs_r") or 1.0)
        roc = ((c / float(df["close"][-15]) - 1.0) * 100.0) if len(df) > 15 and float(df["close"][-15]) > 0 else 0.0

        p_up = _proxy_p_up(
            pdi=pdi, ndi=ndi, rsi=rsi, macd_hist=macd_hist, atr=atr, roc=roc,
            c=c, sma50=sma50, rsi_vel=rsi_vel, range_pos=range_pos, body=body,
            gain=float(p.get("p_up_gain", self._DEFAULTS["p_up_gain"])),
        )
        p_event = _proxy_p_event(
            atr_pct_r=atr_pct_r, range_r=range_r, volstd_r=volstd_r, volr=volr,
            adx=adx, body_abs_r=body_abs_r,
            center=float(p.get("p_event_center", self._DEFAULTS["p_event_center"])),
            gain=float(p.get("p_event_gain", self._DEFAULTS["p_event_gain"])),
        )
        ctx = {"rsi": rsi, "adx": adx, "atr": atr, "sma20": float(pre_val(df, "_pre_sma20") or 0.0)}
        return p_event, p_up, ctx

    def _bearish_excess(self, df: pl.DataFrame, ctx: dict, c_now: float, p: dict) -> bool:
        be_rsi = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma = float(p.get("bearish_excess_sma_pct", 1.5))
        consec_red = (len(df) >= 2 and
                      float(df["close"][-1]) < float(df["open"][-1]) and
                      float(df["close"][-2]) < float(df["open"][-2]))
        sma20 = ctx["sma20"]
        below = (c_now < sma20 * (1.0 - be_sma / 100.0)) if sma20 > 0 else False
        return bool(consec_red or ctx["rsi"] < be_rsi or below)

    # ── Score ──────────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required():
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")
        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)

        p = (params or {}).get(self.name, {})
        if bool(p.get("enable_hour_filter", self._DEFAULTS["enable_hour_filter"])):
            hour, dow = _last_bar_hour_dow(df)
            if hour is not None and dow is not None:
                if dow not in list(p.get("active_days", self._DEFAULTS["active_days"])):
                    return self._none(f"Hors jours actifs (weekday={dow})")
                if hour not in list(p.get("active_hours_utc", self._DEFAULTS["active_hours_utc"])):
                    return self._none(f"Hors session ({hour}h UTC)")

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(f"Timeframe non supporté (détecté={tf})")

        adx_thr   = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        di_rescue = float(p.get("di_rescue", self._DEFAULTS["di_rescue"]))
        exit_td_window_bars = int(p.get("exit_td_window_bars", self._DEFAULTS["exit_td_window_bars"]))
        disable_trailing = bool(p.get("disable_trailing", self._DEFAULTS["disable_trailing"]))
        use_fixed_tp = bool(p.get("use_fixed_tp", self._DEFAULTS["use_fixed_tp"]))
        signal_up_dyn = bool(p.get("signal_up_dynamic_risk", self._DEFAULTS["signal_up_dynamic_risk"]))

        p_event, p_up, ctx = self._proxies(df, p)
        c_now = float(df["close"][-1] or 0.0)
        atr_v = ctx["atr"]
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regimes = self._regimes(df, max(exit_td_window_bars + 2, 5), adx_thr, di_rescue)
        regime = regimes[-1]
        regime_lbl = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regimes, exit_td_window_bars)

        rsi_v, adx_v = ctx["rsi"], ctx["adx"]
        bearish_excess = self._bearish_excess(df, ctx, c_now, p)

        setups = _apply_setup_overrides(p)
        setup = _select_setup(setups, regime, p_event, p_up, exit_td_active, bearish_excess, adx_v)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} rsi={rsi_v:.1f} adx={adx_v:.1f}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        signal_up_dyn_applied = False
        if setup["name"] == "SIGNAL_UP" and signal_up_dyn:
            setup = dict(setup)
            size_mult, sl_override = _signal_up_dynamic_risk(regime)
            setup["size_factor"] = float(setup.get("size_factor", 1.0)) * size_mult
            setup["sl_mult"] = sl_override
            signal_up_dyn_applied = True

        side = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars = int(setup["max_bars"])
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence = abs(p_up - 0.5) * 2.0
        score_val = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        sig: Dict[str, Any] = {
            "score": score_val, "side": side, "name": self.name, "atr": atr_v,
            "sl_atr_mult": sl_atr_mult, "disable_trailing": disable_trailing,
            "size_factor": size_factor, "exit_after_bars": max_bars,
            "p_event": round(p_event, 4), "p_up": round(p_up, 4),
            "regime": regime, "regime_lbl": regime_lbl, "tf_detected": tf,
            "setup": setup["name"], "setup_priority": int(setup["priority"]),
            "exit_td_active": bool(exit_td_active), "bearish_excess": bool(bearish_excess),
            "signal_up_dyn": bool(signal_up_dyn_applied),
            "rsi": round(rsi_v, 1), "adx": round(adx_v, 1),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult
        sig["indicators"] = {
            "adx": round(adx_v, 1), "rsi": round(rsi_v, 1),
            "p_event": round(p_event, 4), "p_up": round(p_up, 4),
            "regime": regime, "regime_lbl": regime_lbl, "setup": setup["name"],
            "setup_priority": int(setup["priority"]), "sl_mult": sl_atr_mult,
            "tp_mult": tp_atr_mult if use_fixed_tp else None, "max_bars": max_bars,
            "proxy": True,
        }
        sig["conditions"] = [
            f"Setup V11 (no-ML) retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime enrichi : {regime_lbl} | ADX={adx_v:.1f} | exit_td={exit_td_active}",
            f"P(événement) proxy={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse) proxy={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse) proxy={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else f"P(hausse) proxy={p_up:.2f}"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | max {max_bars} bougies",
            "Probabilités issues de proxys d'indicateurs (sans modèle ML)",
        ]
        if signal_up_dyn_applied:
            sig["conditions"].append(
                f"SIGNAL_UP dynamique : size×{size_factor:.2f} SL {sl_atr_mult:.2f}×ATR")
        sig["reason"] = (
            f"OmnibusV11-NoML {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}")
        return sig

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        setup_name = position.get("setup") or (position.get("indicators") or {}).get("setup")
        if not setup_name:
            return None
        if df is None or len(df) < self.min_bars_required():
            return None
        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)

        p = (params or {}).get(self.name, {})
        adx_thr = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        di_rescue = float(p.get("di_rescue", self._DEFAULTS["di_rescue"]))
        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return None
        try:
            regime = self._regimes(df, 2, adx_thr, di_rescue)[-1]
            _, p_up, _ = self._proxies(df, p)
        except Exception as e:
            logger.warning(f"[OmnibusV11-NoML] check_early_exit KO : {e}")
            return None
        return _check_early_exit(
            setup_name, regime, p_up,
            dir_inv_short=float(p.get("early_exit_dir_inv_short", 0.55)),
            dir_inv_long=float(p.get("early_exit_dir_inv_long", 0.42)),
            dir_drop_range=float(p.get("early_exit_dir_drop_range", 0.40)),
        )

    def _none(self, reason: str = "", p_event: float = 0.0, p_up: float = 0.5,
              regime: int = -1) -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason,
                "p_event": round(p_event, 4), "p_up": round(p_up, 4),
                "regime": regime, "regime_lbl": REGIME_LABELS.get(regime, "?")}
