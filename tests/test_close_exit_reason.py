"""FIN-06 — exit_reason propagé jusqu'à Trade.exit_reason (DB), distinct de
Trade.reason (motif d'OUVERTURE, jamais écrasé par la clôture)."""
import threading
import time
from unittest.mock import MagicMock

import pytest

from app.core.database import Trade, init_db, session_scope
from app.live.position_close_mixin import PositionCloseMixin
from app.live.position_open_mixin import PositionOpenMixin


def _harness(tmp_path):
    _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")

    class Harness(PositionOpenMixin, PositionCloseMixin):
        def __init__(self):
            self.cfg = {"trading": {
                "paper_mode": True, "paper_slippage": 0.0,
                "taker_fee": 0.001, "borrow_rate_daily": 0.0,
                "reentry_cooldown_bars": 0,
            }}
            self.exchange = MagicMock()
            self.exchange.create_order.return_value = {"price": 101.0, "id": "x"}
            self.risk = MagicMock()
            self.notif = MagicMock()
            self.allocator = MagicMock()
            self.SessionLocal = SessionLocal
            self._positions_lock = threading.Lock()
            self._capital_lock = threading.Lock()
            self._paper_base = 1000.0
            self.capital_display = 1000.0
            self._margin_interest = 0.0
            self._loss_notified = set()
            self._cooldown = {}
            self.signal_log = []
            self._strat_thresholds = {}
            self.threshold = 0.55
            self.interval = 60
            self.tf = "1h"
            self.open_positions = {}

    return Harness(), SessionLocal


def _open_pos(entry=100.0, reason="signal_x"):
    return {
        "id": "p1", "symbol": "BTC/USDC", "side": "long", "strategy": "t",
        "timeframe": "1h", "score": 0.8, "entry": entry, "stop": 95.0,
        "size": 1.0, "notional": entry, "leverage": 1,
        "open_time": time.time() - 3600,
        "fees": 0.0, "pnl": 0.0, "reason": reason, "order_id": "o1",
    }


@pytest.mark.parametrize("exit_reason", [
    "trailing_stop", "stop_loss", "take_profit", "gap", "early_exit", "manual",
])
def test_close_position_persists_exit_reason(tmp_path, exit_reason):
    h, SessionLocal = _harness(tmp_path)
    h.open_positions["p1"] = _open_pos()
    h._close_position("p1", 101.0, exit_reason=exit_reason)

    with session_scope(SessionLocal) as sess:
        rec = sess.query(Trade).filter_by(symbol="BTC/USDC").one()
        assert rec.exit_reason == exit_reason
        assert rec.reason == "signal_x"   # motif d'ouverture préservé, non écrasé


def test_close_position_fee_split_is_all_taker(tmp_path):
    """Le live n'implémente aucune distinction maker/taker à l'exécution
    (cf. docstring PositionMixin._close_position) — fee_taker == fees,
    fee_maker == 0, honnêtement."""
    h, SessionLocal = _harness(tmp_path)
    h.open_positions["p1"] = _open_pos()
    h._close_position("p1", 101.0, exit_reason="take_profit")

    with session_scope(SessionLocal) as sess:
        rec = sess.query(Trade).filter_by(symbol="BTC/USDC").one()
        assert rec.fees > 0
        assert rec.fee_taker == pytest.approx(rec.fees)
        assert rec.fee_maker == pytest.approx(0.0)


def test_close_position_defaults_to_unknown_when_unspecified(tmp_path):
    h, SessionLocal = _harness(tmp_path)
    h.open_positions["p1"] = _open_pos()
    h._close_position("p1", 101.0)   # exit_reason omis par l'appelant

    with session_scope(SessionLocal) as sess:
        rec = sess.query(Trade).filter_by(symbol="BTC/USDC").one()
        assert rec.exit_reason == "unknown"
