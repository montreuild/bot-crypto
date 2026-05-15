"""Stratégie Yoyo — deux bougies consécutives de même couleur.

Règle d'entrée :
  - LONG  : close[i-1] > open[i-1]  ET  close[i] > open[i]   (2 vertes)
  - SHORT : close[i-1] < open[i-1]  ET  close[i] < open[i]   (2 rouges)

Stop loss très serré : open de la dernière bougie.
  - LONG  : SL = open[i] — si on retombe sous l'open, la continuation est morte
  - SHORT : SL = open[i] — si on remonte au-dessus, idem
Soit le mouvement se poursuit, soit on coupe immédiatement.
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
        "sl_buffer_pct":   [0.0, 0.0005, 0.001, 0.002],  # marge sous/au-dessus de l'open
        "score_threshold": [0.55, 0.60, 0.65],
    }

    fixed_params: Dict[str, Any] = {}

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})
        sl_buffer_pct = float(p.get("sl_buffer_pct", 0.0005))

        if len(df) < 3:
            return self._none("Données insuffisantes")

        c_now  = float(df["close"][-1])
        o_now  = float(df["open"][-1])
        c_prev = float(df["close"][-2])
        o_prev = float(df["open"][-2])
        atr_v  = pre_val(df, "_pre_atr14") or 0.0

        if c_now <= 0:
            return self._none("Prix invalide")

        body_now  = c_now  - o_now
        body_prev = c_prev - o_prev

        green_now  = body_now  > 0
        green_prev = body_prev > 0
        red_now    = body_now  < 0
        red_prev   = body_prev < 0

        if green_now and green_prev:
            side = "long"
            stop = o_now * (1.0 - sl_buffer_pct)
            strength = (body_now + body_prev) / max(atr_v, 1e-9) if atr_v > 0 else 1.0
        elif red_now and red_prev:
            side = "short"
            stop = o_now * (1.0 + sl_buffer_pct)
            strength = (abs(body_now) + abs(body_prev)) / max(atr_v, 1e-9) if atr_v > 0 else 1.0
        else:
            return self._none("Pas de pattern 2-bougies consécutives")

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
                "open_now":   round(o_now, 4),
                "atr":        round(atr_v, 4),
                "strength":   round(strength, 3),
            },
            "conditions": [
                f"Bougie N-1 : {'verte' if green_prev else 'rouge'} (corps {body_prev:+.2f})",
                f"Bougie N   : {'verte' if green_now  else 'rouge'} (corps {body_now:+.2f})",
                f"SL = open[N] ({o_now:.2f}) {'-' if side == 'long' else '+'} {sl_buffer_pct:.2%}",
                f"Force signal : {strength:.2f}×ATR",
            ],
            "reason": (
                f"Yoyo {side.upper()} | 2 bougies "
                f"{'vertes' if side == 'long' else 'rouges'} | "
                f"SL={stop:.2f} (open±{sl_buffer_pct:.2%})"
            ),
        }

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
