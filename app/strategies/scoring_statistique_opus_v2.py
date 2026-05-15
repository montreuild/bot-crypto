"""Stratégie Scoring Statistique Opus V2 — identique à V1 + trades en Trend Up.

Différence clé par rapport à V1 :
- V1 : Trend Up → pas de trade (AUC ≈ 0.50 avec la formule neutre)
- V2 : Trend Up → formule miroir avec biais haussier +2.0 (au lieu de -2.0)

Logique "inverse de la baisse" :
  Trend Down : z = -2.0 (biais baissier) → détecte rebonds oversold
  Trend Up   : z = +2.0 (biais haussier) → by symétrie, RSI < 50 = "achat du dip"
                                            RSI > 70 (rsi_n négatif) = prudence/overbought
                                            Upper wick (shooting star) = signal d'alerte

Performance : toutes les features viennent de colonnes _pre_* précompilées → O(1)/barre.

Seuils Trend Up conservateurs :
  amp_thresh_tu  = 0.45, dir_dist_tu = 0.20, size_factor = 0.75
"""

import logging
import math
from typing import Dict, Any, List

import polars as pl
from app.engine.engine import BaseStrategy
from app.core.indicators import pre_val

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

REGIME_AMP_MEDIAN = {
    REGIME_RANGE:    0.60,
    REGIME_TREND_UP: 0.63,
    REGIME_TREND_DN: 0.69,
    REGIME_CHOPPY:   0.65,
}


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, z))))


def _detect_regime(adx: float, sma20: float, sma50: float,
                   sma100: float, sma200: float,
                   pdi: float = 0.0, ndi: float = 0.0,
                   adx_threshold: float = 20.0) -> int:
    """Régime avec confirmation DI : SMAs alignées ET direction cohérente.

    Sans DI, ~40% des barres 'Trend Down' ont +DI > -DI (marché en récupération)
    → shorts perdants. Avec DI, ces barres passent en Choppy (formule équilibrée).
    """
    if adx < adx_threshold:
        return REGIME_RANGE
    if sma20 > sma50 > sma100 > sma200 and pdi > ndi:
        return REGIME_TREND_UP
    if sma20 < sma50 < sma100 < sma200 and ndi > pdi:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _proba_amplitude(atr_r: float, vs_r: float, rs_r: float,
                     body_abs_r: float, wick_total: float) -> float:
    """P(événement) — rapport §6.2. Poids proportionnels aux coefficients du rapport."""
    z = (-0.5
         + 1.50 * (atr_r      - 1.0)
         + 1.08 * (vs_r       - 1.0)
         + 0.72 * (rs_r       - 1.0)
         + 0.36 * (body_abs_r - 1.0)
         + 0.24 * wick_total)
    return _sig(z)


def _proba_direction(rsi: float, rsi_vel6: float, rsi_vel12: float,
                     rsi_accel: float, body: float,
                     lower_wick: float, upper_wick: float,
                     macd_hist_d1: float, range_pos20: float,
                     vol_ratio: float, regime: int) -> float:
    """P(hausse) conditionné par régime.

    Trend Up : formule miroir (+2.0 biais haussier)
    → RSI dip (< 50) → achat du dip en uptrend ✓
    → RSI overbought (> 70) → rsi_n négatif → prudence ✓
    → Shooting star (upper wick élevé) → réduit wick_n → signal d'alerte ✓

    Toutes les features viennent de precompute_df → O(1).
    """
    body_n      =  body * 15.0
    rsi_n       = (50.0 - rsi) / 25.0
    rsi_vel_n   = -rsi_vel6 / 10.0
    rsi_vel12_n = -rsi_vel12 / 15.0
    rsi_acc_n   = -rsi_accel / 5.0
    wick_n      =  lower_wick * 3.0 - upper_wick * 2.0
    macd_vel_n  =  math.copysign(min(abs(macd_hist_d1) * 2000.0, 1.5), macd_hist_d1)
    rpos_n      = (0.5 - range_pos20) * 2.0
    vol_n       =  min(max(vol_ratio - 1.0, 0.0), 1.0) * 0.3

    if regime == REGIME_TREND_UP:
        z = (+2.0
             + body_n      * 1.5
             + rsi_n       * 1.0
             + rsi_vel_n   * 1.0
             + rsi_vel12_n * 0.8
             + rsi_acc_n   * 0.6
             + wick_n      * 1.8
             + macd_vel_n  * 0.8
             + rpos_n      * 0.6
             + vol_n)
    elif regime == REGIME_TREND_DN:
        z = (-2.0
             + body_n      * 1.5
             + rsi_n       * 1.0
             + rsi_vel_n   * 1.0
             + rsi_vel12_n * 0.8
             + rsi_acc_n   * 0.6
             + wick_n      * 1.8
             + macd_vel_n  * 0.8
             + rpos_n      * 0.6
             + vol_n)
    elif regime == REGIME_RANGE:
        z = (body_n * 1.2 + rsi_n * 0.8 + wick_n * 1.0
             + macd_vel_n * 0.8 + rsi_vel_n * 0.5 + rpos_n * 0.8 + vol_n)
    else:  # Choppy
        z = (body_n * 1.0 + rsi_n * 0.8 + wick_n * 1.2
             + macd_vel_n * 0.8 + rsi_vel_n * 0.6 + rpos_n * 0.5 + vol_n)

    return _sig(z)


def _hour_multiplier(df: pl.DataFrame) -> float:
    try:
        ts = df["time"][-1]
        if hasattr(ts, 'hour'):
            h = ts.hour
        else:
            import datetime
            h = datetime.datetime.utcfromtimestamp(int(ts) / 1_000_000).hour
        if 13 <= h <= 17:
            return 1.0
        if 8 <= h <= 12:
            return 0.7
        if 0 <= h <= 5:
            return 0.3
        return 0.5
    except Exception:
        return 0.5


class Strategy(BaseStrategy):
    name = "scoring_statistique_opus_v2"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":     [15, 20, 25],
        "amp_thresh_tu":     [0.35, 0.40, 0.45, 0.50],
        "amp_thresh_td":     [0.35, 0.40, 0.45, 0.50],
        "amp_thresh_other":  [0.45, 0.50, 0.55, 0.60],
        "dir_dist_tu":       [0.15, 0.20, 0.25, 0.30],
        "dir_dist_td":       [0.05, 0.08, 0.10, 0.12],
        "dir_dist_other":    [0.08, 0.10, 0.12, 0.15],
        "rr_min":            [0.5, 0.6, 0.8, 1.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._call_count:  Dict[str, int] = {}
        self._last_signal: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        return 215

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})

        adx_threshold  = float(p.get("adx_threshold",    20.0))
        amp_thresh_tu  = float(p.get("amp_thresh_tu",    0.45))
        amp_thresh_td  = float(p.get("amp_thresh_td",    0.45))
        amp_thresh_oth = float(p.get("amp_thresh_other", 0.55))
        dir_dist_tu    = float(p.get("dir_dist_tu",      0.20))
        dir_dist_td    = float(p.get("dir_dist_td",      0.08))
        dir_dist_oth   = float(p.get("dir_dist_other",   0.12))
        rr_min         = float(p.get("rr_min",           0.6))

        sym = symbol or "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0:
            return self._none("Prix invalide")

        # ── Scalaires O(1) depuis colonnes précompilées ───────────────────
        atr_now  = pre_val(df, "_pre_atr14")      or 0.0
        rsi_now  = pre_val(df, "_pre_rsi14")      or 50.0
        adx_now  = pre_val(df, "_pre_adx14")      or 0.0
        macd_h   = pre_val(df, "_pre_macd_hist")  or 0.0
        vr       = pre_val(df, "_pre_volratio20") or 1.0

        if atr_now <= 0:
            return self._none("ATR invalide")

        atr_r       = pre_val(df, "_pre_atr_pct_r")   or 1.0
        vs_r        = pre_val(df, "_pre_volstd20_r")  or 1.0
        rs_r        = pre_val(df, "_pre_range_r")     or 1.0
        body_abs_r  = pre_val(df, "_pre_body_abs_r")  or 1.0

        body         = pre_val(df, "_pre_body")          or 0.0
        upper_wick   = pre_val(df, "_pre_upper_wick")    or 0.0
        lower_wick   = pre_val(df, "_pre_lower_wick")    or 0.0
        rsi_vel6     = pre_val(df, "_pre_rsi_vel6")      or 0.0
        rsi_vel12    = pre_val(df, "_pre_rsi_vel12")     or 0.0
        rsi_accel    = pre_val(df, "_pre_rsi_accel")     or 0.0
        macd_hist_d1 = pre_val(df, "_pre_macd_hist_d1") or 0.0
        range_pos20  = pre_val(df, "_pre_range_pos20")  or 0.5
        wick_total   = lower_wick + upper_wick

        sma20  = pre_val(df, "_pre_sma20")  or pre_val(df, "_pre_ema20")  or c_now
        sma50  = pre_val(df, "_pre_sma50")  or pre_val(df, "_pre_ema50")  or c_now
        sma100 = pre_val(df, "_pre_sma100") or c_now
        sma200 = pre_val(df, "_pre_sma200") or pre_val(df, "_pre_ema200") or c_now
        pdi    = pre_val(df, "_pre_pdi14")  or 0.0
        ndi    = pre_val(df, "_pre_ndi14")  or 0.0

        # ── Régime + scores ───────────────────────────────────────────────
        regime     = _detect_regime(adx_now, sma20, sma50, sma100, sma200,
                                    pdi, ndi, adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        proba_amp = _proba_amplitude(atr_r, vs_r, rs_r, body_abs_r, wick_total)
        proba_dir = _proba_direction(rsi_now, rsi_vel6, rsi_vel12, rsi_accel,
                                     body, lower_wick, upper_wick,
                                     macd_hist_d1, range_pos20, vr, regime)
        dir_dist  = abs(proba_dir - 0.5)
        hour_mult = _hour_multiplier(df)

        # ── Règle de décision par régime ─────────────────────────────────
        if regime == REGIME_TREND_UP:
            amp_thresh = amp_thresh_tu
            dir_thresh = dir_dist_tu
            size_fac   = 0.75
        elif regime == REGIME_TREND_DN:
            amp_thresh = amp_thresh_td
            dir_thresh = dir_dist_td
            size_fac   = 1.0
        else:
            amp_thresh = amp_thresh_oth
            dir_thresh = dir_dist_oth
            size_fac   = 0.5

        if proba_amp < amp_thresh:
            return self._none(
                f"P(événement)={proba_amp:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )
        if dir_dist < dir_thresh:
            return self._none(
                f"|P(dir)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        side = "long" if proba_dir > 0.5 else "short"

        if side == "long":
            stop   = c_now - 1.5 * atr_now
            target = c_now + 1.0 * atr_now
            rr     = (target - c_now) / max(c_now - stop, 1e-9)
        else:
            stop   = c_now + 1.5 * atr_now
            target = c_now - 1.0 * atr_now
            rr     = (c_now - target) / max(stop - c_now, 1e-9)

        if rr < rr_min:
            return self._none(
                f"R:R {rr:.2f} < {rr_min} | {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        amp_med   = REGIME_AMP_MEDIAN[regime] / 100.0
        score_raw = proba_amp * dir_dist * 2.0 * amp_med * 100.0
        score     = 0.50 + min(score_raw * 0.45, 0.44)
        score    *= (size_fac * 0.5 + 0.5)
        score    *= (hour_mult * 0.3 + 0.7)
        score     = round(min(score, 0.94), 3)

        self._last_signal[sym] = cnt

        size_lbl = "Pleine" if size_fac == 1.0 else ("3/4" if size_fac == 0.75 else "1/2")

        return {
            "score":      score,
            "side":       side,
            "name":       self.name,
            "atr":        atr_now,
            "stop_hint":  round(stop, 2),
            "proba_amp":  round(proba_amp, 3),
            "proba_dir":  round(proba_dir, 3),
            "regime":     regime,
            "regime_lbl": regime_lbl,
            "indicators": {
                "adx":         round(adx_now, 1),
                "rsi":         round(rsi_now, 1),
                "rsi_vel6":    round(rsi_vel6, 2),
                "body":        round(body, 4),
                "atr_r":       round(atr_r, 3),
                "rs_r":        round(rs_r, 3),
                "vs_r":        round(vs_r, 3),
                "upper_wick":  round(upper_wick, 3),
                "lower_wick":  round(lower_wick, 3),
                "hour_mult":   hour_mult,
                "size_factor": size_fac,
                "sma20":       round(sma20, 2),
                "sma200":      round(sma200, 2),
            },
            "conditions": [
                f"Régime : {regime_lbl} (ADX={adx_now:.0f})",
                f"P(événement) = {proba_amp:.2f} ≥ {amp_thresh:.2f} ✓",
                f"P(direction) = {proba_dir:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
                f"body={body:.3f} RSI={rsi_now:.0f} vel6={rsi_vel6:+.1f}",
                f"{size_lbl} taille | Heure ×{hour_mult:.1f} | R:R={rr:.2f}",
            ],
            "reason": (
                f"OpusV2 {side.upper()} | {regime_lbl} | "
                f"amp={proba_amp:.2f} dir={proba_dir:.2f} "
                f"ADX={adx_now:.0f} RSI={rsi_now:.0f}"
            ),
        }

    def _none(self, reason: str = "", proba_amp: float = 0.0,
              proba_dir: float = 0.5, regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "proba_amp":  round(proba_amp, 3),
            "proba_dir":  round(proba_dir, 3),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
