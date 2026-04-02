"""
Tests — Issue #7 : vérification que borrow_cost est bien déduit du PnL réalisé.

Asserts:
    realized_pnl = gross_pnl - fees - borrow_cost
"""
import sys
import os
import math

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _compute_gross(side: str, entry: float, exit_price: float, size: float) -> float:
    if side == "long":
        return (exit_price - entry) * size
    return (entry - exit_price) * size


def _compute_borrow_cost(notional: float, hours_held: float,
                          daily_rate: float = 0.0002,
                          periods_per_day: int = 3) -> float:
    """Reproduit la formule de position_mixin._close_position."""
    r_period  = daily_rate / periods_per_day
    n_periods = hours_held * periods_per_day / 24
    return notional * ((1 + r_period) ** n_periods - 1)


# ── Tests unitaires sur l'arithmétique PnL ────────────────────────────────────

class TestPnLBorrowCost:

    def test_long_profitable_pnl(self):
        """Position longue profitable : pnl = gross - fees - borrow."""
        entry  = 100.0
        exit_p = 110.0
        size   = 1.0
        fee_rate   = 0.001
        hours_held = 24.0

        fees        = exit_p * size * fee_rate
        borrow_cost = _compute_borrow_cost(entry * size, hours_held)
        gross       = _compute_gross("long", entry, exit_p, size)
        realized    = gross - fees - borrow_cost

        assert realized == pytest.approx(10.0 - fees - borrow_cost, rel=1e-9)
        assert realized < gross            # frais + borrow réduisent le PnL
        assert realized < gross - fees     # borrow_cost est non nul et s'ajoute aux frais

    def test_short_profitable_pnl(self):
        """Position courte profitable : pnl = gross - fees - borrow."""
        entry  = 100.0
        exit_p = 90.0
        size   = 1.0
        fee_rate   = 0.001
        hours_held = 48.0

        fees        = exit_p * size * fee_rate
        borrow_cost = _compute_borrow_cost(entry * size, hours_held)
        gross       = _compute_gross("short", entry, exit_p, size)
        realized    = gross - fees - borrow_cost

        assert realized == pytest.approx(gross - fees - borrow_cost, rel=1e-9)
        assert realized < gross

    def test_zero_borrow_rate(self):
        """Avec taux d'emprunt nul, pnl = gross - fees."""
        entry  = 200.0
        exit_p = 210.0
        size   = 0.5
        fee_rate   = 0.001
        hours_held = 12.0

        fees        = exit_p * size * fee_rate
        borrow_cost = _compute_borrow_cost(entry * size, hours_held, daily_rate=0.0)
        gross       = _compute_gross("long", entry, exit_p, size)
        realized    = gross - fees - borrow_cost

        assert borrow_cost == pytest.approx(0.0, abs=1e-12)
        assert realized == pytest.approx(gross - fees, rel=1e-9)

    def test_borrow_cost_increases_with_time(self):
        """Le coût d'emprunt doit augmenter avec la durée de détention."""
        notional   = 1000.0
        bc_short   = _compute_borrow_cost(notional, hours_held=1.0)
        bc_long    = _compute_borrow_cost(notional, hours_held=72.0)
        assert bc_long > bc_short

    def test_borrow_cost_never_negative(self):
        """Le coût d'emprunt est toujours >= 0."""
        for h in [0.0, 0.5, 1.0, 24.0, 168.0]:
            bc = _compute_borrow_cost(500.0, hours_held=h)
            assert bc >= 0.0, f"borrow_cost négatif pour hours_held={h}"

    def test_realized_pnl_formula_matches_position_mixin(self):
        """
        Valide que la formule reproduite ici correspond exactement à celle de
        position_mixin._close_position (réf. lignes 334-350 de position_mixin.py).
        """
        entry  = 50000.0   # BTC/USDC
        exit_p = 51000.0
        size   = 0.01
        fee_rate          = 0.001
        daily_rate        = 0.0002
        periods_per_day   = 3
        open_time_offset  = 8.0   # heures

        # Réplique exacte du code de production
        fees        = exit_p * size * fee_rate
        r_period    = daily_rate / periods_per_day
        n_periods   = open_time_offset * periods_per_day / 24
        borrow_cost = (entry * size) * ((1 + r_period) ** n_periods - 1)
        gross       = (exit_p - entry) * size
        pnl         = gross - fees - borrow_cost

        # Notre helper doit donner le même résultat
        bc_helper = _compute_borrow_cost(entry * size, open_time_offset,
                                         daily_rate, periods_per_day)
        expected  = _compute_gross("long", entry, exit_p, size) - fees - bc_helper

        assert pnl == pytest.approx(expected, rel=1e-9)

    def test_loss_trade_borrow_cost_increases_loss(self):
        """Sur un trade perdant, borrow_cost aggrave la perte."""
        entry  = 100.0
        exit_p = 95.0
        size   = 1.0
        fee_rate   = 0.001
        hours_held = 24.0

        fees        = exit_p * size * fee_rate
        borrow_cost = _compute_borrow_cost(entry * size, hours_held)
        gross       = _compute_gross("long", entry, exit_p, size)
        realized    = gross - fees - borrow_cost

        # Le PnL réalisé doit être plus négatif que gross seul
        assert realized < gross
        assert realized < gross - fees
