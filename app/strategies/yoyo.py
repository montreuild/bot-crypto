"""Stratégie Yoyo — deux bougies consécutives de même couleur.

Règle d'entrée :
  - LONG  : close[i-1] > open[i-1]  ET  close[i] > open[i]   (2 vertes)
  - SHORT : close[i-1] < open[i-1]  ET  close[i] < open[i]   (2 rouges)

Filtre doji : corps < doji_ratio × range total → bougie rejetée.
  - Évite les signaux sur chandeliers indécis où la direction n'est pas franche.

Stop loss très serré : open de la dernière bougie.
  - LONG  : SL = open[i] — si on retombe sous l'open, la continuation est morte
  - SHORT : SL = open[i] — si on remonte au-dessus, idem
Soit le mouvement se poursuit, soit on coupe immédiatement.
"""

import logging
from typing import Any, Dict

import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import pre_val, htf_trend

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "yoyo"

    param_space: Dict[str, Any] = {
        "sl_buffer_pct":   [0.0, 0.0005, 0.001, 0.002],
        "doji_ratio":      [0.1, 0.15, 0.2, 0.25],  # corps min en % du range total
        "score_threshold": [0.55, 0.60, 0.65],
    }

    fixed_params: Dict[str, Any] = {}

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})
        sl_buffer_pct = float(p.get("sl_buffer_pct", 0.0005))
        doji_ratio    = float(p.get("doji_ratio", 0.15))

        if len(df) < 3:
            return self._none("Données insuffisantes")

        c_now  = float(df["close"][-1])
        o_now  = float(df["open"][-1])
        h_now  = float(df["high"][-1])
        l_now  = float(df["low"][-1])
        c_prev = float(df["close"][-2])
        o_prev = float(df["open"][-2])
        h_prev = float(df["high"][-2])
        l_prev = float(df["low"][-2])
        atr_v  = pre_val(df, "_pre_atr14") or 0.0

        if c_now <= 0:
            return self._none("Prix invalide")

        body_now  = c_now  - o_now
        body_prev = c_prev - o_prev
        range_now  = h_now  - l_now
        range_prev = h_prev - l_prev

        # Filtre doji : corps trop petit = bougie indécise
        if range_now > 0 and abs(body_now) / range_now < doji_ratio:
            return self._none(
                f"Doji N : corps {abs(body_now):.4f} < {doji_ratio:.0%}×range {range_now:.4f}"
            )
        if range_prev > 0 and abs(body_prev) / range_prev < doji_ratio:
            return self._none(
                f"Doji N-1 : corps {abs(body_prev):.4f} < {doji_ratio:.0%}×range {range_prev:.4f}"
            )

        green_now  = body_now  > 0
        green_prev = body_prev > 0
        red_now    = body_now  < 0
        red_prev   = body_prev < 0

        htf = htf_trend(df_htf)

        if green_now and green_prev:
            if htf < 0:
                return self._none("HTF baissier — LONG contre-tendance rejeté")
            side = "long"
            stop = o_now * (1.0 - sl_buffer_pct)
            strength = (body_now + body_prev) / max(atr_v, 1e-9) if atr_v > 0 else 1.0
        elif red_now and red_prev:
            if htf > 0:
                return self._none("HTF haussier — SHORT contre-tendance rejeté")
            side = "short"
            stop = o_now * (1.0 + sl_buffer_pct)
            strength = (abs(body_now) + abs(body_prev)) / max(atr_v, 1e-9) if atr_v > 0 else 1.0
        else:
            return self._none("Pas de pattern 2-bougies consécutives")

        score = round(min(0.55 + strength * 0.05, 0.94), 3)

        body_ratio_now  = abs(body_now)  / range_now  if range_now  > 0 else 1.0
        body_ratio_prev = abs(body_prev) / range_prev if range_prev > 0 else 1.0

        return {
            "score":     score,
            "side":      side,
            "name":      self.name,
            "atr":       atr_v,
            "stop_hint": round(stop, 6),
            "indicators": {
                "body_now":        round(body_now, 4),
                "body_prev":       round(body_prev, 4),
                "body_ratio_now":  round(body_ratio_now, 3),
                "body_ratio_prev": round(body_ratio_prev, 3),
                "open_now":        round(o_now, 4),
                "atr":             round(atr_v, 4),
                "strength":        round(strength, 3),
                "htf_trend":       htf,
            },
            "conditions": [
                f"Bougie N-1 : {'verte' if green_prev else 'rouge'} (corps {body_prev:+.2f}, ratio {body_ratio_prev:.0%})",
                f"Bougie N   : {'verte' if green_now  else 'rouge'} (corps {body_now:+.2f}, ratio {body_ratio_now:.0%})",
                f"Filtre doji : ratio min {doji_ratio:.0%} — OK",
                f"Tendance HTF : {'+1 haussier' if htf > 0 else '-1 baissier' if htf < 0 else '0 neutre'} — alignée ✓",
                f"SL = open[N] ({o_now:.2f}) ± {sl_buffer_pct:.2%} → {stop:.2f}",
                f"Force signal : {strength:.2f}×ATR",
            ],
            "reason": (
                f"Yoyo {side.upper()} | 2 bougies "
                f"{'vertes' if side == 'long' else 'rouges'} | "
                f"ratio corps {body_ratio_now:.0%}/{body_ratio_prev:.0%} | "
                f"SL={stop:.2f}"
            ),
        }

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
