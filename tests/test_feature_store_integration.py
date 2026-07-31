"""Intégration FeatureStore ↔ backtest : cache froid vs chaud doit être identique."""
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

# Stratégie lourde : lightgbm est la seule dépendance externe nécessaire.
# (Un ``importorskip("sklearn")`` traînait ici : le dépôt n'a plus sklearn
# depuis phase6-sklearn-removal, donc ce test SKIPPAIT silencieusement — il ne
# vérifiait plus rien. Ne jamais faire dépendre un skip d'un paquet volontairement
# absent.)
pytest.importorskip("lightgbm")


def _make_df(n=600):
    np.random.seed(11)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(closes[-1] + np.random.randn() * 0.6 + 0.05, 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(np.random.randn(n) * 0.005))
    lows = closes * (1 - np.abs(np.random.randn(n) * 0.005))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    vols = np.random.uniform(1000, 5000, n)
    times = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


def _cfg():
    return {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "latency_ms": 0, "partial_fill_pct": 1.0,
                     "monte_carlo_runs": 10, "walk_forward_folds": 3, "atr_stop_mult": 2.0,
                     "trail_wide": 2.5, "trail_normal": 2.0, "trail_lock": 1.5,
                     "trail_tight": 1.0, "grace_bars": 4, "breakeven_r": 1.2,
                     "lock_r": 2.5, "tight_r": 4.0, "lock_ratio": 0.6,
                     "use_swing": False, "max_notional_pct": 0.2},
        "strategies": {"enabled": ["opus_omnibus_v11"]}, "strategy_params": {},
    }


def test_backtest_cold_vs_warm_cache_identical(tmp_path):
    import app.core.feature_store as fs
    from app.engine.backtest import Backtester
    from app.engine.engine import Engine
    from app.strategies.opus_omnibus_v11 import Strategy

    fs.get_feature_store.set(fs.FeatureStore(base_dir=str(tmp_path)))
    df = _make_df()
    cfg = _cfg()

    def run_once():
        eng = Engine()
        eng.register(Strategy(), silent=True)
        bt = Backtester(eng, cfg)
        bt.ml_mode = "inline"
        return bt.run(df, "BTC/USDC", "1h").to_dict()

    r_cold = run_once()   # build → store
    r_warm = run_once()   # pure lecture cache

    # Le catalogue a bien été écrit.
    assert (tmp_path / "BTC_USDC" / "1h.parquet").exists()
    for k in ("total_trades", "total_pnl", "win_rate", "sharpe", "max_drawdown"):
        assert r_cold[k] == r_warm[k], f"divergence cache froid/chaud sur {k}"
