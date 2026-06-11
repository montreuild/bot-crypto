"""Parité backtest ↔ live : même trade ⇒ même PnL net.

Les deux chemins (Backtester._close_at et PositionMixin._close_position)
consomment désormais les mêmes formules (app/core/execution.py). Ces tests
verrouillent :
  1. les formules elles-mêmes (frais, emprunt composé, PnL net) ;
  2. la parité de bout en bout : un trade identique (mêmes prix d'entrée/
     sortie, taille, frais, durée) clôturé par le Backtester et par le
     LiveTrader paper produit le même PnL net.
"""
import time
import types
from unittest.mock import MagicMock

import pytest

from app.core.execution import (borrow_cost, close_pnl, gross_pnl, net_pnl,
                                risk_position_size, trade_fees)


# ── 1. Formules unitaires ───────────────────────────────────────────────────

def test_trade_fees():
    assert trade_fees(100.0, 2.0, 0.001) == pytest.approx(0.2)


def test_borrow_cost_compound():
    # 24 h à 0.072 %/jour en 3 périodes : (1 + 0.00024)^3 - 1
    expected = 1000 * ((1 + 0.00072 / 3) ** 3 - 1)
    assert borrow_cost(1000, 0.00072, 24, 3) == pytest.approx(expected)
    assert borrow_cost(1000, 0.00072, 0, 3) == 0.0
    assert borrow_cost(0, 0.00072, 24, 3) == 0.0


def test_gross_and_net_pnl():
    assert gross_pnl("long", 100, 110, 2) == pytest.approx(20)
    assert gross_pnl("short", 100, 110, 2) == pytest.approx(-20)
    assert net_pnl("long", 100, 110, 2, exit_fees=0.22, borrow=0.05) == \
        pytest.approx(20 - 0.22 - 0.05)


def test_close_pnl_combines_all():
    pnl, fees, borrow = close_pnl("long", 100, 110, 2, notional=200,
                                  fee_rate=0.001, daily_rate=0.00072,
                                  hours_held=24, periods_per_day=3)
    assert fees == pytest.approx(110 * 2 * 0.001)
    assert borrow == pytest.approx(200 * ((1 + 0.00072 / 3) ** 3 - 1))
    assert pnl == pytest.approx(20 - fees - borrow)


def test_risk_position_size_caps_notional():
    size, notional = risk_position_size(10_000, 0.01, entry=100, stop=98,
                                        max_notional_pct=0.30)
    assert size == pytest.approx(3000 / 100)      # cap 30 % du capital
    assert notional == pytest.approx(3000)


# ── 2. Parité de bout en bout backtest ↔ live paper ─────────────────────────

ENTRY, EXIT, SIZE = 100.0, 110.0, 2.0
FEE_RATE, BORROW_RATE, PERIODS = 0.001, 0.00072, 3
HOURS_HELD = 4.0


def _backtest_close_pnl() -> float:
    """Clôture d'un trade connu via Backtester._close_at."""
    from app.engine.backtest import Backtester
    from app.engine.engine import Engine

    cfg = {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "taker_fee": FEE_RATE, "maker_fee": FEE_RATE,
                    "borrow_rate_daily": BORROW_RATE,
                    "borrow_periods_per_day": PERIODS},
        "backtest": {"spread_pct": 0.0},
        "strategy_params": {},
    }
    bt = Backtester(Engine(), cfg)
    position = {
        "side": "long", "entry": ENTRY, "size": SIZE,
        "notional": ENTRY * SIZE, "bar": 0, "stop": 95.0,
        "_stop_trail": [], "_trailing": None,
        "mae": 0.0, "mfe": 0.0,
    }
    ctx = types.SimpleNamespace(
        df=None, timeframe="1h", capital=1000.0,
        trades=[], equity_curve=[], timestamps=[],
    )
    # df minimal pour le timestamp de sortie
    import polars as pl
    from datetime import datetime, timedelta
    t0 = datetime(2024, 1, 1)
    ctx.df = pl.DataFrame({"time": [t0 + timedelta(hours=i) for i in range(5)],
                           "close": [ENTRY] * 5})
    pnl = bt._close_at(ctx, position, int(HOURS_HELD), EXIT,
                       "parity_test", maker=False)
    return pnl


def _live_close_pnl() -> float:
    """Clôture du même trade via PositionMixin._close_position (paper)."""
    from app.live.position_mixin import PositionMixin

    class Harness(PositionMixin):
        def __init__(self):
            import threading
            self.cfg = {"trading": {
                "paper_mode": True, "paper_slippage": 0.0,
                "taker_fee": FEE_RATE, "borrow_rate_daily": BORROW_RATE,
                "borrow_periods_per_day": PERIODS,
                "reentry_cooldown_bars": 0,
            }}
            self.exchange = MagicMock()
            self.exchange.create_order.return_value = {"price": EXIT, "id": "x"}
            self.risk = MagicMock()
            self.notif = MagicMock()
            self.allocator = MagicMock()
            self.SessionLocal = MagicMock()
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

    h = Harness()
    pos = {
        "id": "p1", "symbol": "BTC/USDC", "side": "long", "strategy": "t",
        "timeframe": "1h", "score": 0.8, "entry": ENTRY, "stop": 95.0,
        "size": SIZE, "notional": ENTRY * SIZE, "leverage": 1,
        "open_time": time.time() - HOURS_HELD * 3600,
        "fees": 0.0, "pnl": 0.0, "reason": "", "order_id": "o1",
    }
    h.open_positions["p1"] = pos
    base_before = h._paper_base
    h._close_position("p1", EXIT)
    return h._paper_base - base_before


def test_backtest_and_live_close_same_net_pnl():
    """Même trade (entrée/sortie/taille/frais/durée) ⇒ même PnL net."""
    pnl_bt = _backtest_close_pnl()
    pnl_live = _live_close_pnl()
    pnl_ref, _, _ = close_pnl("long", ENTRY, EXIT, SIZE, ENTRY * SIZE,
                              FEE_RATE, BORROW_RATE, HOURS_HELD, PERIODS)
    # Le chemin live mesure la durée par horloge → tolérance d'une seconde
    # d'intérêts ; tout le reste doit être identique.
    assert pnl_bt == pytest.approx(pnl_ref, abs=1e-9)
    assert pnl_live == pytest.approx(pnl_ref, abs=1e-4)
    assert pnl_bt == pytest.approx(pnl_live, abs=1e-4)
