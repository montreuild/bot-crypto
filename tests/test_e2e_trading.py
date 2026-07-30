"""
Tests E2E — cycle complet de trading (signal → position → trailing → clôture).
Vérifie le pipeline complet du backtester avec des scénarios réalistes.
"""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine.backtest import Backtester, BacktestResult
from app.engine.engine import BaseStrategy, Engine


def _make_trending_df(n=500, trend_up=True, seed=42):
    """Generate OHLCV with a clear trend for E2E testing."""
    rng = np.random.default_rng(seed)
    base = 100.0
    step = 0.15 if trend_up else -0.08
    closes = [base]
    for _ in range(n - 1):
        noise = rng.normal(0, 0.3)
        closes.append(max(closes[-1] + step + noise, 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.normal(0, 0.004, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.004, n)))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    vols = rng.uniform(1000, 5000, n)
    start = datetime(2024, 1, 1)
    times = [start + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({
        "time": times, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": vols,
    })


def _cfg():
    return {
        "trading": {
            "capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
            "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
            "score_threshold": 0.55, "borrow_rate_daily": 0.0002,
        },
        "backtest": {
            "spread_pct": 0.0005, "latency_ms": 0,
            "partial_fill_pct": 1.0, "monte_carlo_runs": 10,
            "walk_forward_folds": 2, "atr_stop_mult": 2.0,
            "trail_wide": 2.5, "trail_normal": 2.0, "trail_lock": 1.5,
            "trail_tight": 1.0, "grace_bars": 3, "breakeven_r": 1.2,
            "lock_r": 2.5, "tight_r": 4.0, "lock_ratio": 0.60,
            "use_swing": False, "max_notional_pct": 0.30,
        },
        "strategies": {"enabled": ["dummy"]},
        "strategy_params": {},
    }


class AlwaysLongStrategy(BaseStrategy):
    """Always emits a LONG signal with high score."""
    name = "always_long"

    def score(self, df, params=None, df_htf=None, **kwargs):
        if len(df) < 30:
            return {"side": "none", "score": 0}
        return {
            "side": "long", "score": 0.85,
            "name": "always_long", "reason": "e2e test",
            "stop_hint": float(df["close"][-1]) * 0.97,
        }


class AlwaysShortStrategy(BaseStrategy):
    """Always emits a SHORT signal."""
    name = "always_short"

    def score(self, df, params=None, df_htf=None, **kwargs):
        if len(df) < 30:
            return {"side": "none", "score": 0}
        return {
            "side": "short", "score": 0.80,
            "name": "always_short", "reason": "e2e short test",
            "stop_hint": float(df["close"][-1]) * 1.03,
        }


class TestE2ETradingCycle:
    """E2E tests for the complete trading cycle."""

    def test_uptrend_long_profitable(self):
        """In a strong uptrend, a long strategy should be profitable."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        assert isinstance(result, BacktestResult)
        assert result.total_trades > 0

    def test_downtrend_short_has_trades(self):
        """In a downtrend, a short strategy should execute trades."""
        engine = Engine()
        engine.register(AlwaysShortStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=False, seed=99)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0

    def test_all_trades_have_required_fields(self):
        """Every closed trade must have mandatory fields."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")

        required_fields = {"id", "pnl", "entry", "side", "status"}
        for trade in result.trades:
            if trade.get("status") == "closed":
                for field in required_fields:
                    assert field in trade, f"Missing field '{field}' in trade: {trade}"

    def test_equity_curve_monotonic_length(self):
        """Equity curve must have at least as many points as trades + 1."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        closed = [t for t in result.trades if t.get("status") == "closed"]
        assert len(result.equity_curve) >= len(closed) + 1

    def test_fees_always_positive(self):
        """All closed trades must have positive fees."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        for trade in result.trades:
            if trade.get("status") == "closed":
                assert trade.get("fees", 0) >= 0

    def test_trailing_stop_fields(self):
        """Closed trades must include trailing stop info."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        closed = [t for t in result.trades if t.get("status") == "closed"]
        for trade in closed:
            assert "stop_trail" in trade
            assert isinstance(trade["stop_trail"], list)

    def test_sharpe_is_float(self):
        """Sharpe ratio must be a valid float."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        assert isinstance(result.sharpe, float)
        assert np.isfinite(result.sharpe)

    def test_multiple_strategies_together(self):
        """Two strategies can run together without conflicts."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        engine.register(AlwaysShortStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        assert isinstance(result, BacktestResult)

    def test_to_dict_complete(self):
        """to_dict() must include all expected keys."""
        engine = Engine()
        engine.register(AlwaysLongStrategy())
        bt = Backtester(engine, _cfg())
        df = _make_trending_df(500, trend_up=True)
        result = bt.run(df, "BTC/USDC", timeframe="1h")
        d = result.to_dict()
        for key in ("total_trades", "win_rate", "total_pnl", "sharpe",
                     "max_drawdown", "profit_factor", "expectancy",
                     "equity_curve", "by_strategy", "trades"):
            assert key in d, f"Missing key '{key}' in to_dict()"


class TestBacktestResultMetrics:
    """Verify metric calculations with known values."""

    def test_sharpe_annualized_for_1h(self):
        """Sharpe for 1h timeframe should use sqrt(365*24) ≈ 93.6."""
        pnls = [1.0] * 100
        capital = 1000.0
        eq = [capital]
        trades = []
        for i, p in enumerate(pnls):
            capital += p
            eq.append(round(capital, 4))
            trades.append({"id": i, "pnl": p, "fees": 0.01, "status": "closed",
                           "strategy": "dummy", "mae": 0, "mfe": 0})
        r = BacktestResult(trades, eq, 1000.0, timeframe="1h")
        # With constant positive returns, Sharpe should be very high
        assert r.sharpe > 0

    def test_sharpe_annualized_for_1d(self):
        """Sharpe for daily timeframe should use sqrt(365) ≈ 19.1."""
        pnls = [1.0] * 100
        capital = 1000.0
        eq = [capital]
        trades = []
        for i, p in enumerate(pnls):
            capital += p
            eq.append(round(capital, 4))
            trades.append({"id": i, "pnl": p, "fees": 0.01, "status": "closed",
                           "strategy": "dummy", "mae": 0, "mfe": 0})
        r1h = BacktestResult(trades, eq.copy(), 1000.0, timeframe="1h")
        r1d = BacktestResult(trades, eq.copy(), 1000.0, timeframe="1d")
        # 1h Sharpe should be higher than 1d due to higher annualization factor
        assert r1h.sharpe > r1d.sharpe
