"""Tests unitaires — stratégie volatility_squeeze (rule-based, anti-Omnibus)."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.indicators import precompute_df
from app.engine.backtest import Backtester
from app.engine.engine import Engine
from app.strategies.volatility_squeeze import Strategy, _bb_width_series


def _df_squeeze_then_break(n=400, seed=1):
    """Construit une compression (faible vol) suivie d'une cassure haussière."""
    np.random.seed(seed)
    base = 30000.0
    closes = [base]
    for i in range(n - 1):
        if i < n - 60:
            vol = 0.004 if (n - 120) < i < (n - 60) else 0.012  # squeeze en fin de range
            drift = 0.0008
        else:
            vol = 0.012
            drift = 0.004  # expansion haussière
        closes.append(max(closes[-1] * (1 + drift + np.random.randn() * vol), 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.randn(n) * 0.003))
    lows = closes * (1 - np.abs(np.random.randn(n) * 0.003))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    vols = np.random.uniform(100, 400, n)
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
        "strategy_params": {"volatility_squeeze": params or {}},
        "optimizer_results": {},
    }


class TestBBWidth:
    def test_bb_width_causal_and_positive(self):
        close = np.linspace(100, 110, 100) + np.random.RandomState(0).randn(100)
        w = _bb_width_series(close, 20)
        assert len(w) == 100
        assert np.all(np.isnan(w[:19]))           # causal : NaN avant period-1
        assert np.all(w[19:] >= 0)


class TestSqueezeScore:
    def test_valid_dict(self):
        s = Strategy()
        df = precompute_df(_df_squeeze_then_break(400))
        r = s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")
        assert r["side"] in ("long", "short", "none")
        assert 0 <= r["score"] <= 1
        assert r["name"] == "volatility_squeeze"

    def test_insufficient_data(self):
        s = Strategy()
        df = precompute_df(_df_squeeze_then_break(140))
        assert s.score(df, _cfg()["strategy_params"], symbol="BTC/USDC")["side"] == "none"

    def test_emits_on_squeeze_release(self):
        """Sur une compression suivie d'une cassure haussière, doit pouvoir
        émettre un long avec les champs de risque (rule-based, déterministe)."""
        s = Strategy()
        emitted = False
        for seed in range(10):
            df = precompute_df(_df_squeeze_then_break(420, seed=seed))
            for end in range(360, 420, 3):
                r = s.score(df[:end], _cfg()["strategy_params"], symbol="BTC/USDC")
                if r["side"] == "long":
                    emitted = True
                    assert r["atr"] > 0 and r["sl_atr_mult"] > 0
                    assert r["exit_after_bars"] > 0
                    assert isinstance(r["trail_override"], dict)
                    assert "coil" in r["indicators"]
                    break
            if emitted:
                break
        assert emitted, "Aucune détente de squeeze détectée sur 10 séries"

    def test_no_crash_flat(self):
        s = Strategy()
        np.random.seed(3)
        n = 400
        closes = 30000 * (1 + np.cumsum(np.random.randn(n) * 0.0005))
        df = pl.DataFrame({
            "time": [datetime(2023, 1, 1) + timedelta(hours=4 * i) for i in range(n)],
            "open": closes, "high": closes * 1.001, "low": closes * 0.999,
            "close": closes, "volume": np.random.uniform(100, 300, n)})
        r = s.score(precompute_df(df), _cfg()["strategy_params"], symbol="BTC/USDC")
        assert r["side"] in ("long", "short", "none")


class TestSqueezeBacktest:
    def test_integrates(self):
        eng = Engine()
        eng.register(Strategy(), silent=True)
        d = Backtester(eng, _cfg(), use_pretrained_ml=False).run(
            _df_squeeze_then_break(600), "BTC/USDC", timeframe="4h").to_dict()
        assert "total_pnl" in d and d["total_trades"] >= 0


class TestSqueezeRegistry:
    def test_discovered(self):
        from app.engine.registry import get_param_spaces, get_strategy_timeframes
        assert "volatility_squeeze" in get_param_spaces()
        assert get_strategy_timeframes()["volatility_squeeze"] == ["4h", "1d"]

    def test_no_ml_dependency(self):
        """La stratégie ne doit dépendre d'aucune lib ML (rule-based)."""
        import app.strategies.volatility_squeeze as mod
        src = open(mod.__file__).read()
        assert "lightgbm" not in src and "sklearn" not in src
