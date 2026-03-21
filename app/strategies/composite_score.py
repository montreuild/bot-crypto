"""
Stratégie Score Composite — Documentation complète

Modèle de scoring qui combine :
  - EMA(20/50)          : Tendance structurelle (prix > EMA20 > EMA50 = uptrend)
  - RSI(14)             : Momentum relatif (survendu/suracheté)
  - MACD(12, 26, 9)     : Momentum de tendance (croisement / accélération)
  - Bandes de Bollinger(20, 2) : Volatilité et mean reversion
  - Stochastique(14, 3) : Oscillateur de momentum court terme
  - Supports/Résistances : Niveaux clés via pivots (clustering 1.8 %)
  - Fibonacci            : Zones de retracement dynamiques
  - Analyse FFT          : Cycles spectraux (optionnel)

Score brut 0–100 (base neutre = 50) :
  RSI < 30         → +20  |  RSI > 70         → −20
  RSI < 40         → +10  |  RSI > 60         → −10
  MACD cross ↑     → +15  |  MACD cross ↓     → −15
  MACD hist > 0    → +7   |  MACD hist < 0    → −7
  Prix < BB basse  → +15  |  Prix > BB haute  → −15
  BB Squeeze       → +5
  EMA haussier     → +10  |  EMA baissier     → −10
  Stoch K < 20     → +8   |  Stoch K > 80     → −8
  FFT haussier     → +10  |  FFT baissier     → −10  (si use_fft=True)
  S/R proche sup   → +5   |  S/R proche rés   → −5
  Fibonacci sup    → +5   |  Fibonacci rés    → −5

Interprétation :
  ≥ 75 → ACHAT FORT  (side=long,  score ≥ 0.67)
  60–74→ ACHAT       (side=long,  score 0.55–0.67)
  40–59→ NEUTRE      (side=none)
  25–39→ VENTE       (side=short, score 0.55–0.67)
  < 25 → VENTE FORTE (side=short, score ≥ 0.67)

Normalisation 0-1 :
  long  : 0.55 + (raw − score_long_threshold)  / (100 − score_long_threshold)  × 0.39
  short : 0.55 + (score_short_threshold − raw)  / score_short_threshold         × 0.39
  → Min = 0.55 (seuil de trading), Max = 0.94

Paramètres optimisables :
  ema_fast / ema_slow           : périodes des EMA de tendance
  rsi_period                    : période RSI
  macd_fast / macd_slow / macd_signal : paramètres MACD
  bb_period / bb_std            : Bollinger Bands
  stoch_k_period / stoch_d_period : Stochastique
  sr_window / sr_lookback / sr_cluster_pct : Support/Résistance
  use_fft / fft_min_period / fft_max_period / fft_top_n : analyse FFT
  score_long_threshold          : seuil minimal pour ACHAT (défaut 60)
  score_short_threshold         : seuil maximal pour VENTE (défaut 40)
  cooldown / rr_min / min_bars  : gestion du signal
"""

import logging
from typing import Dict, Any, List

import polars as pl

from app.engine.engine import BaseStrategy
from app.strategies.utils import fft_direction as _fft_direction
from app.core.indicators import (
    rsi as calc_rsi,
    atr_val as calc_atr,
    macd as calc_macd,
    vol_ratio as calc_vol,
    htf_trend,
    pre_val,
    support_resistance_levels,
    nearest_support,
    nearest_resistance,
    stochastic as calc_stoch,
    bb_squeeze,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers de conditions lisibles
# ══════════════════════════════════════════════════════════════════════════════

def _conditions_long(
    raw: float, rsi: float, lh: float, ema_bull: bool,
    stoch_k: float, price: float, bb_low: float,
    squeeze: bool, fft: dict, rr: float,
) -> list:
    conds = [f"Score composite {raw:.0f}/100 ✓"]
    if rsi < 30:
        conds.append(f"RSI {rsi:.0f} < 30 → survente ✓ (+20)")
    elif rsi < 40:
        conds.append(f"RSI {rsi:.0f} < 40 → zone basse ✓ (+10)")
    if price < bb_low:
        conds.append(f"Prix ({price:.2f}) sous BB basse ({bb_low:.2f}) → rebond ✓ (+15)")
    if squeeze:
        conds.append("BB Squeeze actif → explosion imminente ✓ (+5)")
    if ema_bull:
        conds.append("EMA20 > EMA50 → uptrend ✓ (+10)")
    if lh > 0:
        conds.append(f"MACD hist {lh:+.5f} positif ✓")
    if stoch_k < 20:
        conds.append(f"Stoch K={stoch_k:.0f} < 20 → survente ✓ (+8)")
    if fft.get("direction", 0) > 0:
        conds.append(
            f"FFT cycle haussier (conf={fft.get('confidence', 0):.2f}) ✓ (+10)"
        )
    conds.append(f"R:R {rr:.2f} ✓")
    return conds


def _conditions_short(
    raw: float, rsi: float, lh: float, ema_bear: bool,
    stoch_k: float, price: float, bb_up: float,
    squeeze: bool, fft: dict, rr: float,
) -> list:
    conds = [f"Score composite {raw:.0f}/100 ✓"]
    if rsi > 70:
        conds.append(f"RSI {rsi:.0f} > 70 → surachat ✓ (−20)")
    elif rsi > 60:
        conds.append(f"RSI {rsi:.0f} > 60 → zone haute ✓ (−10)")
    if price > bb_up:
        conds.append(f"Prix ({price:.2f}) sur BB haute ({bb_up:.2f}) → correction ✓ (−15)")
    if squeeze:
        conds.append("BB Squeeze actif ✓")
    if ema_bear:
        conds.append("EMA20 < EMA50 → downtrend ✓ (−10)")
    if lh < 0:
        conds.append(f"MACD hist {lh:+.5f} négatif ✓")
    if stoch_k > 80:
        conds.append(f"Stoch K={stoch_k:.0f} > 80 → surachat ✓ (−8)")
    if fft.get("direction", 0) < 0:
        conds.append(
            f"FFT cycle baissier (conf={fft.get('confidence', 0):.2f}) ✓ (−10)"
        )
    conds.append(f"R:R {rr:.2f} ✓")
    return conds


# ══════════════════════════════════════════════════════════════════════════════
#  Stratégie
# ══════════════════════════════════════════════════════════════════════════════

class Strategy(BaseStrategy):
    name = "composite_score"

    timeframes: List[str] = ["1h", "4h", "1d"]

    param_space: Dict[str, List] = {
        "ema_fast":              [13, 20, 21],
        "ema_slow":              [50, 80],
        "score_long_threshold":  [55, 60, 65],
        "score_short_threshold": [35, 40, 45],
        "sr_lookback":           [120, 180, 240],
        "sr_cluster_pct":        [0.010, 0.018, 0.025],
        "use_fft":               [True, False],
        "fft_min_period":        [5, 7],
        "fft_max_period":        [100, 150],
        "cooldown":              [10, 15, 20],
        "rr_min":                [1.3, 1.5, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get("composite_score", {})
        return max(int(p.get("min_bars", 200)), 60)

    def score(
        self,
        df: pl.DataFrame,
        params: dict = None,
        df_htf=None,
        symbol: str = "",
    ) -> Dict[str, Any]:
        p = (params or {}).get("composite_score", {})

        # ── Paramètres ────────────────────────────────────────────────────────
        ema_fast         = int(p.get("ema_fast",              20))
        ema_slow         = int(p.get("ema_slow",              50))
        rsi_period       = int(p.get("rsi_period",            14))
        macd_fast        = int(p.get("macd_fast",             12))
        macd_slow_p      = int(p.get("macd_slow",             26))
        macd_signal      = int(p.get("macd_signal",            9))
        bb_period        = int(p.get("bb_period",             20))
        bb_std_mult      = float(p.get("bb_std",             2.0))
        stoch_k          = int(p.get("stoch_k_period",        14))
        stoch_d          = int(p.get("stoch_d_period",         3))
        sr_window        = int(p.get("sr_window",              5))
        sr_lookback      = int(p.get("sr_lookback",          180))
        sr_cluster_pct   = float(p.get("sr_cluster_pct",   0.018))
        fft_min_period   = int(p.get("fft_min_period",         5))
        fft_max_period   = int(p.get("fft_max_period",       150))
        fft_top_n        = int(p.get("fft_top_n",             10))
        use_fft          = bool(p.get("use_fft",            True))
        cooldown         = int(p.get("cooldown",              15))
        rr_min           = float(p.get("rr_min",             1.5))
        score_long_thr   = float(p.get("score_long_threshold",  60.0))
        score_short_thr  = float(p.get("score_short_threshold", 40.0))
        min_bars         = max(int(p.get("min_bars",         200)), 60)

        sym = symbol or "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        if len(df) < min_bars:
            return self._none(f"Données insuffisantes : {len(df)}/{min_bars}")

        if cnt - self._last_signal.get(sym, -999) < cooldown:
            return self._none("Cooldown")

        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        c_now = float(close[-1])

        # ── ATR ──────────────────────────────────────────────────────────────
        atr_val = pre_val(df, "_pre_atr14") or calc_atr(df, 14)
        if atr_val <= 0:
            return self._none("ATR invalide")

        # ── EMA(20) et EMA(50) ────────────────────────────────────────────────
        ema20    = float(close.ewm_mean(span=ema_fast, adjust=False)[-1])
        ema50    = float(close.ewm_mean(span=ema_slow, adjust=False)[-1])
        ema_bull = c_now > ema20 > ema50
        ema_bear = c_now < ema20 < ema50

        # ── RSI(14) ───────────────────────────────────────────────────────────
        rsi_val = pre_val(df, "_pre_rsi14") or float(calc_rsi(close, rsi_period)[-1])
        if rsi_val is None:
            rsi_val = 50.0

        # ── MACD(12, 26, 9) ───────────────────────────────────────────────────
        lh = pre_val(df, "_pre_macd_hist")
        if lh is None:
            _, _, hist = calc_macd(close, macd_fast, macd_slow_p, macd_signal)
            lh = float(hist[-1])
            ph = float(hist[-2]) if len(hist) > 1 else 0.0
        else:
            ph_s = df["_pre_macd_hist"]
            ph   = float(ph_s[-2]) if len(ph_s) > 1 else 0.0

        macd_cross_up   = ph <= 0 < lh
        macd_cross_down = ph >= 0 > lh

        # ── Bollinger Bands(20, 2) ─────────────────────────────────────────────
        bb_mid_s  = close.rolling_mean(bb_period)
        bb_sigma  = close.rolling_std(bb_period)
        bb_upper  = float((bb_mid_s + bb_std_mult * bb_sigma)[-1])
        bb_lower  = float((bb_mid_s - bb_std_mult * bb_sigma)[-1])
        bb_mid_v  = float(bb_mid_s[-1]) if bb_mid_s[-1] is not None else 1.0
        bb_sq_act = bb_squeeze(close, lookback=15, bb_period=bb_period)
        bw        = (bb_upper - bb_lower) / bb_mid_v * 100 if bb_mid_v > 0 else 0.0

        # ── Stochastique(14, 3) ───────────────────────────────────────────────
        stoch_k_val, stoch_d_val = calc_stoch(df, k_period=stoch_k, d_period=stoch_d)

        # ── Supports / Résistances (180 j, clustering 1.8 %) ─────────────────
        sr          = support_resistance_levels(
            df,
            window      = sr_window,
            cluster_pct = sr_cluster_pct,
            min_touches = 1,
            max_levels  = 6,
            lookback    = sr_lookback,
        )
        supports    = sr["supports"]
        resistances = sr["resistances"]
        nearest_sup = nearest_support(c_now, supports)
        nearest_res = nearest_resistance(c_now, resistances)

        # ── Retracements de Fibonacci ─────────────────────────────────────────
        lb         = min(sr_lookback, len(high))
        fib_high   = float(high[-lb:].max())
        fib_low    = float(low[-lb:].min())
        fib_range  = fib_high - fib_low
        fib_levels = {
            "0.236": fib_high - 0.236 * fib_range,
            "0.382": fib_high - 0.382 * fib_range,
            "0.500": fib_high - 0.500 * fib_range,
            "0.618": fib_high - 0.618 * fib_range,
            "0.786": fib_high - 0.786 * fib_range,
        }
        fib_tol     = atr_val  # ±1 ATR de tolérance
        near_fib_sup = any(
            abs(c_now - lv) <= fib_tol and c_now > lv
            for lv in fib_levels.values()
        )
        near_fib_res = any(
            abs(c_now - lv) <= fib_tol and c_now < lv
            for lv in fib_levels.values()
        )

        # ── Analyse FFT spectrale ─────────────────────────────────────────────
        fft_result = {
            "direction": 0, "confidence": 0.0,
            "cycles": [], "avg_signal": 0.0, "next_reversal": 0.0,
        }
        if use_fft and len(close) >= max(fft_min_period * 3, 30):
            prices_np  = close.to_numpy().astype(float)
            fft_result = _fft_direction(
                prices_np, fft_min_period, fft_max_period, fft_top_n
            )

        # ══════════════════════════════════════════════════════════════════════
        #  SCORE COMPOSITE  (0–100, base neutre = 50)
        # ══════════════════════════════════════════════════════════════════════
        raw_score = 50.0

        # RSI ±20
        if rsi_val < 30:
            raw_score += 20
        elif rsi_val < 40:
            raw_score += 10
        elif rsi_val > 70:
            raw_score -= 20
        elif rsi_val > 60:
            raw_score -= 10

        # MACD ±15 (croisement) ou ±7 (histogramme)
        if macd_cross_up:
            raw_score += 15
        elif macd_cross_down:
            raw_score -= 15
        elif lh > 0:
            raw_score += 7
        elif lh < 0:
            raw_score -= 7

        # Bollinger Bands ±15
        if c_now < bb_lower:
            raw_score += 15
        elif c_now > bb_upper:
            raw_score -= 15

        # BB Squeeze +5
        if bb_sq_act:
            raw_score += 5

        # EMA trend ±10
        if ema_bull:
            raw_score += 10
        elif ema_bear:
            raw_score -= 10

        # Stochastique ±8
        if stoch_k_val < 20:
            raw_score += 8
        elif stoch_k_val > 80:
            raw_score -= 8

        # FFT spectrale ±10 (proportionnel à la confiance)
        if use_fft and fft_result["direction"] != 0:
            fft_contrib  = fft_result["direction"] * 10.0
            fft_contrib *= min(fft_result["confidence"] * 2.0, 1.0)
            raw_score   += fft_contrib

        # S/R bonus ±5
        if nearest_sup is not None and abs(c_now - nearest_sup) <= atr_val * 1.5:
            raw_score += 5
        elif nearest_res is not None and abs(nearest_res - c_now) <= atr_val * 1.5:
            raw_score -= 5

        # Fibonacci bonus ±5
        if near_fib_sup:
            raw_score += 5
        elif near_fib_res:
            raw_score -= 5

        # Clip [0, 100]
        raw_score = max(0.0, min(100.0, raw_score))

        # ── Indicateurs exportés ──────────────────────────────────────────────
        indicators = {
            "raw_score":       round(raw_score, 1),
            "ema20":           round(ema20, 4),
            "ema50":           round(ema50, 4),
            "rsi":             round(rsi_val, 1),
            "macd_hist":       round(lh, 6),
            "macd_hist_prev":  round(ph, 6),
            "bb_upper":        round(bb_upper, 4),
            "bb_lower":        round(bb_lower, 4),
            "bb_width_pct":    round(bw, 2),
            "bb_squeeze":      bb_sq_act,
            "stoch_k":         round(stoch_k_val, 1),
            "stoch_d":         round(stoch_d_val, 1),
            "fft_direction":   fft_result.get("direction", 0),
            "fft_confidence":  fft_result.get("confidence", 0.0),
            "fft_avg_signal":  fft_result.get("avg_signal", 0.0),
            "fft_cycles":      fft_result.get("cycles", [])[:3],
            "fib_levels":      {k: round(v, 4) for k, v in fib_levels.items()},
            "near_fib_sup":    near_fib_sup,
            "near_fib_res":    near_fib_res,
            "nearest_sup":     round(nearest_sup, 4) if nearest_sup else None,
            "nearest_res":     round(nearest_res, 4) if nearest_res else None,
        }

        # ══════════════════════════════════════════════════════════════════════
        #  SIGNAL DIRECTIONNEL
        # ══════════════════════════════════════════════════════════════════════

        if raw_score >= score_long_thr:
            # ── Long ──────────────────────────────────────────────────────────
            thr_range  = max(100.0 - score_long_thr, 1.0)
            norm_score = min(
                round(0.55 + (raw_score - score_long_thr) / thr_range * 0.39, 3),
                0.94,
            )

            stop_l = (
                nearest_sup - atr_val * 0.5
                if nearest_sup is not None and nearest_sup < c_now
                else c_now - atr_val * 2.0
            )
            risk_l = c_now - stop_l
            if risk_l <= 0:
                return self._none("Stop invalide (long)")

            target_l = nearest_res if nearest_res else c_now + risk_l * rr_min
            reward_l = max(target_l - c_now, 0.0)
            rr_l     = reward_l / risk_l if risk_l > 0 else 0.0
            if rr_l < rr_min:
                return self._none(f"R:R long {rr_l:.2f} < {rr_min}")

            label = "ACHAT FORT" if raw_score >= 75 else "ACHAT"
            self._last_signal[sym] = cnt
            return {
                "score":      norm_score,
                "side":       "long",
                "name":       self.name,
                "atr":        atr_val,
                "stop_hint":  round(stop_l, 4),
                "indicators": indicators,
                "conditions": _conditions_long(
                    raw_score, rsi_val, lh, ema_bull,
                    stoch_k_val, c_now, bb_lower,
                    bb_sq_act, fft_result, rr_l,
                ),
                "reason": (
                    f"{label} score={raw_score:.0f}/100 | "
                    f"RSI={rsi_val:.0f} MACD={'↑' if lh > 0 else '↓'} "
                    f"EMA={'↑' if ema_bull else '-'} "
                    f"Stoch={stoch_k_val:.0f} "
                    f"FFT={fft_result['direction']:+d} | R:R={rr_l:.2f}"
                ),
            }

        elif raw_score <= score_short_thr:
            # ── Short ─────────────────────────────────────────────────────────
            norm_score = min(
                round(0.55 + (score_short_thr - raw_score) / max(score_short_thr, 1.0) * 0.39, 3),
                0.94,
            )

            stop_s = (
                nearest_res + atr_val * 0.5
                if nearest_res is not None and nearest_res > c_now
                else c_now + atr_val * 2.0
            )
            risk_s = stop_s - c_now
            if risk_s <= 0:
                return self._none("Stop invalide (short)")

            target_s = nearest_sup if nearest_sup else c_now - risk_s * rr_min
            reward_s = max(c_now - target_s, 0.0)
            rr_s     = reward_s / risk_s if risk_s > 0 else 0.0
            if rr_s < rr_min:
                return self._none(f"R:R short {rr_s:.2f} < {rr_min}")

            label = "VENTE FORTE" if raw_score < 25 else "VENTE"
            self._last_signal[sym] = cnt
            return {
                "score":      norm_score,
                "side":       "short",
                "name":       self.name,
                "atr":        atr_val,
                "stop_hint":  round(stop_s, 4),
                "indicators": indicators,
                "conditions": _conditions_short(
                    raw_score, rsi_val, lh, ema_bear,
                    stoch_k_val, c_now, bb_upper,
                    bb_sq_act, fft_result, rr_s,
                ),
                "reason": (
                    f"{label} score={raw_score:.0f}/100 | "
                    f"RSI={rsi_val:.0f} MACD={'↑' if lh > 0 else '↓'} "
                    f"EMA={'↓' if ema_bear else '-'} "
                    f"Stoch={stoch_k_val:.0f} "
                    f"FFT={fft_result['direction']:+d} | R:R={rr_s:.2f}"
                ),
            }

        else:
            return self._none(
                f"NEUTRE score={raw_score:.0f}/100 "
                f"(seuil long≥{score_long_thr:.0f} / short≤{score_short_thr:.0f})"
            )
