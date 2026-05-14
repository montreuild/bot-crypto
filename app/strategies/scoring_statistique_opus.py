"""Stratégie Scoring Statistique Opus — scoring d'amplitude et de direction par régime.

Basée sur le rapport statistique V4 (50 000 bougies BTC/USDC) :
- Amplitude (AUC OOS 0.75 sur 15m) : ATR%, vol_std20, range_size, wicks
- Direction (AUC OOS 0.87 en Trend Down) : body direction (#1 feature), RSI velocity,
  structure de mèches, MACD, conditionné par régime
- Régime 4 classes : ADX + alignement SMA20/50/100/200

Performance : toutes les features sont précompilées dans _pre_* (precompute_df),
score() ne fait que des lookups O(1) — zéro calcul de série à chaque barre.

Règle de décision (rapport §6.5) :
- Trend Down : amp ≥ 0.45 ET |dir - 0.5| ≥ 0.08 → taille pleine
- Range/Choppy : amp ≥ 0.55 ET |dir - 0.5| ≥ 0.12 → demi-taille
- Trend Up : pas de trade (AUC direction ≈ 0.50)
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

# Amplitude médiane observée par régime (rapport §6.4, en %)
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
                   adx_threshold: float = 20.0) -> int:
    """Régime par règles ADX + alignement SMA (rapport §6.1)."""
    if adx < adx_threshold:
        return REGIME_RANGE
    if sma20 > sma50 > sma100 > sma200:
        return REGIME_TREND_UP
    if sma20 < sma50 < sma100 < sma200:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _proba_amplitude(atr_r: float, vs_r: float, rs_r: float,
                     wick_total: float) -> float:
    """P(événement) — rapport §6.2, top features LightGBM.

    Poids calibrés sur importances : ATR(250) > vol_std(180) > range(120) > wicks(40).
    Ratios /mean100 → TF-indépendant.
    """
    z = (-0.5
         + 1.5 * (atr_r       - 1.0)   # ATR% ratio (dominant)
         + 1.1 * (vs_r        - 1.0)   # vol_std20 ratio
         + 0.7 * (rs_r        - 1.0)   # range ratio
         + 0.3 * wick_total)            # activité mèches (wick_total = lower+upper)
    return _sig(z)


def _proba_direction(rsi: float, rsi_vel6: float, body: float,
                     lower_wick: float, upper_wick: float,
                     macd_hist_norm: float, vol_ratio: float,
                     regime: int) -> float:
    """P(hausse) conditionné par régime — rapport §6.3.

    Features clés (importance LightGBM) :
      body (#1, 404)       : direction du corps de bougie signé
      rsi_vel6 (#3, 377)   : vélocité RSI sur 6 barres
      wick structure        : lower_wick (#2, 395 amplitude, key dir), upper_wick
      macd_hist_norm        : dynamique MACD

    AUC attendu : Trend Down ~0.87, Choppy ~0.58, Range ~0.50-0.70, Trend Up ~0.50.
    """
    # Normalisation
    body_n     =  body * 15.0                              # (close-open)/close signé
    rsi_n      = (50.0 - rsi) / 25.0                      # RSI level (oversold > 0)
    rsi_vel_n  = -rsi_vel6 / 10.0                         # vélocité (RSI chute → bounce)
    wick_n     =  lower_wick * 3.0 - upper_wick * 2.0     # mèches
    macd_n     =  math.copysign(min(abs(macd_hist_norm) * 500.0, 1.5), macd_hist_norm)
    vol_n      =  min(max(vol_ratio - 1.0, 0.0), 1.0) * 0.3

    if regime == REGIME_TREND_DN:
        # Biais baissier fort, capte rebonds oversold (rapport : AUC 0.87)
        z = (-2.0
             + body_n    * 1.5
             + rsi_n     * 1.2
             + rsi_vel_n * 1.2
             + wick_n    * 1.8
             + macd_n    * 0.6
             + vol_n)
    elif regime == REGIME_RANGE:
        z = (body_n * 1.2 + rsi_n * 0.8 + wick_n * 1.0
             + macd_n * 1.0 + rsi_vel_n * 0.5 + vol_n)
    elif regime == REGIME_CHOPPY:
        z = (body_n * 1.0 + rsi_n * 0.8 + wick_n * 1.2
             + macd_n * 1.0 + rsi_vel_n * 0.6 + vol_n)
    else:  # Trend Up — formule neutre (pas de trade, mais calcul pour affichage)
        z = (body_n * 0.4 + rsi_n * 0.4 + wick_n * 0.4
             + macd_n * 0.4 + rsi_vel_n * 0.3 + vol_n)

    return _sig(z)


def _hour_multiplier(df: pl.DataFrame) -> float:
    """Multiplicateur de taille selon l'heure UTC (rapport §6.5)."""
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
    name = "scoring_statistique_opus"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":     [15, 20, 25],
        "amp_thresh_td":     [0.35, 0.40, 0.45, 0.50],
        "amp_thresh_other":  [0.45, 0.50, 0.55, 0.60],
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
        amp_thresh_td  = float(p.get("amp_thresh_td",    0.45))
        amp_thresh_oth = float(p.get("amp_thresh_other", 0.55))
        dir_dist_td    = float(p.get("dir_dist_td",      0.08))
        dir_dist_oth   = float(p.get("dir_dist_other",   0.12))
        rr_min         = float(p.get("rr_min",           0.6))

        sym = symbol or "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        # ── Scalaires O(1) depuis colonnes précompilées ───────────────────
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0:
            return self._none("Prix invalide")

        atr_now  = pre_val(df, "_pre_atr14")     or 0.0
        rsi_now  = pre_val(df, "_pre_rsi14")     or 50.0
        adx_now  = pre_val(df, "_pre_adx14")     or 0.0
        macd_h   = pre_val(df, "_pre_macd_hist") or 0.0
        vr       = pre_val(df, "_pre_volratio20") or 1.0

        if atr_now <= 0:
            return self._none("ATR invalide")

        # Features amplitude (ratios TF-indépendants)
        atr_r  = pre_val(df, "_pre_atr_pct_r")   or 1.0
        vs_r   = pre_val(df, "_pre_volstd20_r")  or 1.0
        rs_r   = pre_val(df, "_pre_range_r")     or 1.0

        # Features direction
        body        = pre_val(df, "_pre_body")        or 0.0
        upper_wick  = pre_val(df, "_pre_upper_wick")  or 0.0
        lower_wick  = pre_val(df, "_pre_lower_wick")  or 0.0
        rsi_vel6    = pre_val(df, "_pre_rsi_vel6")    or 0.0
        macd_h_norm = macd_h / max(c_now, 1e-9)
        wick_total  = lower_wick + upper_wick

        # SMAs pour régime (rapport §6.1 : SMA20/50/100/200)
        # Fallback sur EMA si _pre_sma* absent (compatibilité ancienne version)
        sma20  = pre_val(df, "_pre_sma20")  or pre_val(df, "_pre_ema20")  or c_now
        sma50  = pre_val(df, "_pre_sma50")  or pre_val(df, "_pre_ema50")  or c_now
        sma100 = pre_val(df, "_pre_sma100") or c_now
        sma200 = pre_val(df, "_pre_sma200") or pre_val(df, "_pre_ema200") or c_now

        # ── Régime + scores ───────────────────────────────────────────────
        regime     = _detect_regime(adx_now, sma20, sma50, sma100, sma200, adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        proba_amp = _proba_amplitude(atr_r, vs_r, rs_r, wick_total)
        proba_dir = _proba_direction(rsi_now, rsi_vel6, body, lower_wick, upper_wick,
                                     macd_h_norm, vr, regime)
        dir_dist  = abs(proba_dir - 0.5)
        hour_mult = _hour_multiplier(df)

        # ── Règle de décision par régime (rapport §6.5) ──────────────────
        if regime == REGIME_TREND_UP:
            return self._none(
                f"Trend Up — pas d'edge (AUC≈0.50) | amp={proba_amp:.2f} dir={proba_dir:.2f}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        if regime == REGIME_TREND_DN:
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

        # ── Stop / Take Profit (rapport §6.6 : SL=1.5×ATR, TP=1.0×ATR) ─
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

        # ── Score composite (rapport §6.5) ────────────────────────────────
        amp_med   = REGIME_AMP_MEDIAN[regime] / 100.0
        score_raw = proba_amp * dir_dist * 2.0 * amp_med * 100.0
        score     = 0.50 + min(score_raw * 0.45, 0.44)
        score    *= (size_fac * 0.5 + 0.5)
        score    *= (hour_mult * 0.3 + 0.7)
        score     = round(min(score, 0.94), 3)

        self._last_signal[sym] = cnt

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
                f"{'Pleine taille' if size_fac == 1.0 else 'Demi-taille'} | Heure ×{hour_mult:.1f} | R:R={rr:.2f}",
            ],
            "reason": (
                f"Opus {side.upper()} | {regime_lbl} | "
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
