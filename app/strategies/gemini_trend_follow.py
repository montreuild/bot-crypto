"""Stratégie Gemini Trend Follow — EMA200 filtre tendance + RSI pullback oversold/overbought + ATR SL dynamique."""

import logging
from typing import Any, Dict, List

import polars as pl

from app.core.indicators import htf_trend, pre_val
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "gemini_trend_follow"

    timeframes: List[str] = ["15m", "1h"]

    param_space: Dict[str, List] = {
        "ema_trend":   [200],
        "rsi_period":  [14],
        "rsi_os":      [28, 30, 32],
        "rsi_ob":      [68, 70, 72],
        "atr_sl_mult": [1.5, 2.0],
        "cooldown":    [10, 15, 20],
        "rr_min":      [1.8, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get("gemini_trend_follow", {})
        ema_trend = int(p.get("ema_trend", 200))
        return max(ema_trend + 10, 220)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("gemini_trend_follow", {})

        ema_trend   = int(p.get("ema_trend",   200))
        rsi_os      = float(p.get("rsi_os",     30))
        rsi_ob      = float(p.get("rsi_ob",     70))
        atr_sl_mult = float(p.get("atr_sl_mult", 1.5))
        cooldown    = int(p.get("cooldown",      15))
        rr_min      = float(p.get("rr_min",      2.0))

        sym = symbol or "default"

        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        min_bars = max(ema_trend + 10, 220)
        if len(df) < min_bars:
            return self._none(f"EMA{ema_trend} requiert {min_bars} bougies min, {len(df)} disponibles")

        # ── Indicateurs pré-calculés ─────────────────────────────────────────
        ema200  = pre_val(df, "_pre_ema200")
        if ema200 is None:
            ema200 = float(df["close"].ewm_mean(span=200, adjust=False)[-1])

        _rsi_s   = df["_pre_rsi14"]
        rsi_now  = float(_rsi_s[-1])
        rsi_prev = float(_rsi_s[-2])

        atr_val = pre_val(df, "_pre_atr14")
        if atr_val is None or atr_val <= 0:
            return self._none("ATR invalide")

        adx_val  = pre_val(df, "_pre_adx14") or 0.0
        vol_ratio = pre_val(df, "_pre_volratio20") or 1.0

        c0 = float(df["close"][-1])

        htf = htf_trend(df_htf)

        # ── Cooldown par symbole ─────────────────────────────────────────────
        last = self._last_signal.get(sym, -999)
        if cnt - last < cooldown:
            return self._none(f"Cooldown {cnt - last}/{cooldown}")

        # ── Indicateurs communs ──────────────────────────────────────────────
        indicators = {
            "ema200":      round(ema200, 2),
            "rsi":         round(rsi_now, 1),
            "rsi_prev":    round(rsi_prev, 1),
            "atr":         round(atr_val, 4),
            "adx":         round(adx_val, 1),
            "vol_ratio":   round(vol_ratio, 2),
            "atr_sl_mult": atr_sl_mult,
        }

        # ══════════════ LONG ═════════════════════════════════════════════════
        # 1. Prix au-dessus de l'EMA200 (tendance haussière)
        # 2. RSI recrossement haussier de la zone oversold (rsi[-2] < rsi_os et rsi[-1] >= rsi_os)
        if c0 > ema200 and rsi_prev < rsi_os and rsi_now >= rsi_os:
            stop_l   = c0 - atr_val * atr_sl_mult
            target_l = c0 + atr_val * atr_sl_mult * 2.0
            risk_l   = c0 - stop_l
            if risk_l <= 0:
                return self._none("Stop invalide (long)")
            rr_l = (target_l - c0) / risk_l
            if rr_l < rr_min:
                return self._none(f"R:R long {rr_l:.2f} < {rr_min}")

            # Score de base 0.65 + bonus plafonné à 0.93
            adx_b    = min((adx_val - 20) / 20, 1.0) * 0.10 if adx_val > 20 else 0.0
            vol_b    = min((vol_ratio - 1.0) * 0.05, 0.08) if vol_ratio > 1.0 else 0.0
            cross_b  = 0.05 if rsi_now >= rsi_os + 2 else 0.02
            htf_b    = 0.05 if htf > 0 else 0.0
            score    = min(0.65 + adx_b + vol_b + cross_b + htf_b, 0.93)

            indicators["rr"] = round(rr_l, 2)
            self._last_signal[sym] = cnt
            return {
                "score":      round(score, 3),
                "side":       "long",
                "name":       self.name,
                "atr":        atr_val,
                "stop_hint":  round(stop_l, 2),
                "indicators": indicators,
                "conditions": [
                    f"Close {c0:.2f} > EMA200 {ema200:.2f} ✓",
                    f"RSI recrossement haussier {rsi_prev:.1f} → {rsi_now:.1f} (OS={rsi_os}) ✓",
                    f"ATR {atr_val:.4f} × {atr_sl_mult} | R:R {rr_l:.2f} ✓",
                    f"ADX {adx_val:.1f} | Vol {vol_ratio:.2f}x | HTF: {'haussier' if htf > 0 else 'neutre'}",
                ],
                "reason": (
                    f"LONG EMA200 — RSI {rsi_prev:.0f}→{rsi_now:.0f} OS={rsi_os} "
                    f"ATR={atr_val:.4f} SL={stop_l:.2f} R:R={rr_l:.2f}"
                ),
            }

        # ══════════════ SHORT ════════════════════════════════════════════════
        # 1. Prix en dessous de l'EMA200 (tendance baissière)
        # 2. RSI recrossement baissier de la zone overbought (rsi[-2] > rsi_ob et rsi[-1] <= rsi_ob)
        if c0 < ema200 and rsi_prev > rsi_ob and rsi_now <= rsi_ob:
            stop_s   = c0 + atr_val * atr_sl_mult
            target_s = c0 - atr_val * atr_sl_mult * 2.0
            risk_s   = stop_s - c0
            if risk_s <= 0:
                return self._none("Stop invalide (short)")
            rr_s = (c0 - target_s) / risk_s
            if rr_s < rr_min:
                return self._none(f"R:R short {rr_s:.2f} < {rr_min}")

            adx_b    = min((adx_val - 20) / 20, 1.0) * 0.10 if adx_val > 20 else 0.0
            vol_b    = min((vol_ratio - 1.0) * 0.05, 0.08) if vol_ratio > 1.0 else 0.0
            cross_b  = 0.05 if rsi_now <= rsi_ob - 2 else 0.02
            htf_b    = 0.05 if htf < 0 else 0.0
            score    = min(0.65 + adx_b + vol_b + cross_b + htf_b, 0.93)

            indicators["rr"] = round(rr_s, 2)
            self._last_signal[sym] = cnt
            return {
                "score":      round(score, 3),
                "side":       "short",
                "name":       self.name,
                "atr":        atr_val,
                "stop_hint":  round(stop_s, 2),
                "indicators": indicators,
                "conditions": [
                    f"Close {c0:.2f} < EMA200 {ema200:.2f} ✓",
                    f"RSI recrossement baissier {rsi_prev:.1f} → {rsi_now:.1f} (OB={rsi_ob}) ✓",
                    f"ATR {atr_val:.4f} × {atr_sl_mult} | R:R {rr_s:.2f} ✓",
                    f"ADX {adx_val:.1f} | Vol {vol_ratio:.2f}x | HTF: {'baissier' if htf < 0 else 'neutre'}",
                ],
                "reason": (
                    f"SHORT EMA200 — RSI {rsi_prev:.0f}→{rsi_now:.0f} OB={rsi_ob} "
                    f"ATR={atr_val:.4f} SL={stop_s:.2f} R:R={rr_s:.2f}"
                ),
            }

        return self._none(
            f"Pas de signal — close={'>' if c0 > ema200 else '<'}EMA200 "
            f"RSI={rsi_now:.1f}(prev={rsi_prev:.1f}) OS={rsi_os} OB={rsi_ob}"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
