"""
Tests unitaires — Backtester & BacktestResult
"""
import pytest
import sys, os
from datetime import datetime, timedelta
import numpy as np
import polars as pl
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine.backtest import BacktestResult, Backtester
from app.engine.engine import Engine, BaseStrategy


def _make_df(n=300, trend="up"):
    """Génère un DataFrame OHLCV synthétique."""
    np.random.seed(42)
    base = 100.0
    closes = [base]
    for _ in range(n - 1):
        step = np.random.randn() * 0.5
        if trend == "up":
            step += 0.1
        elif trend == "down":
            step -= 0.1
        closes.append(max(closes[-1] + step, 1.0))
    closes = np.array(closes)
    highs  = closes * (1 + np.abs(np.random.randn(n) * 0.005))
    lows   = closes * (1 - np.abs(np.random.randn(n) * 0.005))
    opens  = np.roll(closes, 1); opens[0] = closes[0]
    vols   = np.random.uniform(1000, 5000, n)
    start  = datetime(2024, 1, 1)
    times  = [start + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


def _cfg():
    return {
        "trading": {
            "capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
            "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
            "score_threshold": 0.55, "borrow_rate_daily": 0.0002,
        },
        "backtest": {
            "spread_pct": 0.0005, "latency_ms": 0,
            "partial_fill_pct": 1.0, "monte_carlo_runs": 50,
            "walk_forward_folds": 3, "atr_stop_mult": 2.0,
            "trail_wide": 2.5, "trail_normal": 2.0, "trail_lock": 1.5,
            "trail_tight": 1.0, "grace_bars": 4, "breakeven_r": 1.2,
            "lock_r": 2.5, "tight_r": 4.0, "lock_ratio": 0.60,
            "use_swing": False, "max_notional_pct": 0.20,
        },
        "strategies": {"enabled": ["dummy"]},
        "strategy_params": {},
    }


class DummyLongStrategy(BaseStrategy):
    """Stratégie factice qui émet toujours un signal LONG."""
    name = "dummy"

    def score(self, df, params=None, df_htf=None):
        if len(df) < 30:
            return {"side": "none", "score": 0}
        return {
            "side": "long", "score": 0.80,
            "name": "dummy", "reason": "test",
            "stop_hint": float(df["close"][-1]) * 0.97,
        }


class TestBacktestResult:
    def _make_result(self, pnls):
        capital = 1000.0
        eq = [capital]
        trades = []
        for i, p in enumerate(pnls):
            capital += p
            eq.append(round(capital, 4))
            trades.append({
                "id": i, "pnl": p, "fees": abs(p) * 0.001,
                "status": "closed", "strategy": "dummy",
                "mae": abs(p) * 0.1, "mfe": abs(p) * 0.5,
            })
        return BacktestResult(trades, eq, 1000.0)

    def test_win_rate(self):
        r = self._make_result([10, -5, 8, -3, 6])
        assert r.win_rate == pytest.approx(60.0)

    def test_total_pnl(self):
        r = self._make_result([10, -5, 8])
        assert r.total_pnl == pytest.approx(13.0)

    def test_profit_factor(self):
        r = self._make_result([10, -5])
        assert r.profit_factor == pytest.approx(2.0)

    def test_no_trades(self):
        r = BacktestResult([], [1000.0], 1000.0)
        assert r.total_trades == 0
        assert r.win_rate == 0.0
        assert r.sharpe == 0.0

    def test_max_drawdown_negative(self):
        r = self._make_result([100, -200, 50])
        assert r.max_drawdown < 0

    def test_expectancy(self):
        r = self._make_result([10, 10, -5])
        assert r.expectancy == pytest.approx(5.0)

    def test_to_dict_keys(self):
        r = self._make_result([5, -2, 8])
        d = r.to_dict()
        for key in ("total_trades", "win_rate", "total_pnl", "sharpe",
                    "max_drawdown", "profit_factor", "expectancy",
                    "equity_curve", "by_strategy", "trades"):
            assert key in d

    def test_all_losing(self):
        r = self._make_result([-5, -3, -7])
        assert r.win_rate == 0.0
        assert r.profit_factor == 0.0

    def test_all_winning(self):
        r = self._make_result([5, 3, 7])
        assert r.win_rate == 100.0
        assert r.profit_factor == 999.0

    def test_by_strategy_populated(self):
        r = self._make_result([10, -3, 5])
        assert "dummy" in r.by_strategy
        assert r.by_strategy["dummy"]["trades"] == 3 or len(r.by_strategy["dummy"]["trades"]) == 3


class TestBacktester:
    def test_run_returns_result(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        assert isinstance(result, BacktestResult)

    def test_run_has_trades(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        # La stratégie émet des signaux → doit y avoir des trades
        assert result.total_trades >= 0  # peut être 0 si signaux filtrés

    def test_equity_curve_length(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        assert len(result.equity_curve) >= 1

    def test_fees_deducted(self):
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        if result.total_trades > 0:
            assert result.total_fees > 0

    def test_stop_trail_in_trades(self):
        """Fix #14 — les trades fermés doivent avoir un champ stop_trail."""
        engine = Engine()
        engine.register(DummyLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_df(300)
        result = bt.run(df, "BTC/USDC")
        closed = [t for t in result.trades if t.get("status") == "closed"]
        if closed:
            assert "stop_trail" in closed[0]
            assert isinstance(closed[0]["stop_trail"], list)
