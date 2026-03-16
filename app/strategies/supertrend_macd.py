"""
Stratégie SuperTrend + MACD — V5

Corrections V5 vs V4 :
  - Cooldown par symbole
  - RSI zone resserrée [38-65] (était [30-72] — achetait des extrêmes)
  - Cas D supprimé (bruité et redondant avec continuation)
  - Cas simplifié en 3 : A (ST cross), B (ST + MACD cross), C (continuation forte)
  - MACD : exige histogramme croissant depuis 2 barres (pas juste positif)
  - Volume minimum 1.1× pour tous les cas (était 1.0×)
  - R:R check : stop sous le SuperTrend line
  - HTF filtre : pas de long si HTF baissier, pas de short si HTF haussier
  - Tendance fond stricte (± 2.5% EMA200 max)
"""
import logging
from typing import Dict, Any
import polars as pl
from app.engine.engine import BaseStrategy
from app.strategies.indicators import (
    rsi as calc_rsi, atr as calc_atr, macd as calc_macd,
    supertrend as calc_supertrend, vol_ratio as calc_vol, htf_trend, pre_val
)

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "supertrend_macd"

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count:  Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get("supertrend_macd", {})
        ema_trend  = int(p.get("ema_trend",   200))
        macd_slow  = int(p.get("macd_slow",    26))
        macd_sig_s = int(p.get("macd_signal",   9))
        st_period  = int(p.get("st_period",    10))
        return max(ema_trend + 5, macd_slow + macd_sig_s + st_period + 15, 220)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("supertrend_macd", {})

        st_period    = int(p.get("st_period",     10))
        st_mult      = float(p.get("st_mult",      2.5))
        macd_fast    = int(p.get("macd_fast",     12))
        macd_slow    = int(p.get("macd_slow",     26))
        macd_sig_s   = int(p.get("macd_signal",   9))
        vol_min      = float(p.get("vol_min",      1.1))
        cooldown     = int(p.get("cooldown",       15))
        ema_trend    = int(p.get("ema_trend",     200))
        ema_mid_p    = int(p.get("ema_mid",        50))
        rsi_min      = float(p.get("rsi_min",      38))
        rsi_max      = float(p.get("rsi_max",      65))
        rr_min       = float(p.get("rr_min",       1.5))

        sym = symbol or str(df["time"][-1]) if "time" in df.columns else "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        min_bars = max(ema_trend + 5, macd_slow + macd_sig_s + st_period + 15, 220)
        if len(df) < min_bars:
            return self._none(f"EMA{ema_trend} requiert {min_bars} bougies min, {len(df)} disponibles")

        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        # ── EMAs ─────────────────────────────────────────────────────────────
        ema_t   = close.ewm_mean(span=ema_trend, adjust=False)
        ema_mid = close.ewm_mean(span=ema_mid_p, adjust=False)
        lt      = float(ema_t[-1])
        lm      = float(ema_mid[-1])
        c_now   = float(close[-1])

        trend_bull = c_now >= lt * 0.975 and c_now >= lm * 0.99
        trend_bear = c_now <= lt * 1.025 and c_now <= lm * 1.01

        # ── SuperTrend ────────────────────────────────────────────────────────
        direction, st_line = calc_supertrend(df, st_period, st_mult)
        last_dir  = int(direction[-1])
        prev_dir  = int(direction[-2])
        st_bull   = last_dir == 1
        st_bear   = last_dir == -1
        st_x_up   = prev_dir == -1 and last_dir == 1
        st_x_down = prev_dir == 1  and last_dir == -1
        st_val    = float(st_line[-1])

        # ── MACD ──────────────────────────────────────────────────────────────
        _macd_default = (macd_fast == 12 and macd_slow == 26 and macd_sig_s == 9)
        if _macd_default and "_pre_macd_hist" in df.columns:
            _mhist = df["_pre_macd_hist"]
            lh  = float(_mhist[-1])
            ph  = float(_mhist[-2])
            p2h = float(_mhist[-3])
        else:
            _, _, hist = calc_macd(close, macd_fast, macd_slow, macd_sig_s)
            lh  = float(hist[-1])
            ph  = float(hist[-2])
            p2h = float(hist[-3])

        macd_x_bull   = ph < 0 and lh > 0
        macd_x_bear   = ph > 0 and lh < 0
        macd_accel2_bull = lh > 0 and lh > ph and ph > p2h
        macd_accel2_bear = lh < 0 and lh < ph and ph < p2h
        macd_cont_bull   = lh > 0 and lh >= ph
        macd_cont_bear   = lh < 0 and lh <= ph

        # ── RSI / Volume / ATR / HTF ─────────────────────────────────────────
        rsi_now = pre_val(df, "_pre_rsi14") or float(calc_rsi(close, 14)[-1])
        vr      = pre_val(df, "_pre_volratio20") or calc_vol(df)
        atr_val = pre_val(df, "_pre_atr14")      or calc_atr(df, 14)
        htf     = htf_trend(df_htf)

        if atr_val <= 0:
            return self._none()

        if cnt - self._last_signal.get(sym, -999) < cooldown:
            return self._none("Cooldown")

        indicators = {
            "supertrend": round(st_val, 2),
            "st_dir":     "bull" if st_bull else "bear",
            "st_cross_up": st_x_up, "st_cross_down": st_x_down,
            "macd_hist":  round(lh, 6),
            "macd_x_bull": macd_x_bull, "macd_x_bear": macd_x_bear,
            "rsi":        round(rsi_now, 1),
            "vol_ratio":  round(vr, 2),
            "ema200":     round(lt, 2), "ema50": round(lm, 2),
            "atr":        round(atr_val, 2), "htf_trend": htf,
        }

        # ═══ LONG ═════════════════════════════════════════════════════════════
        rsi_ok = rsi_min <= rsi_now <= rsi_max

        long_A = st_x_up and (macd_cont_bull or macd_x_bull) and trend_bull
        long_B = st_bull and not st_x_up and (macd_x_bull or macd_accel2_bull) and trend_bull
        long_C = (st_bull and macd_accel2_bull and
                  vr >= vol_min * 1.2 and trend_bull)

        htf_ok_long = htf >= 0

        if (long_A or long_B or long_C) and vr >= vol_min and rsi_ok and htf_ok_long:

            stop_l = st_val - atr_val * 0.3
            risk_l = c_now - stop_l
            if risk_l <= 0:
                return self._none("Stop invalide")
            if (risk_l * 2.0) / risk_l < rr_min:
                return self._none("R:R insuffisant")

            if long_A:
                base = 0.73; tag = "ST cross↑ + MACD"
            elif long_B:
                base = 0.67; tag = "ST bull + MACD cross/accel×2"
            else:
                base = 0.63; tag = "ST bull + MACD accel forte"

            vol_b   = min((vr - vol_min) * 0.04, 0.08)
            macd_b  = 0.05 if macd_x_bull else (0.03 if macd_accel2_bull else 0.0)
            rsi_b   = 0.04 if 42 <= rsi_now <= 58 else 0.0
            htf_b   = 0.04 if htf > 0 else 0.0
            trend_b = 0.03 if (trend_bull and c_now > lt) else 0.0

            score = min(base + vol_b + macd_b + rsi_b + htf_b + trend_b, 0.94)

            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "long", "name": self.name,
                "atr": atr_val, "stop_hint": round(stop_l, 2),
                "indicators": indicators,
                "conditions": [
                    f"SuperTrend: {'CROSS↑' if st_x_up else 'BULL'} @ {st_val:.0f} ✓",
                    f"MACD hist: {p2h:+.5f}→{ph:+.5f}→{lh:+.5f} "
                    f"({'cross↑' if macd_x_bull else 'accel×2' if macd_accel2_bull else 'pos'}) ✓",
                    f"RSI {rsi_now:.0f} ∈ [{rsi_min:.0f}–{rsi_max:.0f}] ✓",
                    f"Vol {vr:.2f}x ✓ | HTF {'haussier' if htf>0 else 'neutre'} ✓",
                    f"Fond: EMA200={'✓' if c_now>lt else '~'} EMA50={'✓' if c_now>lm else '~'}",
                ],
                "reason": f"{tag} | RSI={rsi_now:.0f} vol={vr:.1f}x htf={htf}",
            }

        # ═══ SHORT ════════════════════════════════════════════════════════════
        rsi_ok_s  = (100 - rsi_max) <= rsi_now <= (100 - rsi_min)
        htf_ok_s  = htf <= 0

        short_A = st_x_down and (macd_cont_bear or macd_x_bear) and trend_bear
        short_B = st_bear and not st_x_down and (macd_x_bear or macd_accel2_bear) and trend_bear
        short_C = st_bear and macd_accel2_bear and vr >= vol_min * 1.2 and trend_bear

        if (short_A or short_B or short_C) and vr >= vol_min and rsi_ok_s and htf_ok_s:

            stop_s = st_val + atr_val * 0.3
            risk_s = stop_s - c_now
            if risk_s <= 0:
                return self._none("Stop invalide (short)")

            if short_A:
                base = 0.72; tag = "ST cross↓ + MACD"
            elif short_B:
                base = 0.66; tag = "ST bear + MACD cross/accel×2"
            else:
                base = 0.62; tag = "ST bear + MACD accel forte"

            vol_b  = min((vr - vol_min) * 0.04, 0.08)
            macd_b = 0.05 if macd_x_bear else (0.03 if macd_accel2_bear else 0.0)
            rsi_b  = 0.04 if 42 <= rsi_now <= 58 else 0.0
            htf_b  = 0.04 if htf < 0 else 0.0

            score = min(base + vol_b + macd_b + rsi_b + htf_b, 0.93)

            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "short", "name": self.name,
                "atr": atr_val, "stop_hint": round(stop_s, 2),
                "indicators": indicators,
                "conditions": [
                    f"SuperTrend: {'CROSS↓' if st_x_down else 'BEAR'} @ {st_val:.0f} ✓",
                    f"MACD hist: {p2h:+.5f}→{ph:+.5f}→{lh:+.5f} ✓",
                    f"RSI {rsi_now:.0f} | Vol {vr:.2f}x | HTF {'baissier' if htf<0 else 'neutre'} ✓",
                ],
                "reason": f"{tag} | RSI={rsi_now:.0f} vol={vr:.1f}x htf={htf}",
            }

        reasons = []
        if not (st_bull or st_x_up): reasons.append(f"ST {'bear' if st_bear else '?'}")
        if not (macd_cont_bull or macd_x_bull or macd_accel2_bull):
            reasons.append(f"MACD {lh:+.5f}")
        if not rsi_ok:  reasons.append(f"RSI {rsi_now:.0f} hors [{rsi_min:.0f}–{rsi_max:.0f}]")
        if vr < vol_min: reasons.append(f"Vol {vr:.1f}x < {vol_min}x")
        if htf < 0 and st_bull: reasons.append("HTF baissier")
        return self._none(" | ".join(reasons) or "Conditions non réunies")

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
