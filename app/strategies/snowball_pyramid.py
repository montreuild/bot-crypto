"""Stratégie Snowball — TVR + pyramidage (effet boule de neige).

Hérite de tvr_trend (mêmes entrées : breakout Donchian filtré par régime 1D,
anti-chop ER + expansion ATR, SL k_sl × ATR, trailing Chandelier k_trail × ATR,
sortie sur perte de régime) et ajoute le PYRAMIDAGE via le hook
``check_scale_in`` (cf. BaseStrategy / Backtester / PositionMixin) :

  - jusqu'à ``max_units`` unités au total (1 entrée + max_units-1 ajouts) ;
  - un ajout est déclenché quand le prix a progressé de ``pyr_step`` × ATR
    depuis la dernière unité, dans le sens de la position ;
  - chaque unité est sizée comme une entrée normale (risque % équité /
    distance au stop courant) — le trailing remonté borne le risque total
    pendant que l'exposition croît sur les trades gagnants.

L'état de pyramidage (``units``, ``next_add``) est stocké dans le dict
position, persisté par l'appelant entre les cycles. Après un redémarrage du
bot, si l'état est absent il est ré-initialisé depuis le prix courant (les
ajouts reprennent, l'historique d'unités est perdu — comportement conservateur).
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.strategies.tvr_trend import Strategy as TVRStrategy, _atr_series

logger = logging.getLogger(__name__)


class Strategy(TVRStrategy):
    name = "snowball_pyramid"

    timeframes: List[str] = ["4h", "1d"]

    param_space: Dict[str, List] = {
        **TVRStrategy.param_space,
        "max_units": [2, 3, 4],
        "pyr_step":  [0.75, 1.0, 1.5],
    }

    fixed_params: Dict[str, Any] = dict(TVRStrategy.fixed_params)

    _DEFAULTS = dict(TVRStrategy._DEFAULTS, max_units=3, pyr_step=1.0)

    # ── Pyramidage ──────────────────────────────────────────────────────────

    def check_scale_in(self, df: pl.DataFrame, position: dict,
                       params: dict = None) -> Optional[Dict[str, Any]]:
        p = self._params(params)
        max_units = int(p["max_units"])
        if max_units <= 1:
            return None

        side = 1 if position.get("side") == "long" else -1
        close = float(df["close"][-1])
        if close <= 0:
            return None

        # ATR courant : cache backtest si dispo, sinon calcul sur la fenêtre
        f = self._bt_lookup(df, p)
        if f is not None:
            atr = f["atr"]
        else:
            atr = float(_atr_series(df, int(p["atr_period"]))[-1])
        if not np.isfinite(atr) or atr <= 0:
            return None

        units = int(position.get("units", 1))
        next_add = position.get("next_add")
        if next_add is None:
            # État absent (1re évaluation ou reprise après redémarrage) :
            # prochain ajout à pyr_step × ATR du prix d'entrée moyen courant.
            entry = float(position.get("entry", close))
            position["units"] = units
            position["next_add"] = entry + side * float(p["pyr_step"]) * atr
            return None

        if units >= max_units:
            return None

        triggered = close >= float(next_add) if side == 1 else close <= float(next_add)
        if not triggered:
            return None

        position["units"] = units + 1
        position["next_add"] = close + side * float(p["pyr_step"]) * atr
        return {
            "size_factor": 1.0,
            "reason": f"pyramid_unit_{units + 1}/{max_units}",
        }
