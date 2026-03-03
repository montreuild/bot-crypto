"""
Stratégie Breakout Donchian — V4

Lacunes V3 corrigées :
  - period : 20 → 30 (canal 30h = breakout plus significatif, moins de faux)
  - pen_max_atr : 1.2 → 2.0 (stopper les gros mouvements n'a pas de sens)
  - atr_expan_min : 1.20 → 1.05 (seuil plus réaliste, était trop rarement atteint)
  - Confirmation body : la bougie de breakout doit avoir un corps > 0.4×ATR
    (filtre les breakouts en "mèche" qui souvent retracent)
  - Filtre tendance plus nuancé : EMA200 requis pour longs mais 
    EMA50 accepté si prix reste structurellement haussier
  - vol_min abaissé à 1.1 (était 1.3, trop sélectif la nuit/weekend)
  - Nouveau bonus : breakout > 1 close précédente (confirmation structurelle)
"""
import logging
from typing import Dict, Any
import numpy as np
import pandas as pd
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "breakout"

    def __init__(self):
        self._last_signal_bar: int = -999
        self._call_count: int = 0

    def score(self, df: pd.DataFrame, params: dict = None) -> Dict[str, Any]:
        self._call_count += 1
        p = (params or {}).get("breakout", {})

        period        = int(p.get("period",          30))   # ↑ 20→30
        atr_mult      = float(p.get("atr_mult",      2.0))
        vol_min       = float(p.get("vol_min",       1.1))  # ↓ 1.3→1.1
        squeeze_bars  = int(p.get("squeeze_bars",    15))
        cooldown      = int(p.get("cooldown",         20))
        ema_trend     = int(p.get("ema_trend",       200))
        ema_mid       = int(p.get("ema_mid",          50))
        atr_expan_min = float(p.get("atr_expan_min", 1.05)) # ↓ 1.20→1.05
        pen_max_atr   = float(p.get("pen_max_atr",   2.0))  # ↑ 1.2→2.0
        # Minimum body de la bougie de breakout (en fraction d'ATR)
        body_min_atr  = float(p.get("body_min_atr",  0.30)) # nouveau

        min_bars = max(ema_trend + 5, period + squeeze_bars + 25)
        if len(df) < min_bars:
            return self._none()

        close  = df["close"].astype(float)
        high   = df["high"].astype(float)
        low    = df["low"].astype(float)
        open_  = df["open"].astype(float)

        # ── Tendance de fond ──────────────────────────────────────────────────
        ema_t = close.ewm(span=ema_trend, adjust=False).mean()
        ema_m = close.ewm(span=ema_mid,   adjust=False).mean()
        lt    = float(ema_t.iloc[-1])
        lm    = float(ema_m.iloc[-1])
        c_now = float(close.iloc[-1])
        o_now = float(open_.iloc[-1])

        # Tendance haussière si au-dessus de l'EMA200 ou de l'EMA50 (backup)
        trend_bull = c_now > lt or (c_now > lm and c_now > lt * 0.97)
        trend_bear = c_now < lt or (c_now < lm and c_now < lt * 1.03)

        # ── Canal Donchian (exclu barre courante) ──────────────────────────────
        highest = float(high.iloc[-(period + 1):-1].max())
        lowest  = float(low.iloc[-(period + 1):-1].min())

        # ── ATR ────────────────────────────────────────────────────────────────
        atr_series = self._atr_series(df, 14)
        atr_now    = float(atr_series.iloc[-1])
        if atr_now <= 0:
            return self._none()

        # ── Corps de la bougie de breakout ─────────────────────────────────────
        body = abs(c_now - o_now)
        body_ok = body >= atr_now * body_min_atr

        # ── Expansion ATR ──────────────────────────────────────────────────────
        atr_prev      = float(atr_series.iloc[-(squeeze_bars + 1):-1].mean())
        atr_ratio     = atr_now / max(atr_prev, 1e-9)
        vol_expanding = atr_ratio >= atr_expan_min

        # ── Squeeze Bollinger ─────────────────────────────────────────────────
        bb_squeeze = self._bb_squeeze(close, squeeze_bars)

        # ── Volume ────────────────────────────────────────────────────────────
        vol_avg   = df["volume"].rolling(20).mean().iloc[-1]
        vol_now   = float(df["volume"].iloc[-1])
        vol_ratio = vol_now / max(vol_avg, 1e-9)

        # ── Confirmation par barre précédente ─────────────────────────────────
        # La barre n-1 doit être dans la même direction (pas un spike isolé)
        c1 = float(close.iloc[-2])
        prev_bullish = c1 > float(close.iloc[-3])
        prev_bearish = c1 < float(close.iloc[-3])

        # ── Cooldown ─────────────────────────────────────────────────────────
        if self._call_count - self._last_signal_bar < cooldown:
            return self._none("Cooldown")

        indicators = {
            "donchian_high": round(highest, 2), "donchian_low": round(lowest, 2),
            "ema200": round(lt, 2), "ema50": round(lm, 2), "atr": round(atr_now, 2),
            "atr_ratio": round(atr_ratio, 3), "vol_ratio": round(vol_ratio, 2),
            "bb_squeeze": bb_squeeze, "body_atr": round(body / atr_now, 3),
        }

        # ── LONG breakout ─────────────────────────────────────────────────────
        if c_now > highest and trend_bull and vol_ratio >= vol_min:
            penetration = (c_now - highest) / atr_now
            if penetration > pen_max_atr:
                return self._none(f"Breakout épuisé {penetration:.2f}x ATR")

            if not body_ok:
                return self._none(f"Corps trop petit {body/atr_now:.2f}x ATR (min {body_min_atr})")

            quality = 0
            if vol_expanding: quality += 1
            if bb_squeeze:    quality += 1
            if penetration > 0.1: quality += 1
            if prev_bullish:      quality += 1  # confirmation barre précédente

            if quality < 1:
                return self._none("Qualité insuffisante (0/4)")

            vol_factor  = min((vol_ratio - vol_min) / 2.5, 0.12)
            pen_factor  = min(penetration * 0.05, 0.08)
            qual_bonus  = quality * 0.04
            score = min(0.60 + vol_factor + pen_factor + qual_bonus, 0.93)

            self._last_signal_bar = self._call_count
            return {
                "score": round(score, 3), "side": "long", "name": self.name,
                "atr": atr_now, "indicators": indicators,
                "conditions": [
                    f"Close {c_now:.0f} > Donchian{period}H {highest:.0f} ✓",
                    f"Tendance fond haussière (EMA200={lt:.0f}) ✓",
                    f"Volume {vol_ratio:.2f}x (min {vol_min}x) ✓",
                    f"ATR ratio {atr_ratio:.2f}x | BB squeeze: {'✓' if bb_squeeze else '✗'}",
                    f"Corps bougie {body/atr_now:.2f}x ATR ✓",
                    f"Pénétration {penetration:.2f}x ATR | Qualité {quality}/4",
                ],
                "reason": (
                    f"Breakout LONG D{period} "
                    f"pén={penetration:.2f} vol={vol_ratio:.1f}x q={quality}/4"
                ),
            }

        # ── SHORT breakout ────────────────────────────────────────────────────
        if c_now < lowest and trend_bear and vol_ratio >= vol_min:
            penetration = (lowest - c_now) / atr_now
            if penetration > pen_max_atr:
                return self._none(f"Breakout épuisé {penetration:.2f}x ATR")

            if not body_ok:
                return self._none(f"Corps trop petit {body/atr_now:.2f}x ATR")

            quality = 0
            if vol_expanding: quality += 1
            if bb_squeeze:    quality += 1
            if penetration > 0.1: quality += 1
            if prev_bearish:      quality += 1

            if quality < 1:
                return self._none("Qualité insuffisante")

            vol_factor = min((vol_ratio - vol_min) / 2.5, 0.12)
            pen_factor = min(penetration * 0.05, 0.08)
            qual_bonus = quality * 0.04
            score = min(0.60 + vol_factor + pen_factor + qual_bonus, 0.92)

            self._last_signal_bar = self._call_count
            return {
                "score": round(score, 3), "side": "short", "name": self.name,
                "atr": atr_now, "indicators": indicators,
                "conditions": [
                    f"Close {c_now:.0f} < Donchian{period}L {lowest:.0f} ✓",
                    f"Tendance fond baissière (EMA200={lt:.0f}) ✓",
                    f"Volume {vol_ratio:.2f}x | ATR {atr_ratio:.2f}x | q={quality}/4",
                ],
                "reason": f"Breakout SHORT D{period} pén={penetration:.2f} vol={vol_ratio:.1f}x q={quality}/4",
            }

        return self._none(
            f"Close {c_now:.0f} vs [{lowest:.0f}-{highest:.0f}] | "
            f"trend={'bull' if trend_bull else 'bear'} | vol {vol_ratio:.1f}x"
        )

    def _bb_squeeze(self, close: pd.Series, lookback: int = 15) -> bool:
        if len(close) < lookback + 20:
            return False
        sma  = close.rolling(20).mean()
        std  = close.rolling(20).std()
        bb_width    = (4 * std / sma.clip(lower=1e-9))
        cur_w       = float(bb_width.iloc[-1])
        past_widths = bb_width.iloc[-(lookback + 1):-1].dropna()
        if len(past_widths) < 5:
            return False
        return cur_w <= float(past_widths.quantile(0.30))

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}

    def _atr_series(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean()
