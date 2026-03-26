"""Stratégie Breakout Donchian — cassure de canal avec confirmations ATR, MACD et volume."""
import logging
from typing import Dict, Any, List
import polars as pl
from app.engine.engine import BaseStrategy
from app.core.indicators import (
    atr_series as calc_atr_series, macd as calc_macd,
    vol_ratio as calc_vol, bb_squeeze as calc_squeeze, htf_trend, pre_val
)

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "breakout"

    timeframes: List[str] = ["5m", "15m", "1h"]

    param_space: Dict[str, List] = {
        "period":         [20, 25, 30, 40],
        "vol_min":        [1.1, 1.2, 1.5],
        "atr_expan_min":  [1.05, 1.08, 1.12, 1.20],
        "pen_max_atr":    [1.5, 2.0, 2.5],
        "body_min_atr":   [0.30, 0.35, 0.45],
        "squeeze_bars":   [10, 15, 20],
        "cooldown":       [15, 20, 25],
        "rr_min":         [1.3, 1.5, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count:  Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get("breakout", {})
        ema_trend    = int(p.get("ema_trend",    200))
        period       = int(p.get("period",        30))
        squeeze_bars = int(p.get("squeeze_bars",  15))
        return max(ema_trend + 5, period + squeeze_bars + 25)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("breakout", {})

        period        = int(p.get("period",          30))
        atr_mult      = float(p.get("atr_mult",      2.0))
        vol_min       = float(p.get("vol_min",       1.2))   # ↑ 1.1→1.2
        squeeze_bars  = int(p.get("squeeze_bars",    15))
        cooldown      = int(p.get("cooldown",         20))
        ema_trend     = int(p.get("ema_trend",       200))
        ema_mid       = int(p.get("ema_mid",          50))
        atr_expan_min = float(p.get("atr_expan_min", 1.08))  # ↑ 1.05→1.08 obligatoire
        pen_max_atr   = float(p.get("pen_max_atr",   2.0))
        body_min_atr  = float(p.get("body_min_atr",  0.35))  # ↑ 0.30→0.35
        rr_min        = float(p.get("rr_min",        1.5))

        sym = symbol or str(df["time"][-1]) if "time" in df.columns else "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        min_bars = max(ema_trend + 5, period + squeeze_bars + 25)
        if len(df) < min_bars:
            return self._none(f"EMA{ema_trend} requiert {min_bars} bougies min, {len(df)} disponibles")

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        open_  = df["open"]

        # ── Tendance fond ────────────────────────────────────────────────────
        ema_t = close.ewm_mean(span=ema_trend, adjust=False)
        ema_m = close.ewm_mean(span=ema_mid,   adjust=False)
        lt    = float(ema_t[-1])
        lm    = float(ema_m[-1])
        c_now = float(close[-1])
        o_now = float(open_[-1])

        # Tendance stricte : les deux EMAs doivent être du bon côté
        trend_bull = c_now > lt and c_now > lm        # strict
        trend_bear = c_now < lt and c_now < lm        # strict
        # Souplesse pour corrections profondes : -3% sous EMA200 ok si EMA50 valide
        trend_bull_soft = (c_now > lt * 0.97 and c_now > lm * 1.01)
        trend_bear_soft = (c_now < lt * 1.03 and c_now < lm * 0.99)

        # ── Canal Donchian ───────────────────────────────────────────────────
        highest = float(high[-(period + 1):-1].max())
        lowest  = float(low[-(period + 1):-1].min())

        # ── ATR — série pré-calculée si dispo ────────────────────────────────
        atr_s    = df["_pre_atr14"] if "_pre_atr14" in df.columns else calc_atr_series(df, 14)
        atr_now  = float(atr_s[-1])
        if atr_now <= 0:
            return self._none()

        body    = abs(c_now - o_now)
        body_ok = body >= atr_now * body_min_atr

        # ATR expansion obligatoire
        atr_prev  = float(atr_s[-(squeeze_bars + 1):-1].mean())
        atr_ratio = atr_now / max(atr_prev, 1e-9)
        expanding = atr_ratio >= atr_expan_min

        # Squeeze
        squeeze = calc_squeeze(close, squeeze_bars)

        # Volume
        vr       = pre_val(df, "_pre_volratio20") or calc_vol(df)
        vol_prev = float(df["volume"][-2])
        vol_now  = float(df["volume"][-1])
        vol_rising = vol_now > vol_prev * 1.05   # volume monte sur la cassure

        # MACD momentum
        lh = pre_val(df, "_pre_macd_hist")
        if lh is None:
            _, _, hist = calc_macd(close, 12, 26, 9)
            lh = float(hist[-1])
            ph = float(hist[-2])
        else:
            ph = float(df["_pre_macd_hist"][-2])
        macd_bull = lh > 0
        macd_bear = lh < 0
        macd_accel_bull = lh > ph
        macd_accel_bear = lh < ph

        # HTF
        htf = htf_trend(df_htf)

        # Confirmation 2 barres précédentes dans la même direction
        c1, c2, c3 = float(close[-2]), float(close[-3]), float(close[-4])
        prev2_bullish = c1 > c3   # n-1 et n-2 toutes deux haussières
        prev2_bearish = c1 < c3

        if cnt - self._last_signal.get(sym, -999) < cooldown:
            return self._none("Cooldown")

        indicators = {
            "donchian_high": round(highest, 2), "donchian_low": round(lowest, 2),
            "ema200": round(lt, 2), "ema50": round(lm, 2), "atr": round(atr_now, 2),
            "atr_ratio": round(atr_ratio, 3), "vol_ratio": round(vr, 2),
            "bb_squeeze": squeeze, "body_atr": round(body / atr_now, 3),
            "macd_hist": round(lh, 6), "htf_trend": htf,
        }

        # ── LONG breakout ────────────────────────────────────────────────────
        if c_now > highest and (trend_bull or trend_bull_soft) and vr >= vol_min:
            penetration = (c_now - highest) / atr_now
            if penetration > pen_max_atr:
                return self._none(f"Épuisé {penetration:.2f}x ATR")
            if not body_ok:
                return self._none(f"Corps {body/atr_now:.2f}x ATR < {body_min_atr}")
            if not expanding:
                return self._none(f"ATR pas en expansion ({atr_ratio:.2f}x < {atr_expan_min}x)")
            if not macd_bull:
                return self._none(f"MACD négatif ({lh:+.5f})")
            if htf < 0:
                return self._none("HTF baissier — breakout contre tendance")

            # Quality score (minimum 2 sur 4)
            quality = sum([
                squeeze,            # compression avant cassure
                penetration > 0.15, # cassure significative
                prev2_bullish,      # confirmation 2 barres
                vol_rising,         # volume monte
            ])
            if quality < 2:
                return self._none(f"Qualité {quality}/4 < 2")

            # R:R : stop sous le canal Donchian
            stop_l  = highest - atr_now * atr_mult
            risk_l  = c_now - stop_l
            if risk_l <= 0:
                return self._none("Stop invalide")
            target_l = highest + (highest - lowest)
            reward_l = max(target_l - c_now, 0.0)
            rr_l = reward_l / risk_l if risk_l > 0 else 0.0
            if rr_l < rr_min:
                return self._none(f"R:R {rr_l:.2f} < {rr_min}")

            vol_f     = min((vr - vol_min) / 2.0, 0.10)
            pen_f     = min(penetration * 0.05, 0.08)
            qual_b    = quality * 0.04
            macd_b    = 0.04 if macd_accel_bull else 0.0
            htf_b     = 0.04 if htf > 0 else 0.0
            trend_b   = 0.04 if trend_bull else 0.0

            score = min(0.62 + vol_f + pen_f + qual_b + macd_b + htf_b + trend_b, 0.94)
            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "long", "name": self.name,
                "atr": atr_now, "stop_hint": round(stop_l, 2),
                "indicators": indicators,
                "conditions": [
                    f"Close {c_now:.0f} > Donchian{period}H {highest:.0f} ✓",
                    f"Tendance: EMA200={lt:.0f} EMA50={lm:.0f} ✓",
                    f"Vol {vr:.2f}x ↑ | ATR expansion {atr_ratio:.2f}x ✓",
                    f"MACD {lh:+.5f} positif ✓ | Corps {body/atr_now:.2f}x ATR ✓",
                    f"Qualité {quality}/4 | Pénétration {penetration:.2f}x ATR",
                ],
                "reason": (
                    f"Breakout LONG D{period} pén={penetration:.2f} "
                    f"vol={vr:.1f}x q={quality}/4 MACD={lh:+.5f}"
                ),
            }

        # ── SHORT breakout ───────────────────────────────────────────────────
        if c_now < lowest and (trend_bear or trend_bear_soft) and vr >= vol_min:
            penetration = (lowest - c_now) / atr_now
            if penetration > pen_max_atr:
                return self._none(f"Épuisé {penetration:.2f}x ATR")
            if not body_ok:
                return self._none(f"Corps {body/atr_now:.2f}x ATR < {body_min_atr}")
            if not expanding:
                return self._none(f"ATR pas en expansion ({atr_ratio:.2f}x)")
            if not macd_bear:
                return self._none(f"MACD positif ({lh:+.5f})")
            if htf > 0:
                return self._none("HTF haussier — breakout contre tendance")

            quality = sum([squeeze, penetration > 0.15, prev2_bearish, vol_rising])
            if quality < 2:
                return self._none(f"Qualité {quality}/4 < 2")

            stop_s  = lowest + atr_now * atr_mult
            risk_s  = stop_s - c_now
            if risk_s <= 0:
                return self._none("Stop invalide")
            target_s = lowest - (highest - lowest)
            reward_s = max(c_now - target_s, 0.0)
            rr_s = reward_s / risk_s if risk_s > 0 else 0.0
            if rr_s < rr_min:
                return self._none(f"R:R short {rr_s:.2f} < {rr_min}")

            vol_f   = min((vr - vol_min) / 2.0, 0.10)
            pen_f   = min(penetration * 0.05, 0.08)
            qual_b  = quality * 0.04
            macd_b  = 0.04 if macd_accel_bear else 0.0
            htf_b   = 0.04 if htf < 0 else 0.0

            score = min(0.62 + vol_f + pen_f + qual_b + macd_b + htf_b, 0.93)
            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "short", "name": self.name,
                "atr": atr_now, "stop_hint": round(stop_s, 2),
                "indicators": indicators,
                "conditions": [
                    f"Close {c_now:.0f} < Donchian{period}L {lowest:.0f} ✓",
                    f"Vol {vr:.2f}x | ATR {atr_ratio:.2f}x | q={quality}/4 ✓",
                    f"MACD {lh:+.5f} négatif ✓ | Corps {body/atr_now:.2f}x ATR ✓",
                ],
                "reason": (
                    f"Breakout SHORT D{period} pén={penetration:.2f} "
                    f"vol={vr:.1f}x q={quality}/4"
                ),
            }

        return self._none(
            f"Close {c_now:.0f} ∉ [{lowest:.0f}–{highest:.0f}] | "
            f"trend={'bull' if trend_bull else 'bear' if trend_bear else '?'} | "
            f"vol {vr:.1f}x | MACD {lh:+.5f}"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
