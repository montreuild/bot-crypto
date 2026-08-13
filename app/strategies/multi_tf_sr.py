"""Stratégie Multi-Timeframe Support/Résistance — entrées sur niveaux S/R avec filtre HTF."""

import logging
from typing import Any, Dict, List

import polars as pl

from app.core.indicators import (
    adx_val as calc_adx,
)
from app.core.indicators import (
    atr_val as calc_atr,
)
from app.core.indicators import (
    htf_trend,
    macd_hist_last3,
    nearest_resistance,
    nearest_support,
    pre_val,
    support_resistance_levels,
)
from app.core.indicators import (
    rsi as calc_rsi,
)
from app.core.indicators import (
    vol_ratio as calc_vol,
)
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "multi_tf_sr"

    timeframes: List[str] = ["15m", "1h", "4h"]

    param_space: Dict[str, List] = {
        "sr_window":        [3, 5, 7],
        "sr_lookback":      [100, 150, 200],
        "sr_cluster_pct":   [0.003, 0.005, 0.008],
        "sr_min_touches":   [1, 2],
        "sr_proximity_atr": [0.8, 1.0, 1.5, 2.0],
        "adx_min":          [18, 20, 25],
        "rsi_low":          [30, 35, 40],
        "rsi_high":         [60, 65, 70],
        "vol_min":          [0.7, 0.8, 1.0],
        "cooldown":         [10, 15, 20],
        "rr_min":           [1.3, 1.5, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count:  Dict[str, int] = {}
        # Réutilisation causale de l'histogramme MACD si params non par défaut.
        self._bt_full_df = None
        self._macd_cache: Dict[tuple, Any] = {}

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Mémorise le df complet pour réutiliser l'histogramme MACD (causal)."""
        self._bt_full_df = df

    def min_bars_required(self, params: dict = None) -> int:
        p        = (params or {}).get("multi_tf_sr", {})
        lookback = int(p.get("sr_lookback", 150))
        window   = int(p.get("sr_window",   5))
        return max(lookback + window * 2 + 10, 60)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("multi_tf_sr", {})

        # ── Paramètres ────────────────────────────────────────────────────────
        sr_window        = int(p.get("sr_window",        5))
        sr_lookback      = int(p.get("sr_lookback",    150))
        sr_cluster_pct   = float(p.get("sr_cluster_pct", 0.005))
        sr_min_touches   = int(p.get("sr_min_touches",   1))
        sr_proximity_atr = float(p.get("sr_proximity_atr", 1.0))
        adx_min          = float(p.get("adx_min",        20.0))
        rsi_low          = float(p.get("rsi_low",        35.0))
        rsi_high         = float(p.get("rsi_high",       65.0))
        vol_min          = float(p.get("vol_min",         0.8))
        cooldown         = int(p.get("cooldown",         15))
        rr_min           = float(p.get("rr_min",          1.5))

        if rsi_low >= rsi_high:
            return self._none(f"rsi_low ({rsi_low}) doit être < rsi_high ({rsi_high})")

        sym = symbol or "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        min_bars = max(sr_lookback + sr_window * 2 + 10, 60)
        if len(df) < min_bars:
            return self._none(f"Données insuffisantes: {len(df)}/{min_bars}")

        if cnt - self._last_signal.get(sym, -999) < cooldown:
            return self._none("Cooldown")

        close = df["close"]
        c_now = float(close[-1])

        # ── Indicateurs de base ───────────────────────────────────────────────
        atr_val = pre_val(df, "_pre_atr14") or calc_atr(df, 14)
        if atr_val <= 0:
            return self._none("ATR invalide")

        rsi_now = pre_val(df, "_pre_rsi14") or float(calc_rsi(close, 14)[-1])
        adx_val = pre_val(df, "_pre_adx14") or calc_adx(df, 14)
        vr      = pre_val(df, "_pre_volratio20") or calc_vol(df)

        # MACD(12,26,9) — colonne pré-calculée O(1), sinon série causale réutilisée.
        lh = pre_val(df, "_pre_macd_hist")
        if lh is None:
            lh, ph, _ = macd_hist_last3(
                df, 12, 26, 9, full_df=self._bt_full_df, cache=self._macd_cache)
        else:
            ph_s = df["_pre_macd_hist"]
            ph   = float(ph_s[-2]) if len(ph_s) > 1 else 0.0

        # Momentum : MACD qui tourne (signal plus tôt qu'un cross complet)
        macd_turning_bull = lh > ph and lh > -atr_val * 0.005
        macd_turning_bear = lh < ph and lh <  atr_val * 0.005

        # ── Tendance HTF ──────────────────────────────────────────────────────
        htf = htf_trend(df_htf, df_ltf=df)

        # ── S/R sur le TF courant ─────────────────────────────────────────────
        sr = support_resistance_levels(
            df,
            window      = sr_window,
            cluster_pct = sr_cluster_pct,
            min_touches = sr_min_touches,
            max_levels  = 6,
            lookback    = sr_lookback,
        )
        supports    = sr["supports"]
        resistances = sr["resistances"]

        nearest_sup = nearest_support(c_now, supports)
        nearest_res = nearest_resistance(c_now, resistances)

        # ── S/R sur le HTF (confluence) ───────────────────────────────────────
        htf_near_sup = False
        htf_near_res = False
        if df_htf is not None and len(df_htf) >= sr_window * 2 + 5:
            htf_sr = support_resistance_levels(
                df_htf,
                window      = sr_window,
                cluster_pct = sr_cluster_pct * 1.5,   # tolérance plus large sur HTF
                min_touches = sr_min_touches,
                max_levels  = 4,
                lookback    = min(100, len(df_htf) - 1),
            )
            htf_near_sup = any(
                abs(c_now - lvl["price"]) <= atr_val * sr_proximity_atr * 2.0
                for lvl in htf_sr["supports"]
            )
            htf_near_res = any(
                abs(c_now - lvl["price"]) <= atr_val * sr_proximity_atr * 2.0
                for lvl in htf_sr["resistances"]
            )

        indicators = {
            "rsi":                round(rsi_now, 1),
            "adx":                round(adx_val, 1),
            "vol_ratio":          round(vr, 2),
            "macd_hist":          round(lh, 6),
            "htf_trend":          htf,
            "nearest_support":    round(nearest_sup, 4) if nearest_sup else None,
            "nearest_resistance": round(nearest_res, 4) if nearest_res else None,
            "n_supports":         len(supports),
            "n_resistances":      len(resistances),
            "htf_near_sup":       htf_near_sup,
            "htf_near_res":       htf_near_res,
        }

        # ═══ LONG : rebond sur support ════════════════════════════════════════
        if nearest_sup is not None:
            dist_sup = c_now - nearest_sup
            near_sup = dist_sup <= atr_val * sr_proximity_atr

            if (near_sup
                    and htf >= 0           # HTF haussier ou neutre
                    and vr >= vol_min
                    and adx_val >= adx_min
                    and rsi_low <= rsi_now <= rsi_high
                    and (macd_turning_bull or lh > 0)):

                stop_l = nearest_sup - atr_val * 0.5
                risk_l = c_now - stop_l
                if risk_l <= 0:
                    return self._none("Risk long invalide")

                target_l = nearest_res if nearest_res else c_now + risk_l * rr_min
                reward_l = max(target_l - c_now, 0.0)
                rr_l     = reward_l / risk_l if risk_l > 0 else 0.0
                if rr_l < rr_min:
                    return self._none(f"R:R long {rr_l:.2f} < {rr_min}")

                sup_strength = next(
                    (lvl["strength"] for lvl in supports
                     if abs(lvl["price"] - nearest_sup) < atr_val * 0.1), 1
                )

                htf_b      = 0.08 if htf > 0 else 0.0
                htf_conf_b = 0.06 if htf_near_sup else 0.0
                strength_b = min(sup_strength * 0.025, 0.08)
                rsi_b      = max(1.0 - abs(rsi_now - 50) / 20, 0) * 0.05
                macd_b     = 0.05 if (ph < 0 < lh) else (0.03 if macd_turning_bull else 0.0)
                adx_b      = min((adx_val - adx_min) / 30, 1.0) * 0.05
                prox_b     = max(0.0, 1.0 - dist_sup / (atr_val * sr_proximity_atr)) * 0.04

                score = min(
                    0.60 + htf_b + htf_conf_b + strength_b + rsi_b + macd_b + adx_b + prox_b,
                    0.94,
                )
                self._last_signal[sym] = cnt
                return {
                    "score":      round(score, 3),
                    "side":       "long",
                    "name":       self.name,
                    "atr":        atr_val,
                    "stop_hint":  round(stop_l, 4),
                    "indicators": indicators,
                    "conditions": [
                        f"Support {nearest_sup:.4f} (force ×{sup_strength}) ✓",
                        f"Distance: {dist_sup / atr_val:.2f} ATR ≤ {sr_proximity_atr} ✓",
                        f"HTF: {'haussier ✓' if htf > 0 else 'neutre ✓'}"
                        + (" + confluence HTF ✓" if htf_near_sup else ""),
                        f"RSI {rsi_now:.0f} | ADX {adx_val:.0f} | Vol {vr:.1f}× ✓",
                        f"R:R {rr_l:.2f} | Target {target_l:.4f} ✓",
                    ],
                    "reason": (
                        f"MTF-SR LONG rebond support {nearest_sup:.4f} (×{sup_strength}) | "
                        f"HTF={'↑' if htf > 0 else '='}"
                        f"{'+HTF' if htf_near_sup else ''} | "
                        f"RSI={rsi_now:.0f} ADX={adx_val:.0f} R:R={rr_l:.2f}"
                    ),
                }

        # ═══ SHORT : rejet sur résistance ════════════════════════════════════
        if nearest_res is not None:
            dist_res = nearest_res - c_now
            near_res = dist_res <= atr_val * sr_proximity_atr

            if (near_res
                    and htf <= 0           # HTF baissier ou neutre
                    and vr >= vol_min
                    and adx_val >= adx_min
                    and rsi_low <= rsi_now <= rsi_high
                    and (macd_turning_bear or lh < 0)):

                stop_s = nearest_res + atr_val * 0.5
                risk_s = stop_s - c_now
                if risk_s <= 0:
                    return self._none("Risk short invalide")

                target_s = nearest_sup if nearest_sup else c_now - risk_s * rr_min
                reward_s = max(c_now - target_s, 0.0)
                rr_s     = reward_s / risk_s if risk_s > 0 else 0.0
                if rr_s < rr_min:
                    return self._none(f"R:R short {rr_s:.2f} < {rr_min}")

                res_strength = next(
                    (lvl["strength"] for lvl in resistances
                     if abs(lvl["price"] - nearest_res) < atr_val * 0.1), 1
                )

                htf_b      = 0.08 if htf < 0 else 0.0
                htf_conf_b = 0.06 if htf_near_res else 0.0
                strength_b = min(res_strength * 0.025, 0.08)
                rsi_b      = max(1.0 - abs(rsi_now - 50) / 20, 0) * 0.05
                macd_b     = 0.05 if (ph > 0 > lh) else (0.03 if macd_turning_bear else 0.0)
                adx_b      = min((adx_val - adx_min) / 30, 1.0) * 0.05
                prox_b     = max(0.0, 1.0 - dist_res / (atr_val * sr_proximity_atr)) * 0.04

                score = min(
                    0.60 + htf_b + htf_conf_b + strength_b + rsi_b + macd_b + adx_b + prox_b,
                    0.94,
                )
                self._last_signal[sym] = cnt
                return {
                    "score":      round(score, 3),
                    "side":       "short",
                    "name":       self.name,
                    "atr":        atr_val,
                    "stop_hint":  round(stop_s, 4),
                    "indicators": indicators,
                    "conditions": [
                        f"Résistance {nearest_res:.4f} (force ×{res_strength}) ✓",
                        f"Distance: {dist_res / atr_val:.2f} ATR ≤ {sr_proximity_atr} ✓",
                        f"HTF: {'baissier ✓' if htf < 0 else 'neutre ✓'}"
                        + (" + confluence HTF ✓" if htf_near_res else ""),
                        f"RSI {rsi_now:.0f} | ADX {adx_val:.0f} | Vol {vr:.1f}× ✓",
                        f"R:R {rr_s:.2f} | Target {target_s:.4f} ✓",
                    ],
                    "reason": (
                        f"MTF-SR SHORT rejet résistance {nearest_res:.4f} (×{res_strength}) | "
                        f"HTF={'↓' if htf < 0 else '='}"
                        f"{'+HTF' if htf_near_res else ''} | "
                        f"RSI={rsi_now:.0f} ADX={adx_val:.0f} R:R={rr_s:.2f}"
                    ),
                }

        return self._none("Pas de niveau S/R proche ou conditions non réunies")

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
