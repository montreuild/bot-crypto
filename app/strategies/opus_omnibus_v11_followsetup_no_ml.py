"""Opus Omnibus V11 FollowSetup — variante *sans ML*
(``opus_omnibus_v11_followsetup_no_ml``).

Jumeau déterministe et **autonome** de ``opus_omnibus_v11_followsetup`` : pas de
TP/trailing/timeout serré ; une position reste ouverte tant que le setup actif
pointe dans sa direction, et n'est clôturée que lorsqu'un setup **opposé** est
confirmé (anti-whipsaw : confirmation sur K bougies, cooldown, score minimum,
hystérésis). ``p_event``/``p_up`` sont des proxys d'indicateurs au lieu des
modèles LightGBM.

Autonome : aucune dépendance à un autre module de stratégie ni à un modèle. Les
indicateurs sont lus en O(1) depuis les colonnes ``_pre_*`` de
``app.core.indicators`` (``precompute_df``, repli idempotent si absentes).
"""

import datetime as _dt
import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import pre_val, precompute_df
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")
_FLIP_LOG_PATH = os.path.join("logs", "opus_omnibus_v11_followsetup_no_ml_flips.jsonl")

REGIME_RANGE, REGIME_TREND_UP, REGIME_TREND_DN, REGIME_CHOPPY = 0, 1, 2, 3
REGIME_LABELS = {
    REGIME_RANGE: "Range", REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down", REGIME_CHOPPY: "Choppy", -1: "?",
}


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
        med_s = float(df["time"].tail(64).diff().drop_nulls().dt.total_microseconds().median()) / 1e6
    except Exception:
        return None
    if med_s <= 0:
        return None
    if abs(med_s - 900) < 60:
        return "15m"
    if abs(med_s - 1800) < 120:
        return "30m"
    if abs(med_s - 3600) < 240:
        return "1h"
    if abs(med_s - 14400) < 960:
        return "4h"
    if abs(med_s - 86400) < 5760:
        return "1d"
    return None


def _proxy_p_up(*, pdi, ndi, rsi, macd_hist, atr, roc, c, sma50,
                rsi_vel, range_pos, body, gain: float) -> float:
    di   = _tanh((pdi - ndi) / 20.0)
    r    = _clip((rsi - 50.0) / 30.0, -1.0, 1.0)
    macd = _tanh(macd_hist / (0.5 * atr + 1e-9))
    rocs = _tanh(roc / 5.0)
    dist = _tanh(((c - sma50) / sma50 * 15.0) if sma50 > 0 else 0.0)
    rvel = _tanh(rsi_vel / 15.0)
    rpos = _clip((range_pos - 0.5) * 2.0, -1.0, 1.0)
    bdy  = _tanh(body * 200.0)
    w = (1.0, 0.7, 0.8, 0.8, 0.8, 0.6, 0.6, 0.4)
    s = (di, r, macd, rocs, dist, rvel, rpos, bdy)
    return _sig(gain * (sum(wi * si for wi, si in zip(w, s)) / sum(w)))


def _proxy_p_event(*, atr_pct_r, range_r, volstd_r, volr, adx, body_abs_r,
                   center: float, gain: float) -> float:
    def _norm(x):
        return _clip((x - 0.7) / 1.3, 0.0, 1.0)
    a = (_norm(atr_pct_r), _norm(range_r), _norm(volstd_r),
         _clip((volr - 1.0) / 1.5, 0.0, 1.0), _clip(adx / 40.0, 0.0, 1.0),
         _norm(body_abs_r))
    w = (1.0, 0.7, 0.7, 0.7, 0.6, 0.5)
    raw = sum(wi * ai for wi, ai in zip(w, a)) / sum(w)
    return _sig(gain * (raw - center))


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


# Setups FollowSetup : routing V10 SANS LONG_EXIT_TD, size_factor uniformisé.
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {"name": "SIGNAL_UP", "priority": -1, "direction": 1, "regime": None,
     "needs_bearish_excess": True, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": None, "dir_min": 0.60, "size_factor": 1.0},
    {"name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "regime": REGIME_TREND_DN,
     "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.60, "dir_max": 0.30, "dir_min": None, "size_factor": 1.0},
    {"name": "LONG_CHOPPY", "priority": 2, "direction": 1, "regime": REGIME_CHOPPY,
     "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": None, "dir_min": 0.58, "size_factor": 1.0},
    {"name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "regime": REGIME_CHOPPY,
     "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": 0.42, "dir_min": None, "size_factor": 1.0},
    {"name": "LONG_TU", "priority": 3, "direction": 1, "regime": REGIME_TREND_UP,
     "needs_bearish_excess": False, "needs_adx_above": 25.0,
     "amp_min": 0.55, "dir_max": None, "dir_min": 0.62, "size_factor": 1.0},
    {"name": "LONG_RANGE_STRICT", "priority": 5, "direction": 1, "regime": REGIME_RANGE,
     "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.60, "dir_max": None, "dir_min": 0.60, "size_factor": 1.0},
    {"name": "LONG_RANGE_LIGHT", "priority": 6, "direction": 1, "regime": REGIME_RANGE,
     "needs_bearish_excess": False, "needs_adx_above": None,
     "amp_min": 0.50, "dir_max": None, "dir_min": 0.55, "size_factor": 1.0},
)

_OVERRIDE_FIELDS = ("priority", "direction", "amp_min", "dir_max", "dir_min",
                    "enabled", "size_factor", "needs_bearish_excess", "needs_adx_above")


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


def _evaluate_setup(s, regime, p_event, p_up, bearish_excess, adx) -> bool:
    if not s.get("enabled", True):
        return False
    if s["regime"] is not None and regime != s["regime"]:
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


def _select_setup(setups, regime, p_event, p_up, bearish_excess, adx):
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, bearish_excess, adx)]
    return min(cands, key=lambda s: s["priority"]) if cands else None


class Strategy(BaseStrategy):
    """V11 FollowSetup sans ML — sortie sur flip de setup, proxys d'indicateurs."""

    name = "opus_omnibus_v11_followsetup_no_ml"
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
        "di_rescue":              [8.0, 10.0, 14.0],
        "safety_sl_atr_mult":     [6.0, 8.0, 10.0, 15.0],
        "flip_confirm_bars":      [1, 2, 3, 4],
        "flip_cooldown_bars":     [0, 3, 5, 8],
        "flip_min_score":         [0.0, 0.50, 0.55, 0.60],
        "flip_hysteresis_margin": [0.0, 0.03, 0.05, 0.08],
        "max_bars_safety":        [100, 200, 400, 800],
        "p_up_gain":              [1.5, 2.0, 2.5],
        "p_event_gain":           [2.5, 3.0, 4.0],
        "p_event_center":         [0.38, 0.42, 0.46],
    }
    fixed_params: Dict[str, Any] = {}

    _DEFAULTS = {
        "enable_hour_filter": False,
        "active_hours_utc": list(range(0, 24)),
        "active_days": [0, 1, 2, 3, 4, 5, 6],
        "adx_threshold": 20.0,
        "safety_sl_atr_mult": 10.0,
        "disable_trailing": True,
        "use_fixed_tp": False,
        "bearish_excess_rsi_threshold": 38.0,
        "bearish_excess_sma_pct": 1.5,
        "di_rescue": 10.0,
        "log_flips": True,
        "flip_confirm_bars": 2,
        "flip_cooldown_bars": 5,
        "flip_min_score": 0.55,
        "flip_hysteresis_margin": 0.05,
        "max_bars_safety": 200,
        "p_up_gain": 2.0,
        "p_event_gain": 3.0,
        "p_event_center": 0.42,
    }

    def __init__(self):
        self._call_cnt: Dict[str, int] = {}
        self._last_flip_cnt: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        return 230

    @staticmethod
    def _tail(df: pl.DataFrame, col: str, k: int) -> np.ndarray:
        return df[col][-k:].to_numpy().astype(np.float64)

    def _regime_now(self, df: pl.DataFrame, adx_thr: float, di_rescue: float) -> int:
        adx = float(pre_val(df, "_pre_adx14") or 0.0)
        s20 = self._tail(df, "_pre_sma20", 6)
        s50 = float(pre_val(df, "_pre_sma50") or 0.0)
        s100 = float(pre_val(df, "_pre_sma100") or 0.0)
        s200 = float(pre_val(df, "_pre_sma200") or 0.0)
        pdi = float(pre_val(df, "_pre_pdi14") or 0.0)
        ndi = float(pre_val(df, "_pre_ndi14") or 0.0)
        c = float(df["close"][-1] or 1.0)
        bull = int(s20[-1] > s50 > s100 > s200)
        bear = int(s20[-1] < s50 < s100 < s200)
        slope20 = (s20[-1] - s20[-5]) / (4.0 * c) if len(s20) >= 5 and c > 0 else 0.0
        return _regime(adx, bull, bear, pdi - ndi, slope20, adx_thr, di_rescue)

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
            gain=float(p.get("p_up_gain", self._DEFAULTS["p_up_gain"])))
        p_event = _proxy_p_event(
            atr_pct_r=atr_pct_r, range_r=range_r, volstd_r=volstd_r, volr=volr,
            adx=adx, body_abs_r=body_abs_r,
            center=float(p.get("p_event_center", self._DEFAULTS["p_event_center"])),
            gain=float(p.get("p_event_gain", self._DEFAULTS["p_event_gain"])))
        ctx = {"rsi": rsi, "adx": adx, "atr": atr, "sma20": float(pre_val(df, "_pre_sma20") or 0.0)}
        return p_event, p_up, ctx

    def _bearish_excess(self, df, ctx, c_now, p) -> bool:
        be_rsi = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma = float(p.get("bearish_excess_sma_pct", 1.5))
        consec_red = (len(df) >= 2 and
                      float(df["close"][-1]) < float(df["open"][-1]) and
                      float(df["close"][-2]) < float(df["open"][-2]))
        sma20 = ctx["sma20"]
        below = (c_now < sma20 * (1.0 - be_sma / 100.0)) if sma20 > 0 else False
        return bool(consec_red or ctx["rsi"] < be_rsi or below)

    @staticmethod
    def _score_of(setup, p_event, p_up) -> float:
        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence = abs(p_up - 0.5) * 2.0
        return round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required():
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")
        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)

        p = (params or {}).get(self.name, {})
        adx_thr = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        di_rescue = float(p.get("di_rescue", self._DEFAULTS["di_rescue"]))
        safety_sl_mult = float(p.get("safety_sl_atr_mult", self._DEFAULTS["safety_sl_atr_mult"]))
        disable_trailing = bool(p.get("disable_trailing", self._DEFAULTS["disable_trailing"]))
        cooldown_bars = int(p.get("flip_cooldown_bars", self._DEFAULTS["flip_cooldown_bars"]))
        max_bars_safety = int(p.get("max_bars_safety", self._DEFAULTS["max_bars_safety"]))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(f"Timeframe non supporté (détecté={tf})")

        cnt = self._call_cnt.get(tf, 0) + 1
        self._call_cnt[tf] = cnt
        last_flip = self._last_flip_cnt.get(tf, -10**9)
        if cooldown_bars > 0 and (cnt - last_flip) < cooldown_bars:
            return self._none(f"Cooldown post-flip ({cnt - last_flip}/{cooldown_bars} bougies)")

        p_event, p_up, ctx = self._proxies(df, p)
        c_now = float(df["close"][-1] or 0.0)
        atr_v = ctx["atr"]
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime = self._regime_now(df, adx_thr, di_rescue)
        regime_lbl = REGIME_LABELS[regime]
        rsi_v, adx_v = ctx["rsi"], ctx["adx"]
        bearish_excess = self._bearish_excess(df, ctx, c_now, p)

        setups = _apply_setup_overrides(p)
        setup = _select_setup(setups, regime, p_event, p_up, bearish_excess, adx_v)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} rsi={rsi_v:.1f} adx={adx_v:.1f}",
                p_event=p_event, p_up=p_up, regime=regime)

        side = "long" if setup["direction"] == 1 else "short"
        size_factor = float(setup.get("size_factor", 1.0))
        score_val = self._score_of(setup, p_event, p_up)

        sig: Dict[str, Any] = {
            "score": score_val, "side": side, "name": self.name, "atr": atr_v,
            "sl_atr_mult": safety_sl_mult, "disable_trailing": disable_trailing,
            "size_factor": size_factor, "exit_after_bars": max_bars_safety,
            "p_event": round(p_event, 4), "p_up": round(p_up, 4),
            "regime": regime, "regime_lbl": regime_lbl, "tf_detected": tf,
            "setup": setup["name"], "setup_priority": int(setup["priority"]),
            "setup_direction": int(setup["direction"]),
            "bearish_excess": bool(bearish_excess),
            "rsi": round(rsi_v, 1), "adx": round(adx_v, 1),
        }
        sig["indicators"] = {
            "adx": round(adx_v, 1), "rsi": round(rsi_v, 1),
            "p_event": round(p_event, 4), "p_up": round(p_up, 4),
            "regime": regime, "regime_lbl": regime_lbl, "setup": setup["name"],
            "setup_priority": int(setup["priority"]), "setup_direction": int(setup["direction"]),
            "sl_mult": safety_sl_mult, "proxy": True,
        }
        sig["conditions"] = [
            f"Setup FollowSetup (no-ML) retenu : {setup['name']} (priorité {setup['priority']}, dir={side})",
            f"Régime : {regime_lbl} | ADX={adx_v:.1f}",
            f"P(événement) proxy={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse) proxy={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse) proxy={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else f"P(hausse) proxy={p_up:.2f}"),
            f"Pas de TP/trailing — sortie sur flip de setup (confirm "
            f"{int(p.get('flip_confirm_bars', self._DEFAULTS['flip_confirm_bars']))} "
            f"bougies, cooldown {cooldown_bars}) | SL safety {safety_sl_mult:.1f}×ATR "
            f"| timeout {max_bars_safety} bougies",
            "Probabilités issues de proxys d'indicateurs (sans modèle ML)",
        ]
        sig["reason"] = (
            f"OmnibusV11-FollowSetup-NoML {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}")
        return sig

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        side = position.get("side")
        if side not in ("long", "short"):
            return None
        if df is None or len(df) < self.min_bars_required():
            return None
        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)

        p = (params or {}).get(self.name, {})
        adx_thr = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        di_rescue = float(p.get("di_rescue", self._DEFAULTS["di_rescue"]))
        confirm_bars = int(p.get("flip_confirm_bars", self._DEFAULTS["flip_confirm_bars"]))
        flip_min_score = float(p.get("flip_min_score", self._DEFAULTS["flip_min_score"]))
        hyst = float(p.get("flip_hysteresis_margin", self._DEFAULTS["flip_hysteresis_margin"]))
        log_flips = bool(p.get("log_flips", self._DEFAULTS["log_flips"]))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return None
        try:
            c_now = float(df["close"][-1] or 0.0)
            if c_now <= 0:
                return None
            p_event, p_up, ctx = self._proxies(df, p)
            regime = self._regime_now(df, adx_thr, di_rescue)
            bearish_excess = self._bearish_excess(df, ctx, c_now, p)
            setups = _apply_setup_overrides(p)
            current = _select_setup(setups, regime, p_event, p_up, bearish_excess, ctx["adx"])
        except Exception as e:
            logger.warning(f"[OmnibusV11-FollowSetup-NoML] check_early_exit KO : {e}")
            return None

        held_dir = 1 if side == "long" else -1
        if current is None or int(current["direction"]) == held_dir:
            position["_fs_opp_count"] = 0
            position["_fs_opp_setup"] = None
            return None

        if hyst > 0.0:
            d_max, d_min = current.get("dir_max"), current.get("dir_min")
            if d_max is not None and p_up >= (float(d_max) - hyst):
                position["_fs_opp_count"] = 0
                position["_fs_opp_setup"] = None
                return None
            if d_min is not None and p_up <= (float(d_min) + hyst):
                position["_fs_opp_count"] = 0
                position["_fs_opp_setup"] = None
                return None

        new_score = self._score_of(current, p_event, p_up)
        if new_score < flip_min_score:
            position["_fs_opp_count"] = 0
            position["_fs_opp_setup"] = None
            return None

        if position.get("_fs_opp_setup") == current["name"]:
            cnt_opp = int(position.get("_fs_opp_count", 0)) + 1
        else:
            cnt_opp = 1
        position["_fs_opp_count"] = cnt_opp
        position["_fs_opp_setup"] = current["name"]
        if cnt_opp < max(1, confirm_bars):
            return None

        self._last_flip_cnt[tf] = self._call_cnt.get(tf, 0)
        if log_flips:
            self._log_flip(tf, position, current, new_score, float(p_event),
                           float(p_up), regime, ctx["adx"], ctx["rsi"], int(cnt_opp))
        return f"setup_flip_to_{current['name']}"

    def _log_flip(self, tf, position, new_setup, new_score, p_event, p_up,
                  regime, adx, rsi, confirm_bars) -> None:
        try:
            os.makedirs(os.path.dirname(_FLIP_LOG_PATH) or ".", exist_ok=True)
            record = {
                "ts": _dt.datetime.utcnow().isoformat(), "strategy": self.name, "tf": tf,
                "symbol": position.get("symbol"), "from_side": position.get("side"),
                "from_setup": position.get("setup"), "to_setup": new_setup.get("name"),
                "to_direction": int(new_setup.get("direction", 0)),
                "new_score": round(float(new_score), 4), "p_event": round(float(p_event), 4),
                "p_up": round(float(p_up), 4), "regime": regime,
                "regime_lbl": REGIME_LABELS.get(regime, "?"),
                "adx": round(float(adx), 2), "rsi": round(float(rsi), 2),
                "confirm_bars": int(confirm_bars),
            }
            with open(_FLIP_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[OmnibusV11-FollowSetup-NoML] log flip KO : {e}")

    def _none(self, reason: str = "", p_event: float = 0.0, p_up: float = 0.5,
              regime: int = -1) -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason,
                "p_event": round(p_event, 4), "p_up": round(p_up, 4),
                "regime": regime, "regime_lbl": REGIME_LABELS.get(regime, "?")}
