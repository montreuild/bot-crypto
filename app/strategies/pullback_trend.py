"""Stratégie Pullback Trend — entrée en pullback vers EMA fast dans une tendance confirmée."""

import logging
from typing import Any, Dict, List

import polars as pl

from app.core.indicators import ema_window, htf_trend, market_structure, pre_val
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "pullback_trend"

    timeframes: List[str] = ["15m", "1h", "1d"]

    param_space: Dict[str, List] = {
        "ema_fast":       [13, 21, 34],
        "ema_mid":        [50],
        "ema_slow":       [100],
        "adx_min":        [18, 20, 22, 25],
        "pb_zone_pct":    [0.003, 0.005, 0.007],
        "rsi_pb_low":     [30, 33, 38],
        "rsi_pb_high":    [55, 58, 62],
        "vol_min":        [0.7, 0.8, 1.0],
        "cooldown":       [15, 20, 25],
        "rr_min":         [1.3, 1.5, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count: Dict[str, int] = {}
        # Réutilisation causale des EMA custom (hors colonnes _pre_ema*).
        self._bt_full_df = None
        self._ema_cache:  Dict[tuple, Any] = {}
        # Série htf_trend pré-calculée une fois par df de backtest (O(n²)→O(n)).
        self._htf_cache:  Dict[tuple, Any] = {}

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Mémorise le df complet pour réutiliser les EMA causales (O(n²)→O(n))."""
        self._bt_full_df = df

    def min_bars_required(self, params: dict | None = None) -> int:
        p = (params or {}).get("pullback_trend", {})
        ema_trend = int(p.get("ema_trend", 200))
        return max(ema_trend + 10, 220)

    def score(self, df: pl.DataFrame, params: dict | None = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("pullback_trend", {})

        ema_fast   = int(p.get("ema_fast",    21))
        ema_mid    = int(p.get("ema_mid",     50))
        ema_slow   = int(p.get("ema_slow",   100))
        ema_trend  = int(p.get("ema_trend",  200))
        adx_min    = float(p.get("adx_min",   20))
        pb_zone    = float(p.get("pb_zone_pct", 0.005))
        rsi_pb_low = float(p.get("rsi_pb_low",  33))
        rsi_pb_high= float(p.get("rsi_pb_high", 58))
        vol_min    = float(p.get("vol_min",    0.8))
        cooldown   = int(p.get("cooldown",      20))
        rr_min     = float(p.get("rr_min",     1.5))

        if not (ema_fast < ema_mid < ema_slow):
            return self._none(f"EMAs doivent être ordonnées : fast({ema_fast}) < mid({ema_mid}) < slow({ema_slow})")

        sym = symbol or str(df["time"][-1]) if "time" in df.columns else "default"

        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        min_bars = max(ema_trend + 10, 220)
        if len(df) < min_bars:
            return self._none(f"EMA{ema_trend} requiert {min_bars} bougies min, {len(df)} disponibles")

        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        # ── EMAs ─────────────────────────────────────────────────────────────
        _ema_map = {20: "_pre_ema20", 50: "_pre_ema50", 200: "_pre_ema200"}
        _ema_col_f = _ema_map.get(ema_fast)
        ef  = df[_ema_col_f] if (_ema_col_f and _ema_col_f in df.columns) \
            else ema_window(df, ema_fast, full_df=self._bt_full_df, cache=self._ema_cache)

        lf  = float(ef[-1])
        lm  = pre_val(df, _ema_map.get(ema_mid, ""))   or float(
            ema_window(df, ema_mid,   full_df=self._bt_full_df, cache=self._ema_cache)[-1])
        ls  = pre_val(df, _ema_map.get(ema_slow, ""))  or float(
            ema_window(df, ema_slow,  full_df=self._bt_full_df, cache=self._ema_cache)[-1])
        lt  = pre_val(df, _ema_map.get(ema_trend, "")) or float(
            ema_window(df, ema_trend, full_df=self._bt_full_df, cache=self._ema_cache)[-1])
        c0  = float(close[-1])
        c1  = float(close[-2])

        # ── Indicateurs ──────────────────────────────────────────────────────
        _rsi_s    = df["_pre_rsi14"]
        rsi_now   = float(_rsi_s[-1])
        rsi_prev  = float(_rsi_s[-2])
        rsi_prev2 = float(_rsi_s[-3])
        adx_val   = float(df["_pre_adx14"][-1])
        atr_val   = float(df["_pre_atr14"][-1])
        if atr_val <= 0:
            return self._none()

        vr = float(df["_pre_volratio20"][-1])
        vol_prev = float(df["volume"][-2])
        vol_now  = float(df["volume"][-1])
        ms_window = int(p.get("market_struct_window", 5))
        struct   = market_structure(high, low, window=ms_window)
        htf      = htf_trend(df_htf, df_ltf=df,
                             full_df=self._bt_full_df, cache=self._htf_cache)

        # ── Cooldown par symbole ──────────────────────────────────────────────
        last = self._last_signal.get(sym, -999)
        if cnt - last < cooldown:
            return self._none(f"Cooldown {cnt - last}/{cooldown}")

        # ══════════════ LONG ══════════════════════════════════════════════════
        ema_bull   = lf > lm and lm > ls
        above_mid  = c0 > lm
        trend_ok   = c0 > lt * 0.96
        adx_ok     = adx_val >= adx_min
        htf_ok_long = htf >= 0

        if ema_bull and above_mid and adx_ok and trend_ok and htf_ok_long:

            pb_found = False
            pb_low   = float('inf')
            for k in range(1, 6):
                c_k   = float(close[-1 - k])
                ef_k  = float(ef[-1 - k])
                low_k = float(low[-1 - k])
                if abs(c_k - ef_k) / ef_k <= pb_zone * 1.2:
                    pb_found = True
                    pb_low   = min(pb_low, low_k)

            price_back  = c0 >= lf * (1 - pb_zone * 0.5)
            bull_bar    = c0 > c1

            rsi_rising  = (rsi_now >= rsi_pb_low and
                           rsi_now <= rsi_pb_high + 15 and
                           rsi_now > rsi_prev and
                           rsi_prev >= rsi_prev2 - 2)

            ef_slope  = (lf - float(ef[-4])) / lf * 100
            ema_up    = ef_slope > 0

            vol_ok    = vr >= vol_min and vol_now >= vol_prev * 0.85

            if pb_found and price_back and bull_bar and rsi_rising and ema_up and vol_ok:

                stop_nat  = pb_low - atr_val * 0.3
                stop_atr  = c0 - atr_val * 2.0
                stop      = max(stop_nat, stop_atr)
                risk_pts  = c0 - stop
                if risk_pts <= 0:
                    return self._none("Stop invalide")

                pb_depth  = (lf - pb_low) / lf * 100
                target    = c0 + risk_pts * 2.0
                rr        = (target - c0) / risk_pts

                if rr < rr_min:
                    return self._none(f"R:R {rr:.2f} < {rr_min}")

                adx_b    = min((adx_val - adx_min) / 20, 1.0) * 0.10
                rsi_b    = max(1.0 - abs(rsi_now - 48) / 22, 0) * 0.07
                vol_b    = min((vr - vol_min) * 0.05, 0.08)
                pb_b     = 0.07 if 0.15 <= pb_depth <= 1.5 else 0.02
                ema_b    = min((lf - lm) / lm * 300, 0.08)
                htf_b    = 0.05 if htf > 0 else 0.0
                struct_b = 0.04 if struct > 0 else 0.0
                rsi_b2   = 0.03 if rsi_now > rsi_prev + 2 else 0.0

                score = min(0.63 + adx_b + rsi_b + vol_b + pb_b + ema_b + htf_b + struct_b + rsi_b2, 0.94)

                self._last_signal[sym] = cnt
                return {
                    "score":      round(score, 3),
                    "side":       "long",
                    "name":       self.name,
                    "atr":        atr_val,
                    "stop_hint":  round(stop, 2),
                    "indicators": {
                        "ema_fast": round(lf, 2), "ema_mid": round(lm, 2),
                        "ema_slow": round(ls, 2), "ema200":  round(lt, 2),
                        "rsi":      round(rsi_now, 1), "adx":  round(adx_val, 1),
                        "vol_ratio":round(vr, 2), "pb_low":  round(pb_low, 2),
                        "pb_depth_pct": round(pb_depth, 3),
                        "ef_slope": round(ef_slope, 4), "rr":  round(rr, 2),
                        "htf_trend": htf, "struct": struct,
                    },
                    "conditions": [
                        f"EMA: {lf:.0f} > {lm:.0f} > {ls:.0f} ✓",
                        f"Pullback {pb_depth:.2f}% vers EMA{ema_fast} ✓",
                        f"RSI {rsi_now:.0f} en hausse ({rsi_prev:.0f}→{rsi_now:.0f}) ✓",
                        f"ADX {adx_val:.1f} | Vol {vr:.2f}x | R:R {rr:.2f} ✓",
                        f"HTF: {'haussier' if htf>0 else 'neutre'} | Struct: {'HH/HL' if struct>0 else 'neutre'}",
                    ],
                    "reason": (
                        f"Pullback LONG EMA{ema_fast} — depth={pb_depth:.2f}% "
                        f"RSI={rsi_now:.0f}↑ ADX={adx_val:.0f} vol={vr:.1f}x R:R={rr:.2f}"
                    ),
                }

        # ══════════════ SHORT ═════════════════════════════════════════════════
        ema_bear    = lf < lm and lm < ls
        below_mid   = c0 < lm
        trend_dn    = c0 < lt * 1.04
        htf_ok_short= htf <= 0

        if ema_bear and below_mid and adx_ok and trend_dn and htf_ok_short:

            rally_found = False
            rally_high  = 0.0
            for k in range(1, 6):
                c_k   = float(close[-1 - k])
                ef_k  = float(ef[-1 - k])
                h_k   = float(high[-1 - k])
                if abs(c_k - ef_k) / ef_k <= pb_zone * 1.2:
                    rally_found = True
                    rally_high  = max(rally_high, h_k)

            price_back_s   = c0 <= lf * (1 + pb_zone * 0.5)
            bear_bar       = c0 < c1
            rsi_falling    = (rsi_now <= (100 - rsi_pb_low) and
                              rsi_now < rsi_prev and
                              rsi_prev <= rsi_prev2 + 2)
            ef_down        = (lf - float(ef[-4])) / lf * 100 < 0
            vol_ok_s       = vr >= vol_min and vol_now >= vol_prev * 0.85

            if rally_found and price_back_s and bear_bar and rsi_falling and ef_down and vol_ok_s:

                stop_nat_s = rally_high + atr_val * 0.3
                stop_atr_s = c0 + atr_val * 2.0
                stop_s     = min(stop_nat_s, stop_atr_s)
                risk_s     = stop_s - c0
                if risk_s <= 0:
                    return self._none("Stop invalide (short)")
                target_s = c0 - risk_s * 2.0
                rr_s     = (c0 - target_s) / risk_s
                if rr_s < rr_min:
                    return self._none(f"R:R short {rr_s:.2f} < {rr_min}")

                adx_b    = min((adx_val - adx_min) / 20, 1.0) * 0.10
                rsi_b    = max(1.0 - abs(rsi_now - 52) / 22, 0) * 0.07
                vol_b    = min((vr - vol_min) * 0.05, 0.08)
                rd       = (rally_high - lf) / lf * 100
                pb_b     = 0.07 if 0.15 <= rd <= 1.5 else 0.02
                ema_b    = min((lm - lf) / lm * 300, 0.08)
                htf_b    = 0.05 if htf < 0 else 0.0
                struct_b = 0.04 if struct < 0 else 0.0

                score = min(0.62 + adx_b + rsi_b + vol_b + pb_b + ema_b + htf_b + struct_b, 0.93)

                self._last_signal[sym] = cnt
                return {
                    "score":      round(score, 3),
                    "side":       "short",
                    "name":       self.name,
                    "atr":        atr_val,
                    "stop_hint":  round(stop_s, 2),
                    "indicators": {
                        "ema_fast": round(lf, 2), "ema_mid": round(lm, 2),
                        "ema_slow": round(ls, 2), "ema200":  round(lt, 2),
                        "rsi":      round(rsi_now, 1), "adx": round(adx_val, 1),
                        "vol_ratio":round(vr, 2), "rally_high": round(rally_high, 2),
                        "htf_trend": htf,
                    },
                    "conditions": [
                        f"EMA: {lf:.0f} < {lm:.0f} < {ls:.0f} ✓",
                        f"Rally rejeté vers EMA{ema_fast} ✓",
                        f"RSI {rsi_now:.0f} en baisse ✓ | ADX {adx_val:.0f} | Vol {vr:.2f}x",
                    ],
                    "reason": (
                        f"Pullback SHORT EMA{ema_fast} — rally rejeté "
                        f"RSI={rsi_now:.0f}↓ ADX={adx_val:.0f} vol={vr:.1f}x"
                    ),
                }

        return self._none(
            f"EMA={'bull' if ema_bull else 'bear' if ema_bear else 'neutre'} "
            f"ADX={adx_val:.0f} RSI={rsi_now:.0f} vol={vr:.1f}x htf={htf}"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
