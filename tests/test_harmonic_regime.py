"""Tests unitaires — stratégie harmonic_regime."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.strategies.harmonic_regime import Strategy
from app.core.indicators import precompute_df
from app.engine.engine import Engine
from app.engine.backtest import Backtester


def _df(n=400, trend="up", seed=1):
    np.random.seed(seed)
    base = 30000.0
    closes = [base]
    drift = {"up": 0.0015, "down": -0.0015, "flat": 0.0}[trend]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + np.random.randn() * 0.012), 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.randn(n) * 0.004))
    lows = closes * (1 - np.abs(np.random.randn(n) * 0.004))
    opens = np.roll(closes, 1); opens[0] = closes[0]
    vols = np.random.uniform(100, 500, n)
    times = [datetime(2023, 1, 1) + timedelta(hours=4 * i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


def _cfg(params=None):
    return {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "4h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.55, "borrow_rate_daily": 0.00072},
        "backtest": {"spread_pct": 0.0005, "partial_fill_pct": 0.95,
                     "max_notional_pct": 0.50},
        "strategy_params": {"harmonic_regime": params or {}},
        "optimizer_results": {},
    }


class TestHarmonicScore:
    def test_score_returns_valid_dict(self):
        s = Strategy()
        df = precompute_df(_df(400, "up"))
        r = s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")
        assert isinstance(r, dict)
        assert r["side"] in ("long", "short", "none")
        assert 0 <= r["score"] <= 1
        assert r["name"] == "harmonic_regime"

    def test_insufficient_data_returns_none(self):
        s = Strategy()
        df = precompute_df(_df(120, "up"))
        r = s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")
        assert r["side"] == "none"

    def test_signal_has_risk_fields_when_directional(self):
        """Un signal directionnel doit fournir ATR, stop ATR, trailing, max-hold."""
        s = Strategy()
        emitted = False
        for seed in range(8):
            df = precompute_df(_df(500, "up", seed=seed))
            # rejoue plusieurs barres pour avoir une chance d'émettre
            for end in range(380, 500, 5):
                r = s.score(df[:end], _cfg()["strategy_params"], symbol="BTC/USDC")
                if r["side"] != "none":
                    emitted = True
                    assert r["atr"] > 0
                    assert r["sl_atr_mult"] > 0
                    assert r["exit_after_bars"] > 0
                    assert isinstance(r["trail_override"], dict)
                    assert 0 < r["size_factor"] <= 1.5
                    break
            if emitted:
                break
        assert emitted, "Aucun signal directionnel émis sur 8 séries haussières"

    def test_no_crash_on_all_regimes(self):
        s = Strategy()
        for trend in ("up", "down", "flat"):
            df = precompute_df(_df(450, trend))
            r = s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")
            assert r["side"] in ("long", "short", "none")


class TestHarmonicBacktest:
    def test_integrates_with_backtester(self):
        eng = Engine()
        eng.register(Strategy(), silent=True)
        bt = Backtester(eng, _cfg(), use_pretrained_ml=False)
        res = bt.run(_df(600, "up"), "BTC/USDC", timeframe="4h")
        d = res.to_dict()
        assert "total_pnl" in d
        assert d["total_trades"] >= 0
        # equity curve cohérente
        assert len(d["equity_curve"]) >= 1


class TestHarmonicRegistry:
    def test_discovered_by_registry(self):
        from app.engine.registry import get_param_spaces, get_strategy_timeframes
        assert "harmonic_regime" in get_param_spaces()
        assert get_strategy_timeframes()["harmonic_regime"] == ["4h", "1d"]

    def test_param_space_valid(self):
        for k, v in Strategy.param_space.items():
            assert isinstance(v, list) and len(v) > 0
