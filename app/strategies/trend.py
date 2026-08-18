"""Stratégie Trend Following — tendance EMA + ADX + MACD avec filtres volume et R:R."""

import logging
from typing import Any, Dict, List

import polars as pl

from app.core.indicators import ema_window, htf_trend, market_structure, pre_val
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "trend"

    timeframes: List[str] = ["1h", "1d"]

    param_space: Dict[str, List] = {
        "ema_fast":       [13, 21, 34],
        "ema_slow":       [50, 80],
        "adx_min":        [20, 22, 25, 28],
        "rsi_low":        [28, 30, 33],
        "rsi_high":       [67, 70, 73],
        "vol_min":        [0.9, 1.0, 1.2],
        "cross_lookback": [4, 6, 8],
        "overext_pct":    [0.012, 0.015, 0.020],
        "ema200_tol":     [0.025, 0.035, 0.045],
        "cooldown":       [15, 20, 25],
        "rr_min":         [1.3, 1.5, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count:  Dict[str, int] = {}
        # Réutilisation causale des EMA custom (ema_fast/slow hors _pre_ema*).
        self._bt_full_df = None
        self._ema_cache:  Dict[tuple, Any] = {}
        # Série htf_trend pré-calculée une fois par df de backtest (O(n²)→O(n)).
        self._htf_cache:  Dict[tuple, Any] = {}

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Mémorise le df complet pour réutiliser les EMA causales (O(n²)→O(n))."""
        self._bt_full_df = df

    def min_bars_required(self, params: dict | None = None) -> int:
        p = (params or {}).get("trend", {})
        ema_trend = int(p.get("ema_trend", 200))
        return max(ema_trend + 10, 220)

    def score(self, df: pl.DataFrame, params: dict | None = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("trend", {})

        ema_fast       = int(p.get("ema_fast",       21))
        ema_slow       = int(p.get("ema_slow",        50))
        ema_trend      = int(p.get("ema_trend",      200))
        adx_min        = float(p.get("adx_min",       22))
        vol_min        = float(p.get("vol_min",       1.0))
        cross_lookback = int(p.get("cross_lookback",   6))
        cooldown       = int(p.get("cooldown",         20))
        overext_pct    = float(p.get("overext_pct",  0.015))
        ema200_tol     = float(p.get("ema200_tol",   0.035))
        rsi_low        = float(p.get("rsi_low",       30))
        rsi_high       = float(p.get("rsi_high",      70))
        rr_min         = float(p.get("rr_min",        1.5))

        if ema_fast >= ema_slow:
            return self._none(f"ema_fast ({ema_fast}) doit être < ema_slow ({ema_slow})")

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
        _ema_col_s = _ema_map.get(ema_slow)
        ema_f = df[_ema_col_f] if (_ema_col_f and _ema_col_f in df.columns) \
            else ema_window(df, ema_fast, full_df=self._bt_full_df, cache=self._ema_cache)
        ema_s = df[_ema_col_s] if (_ema_col_s and _ema_col_s in df.columns) \
            else ema_window(df, ema_slow, full_df=self._bt_full_df, cache=self._ema_cache)

        lf    = float(ema_f[-1])
        ls    = float(ema_s[-1])
        lt    = pre_val(df, _ema_map.get(ema_trend, "")) or float(
            ema_window(df, ema_trend, full_df=self._bt_full_df, cache=self._ema_cache)[-1])
        c_now = float(close[-1])

        atr_val = float(df["_pre_atr14"][-1])
        if atr_val <= 0:
            return self._none()

        rsi_now = float(df["_pre_rsi14"][-1])
        adx_val = float(df["_pre_adx14"][-1])
        vr      = float(df["_pre_volratio20"][-1])
        ms_window = int(p.get("market_struct_window", 5))
        struct  = market_structure(high, low, window=ms_window)
        htf     = htf_trend(df_htf, df_ltf=df,
                            full_df=self._bt_full_df, cache=self._htf_cache)

        # MACD pour confirmation du momentum
        lh = float(df["_pre_macd_hist"][-1])
        ph = float(df["_pre_macd_hist"][-2]) if len(df) > 1 else 0.0
        macd_bull = lh > 0 and lh >= ph
        macd_bear = lh < 0 and lh <= ph
        macd_x_bull = ph < 0 and lh > 0
        macd_x_bear = ph > 0 and lh < 0

        # Pente EMA fast normalisée par ATR
        slope_atr = (lf - float(ema_f[-4])) / atr_val

        # Cross EMA récent
        cross_bull = cross_bear = False
        for k in range(1, min(cross_lookback + 1, len(df) - 2)):
            pf = float(ema_f[-1 - k])
            ps = float(ema_s[-1 - k])
            if pf <= ps and lf > ls:
                cross_bull = True
                break
            if pf >= ps and lf < ls:
                cross_bear = True
                break

        # Overextension
        overextended = (
            (lf > ls and (c_now - lf) / lf > overext_pct) or
            (lf < ls and (lf - c_now) / lf > overext_pct)
        )

        if cnt - self._last_signal.get(sym, -999) < cooldown:
            return self._none("Cooldown")

        indicators = {
            "ema_fast": round(lf, 2), "ema_slow": round(ls, 2), "ema200": round(lt, 2),
            "rsi": round(rsi_now, 1), "adx": round(adx_val, 1),
            "vol_ratio": round(vr, 2), "slope_atr": round(slope_atr, 4),
            "macd_hist": round(lh, 6), "overextended": overextended,
            "struct": struct, "htf_trend": htf,
        }

        # ═══ LONG ════════════════════════════════════════════════════════════
        cond_ema   = lf > ls
        cond_trend = c_now >= lt * (1 - ema200_tol)
        cond_macd  = macd_bull or macd_x_bull
        cond_entry = cross_bull or (slope_atr > 0.04 and cond_macd)
        cond_adx   = adx_val >= adx_min
        cond_rsi   = rsi_low <= rsi_now <= rsi_high
        cond_vol   = vr >= vol_min
        cond_noext = not overextended
        htf_ok     = htf >= 0

        if all([cond_ema, cond_trend, cond_entry, cond_adx,
                cond_rsi, cond_vol, cond_noext, cond_macd, htf_ok]):

            stop_l  = lf - atr_val * 1.5
            risk_l  = c_now - stop_l
            if risk_l <= 0:
                return self._none("Stop invalide")
            # Cible = max(plus haut 20 barres, prix courant + risque × rr_min)
            # Évite R:R=0 quand le prix est proche de ses récents sommets
            recent_high  = float(high[-20:].max())
            atr_target_l = c_now + risk_l * rr_min
            target_l     = max(recent_high, atr_target_l)
            reward_l = target_l - c_now
            rr_l = reward_l / risk_l if risk_l > 0 else 0.0
            if rr_l < rr_min:
                return self._none(f"R:R {rr_l:.2f} < {rr_min}")

            ema_spread   = (lf - ls) / ls * 100
            adx_norm     = min((adx_val - adx_min) / 20, 1.0)
            rsi_opt      = max(1.0 - abs(rsi_now - 52) / 20, 0)
            vol_b        = min((vr - vol_min) * 0.04, 0.08)
            cross_b      = 0.07 if cross_bull else 0.0
            macd_x_b     = 0.05 if macd_x_bull else 0.0
            struct_b     = 0.04 if struct > 0 else 0.0
            htf_b        = 0.05 if htf > 0 else 0.0
            slope_b      = min(slope_atr * 0.04, 0.06)

            score = min(
                0.60
                + min(ema_spread * 4, 0.10)
                + adx_norm * 0.10
                + rsi_opt  * 0.06
                + vol_b + cross_b + macd_x_b + struct_b + htf_b + slope_b,
                0.94
            )
            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "long", "name": self.name,
                "atr": atr_val, "stop_hint": round(stop_l, 2),
                "indicators": indicators,
                "conditions": [
                    f"EMA{ema_fast}({lf:.0f}) > EMA{ema_slow}({ls:.0f}) ✓",
                    f"Prix({c_now:.0f}) ≥ EMA200({lt:.0f})×{1-ema200_tol:.2f} ✓",
                    f"MACD hist {ph:+.5f}→{lh:+.5f} {'cross↑' if macd_x_bull else 'bull'} ✓",
                    f"ADX {adx_val:.0f} ≥ {adx_min} | RSI {rsi_now:.0f} | Vol {vr:.2f}x ✓",
                    f"HTF: {'haussier' if htf>0 else 'neutre'} | Struct: {'HH/HL ✓' if struct>0 else 'neutre'}",
                ],
                "reason": (
                    f"Trend LONG {'cross' if cross_bull else 'cont'} | "
                    f"MACD {'x↑' if macd_x_bull else 'bull'} | "
                    f"ADX={adx_val:.0f} RSI={rsi_now:.0f} vol={vr:.1f}x"
                ),
            }

        # ═══ SHORT ═══════════════════════════════════════════════════════════
        cond_ema_s   = lf < ls
        cond_trend_s = c_now <= lt * (1 + ema200_tol)
        cond_macd_s  = macd_bear or macd_x_bear
        cond_entry_s = cross_bear or (slope_atr < -0.04 and cond_macd_s)
        cond_rsi_s   = (100 - rsi_high) <= rsi_now <= (100 - rsi_low)
        cond_noext_s = not overextended
        htf_ok_s     = htf <= 0

        if all([cond_ema_s, cond_trend_s, cond_entry_s, cond_adx,
                cond_rsi_s, cond_vol, cond_noext_s, cond_macd_s, htf_ok_s]):

            stop_s   = lf + atr_val * 1.5
            risk_s   = stop_s - c_now
            if risk_s <= 0:
                return self._none("Stop invalide (short)")
            # Cible = min(plus bas 20 barres, prix courant - risque × rr_min)
            recent_low   = float(low[-20:].min())
            atr_target_s = c_now - risk_s * rr_min
            target_s     = min(recent_low, atr_target_s)
            reward_s     = max(c_now - target_s, 0.0)
            rr_s = reward_s / risk_s if risk_s > 0 else 0.0
            if rr_s < rr_min:
                return self._none(f"R:R short {rr_s:.2f} < {rr_min}")

            ema_spread_s = (ls - lf) / ls * 100
            adx_norm_s   = min((adx_val - adx_min) / 20, 1.0)
            rsi_opt_s    = max(1.0 - abs(rsi_now - 48) / 20, 0)
            vol_b_s      = min((vr - vol_min) * 0.04, 0.08)
            cross_b_s    = 0.07 if cross_bear else 0.0
            macd_x_b_s   = 0.05 if macd_x_bear else 0.0
            struct_b_s   = 0.04 if struct < 0 else 0.0
            htf_b_s      = 0.05 if htf < 0 else 0.0

            score = min(
                0.60
                + min(ema_spread_s * 4, 0.10)
                + adx_norm_s * 0.10
                + rsi_opt_s  * 0.06
                + vol_b_s + cross_b_s + macd_x_b_s + struct_b_s + htf_b_s,
                0.93
            )
            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3), "side": "short", "name": self.name,
                "atr": atr_val, "stop_hint": round(stop_s, 2),
                "indicators": indicators,
                "conditions": [
                    f"EMA{ema_fast}({lf:.0f}) < EMA{ema_slow}({ls:.0f}) ✓",
                    f"MACD hist {lh:+.5f} {'cross↓' if macd_x_bear else 'bear'} ✓",
                    f"ADX {adx_val:.0f} | RSI {rsi_now:.0f} | Vol {vr:.2f}x ✓",
                ],
                "reason": (
                    f"Trend SHORT {'cross' if cross_bear else 'cont'} | "
                    f"MACD {'x↓' if macd_x_bear else 'bear'} | "
                    f"ADX={adx_val:.0f} RSI={rsi_now:.0f} vol={vr:.1f}x"
                ),
            }

        missing = []
        if not cond_ema:
            missing.append("EMA align ✗")
        if not cond_trend:
            missing.append("EMA200 ✗")
        if not cond_macd:
            missing.append(f"MACD ✗ ({lh:+.5f})")
        if not cond_adx:
            missing.append(f"ADX {adx_val:.0f}<{adx_min} ✗")
        if not cond_rsi:
            missing.append(f"RSI {rsi_now:.0f} ✗")
        if overextended:
            missing.append("Overext ✗")
        if htf < 0 and lf > ls:
            missing.append("HTF baissier ✗")
        return self._none(" | ".join(missing) or "Conditions non réunies")

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
