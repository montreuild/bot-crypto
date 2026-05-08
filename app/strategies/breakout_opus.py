"""Stratégie Breakout Opus — cassure Donchian(20) confirmée par flip de l'histogramme MACD.

Logique :
  Long  : close > plus-haut Donchian(period) ET histogramme MACD flip négatif → positif
  Short : close < plus-bas  Donchian(period) ET histogramme MACD flip positif → négatif

Risque (R:R = 1:3) :
  Stop-loss   : 1 × ATR du prix d'entrée
  Take-profit : 3 × ATR du prix d'entrée

Sortie sur signal opposé : gérée nativement par le moteur (un signal short ferme une
position longue ouverte, et inversement).
"""
import logging
from typing import Dict, Any, List

import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import macd as calc_macd, pre_val

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "breakout_opus"

    timeframes: List[str] = ["15m", "1h", "4h", "1d"]

    # Espace d'optimisation — petit pour rester proche de la spec d'origine
    param_space: Dict[str, List] = {
        "period":     [15, 20, 25, 30],
        "macd_fast":  [10, 12, 14],
        "macd_slow":  [22, 26, 30],
        "sl_atr":     [0.8, 1.0, 1.2],
        "tp_atr":     [2.5, 3.0, 3.5],
        "cooldown":   [3, 5, 8],
    }

    fixed_params: Dict[str, Any] = {
        "macd_signal": 9,
        "atr_period":  14,
    }

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count:  Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        period    = int(p.get("period",     20))
        macd_slow = int(p.get("macd_slow",  26))
        macd_sig  = int(p.get("macd_signal", 9))
        return max(period + 5, macd_slow + macd_sig + 10, 50)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})

        period      = int(p.get("period",       20))
        macd_fast   = int(p.get("macd_fast",    12))
        macd_slow   = int(p.get("macd_slow",    26))
        macd_sig_p  = int(p.get("macd_signal",   9))
        sl_atr      = float(p.get("sl_atr",      1.0))
        tp_atr      = float(p.get("tp_atr",      3.0))
        cooldown    = int(p.get("cooldown",      5))

        if macd_fast >= macd_slow:
            return self._none(f"macd_fast ({macd_fast}) >= macd_slow ({macd_slow})")

        sym = symbol or (str(df["time"][-1]) if "time" in df.columns else "default")
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        min_bars = self.min_bars_required(params)
        if len(df) < min_bars:
            return self._none(f"Données insuffisantes : {len(df)}/{min_bars}")

        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        c_now = float(close[-1])

        # ── Canal Donchian(period) ──────────────────────────────────────────────
        # On exclut la bougie courante : la cassure doit dépasser le plus haut/bas
        # des `period` bougies précédentes.
        highest = float(high[-(period + 1):-1].max())
        lowest  = float(low[-(period + 1):-1].min())

        # ── ATR — colonne pré-calculée ATR(14) ──────────────────────────────────
        atr_now = pre_val(df, "_pre_atr14")
        if atr_now is None or atr_now <= 0:
            return self._none("ATR invalide")

        # ── MACD histogramme — flip de signe sur la dernière bougie ─────────────
        # On utilise les colonnes pré-calculées (12,26,9) si compatibles, sinon
        # recalcul on-demand via calc_macd().
        if macd_fast == 12 and macd_slow == 26 and macd_sig_p == 9 \
                and "_pre_macd_hist" in df.columns:
            hist_now  = float(df["_pre_macd_hist"][-1])
            hist_prev = float(df["_pre_macd_hist"][-2])
        else:
            _, _, hist = calc_macd(close, macd_fast, macd_slow, macd_sig_p)
            hist_now  = float(hist[-1])
            hist_prev = float(hist[-2])

        macd_flip_bull = hist_prev <= 0 and hist_now > 0
        macd_flip_bear = hist_prev >= 0 and hist_now < 0

        # Cooldown anti-rebond entre signaux successifs
        if cnt - self._last_signal.get(sym, -10**9) < cooldown:
            return self._none("Cooldown")

        indicators = {
            "donchian_high": round(highest, 4),
            "donchian_low":  round(lowest, 4),
            "atr":           round(atr_now, 4),
            "macd_hist":     round(hist_now, 6),
            "macd_hist_prev": round(hist_prev, 6),
        }

        # ── LONG : cassure haute + flip MACD haussier ───────────────────────────
        if c_now > highest and macd_flip_bull:
            stop_l   = c_now - atr_now * sl_atr
            target_l = c_now + atr_now * tp_atr
            risk_l   = c_now - stop_l
            if risk_l <= 0:
                return self._none("Stop invalide")

            # Score : base 0.70, bonus selon force de la cassure et du flip
            penetration = (c_now - highest) / atr_now
            pen_b   = min(penetration * 0.05, 0.10)
            flip_b  = min(abs(hist_now - hist_prev) * 50, 0.10)
            score   = min(0.70 + pen_b + flip_b, 0.92)

            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3),
                "side":  "long",
                "name":  self.name,
                "atr":   atr_now,
                "stop_hint":   round(stop_l, 4),
                "target_hint": round(target_l, 4),
                "indicators":  indicators,
                "conditions": [
                    f"Close {c_now:.4f} > Donchian{period}H {highest:.4f} ✓",
                    f"MACD hist flip {hist_prev:+.5f} → {hist_now:+.5f} ✓",
                    f"SL = entry − {sl_atr}×ATR ({atr_now:.4f}) | "
                    f"TP = entry + {tp_atr}×ATR (R:R 1:{tp_atr/sl_atr:.1f})",
                ],
                "reason": (
                    f"Breakout Opus LONG D{period} pen={penetration:.2f}×ATR "
                    f"hist {hist_prev:+.4f}→{hist_now:+.4f}"
                ),
            }

        # ── SHORT : cassure basse + flip MACD baissier ──────────────────────────
        if c_now < lowest and macd_flip_bear:
            stop_s   = c_now + atr_now * sl_atr
            target_s = c_now - atr_now * tp_atr
            risk_s   = stop_s - c_now
            if risk_s <= 0:
                return self._none("Stop invalide")

            penetration = (lowest - c_now) / atr_now
            pen_b  = min(penetration * 0.05, 0.10)
            flip_b = min(abs(hist_now - hist_prev) * 50, 0.10)
            score  = min(0.70 + pen_b + flip_b, 0.92)

            self._last_signal[sym] = cnt
            return {
                "score": round(score, 3),
                "side":  "short",
                "name":  self.name,
                "atr":   atr_now,
                "stop_hint":   round(stop_s, 4),
                "target_hint": round(target_s, 4),
                "indicators":  indicators,
                "conditions": [
                    f"Close {c_now:.4f} < Donchian{period}L {lowest:.4f} ✓",
                    f"MACD hist flip {hist_prev:+.5f} → {hist_now:+.5f} ✓",
                    f"SL = entry + {sl_atr}×ATR ({atr_now:.4f}) | "
                    f"TP = entry − {tp_atr}×ATR (R:R 1:{tp_atr/sl_atr:.1f})",
                ],
                "reason": (
                    f"Breakout Opus SHORT D{period} pen={penetration:.2f}×ATR "
                    f"hist {hist_prev:+.4f}→{hist_now:+.4f}"
                ),
            }

        # ── Pas de signal ───────────────────────────────────────────────────────
        return self._none(
            f"Close {c_now:.4f} ∈ [{lowest:.4f}–{highest:.4f}] | "
            f"hist {hist_prev:+.5f}→{hist_now:+.5f} (pas de flip)"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
