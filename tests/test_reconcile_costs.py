"""Réconciliation des coûts réels (frais fetch_my_trades, intérêts margin)."""
import time
from unittest.mock import MagicMock

import pytest

from app.live.position_close_mixin import PositionCloseMixin
from app.live.position_restore_mixin import PositionRestoreMixin


def _harness(margin=True, my_trades=None, interest_rows=None):
    h = PositionCloseMixin()
    h.cfg = {"trading": {"paper_mode": False, "reconcile_real_costs": True},
             "exchange": {"margin": margin}}
    h.exchange = MagicMock()
    h.exchange.fetch_my_trades.return_value = my_trades or []
    if interest_rows is None:
        h.exchange.fetch_borrow_interest = MagicMock(return_value=[])
    else:
        h.exchange.fetch_borrow_interest = MagicMock(return_value=interest_rows)
    return h


def _pos():
    return {"symbol": "BTC/USDC", "side": "long", "entry": 100.0, "size": 2.0,
            "notional": 200.0, "open_time": time.time() - 3600, "order_id": "open-1"}


def test_real_fees_replace_estimates():
    trades = [
        {"order": "close-1", "fee": {"cost": 0.30, "currency": "USDC"}},
        {"order": "open-1",  "fee": {"cost": 0.20, "currency": "USDC"}},  # ignoré (entrée)
    ]
    h = _harness(margin=False, my_trades=trades)
    pnl, fees, borrow = h._reconcile_close_costs(
        _pos(), {"id": "close-1"}, fees_est=0.22, borrow_est=0.0, pnl_est=19.78)
    assert fees == pytest.approx(0.30)         # frais réels du fill de clôture seul
    assert borrow == 0.0
    assert pnl == pytest.approx(19.78 + 0.22 - 0.30)


def test_real_borrow_interest_replaces_estimate():
    h = _harness(margin=True, my_trades=[],
                 interest_rows=[{"interest": 0.011}, {"interest": 0.004}])
    pnl, fees, borrow = h._reconcile_close_costs(
        _pos(), {"id": "close-1"}, fees_est=0.22, borrow_est=0.006, pnl_est=19.0)
    assert borrow == pytest.approx(0.015)
    assert fees == pytest.approx(0.22)         # pas de trade trouvé → estimation
    assert pnl == pytest.approx(19.0 + 0.006 - 0.015)


def test_bnb_fees_keep_estimate():
    """Frais en BNB : pas de conversion fiable → estimation conservée."""
    trades = [{"order": "close-1", "fee": {"cost": 0.001, "currency": "BNB"}}]
    h = _harness(margin=False, my_trades=trades)
    pnl, fees, borrow = h._reconcile_close_costs(
        _pos(), {"id": "close-1"}, fees_est=0.22, borrow_est=0.0, pnl_est=19.78)
    assert fees == pytest.approx(0.22)
    assert pnl == pytest.approx(19.78)


def test_exchange_failure_is_graceful():
    h = _harness(margin=True)
    h.exchange.fetch_my_trades.side_effect = RuntimeError("réseau KO")
    h.exchange.fetch_borrow_interest.side_effect = RuntimeError("réseau KO")
    pnl, fees, borrow = h._reconcile_close_costs(
        _pos(), {"id": "close-1"}, fees_est=0.22, borrow_est=0.01, pnl_est=19.0)
    assert (pnl, fees, borrow) == (19.0, 0.22, 0.01)


def test_paper_order_skipped():
    h = _harness(margin=False, my_trades=[{"order": "paper_1", "fee": {"cost": 9, "currency": "USDC"}}])
    pnl, fees, borrow = h._reconcile_close_costs(
        _pos(), {"id": "paper_1"}, fees_est=0.22, borrow_est=0.0, pnl_est=19.78)
    assert fees == pytest.approx(0.22)


# ── Vérification entry/taille à la restauration ─────────────────────────────

def _restore_harness(order):
    h = PositionRestoreMixin()
    h.cfg = {"trading": {"paper_mode": False}, "exchange": {"margin": False}}
    h.exchange = MagicMock()
    if isinstance(order, Exception):
        h.exchange.fetch_order.side_effect = order
    else:
        h.exchange.fetch_order.return_value = order
    return h


def test_restore_fixes_entry_and_size_drift():
    h = _restore_harness({"average": 101.5, "filled": 1.5})
    pos = {"symbol": "BTC/USDC", "order_id": "o1", "entry": 100.0,
           "size": 2.0, "notional": 200.0}
    h._verify_restored_position(pos)
    assert pos["entry"] == pytest.approx(101.5)
    assert pos["size"] == pytest.approx(1.5)
    assert pos["notional"] == pytest.approx(1.5 * 101.5)


def test_restore_small_drift_untouched():
    h = _restore_harness({"average": 100.05, "filled": 1.99})
    pos = {"symbol": "BTC/USDC", "order_id": "o1", "entry": 100.0,
           "size": 2.0, "notional": 200.0}
    h._verify_restored_position(pos)
    assert pos["entry"] == 100.0      # 0,05 % < seuil 0,1 %
    assert pos["size"] == 2.0         # 0,5 % < seuil 2 %


def test_restore_order_not_found_keeps_position():
    h = _restore_harness(RuntimeError("ordre inconnu"))
    pos = {"symbol": "BTC/USDC", "order_id": "o1", "entry": 100.0,
           "size": 2.0, "notional": 200.0}
    h._verify_restored_position(pos)
    assert pos["entry"] == 100.0 and pos["size"] == 2.0
