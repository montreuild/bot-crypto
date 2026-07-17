"""
Tests unitaires — RiskManager
"""
import threading

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.risk import RiskManager


def _cfg(capital=1000, paper=True, dd_daily=0.05, dd_global=0.20,
         risk=0.01, max_pos=3):
    return {
        "trading": {
            "capital": capital,
            "paper_mode": paper,
            "daily_drawdown_limit": dd_daily,
            "max_drawdown_global": dd_global,
            "risk_per_trade": risk,
            "max_positions": max_pos,
            "max_longs": 2,
            "max_shorts": 2,
            "max_trades_per_minute": 10,
            "max_leverage": 1,
            "max_notional_pct": 0.20,
        },
        "backtest": {"max_notional_pct": 0.20},
        "notifications": {"dd_warning_ratio": 0.80},
    }


class TestRiskManager:
    def test_initial_state(self):
        rm = RiskManager(_cfg())
        assert rm.equity == 1000
        assert rm.peak_equity == 1000
        assert not rm.halted

    def test_compute_risk_no_drawdown(self):
        rm = RiskManager(_cfg(risk=0.01))
        assert rm.compute_risk() == pytest.approx(0.01)

    def test_compute_risk_reduced_above_5pct_dd(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.equity = 940      # 6% de drawdown
        rm.peak_equity = 1000
        assert rm.compute_risk() == pytest.approx(0.0075)  # 0.01 * 0.75

    def test_compute_risk_halved_above_10pct_dd(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.equity = 880      # 12% de drawdown
        rm.peak_equity = 1000
        assert rm.compute_risk() == pytest.approx(0.005)  # 0.01 * 0.5

    def test_circuit_breaker_daily(self):
        rm = RiskManager(_cfg(dd_daily=0.05))
        rm.daily_start = 1000
        rm.update_equity(940)   # perte de 6% → dépasse 5%
        assert rm.halted
        assert "journalier" in rm.halt_reason

    def test_circuit_breaker_global(self):
        rm = RiskManager(_cfg(dd_global=0.20))
        rm.peak_equity = 1000
        rm.daily_start = 800
        rm.update_equity(790)   # drawdown global 21%
        assert rm.halted
        assert "global" in rm.halt_reason

    def test_reset_halt(self):
        rm = RiskManager(_cfg())
        rm.halted = True
        rm.halt_reason = "test"
        rm.reset_halt()
        assert not rm.halted
        assert rm.halt_reason == ""

    def test_can_trade_max_positions(self):
        rm = RiskManager(_cfg(max_pos=2))
        rm.open_positions = {
            "p1": {"side": "long"},
            "p2": {"side": "long"},
        }
        can, reason = rm.can_trade("long")
        assert not can
        assert "Max positions" in reason

    def test_can_trade_ok(self):
        rm = RiskManager(_cfg())
        can, reason = rm.can_trade("long")
        assert can
        assert reason == ""

    def test_can_trade_halted(self):
        rm = RiskManager(_cfg())
        rm.halted = True
        rm.halt_reason = "Test halt"
        can, reason = rm.can_trade("long")
        assert not can
        assert reason == "Test halt"

    def test_compute_size_basic(self):
        rm = RiskManager(_cfg(capital=1000, risk=0.01))
        size, notional = rm.compute_size(entry=100, atr=5)
        assert size > 0
        assert notional <= 1000 * 0.20

    def test_compute_size_notional_capped(self):
        rm = RiskManager(_cfg(capital=1000))
        # ATR très petit → size serait énorme, doit être plafonné
        size, notional = rm.compute_size(entry=100, atr=0.001)
        assert notional <= 1000 * 0.20 + 1e-6

    def test_daily_pnl_pct_positive(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.daily_start = 1000
        rm.equity = 1050
        assert rm.daily_pnl_pct == pytest.approx(0.05)

    def test_daily_pnl_pct_negative(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.daily_start = 1000
        rm.equity = 950
        assert rm.daily_pnl_pct == pytest.approx(-0.05)

    def test_global_dd_pct(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.peak_equity = 1200
        rm.equity = 1000
        assert rm.global_dd_pct == pytest.approx(1/6, rel=1e-3)

    def test_register_and_close_position(self):
        rm = RiskManager(_cfg())
        pos = {"id": "abc", "symbol": "BTC/USDC", "side": "long"}
        rm.register_open(pos)
        assert "abc" in rm.open_positions
        rm.register_close("abc")
        assert "abc" not in rm.open_positions

    def test_has_hedge(self):
        rm = RiskManager(_cfg())
        rm.open_positions = {
            "a": {"symbol": "BTC/USDC", "side": "long"},
            "b": {"symbol": "BTC/USDC", "side": "short"},
        }
        assert rm.has_hedge("BTC/USDC")
        assert not rm.has_hedge("ETH/USDC")

    def test_status_dict_keys(self):
        rm = RiskManager(_cfg())
        d = rm.status_dict()
        for key in ("equity", "peak_equity", "daily_pnl_pct", "global_dd_pct",
                    "open_positions", "halted", "halt_reason", "current_risk"):
            assert key in d
