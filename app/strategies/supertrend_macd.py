"""Stratégie SuperTrend + MACD — confirmation de tendance avec cross et continuation."""

import logging
from typing import Dict, Any, List
import polars as pl
from app.engine.engine import BaseStrategy
from app.core.indicators import macd as calc_macd, supertrend as calc_supertrend, htf_trend, pre_val

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "supertrend_macd"

    timeframes: List[str] = ["5m", "15m", "1h"]

    # 5 paramètres × 3 valeurs = 243 combinaisons
    param_space: Dict[str, List] = {
        "st_period":  [7, 10, 14],
        "st_mult":    [2.0, 2.5, 3.0],
        "macd_fast":  [10, 12, 14],
        "macd_slow":  [24, 26, 28],
        "vol_min":    [1.0, 1.1, 1.3],
    }

    fixed_params: Dict[str, Any] = {
        "macd_signal": 9,
        "rsi_min":     35,
        "rsi_max":     68,
        "cooldown":    12,
        "rr_min":      1.3,
        "tp_mult":     2.0,   # target = atr × tp_mult
        "ema_trend":   200,
        "ema_mid":     50,
    }

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

        # Paramètres optimisables
        st_period  = int(p.get("st_period",     10))
        st_mult    = float(p.get("st_mult",      2.5))
        macd_fast  = int(p.get("macd_fast",     12))
        macd_slow  = int(p.get("macd_slow",     26))
        vol_min    = float(p.get("vol_min",      1.1))

        # Paramètres fixes
        macd_sig_s = int(p.get("macd_signal",    9))
        rsi_min    = float(p.get("rsi_min",      35))
        rsi_max    = float(p.get("rsi_max",      68))
        cooldown   = int(p.get("cooldown",       12))
        rr_min     = float(p.get("rr_min",       1.3))
        tp_mult    = float(p.get("tp_mult",      2.0))
        ema_trend  = int(p.get("ema_trend",     200))
        ema_mid_p  = int(p.get("ema_mid",        50))

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
        _ema_map = {20: "_pre_ema20", 50: "_pre_ema50", 200: "_pre_ema200"}
        lt      = pre_val(df, _ema_map.get(ema_trend, "")) or float(close.ewm_mean(span=ema_trend, adjust=False)[-1])
        lm      = pre_val(df, _ema_map.get(ema_mid_p, "")) or float(close.ewm_mean(span=ema_mid_p, adjust=False)[-1])
        c_now   = float(close[-1])

        trend_bull = c_now >= lt * 0.970 and c_now >= lm * 0.985
        trend_bear = c_now <= lt * 1.030 and c_now <= lm * 1.015

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
        # Utilise les colonnes pré-calculées pour les params par défaut (12,26,9) ;
        # recalcule si l'optimiseur utilise des params différents.
        _macd_default = (macd_fast == 12 and macd_slow == 26 and macd_sig_s == 9)
        if _macd_default:
            lh  = float(df["_pre_macd_hist"][-1])
            ph  = float(df["_pre_macd_hist"][-2])
            p2h = float(df["_pre_macd_hist"][-3])
        else:
            _, _, hist = calc_macd(close, macd_fast, macd_slow, macd_sig_s)
            lh  = float(hist[-1])
            ph  = float(hist[-2])
            p2h = float(hist[-3])

        macd_x_bull      = ph < 0 and lh > 0
        macd_x_bear      = ph > 0 and lh < 0
        macd_accel_bull  = lh > 0 and lh > ph
        macd_accel_bear  = lh < 0 and lh < ph

        # ── RSI / Volume / ATR / HTF ─────────────────────────────────────────
        rsi_now = float(df["_pre_rsi14"][-1])
        vr      = float(df["_pre_volratio20"][-1])
        atr_val = float(df["_pre_atr14"][-1])
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

        rsi_ok   = rsi_min <= rsi_now <= rsi_max
        htf_ok_l = htf >= 0
        htf_ok_s = htf <= 0

        # ═══ LONG ═════════════════════════════════════════════════════════════
        # Cas A : ST vient de passer haussier + MACD en momentum positif
        long_A = st_x_up and (macd_x_bull or macd_accel_bull) and trend_bull
        # Cas B : ST haussier continu + MACD cross ou accélération + volume
        long_B = st_bull and not st_x_up and (macd_x_bull or (lh > 0 and lh > ph and ph > p2h)) and trend_bull

        if (long_A or long_B) and vr >= vol_min and rsi_ok and htf_ok_l:
            # Stop : ligne SuperTrend (stop naturel)
            stop_l = st_val
            risk_l = c_now - stop_l
            if risk_l <= 0:
                return self._none("Stop invalide")

            # Target forward : atr × tp_mult
            target_l = c_now + atr_val * tp_mult
            reward_l = target_l - c_now
            rr_l = reward_l / risk_l

            if rr_l < rr_min:
                return self._none(f"R:R {rr_l:.2f} < {rr_min}")

            if long_A:
                base = 0.72; tag = "ST cross↑ + MACD"
            else:
                base = 0.65; tag = "ST bull + MACD accel"

            vol_b   = min((vr - vol_min) * 0.04, 0.08)
            macd_b  = 0.05 if macd_x_bull else 0.02
            rsi_b   = 0.03 if 40 <= rsi_now <= 60 else 0.0
            htf_b   = 0.04 if htf > 0 else 0.0

            score = min(base + vol_b + macd_b + rsi_b + htf_b, 0.93)

            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "long", "name": self.name,
                "atr": atr_val, "stop_hint": round(stop_l, 2),
                "indicators": indicators,
                "conditions": [
                    f"SuperTrend: {'CROSS↑' if st_x_up else 'BULL'} @ {st_val:.0f} ✓",
                    f"MACD hist: {p2h:+.5f}→{ph:+.5f}→{lh:+.5f} "
                    f"({'cross↑' if macd_x_bull else 'accel'}) ✓",
                    f"RSI {rsi_now:.0f} ∈ [{rsi_min:.0f}–{rsi_max:.0f}] ✓",
                    f"Vol {vr:.2f}x ✓ | HTF {'haussier' if htf>0 else 'neutre'} ✓",
                    f"R:R {rr_l:.2f} (stop@ST={stop_l:.0f}, tp+{tp_mult}×ATR) ✓",
                    f"Fond: EMA200={'✓' if c_now>lt else '~'} EMA50={'✓' if c_now>lm else '~'}",
                ],
                "reason": f"{tag} | RSI={rsi_now:.0f} vol={vr:.1f}x htf={htf} RR={rr_l:.2f}",
            }

        # ═══ SHORT ════════════════════════════════════════════════════════════
        # RSI miroir pour les shorts (zone symétrique)
        rsi_ok_s = (100 - rsi_max) <= rsi_now <= (100 - rsi_min)

        short_A = st_x_down and (macd_x_bear or macd_accel_bear) and trend_bear
        short_B = st_bear and not st_x_down and (macd_x_bear or (lh < 0 and lh < ph and ph < p2h)) and trend_bear

        if (short_A or short_B) and vr >= vol_min and rsi_ok_s and htf_ok_s:
            stop_s = st_val
            risk_s = stop_s - c_now
            if risk_s <= 0:
                return self._none("Stop invalide (short)")

            target_s = c_now - atr_val * tp_mult
            reward_s = c_now - target_s
            rr_s = reward_s / risk_s

            if rr_s < rr_min:
                return self._none(f"R:R {rr_s:.2f} < {rr_min} (short)")

            if short_A:
                base = 0.71; tag = "ST cross↓ + MACD"
            else:
                base = 0.64; tag = "ST bear + MACD accel"

            vol_b  = min((vr - vol_min) * 0.04, 0.08)
            macd_b = 0.05 if macd_x_bear else 0.02
            rsi_b  = 0.03 if 40 <= rsi_now <= 60 else 0.0
            htf_b  = 0.04 if htf < 0 else 0.0

            score = min(base + vol_b + macd_b + rsi_b + htf_b, 0.92)

            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "short", "name": self.name,
                "atr": atr_val, "stop_hint": round(stop_s, 2),
                "indicators": indicators,
                "conditions": [
                    f"SuperTrend: {'CROSS↓' if st_x_down else 'BEAR'} @ {st_val:.0f} ✓",
                    f"MACD hist: {p2h:+.5f}→{ph:+.5f}→{lh:+.5f} ✓",
                    f"RSI {rsi_now:.0f} | Vol {vr:.2f}x | HTF {'baissier' if htf<0 else 'neutre'} ✓",
                    f"R:R {rr_s:.2f} (stop@ST={stop_s:.0f}, tp-{tp_mult}×ATR) ✓",
                ],
                "reason": f"{tag} | RSI={rsi_now:.0f} vol={vr:.1f}x htf={htf} RR={rr_s:.2f}",
            }

        reasons = []
        if not (st_bull or st_x_up):   reasons.append(f"ST {'bear' if st_bear else '?'}")
        if not (macd_x_bull or macd_accel_bull):
            reasons.append(f"MACD {lh:+.5f}")
        if not rsi_ok:   reasons.append(f"RSI {rsi_now:.0f} hors [{rsi_min:.0f}–{rsi_max:.0f}]")
        if vr < vol_min: reasons.append(f"Vol {vr:.1f}x < {vol_min}x")
        if htf < 0 and st_bull: reasons.append("HTF baissier")
        return self._none(" | ".join(reasons) or "Conditions non réunies")

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
