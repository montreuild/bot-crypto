"""Tests unitaires — stratégie momentum_blitz (agressive)."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.indicators import precompute_df
from app.engine.backtest import Backtester
from app.engine.engine import Engine
from app.strategies.momentum_blitz import Strategy


def _df(n=400, trend="up", seed=1):
    np.random.seed(seed)
    base = 30000.0
    closes = [base]
    drift = {"up": 0.002, "down": -0.002, "flat": 0.0}[trend]
    for _ in range(n - 1):
        closes.append(max(closes[-1] * (1 + drift + np.random.randn() * 0.012), 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.randn(n) * 0.005))
    lows = closes * (1 - np.abs(np.random.randn(n) * 0.005))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    # volume avec quelques surges pour déclencher l'ignition
    vols = np.random.uniform(100, 300, n)
    vols[np.random.randint(0, n, n // 10)] *= 3
    times = [datetime(2023, 1, 1) + timedelta(hours=4 * i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


def _cfg(params=None, max_notional=0.5):
    return {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "4h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.52, "borrow_rate_daily": 0.00072},
        "backtest": {"spread_pct": 0.0005, "partial_fill_pct": 0.95,
                     "max_notional_pct": max_notional},
        "strategy_params": {"momentum_blitz": params or {}},
        "optimizer_results": {},
    }


class TestBlitzScore:
    def test_score_returns_valid_dict(self):
        s = Strategy()
        df = precompute_df(_df(400, "up"))
        r = s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")
        assert isinstance(r, dict)
        assert r["side"] in ("long", "short", "none")
        assert 0 <= r["score"] <= 1
        assert r["name"] == "momentum_blitz"

    def test_insufficient_data_none(self):
        s = Strategy()
        df = precompute_df(_df(150, "up"))
        assert s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")["side"] == "none"

    def test_aggressive_sizing_when_directional(self):
        """Un signal directionnel doit demander un sizing agressif (size_factor>1)."""
        s = Strategy()
        emitted = False
        for seed in range(12):
            df = precompute_df(_df(500, "up", seed=seed))
            for end in range(360, 500, 4):
                r = s.score(df[:end], _cfg()["strategy_params"], symbol="BTC/USDC")
                if r["side"] != "none":
                    emitted = True
                    assert r["sl_atr_mult"] > 0
                    assert r["exit_after_bars"] > 0
                    assert r["size_factor"] >= 1.0       # plein capital / agressif
                    assert r["size_factor"] <= 2.0
                    assert isinstance(r["trail_override"], dict)
                    # trailing large (laisse courir)
                    assert r["trail_override"]["trail_wide"] >= 2.0
                    break
            if emitted:
                break
        assert emitted, "Aucun signal agressif émis sur 12 séries haussières"

    def test_short_disabled_by_default(self):
        """Par défaut enable_short=False → aucun short même en tendance baissière."""
        s = Strategy()
        for seed in range(6):
            df = precompute_df(_df(450, "down", seed=seed))
            r = s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")
            assert r["side"] != "short"

    def test_no_crash_all_regimes(self):
        s = Strategy()
        for trend in ("up", "down", "flat"):
            df = precompute_df(_df(450, trend))
            assert s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")["side"] in (
                "long", "short", "none")


class TestBlitzBacktest:
    def test_integrates_with_backtester(self):
        eng = Engine()
        eng.register(Strategy(), silent=True)
        bt = Backtester(eng, _cfg(), ml_mode="inline")
        d = bt.run(_df(700, "up"), "BTC/USDC", timeframe="4h").to_dict()
        assert "total_pnl" in d and d["total_trades"] >= 0


class TestBlitzRegistry:
    def test_discovered(self):
        from app.engine.registry import get_param_spaces, get_strategy_timeframes
        assert "momentum_blitz" in get_param_spaces()
        assert get_strategy_timeframes()["momentum_blitz"] == ["4h"]

    def test_param_space_valid(self):
        for k, v in Strategy.param_space.items():
            assert isinstance(v, list) and len(v) > 0
