"""Stratégie Yoyo — deux bougies consécutives de même couleur.

Règle d'entrée :
  - LONG  : close[i-1] > open[i-1]  ET  close[i] > open[i]   (2 vertes)
  - SHORT : close[i-1] < open[i-1]  ET  close[i] < open[i]   (2 rouges)

Stop loss dynamique ATR (serré, adapté 1h).
"""

import logging
from typing import Any, Dict

import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import pre_val

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "yoyo"

    param_space: Dict[str, Any] = {
        "atr_mult_sl":    [0.8, 1.0, 1.2, 1.5, 1.8],
        "min_body_pct":   [0.0, 0.001, 0.002, 0.003],
        "atr_filter":     [0.0, 0.001, 0.002, 0.003],
        "score_threshold":[0.55, 0.60, 0.65],
    }

    fixed_params: Dict[str, Any] = {}

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})
        atr_mult_sl    = float(p.get("atr_mult_sl",  1.2))
        min_body_pct   = float(p.get("min_body_pct", 0.001))
        atr_filter     = float(p.get("atr_filter",   0.001))

        if len(df) < 3:
            return self._none("Données insuffisantes")

        c_now  = float(df["close"][-1])
        o_now  = float(df["open"][-1])
        c_prev = float(df["close"][-2])
        o_prev = float(df["open"][-2])
        atr_v  = pre_val(df, "_pre_atr14") or 0.0

        if c_now <= 0 or atr_v <= 0:
            return self._none("ATR ou prix invalide")

        # Filtre volatilité minimale — évite les barres plates
        if atr_filter > 0 and atr_v / c_now < atr_filter:
            return self._none(f"ATR trop faible ({atr_v/c_now:.4%} < {atr_filter:.4%})")

        body_now  = c_now  - o_now
        body_prev = c_prev - o_prev

        # Filtre taille de corps : évite les doji
        if min_body_pct > 0:
            if abs(body_now)  / c_now  < min_body_pct:
                return self._none("Corps barre actuelle trop petit")
            if abs(body_prev) / c_prev < min_body_pct:
                return self._none("Corps barre précédente trop petit")

        green_now  = body_now  > 0
        green_prev = body_prev > 0
        red_now    = body_now  < 0
        red_prev   = body_prev < 0

        if green_now and green_prev:
            side = "long"
            stop = c_now - atr_mult_sl * atr_v
            # Force du signal : amplitude des deux corps / ATR
            strength = (body_now + body_prev) / atr_v
        elif red_now and red_prev:
            side = "short"
            stop = c_now + atr_mult_sl * atr_v
            strength = (abs(body_now) + abs(body_prev)) / atr_v
        else:
            return self._none("Pas de pattern 2-bougies consécutives")

        # Score [0.55 – 0.94], bonus confiance issu de la force des corps
        score = round(min(0.55 + strength * 0.05, 0.94), 3)

        return {
            "score":     score,
            "side":      side,
            "name":      self.name,
            "atr":       atr_v,
            "stop_hint": round(stop, 2),
            "indicators": {
                "body_now":   round(body_now, 4),
                "body_prev":  round(body_prev, 4),
                "atr":        round(atr_v, 4),
                "strength":   round(strength, 3),
            },
            "conditions": [
                f"Bougie N-1 : {'verte' if green_prev else 'rouge'} (corps {body_prev:+.2f})",
                f"Bougie N   : {'verte' if green_now  else 'rouge'} (corps {body_now:+.2f})",
                f"ATR={atr_v:.2f} | SL={atr_mult_sl:.1f}×ATR",
                f"Force signal : {strength:.2f}×ATR",
            ],
            "reason": f"Yoyo {side.upper()} | 2 bougies {'vertes' if side == 'long' else 'rouges'} | force={strength:.2f}",
        }

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
