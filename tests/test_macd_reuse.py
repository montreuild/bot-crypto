"""Tests — réutilisation causale de l'histogramme MACD dans les stratégies.

Vérifie que le pré-calcul de la série complète (via prepare_for_backtest +
macd_hist_last3) donne un backtest STRICTEMENT identique au recalcul par fenêtre,
y compris avec des paramètres MACD non par défaut (chemin réellement accéléré).
"""
import os
import sys
import datetime as dt

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine.engine import Engine
from app.engine.backtest import Backtester


def _ohlcv(n=1200, seed=3):
    rng = np.random.default_rng(seed)
    t0 = dt.datetime(2024, 1, 1)
    times = pl.Series([t0 + dt.timedelta(minutes=15 * i) for i in range(n)]).cast(pl.Datetime("ms"))
    price = 100 + np.cumsum(rng.normal(0, 2, n))
    return pl.DataFrame({
        "time": times,
        "open": price + rng.normal(0, 0.1, n),
        "high": price + np.abs(rng.normal(0, 0.8, n)),
        "low":  price - np.abs(rng.normal(0, 0.8, n)),
        "close": price,
        "volume": np.abs(rng.normal(0, 1000, n)) + 100,
    })


def _run(strategy_name, strat_params, disable_reuse=False):
    import importlib
    mod = importlib.import_module(f"app.strategies.{strategy_name}")
    strat = mod.Strategy()
    if disable_reuse:
        # Neutralise le pré-calcul → calcul par fenêtre à chaque barre (référence).
        strat.prepare_for_backtest = lambda df: None
    eng = Engine()
    eng.register(strat, silent=True)
    cfg = {
        "trading": {"timeframe": "15m", "capital": 1000.0, "risk_per_trade": 0.01,
                    "score_threshold": 0.5, "taker_fee": 0.0004, "maker_fee": 0.0002,
                    "max_positions": 1},
        "strategies": {"enabled": [strategy_name]},
        "strategy_params": {strategy_name: strat_params},
    }
    res = Backtester(eng, cfg, use_pretrained_ml=False).run(
        _ohlcv(), "BTC/USDC", timeframe="15m")
    return res.total_trades, round(res.total_pnl, 6)


def test_breakout_opus_custom_macd_reuse_matches_per_window():
    # macd_fast/slow non par défaut → emprunte le chemin de réutilisation.
    params = {"macd_fast": 14, "macd_slow": 22}
    assert _run("breakout_opus", params, disable_reuse=False) == \
        _run("breakout_opus", params, disable_reuse=True)


def test_breakout_opus_default_macd_matches_per_window():
    assert _run("breakout_opus", {}, disable_reuse=False) == \
        _run("breakout_opus", {}, disable_reuse=True)
