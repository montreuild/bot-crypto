"""Coûts d'exécution d'un backtest : funding, impact, frais (DETTE-04c).

Extraits de `backtest.py`. `FIN-01`/`FIN-02` sont nés d'un déplacement de la
comptabilité des frais : celui-ci est littéral, et les deux invariants
comptables de `test_partial_exits` le verrouillent.
"""
import logging
from typing import Any

import numpy as np

from app.core.execution import size_impact_cost as _size_impact_cost
from app.core.execution import venue_trade_cost as _venue_trade_cost
from app.core.trade_economics import funding_cost as _funding_cost

logger = logging.getLogger(__name__)


class BacktestCostsMixin:
    """Contrat d'hôte : le `Backtester` porte la venue et les taux."""

    _venue: Any
    cfg: dict
    taker_fee: float
    maker_fee: float
    spread_pct: float
    slippage_model: Any
    slippage_k: Any

    def _funding_cost(self, ctx, position: dict, i: int,
                      hours_held: float) -> float:
        """L2 (§27) — funding d'un perpétuel sur la durée de détention.

        0.0 hors venue perp, ou quand la série de funding n'est pas disponible
        (``ctx.funding_arr``, alimentée par ``derivatives.align_to_ohlcv``).
        Un long paie quand le funding est positif, un short encaisse — d'où le
        signe porté par le sens de la position."""
        arr = getattr(ctx, "funding_arr", None)
        if arr is None or self._venue is None:
            return 0.0
        if getattr(self._venue, "market_type", "spot") != "perp":
            return 0.0
        debut = int(position.get("bar", i))
        if not (0 <= debut <= i < len(arr)):
            return 0.0
        taux_moyen = float(np.nanmean(arr[debut:i + 1]))
        if taux_moyen != taux_moyen:      # NaN
            return 0.0
        cout = _funding_cost(position["notional"], taux_moyen, hours_held)
        return cout if position["side"] == "long" else -cout

    def _impact_cost(self, ctx, i: int, notional: float) -> float:
        """BT-10 : coût d'impact croissant avec la taille RELATIVE du trade
        (participation au volume) — 0.0 si le modèle est off ou volume absent.
        Formule partagée avec le paper trading live (FIN-07) : voir
        ``app.core.execution.size_impact_cost``."""
        if self.slippage_model != "size":
            return 0.0
        qv = getattr(ctx, "qvol_arr", None)
        if qv is None or not (0 <= i < len(qv)):
            return 0.0
        return _size_impact_cost(notional, self.spread_pct, self.slippage_k, float(qv[i]))

    def _fees(self, price: float, size: float, maker: bool = False,
              side: str = "long", is_entry: bool = True) -> float:
        """Coût d'un fill. Passe par le modèle de la venue quand il y en a une
        (G2 : commission fixe, plancher, TTF) — sinon frais proportionnels,
        strictement comme avant."""
        rate = self.maker_fee if maker else self.taker_fee
        return _venue_trade_cost(price, size, rate, side=side,
                                 venue=self._venue, is_entry=is_entry)


# Imports en fin de module : walk_forward importe Backtester en lazy dans run().
from app.engine.monte_carlo import MonteCarlo  # noqa: E402,F401
from app.engine.walk_forward import WalkForwardAnalyzer  # noqa: E402,F401
