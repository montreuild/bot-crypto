"""Tests du cycle de vie d'une position (TEST-07).

PositionMixin n'était couvert que par les tests e2e/phase* — pas
d'unittests isolés du chemin ouverture → gestion → clôture. Ces tests
construisent une vraie instance LiveTrader (MockExchange en mémoire, DB
sqlite jetable) et exercent _try_open_from_signal (chemin unique
d'ouverture), _manage_position (gap → clôture forcée) et _close_position
(PnL + persistance) directement.
"""
import tempfile
import os

import pytest

from app.core.bot_identity import build_pos_key, build_slot_key
from app.core.database import get_trades, session_scope
from app.live.live_trader import LiveTrader


class MockExchange:
    """Exchange minimal en mémoire — aucun appel réseau."""

    def __init__(self, ticker_price: float = 100.0):
        self.ticker_price = ticker_price
        self.orders = []

    def fetch_ticker(self, symbol):
        return {"last": self.ticker_price}

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        order = {
            "id": f"order{len(self.orders)}", "symbol": symbol, "side": side,
            "amount": amount, "price": self.ticker_price,
            "average": self.ticker_price, "filled": amount,
        }
        self.orders.append(order)
        return order

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=None):
        # Pas de données historiques mockées : check_early_exit/scale_in de la
        # stratégie se dégradent alors silencieusement (df_ee/df_si is None).
        return []

    def fetch_positions(self):
        return []

    def fetch_balance(self):
        return {"free": {"USDC": 1000.0}, "total": {"USDC": 1000.0}}

    def fetch_balance_detail(self):
        return {"free": 1000.0, "used": 0.0, "total": 1000.0, "borrowed": 0.0}


def _make_cfg(db_path: str, max_positions: int = 5) -> dict:
    return {
        "trading": {
            "capital": 1000.0, "timeframes": ["1h"], "scan_interval": 60,
            "score_threshold": 0.5, "paper_mode": True, "max_positions": max_positions,
        },
        "database": {"url": f"sqlite:///{db_path}"},
        "exchange": {"name": "okx", "margin": False},
        "strategies": {"enabled": ["trend_rider"]},
        "strategy_params": {}, "optimizer_results": {}, "scanner": {}, "risk": {},
        "notifications": {}, "optimizer": {"enabled": False},
        "forward_test": {"enabled": False}, "lifecycle": {"enabled": False},
        "capital_allocator": {},
    }


SYMBOL, STRATEGY, TF = "BTC/USDC", "trend_rider", "1h"


@pytest.fixture
def trader_exchange(tmp_path):
    """(trader, exchange) — LiveTrader réel + MockExchange contrôlable."""
    exchange = MockExchange()
    trader = LiveTrader(_make_cfg(str(tmp_path / "live.db")), exchange)
    return trader, exchange


def _open(trader, symbol=SYMBOL, price=100.0, stop_hint=95.0):
    slot_key = build_slot_key(STRATEGY, TF, symbol)
    pos_key = build_pos_key(symbol, STRATEGY, TF)
    signal_dict = {"side": "long", "score": 0.8, "name": STRATEGY,
                   "stop_hint": stop_hint, "reason": "test signal"}
    opened = trader._try_open_from_signal(
        pos_key=pos_key, symbol=symbol, strategy_name=STRATEGY, side="long",
        score=0.8, strat_threshold=0.5, tf=TF, slot_key=slot_key,
        signal_dict=signal_dict, atr=1.0, price=price,
    )
    return opened, pos_key


# ── Ouverture ─────────────────────────────────────────────────────────────

class TestOpen:
    def test_try_open_from_signal_creates_position(self, trader_exchange):
        trader, _ = trader_exchange
        opened, pos_key = _open(trader)
        assert opened is True
        assert pos_key in trader.open_positions
        pos = trader.open_positions[pos_key]
        assert pos["symbol"] == SYMBOL
        assert pos["side"] == "long"
        assert pos["stop"] == 95.0
        assert pos["strategy"] == STRATEGY

    def test_try_open_from_signal_blocked_when_risk_halted(self, trader_exchange):
        trader, _ = trader_exchange
        trader.risk.halted = True
        trader.risk.halt_reason = "test halt"
        opened, pos_key = _open(trader)
        assert opened is False
        assert trader.open_positions == {}

    def test_try_open_from_signal_respects_max_positions(self, tmp_path):
        exchange = MockExchange()
        trader = LiveTrader(_make_cfg(str(tmp_path / "live.db"), max_positions=1), exchange)
        opened1, _ = _open(trader, symbol="BTC/USDC")
        opened2, _ = _open(trader, symbol="ETH/USDC")
        assert opened1 is True
        assert opened2 is False
        assert len(trader.open_positions) == 1


# ── Gestion (tick-by-tick) ────────────────────────────────────────────────

class TestManage:
    def test_manage_position_flat_price_stays_open(self, trader_exchange):
        trader, exchange = trader_exchange
        _, pos_key = _open(trader)
        trader.ohlcv_cache.set_atr(SYMBOL, 1.0)
        trader._manage_position(pos_key)
        assert pos_key in trader.open_positions

    def test_manage_position_gap_below_stop_force_closes(self, trader_exchange):
        trader, exchange = trader_exchange
        _, pos_key = _open(trader, price=100.0, stop_hint=95.0)
        trader.ohlcv_cache.set_atr(SYMBOL, 1.0)
        exchange.ticker_price = 90.0  # < stop(95) * 0.98 → gap
        trader._manage_position(pos_key)
        assert pos_key not in trader.open_positions
        with session_scope(trader.SessionLocal) as sess:
            trades = get_trades(sess, limit=10)
        assert len(trades) == 1
        assert trades[0].symbol == SYMBOL
        assert trades[0].pnl < 0  # clôturé en perte sous le stop


# ── Clôture ───────────────────────────────────────────────────────────────

class TestClose:
    def test_close_position_computes_positive_pnl_and_persists_trade(self, trader_exchange):
        trader, exchange = trader_exchange
        _, pos_key = _open(trader, price=100.0, stop_hint=95.0)
        base_capital = trader._paper_base

        exchange.ticker_price = 120.0
        trader._close_position(pos_key, 120.0)

        assert pos_key not in trader.open_positions
        assert trader._paper_base > base_capital  # PnL positif crédité
        with session_scope(trader.SessionLocal) as sess:
            trades = get_trades(sess, limit=10)
        assert len(trades) == 1
        assert trades[0].pnl > 0

    def test_close_position_unknown_pos_id_is_noop(self, trader_exchange):
        trader, _ = trader_exchange
        trader._close_position("does-not-exist", 100.0)  # ne doit pas lever
        with session_scope(trader.SessionLocal) as sess:
            assert get_trades(sess, limit=10) == []
