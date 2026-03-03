"""
Stratégie SuperTrend + MACD — V4

Lacunes V3 corrigées :
  - st_mult : 3.0 → 2.5 (plus de cross sur 1h, signal moins tardif)
  - Condition trend_bull allégée pour le cas B :
    accepte prix > EMA200×0.97 (correction de -3% = normale)
  - Cas C : vol_cas_c abaissé à 1.1, trend_bull assoupli à EMA50
  - RSI zone optimale élargie [35-68] pour les cas A/B
  - Ajout d'un cas D (nouveau) : ST bear-to-bull + pullback + MACD > 0
    → Signal de retournement fort, très sélectif
  - Score différencié et plus lisible
"""
import logging
from typing import Dict, Any, Tuple
import numpy as np
import pandas as pd
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "supertrend_macd"

    def __init__(self):
        self._last_signal_bar: int = -999
        self._call_count: int = 0

    def score(self, df: pd.DataFrame, params: dict = None) -> Dict[str, Any]:
        self._call_count += 1
        p = (params or {}).get("supertrend_macd", {})

        st_period    = int(p.get("st_period",     10))
        st_mult      = float(p.get("st_mult",      2.5))   # ↓ 3.0→2.5
        macd_fast    = int(p.get("macd_fast",     12))
        macd_slow    = int(p.get("macd_slow",     26))
        macd_sig_s   = int(p.get("macd_signal",   9))
        vol_min      = float(p.get("vol_min",      1.0))   # ↓ 1.1→1.0
        vol_cas_c    = float(p.get("vol_cas_c",    1.1))   # ↓ 1.3→1.1
        cooldown     = int(p.get("cooldown",       15))
        ema_trend    = int(p.get("ema_trend",     200))
        ema_mid_p    = int(p.get("ema_mid",        50))
        rsi_max_long = float(p.get("rsi_max_long", 72))    # ↑ 76 mais cohérent
        rsi_min_long = float(p.get("rsi_min_long", 30))

        min_bars = max(ema_trend + 5, macd_slow + macd_sig_s + st_period + 15, 220)
        if len(df) < min_bars:
            return self._none()

        close = df["close"].astype(float)
        high  = df["high"].astype(float)
        low   = df["low"].astype(float)

        # ── EMAs ──────────────────────────────────────────────────────────────
        ema_t   = close.ewm(span=ema_trend, adjust=False).mean()
        ema_mid = close.ewm(span=ema_mid_p, adjust=False).mean()
        lt      = float(ema_t.iloc[-1])
        lm_mid  = float(ema_mid.iloc[-1])
        c_now   = float(close.iloc[-1])

        # Tendance souple : 3% de tolérance sous EMA200
        trend_bull   = c_now >= lt * 0.97
        trend_bear   = c_now <= lt * 1.03
        trend_bull_s = c_now >= lm_mid   # EMA50 backup

        # ── SuperTrend ─────────────────────────────────────────────────────────
        direction, st_line = self._supertrend(high, low, close, st_period, st_mult)
        last_dir      = int(direction.iloc[-1])
        prev_dir      = int(direction.iloc[-2])
        st_bull       = (last_dir == 1)
        st_bear       = (last_dir == -1)
        st_cross_up   = (prev_dir == -1 and last_dir == 1)
        st_cross_down = (prev_dir == 1  and last_dir == -1)

        # ── MACD ──────────────────────────────────────────────────────────────
        ema12     = close.ewm(span=macd_fast,  adjust=False).mean()
        ema26     = close.ewm(span=macd_slow,  adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line  = macd_line.ewm(span=macd_sig_s, adjust=False).mean()
        hist      = macd_line - sig_line

        lh  = float(hist.iloc[-1])
        ph  = float(hist.iloc[-2])
        p2h = float(hist.iloc[-3])

        macd_cross_bull  = ph < 0 and lh > 0
        macd_cross_bear  = ph > 0 and lh < 0
        macd_accel_bull  = lh > 0 and lh > ph > p2h
        macd_accel_bear  = lh < 0 and lh < ph < p2h
        macd_cont_bull   = lh > 0 and lh >= ph
        macd_cont_bear   = lh < 0 and lh <= ph
        macd_pos         = lh > 0
        macd_neg         = lh < 0

        # ── RSI ───────────────────────────────────────────────────────────────
        rsi_now = float(self._rsi(close, 14).iloc[-1])

        # ── Volume ────────────────────────────────────────────────────────────
        vol_avg   = df["volume"].rolling(20).mean().iloc[-1]
        vol_now   = float(df["volume"].iloc[-1])
        vol_ratio = vol_now / max(vol_avg, 1e-9)

        # ── ATR ───────────────────────────────────────────────────────────────
        atr = self._atr(df, 14)
        if atr <= 0:
            return self._none()

        # ── Cooldown ─────────────────────────────────────────────────────────
        if self._call_count - self._last_signal_bar < cooldown:
            return self._none("Cooldown")

        indicators = {
            "supertrend":  round(float(st_line.iloc[-1]), 2),
            "st_dir":      "bull" if st_bull else "bear",
            "st_cross_up": st_cross_up, "st_cross_down": st_cross_down,
            "macd_hist":   round(lh, 6),
            "macd_x_bull": macd_cross_bull, "macd_x_bear": macd_cross_bear,
            "rsi":         round(rsi_now, 1),
            "vol_ratio":   round(vol_ratio, 2),
            "ema200":      round(lt, 2), "ema50": round(lm_mid, 2),
            "atr":         round(atr, 2),
        }

        # ═══ LONG ════════════════════════════════════════════════════════════
        rsi_ok_long = rsi_min_long <= rsi_now <= rsi_max_long

        # Cas A : ST cross up + MACD quelconque positif ou cross → fort
        long_A = st_cross_up and (macd_cont_bull or macd_cross_bull or macd_pos) and trend_bull
        # Cas B : ST bull + cross MACD + tendance fond OK (assouplie)
        long_B = st_bull and macd_cross_bull and (trend_bull or trend_bull_s)
        # Cas C (assoupli) : ST bull + MACD accélère + volume correct + fond EMA50
        long_C = (st_bull and macd_accel_bull and
                  vol_ratio >= vol_cas_c and
                  (trend_bull or trend_bull_s))
        # Cas D : ST vient de repasser bull (< 3 barres) + MACD > 0 + pullback récent
        long_D = st_bull and not st_cross_up and macd_pos and trend_bull
        recent_bull = sum(1 for k in range(1, 4)
                          if len(direction) > k and direction.iloc[-k] == 1) >= 2
        long_D = long_D and recent_bull and macd_accel_bull

        if (long_A or long_B or long_C or long_D) and vol_ratio >= vol_min and rsi_ok_long:
            if long_A:
                base = 0.72; tag = "ST cross↑ + MACD bull"
            elif long_B:
                base = 0.67; tag = "ST bull + MACD cross↑"
            elif long_C:
                base = 0.62; tag = "ST bull + MACD accel"
            else:
                base = 0.63; tag = "ST bull récent + MACD accel"

            vol_bonus  = min((vol_ratio - vol_min) * 0.04, 0.08)
            macd_bonus = 0.05 if macd_cross_bull else 0.0
            rsi_bonus  = 0.04 if 40 <= rsi_now <= 62 else 0.0
            trend_bonus = 0.04 if trend_bull and trend_bull_s else 0.0
            score = min(base + vol_bonus + macd_bonus + rsi_bonus + trend_bonus, 0.94)

            self._last_signal_bar = self._call_count
            return {
                "score": round(score, 3), "side": "long", "name": self.name,
                "atr": atr, "indicators": indicators,
                "conditions": [
                    f"SuperTrend: {'CROSS↑' if st_cross_up else 'BULL'} ✓",
                    f"MACD hist: {p2h:+.5f}→{ph:+.5f}→{lh:+.5f} "
                    f"({'cross↑' if macd_cross_bull else 'accel' if macd_accel_bull else 'pos'})",
                    f"Fond: EMA200 {'✓' if trend_bull else '~'} EMA50 {'✓' if trend_bull_s else '~'}",
                    f"RSI {rsi_now:.0f} ✓ | Vol {vol_ratio:.2f}x ✓",
                ],
                "reason": f"{tag} | RSI={rsi_now:.0f} vol={vol_ratio:.1f}x",
            }

        # ═══ SHORT ════════════════════════════════════════════════════════════
        rsi_ok_short = (100 - rsi_max_long) <= rsi_now <= (100 - rsi_min_long)
        trend_bear_s = c_now <= lm_mid

        short_A = st_cross_down and (macd_cont_bear or macd_cross_bear or macd_neg) and trend_bear
        short_B = st_bear and macd_cross_bear and (trend_bear or trend_bear_s)
        short_C = st_bear and macd_accel_bear and vol_ratio >= vol_cas_c and (trend_bear or trend_bear_s)
        recent_bear = sum(1 for k in range(1, 4)
                          if len(direction) > k and direction.iloc[-k] == -1) >= 2
        short_D = st_bear and not st_cross_down and macd_neg and trend_bear and recent_bear and macd_accel_bear

        if (short_A or short_B or short_C or short_D) and vol_ratio >= vol_min and rsi_ok_short:
            if short_A:
                base = 0.72; tag = "ST cross↓ + MACD bear"
            elif short_B:
                base = 0.67; tag = "ST bear + MACD cross↓"
            elif short_C:
                base = 0.62; tag = "ST bear + MACD accel"
            else:
                base = 0.63; tag = "ST bear récent + MACD accel"

            vol_bonus  = min((vol_ratio - vol_min) * 0.04, 0.08)
            macd_bonus = 0.05 if macd_cross_bear else 0.0
            rsi_bonus  = 0.04 if 38 <= rsi_now <= 60 else 0.0
            score = min(base + vol_bonus + macd_bonus + rsi_bonus, 0.93)

            self._last_signal_bar = self._call_count
            return {
                "score": round(score, 3), "side": "short", "name": self.name,
                "atr": atr, "indicators": indicators,
                "conditions": [
                    f"SuperTrend: {'CROSS↓' if st_cross_down else 'BEAR'} ✓",
                    f"MACD hist: {p2h:+.5f}→{ph:+.5f}→{lh:+.5f}",
                    f"Fond: {'✓' if trend_bear else '~'} | RSI {rsi_now:.0f} | Vol {vol_ratio:.2f}x",
                ],
                "reason": f"{tag} | RSI={rsi_now:.0f} vol={vol_ratio:.1f}x",
            }

        return self._none(
            f"ST={'bull' if st_bull else 'bear'} MACD={lh:+.5f} "
            f"fond={'bull' if trend_bull else 'bear'} vol={vol_ratio:.1f}x RSI={rsi_now:.0f}"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}

    def _rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        d = close.diff()
        g = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        l = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        return 100 - 100 / (1 + g / l.replace(0, np.nan))

    def _supertrend(self, high, low, close, period, mult):
        pc   = close.shift(1)
        tr   = pd.concat([high-low,(high-pc).abs(),(low-pc).abs()],axis=1).max(axis=1)
        atr  = tr.ewm(span=period, adjust=False).mean()
        hl2  = (high + low) / 2.0
        bu   = (hl2 + mult * atr).astype(float)
        bl   = (hl2 - mult * atr).astype(float)
        n    = len(close)
        fu   = bu.copy(); fl = bl.copy()
        st   = pd.Series(np.nan, index=close.index, dtype=float)
        dir_ = pd.Series(1, index=close.index, dtype=int)
        for i in range(1, n):
            cu,cl_,pu,pl = float(bu.iloc[i]),float(bl.iloc[i]),float(fu.iloc[i-1]),float(fl.iloc[i-1])
            fu.iloc[i] = min(cu,pu) if float(close.iloc[i-1])<=pu else cu
            fl.iloc[i] = max(cl_,pl) if float(close.iloc[i-1])>=pl else cl_
            pst = float(st.iloc[i-1]) if not np.isnan(st.iloc[i-1]) else pu
            cn  = float(close.iloc[i])
            if np.isnan(st.iloc[i-1]) or pst >= float(close.iloc[i-1])*0.99:
                dir_.iloc[i] = 1 if cn > float(fu.iloc[i]) else -1
                st.iloc[i]   = float(fl.iloc[i]) if dir_.iloc[i]==1 else float(fu.iloc[i])
            else:
                if cn < float(fl.iloc[i]):   dir_.iloc[i]=-1; st.iloc[i]=float(fu.iloc[i])
                else:                         dir_.iloc[i]=1;  st.iloc[i]=float(fl.iloc[i])
        return dir_, st

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        h,l,c = df["high"].astype(float),df["low"].astype(float),df["close"].astype(float)
        tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        v  = float(tr.rolling(period).mean().iloc[-1])
        return v if v > 0 else 0.0
