"""Smoke-test du script d'analyse d'indicateurs (scripts/analyze_indicators.py)."""
import os
import sys

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import analyze_indicators as ai  # noqa: E402


def _ohlcv(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = np.maximum(100 + rng.normal(0, 1, n).cumsum(), 1.0)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + np.abs(rng.normal(0, 0.3, n))
    lo = np.minimum(o, c) - np.abs(rng.normal(0, 0.3, n))
    return pl.DataFrame({"open": o, "high": h, "low": lo, "close": c,
                         "volume": rng.uniform(1e3, 5e3, n)})


def test_csv_alias_and_missing_volume(tmp_path):
    p = tmp_path / "s.csv"
    _ohlcv(120).rename({"open": "Open", "high": "High", "low": "Low",
                        "close": "Adj Close"}).drop("volume").write_csv(p)
    df = ai.load_ohlcv(str(p))
    assert {"open", "high", "low", "close", "volume"} <= set(df.columns)
    assert (df["volume"] == 1.0).all()      # volume par défaut si absent


def test_build_signals_and_analyze():
    df = _ohlcv(500, seed=3)
    sigs = ai.build_signals(df)
    assert sigs and all(k in ("trend", "mr") for _, (_, k, _) in sigs.items())
    for _, (s, _, _) in sigs.items():
        assert len(s) == df.height and set(np.unique(s)).issubset({-1, 0, 1})
    rows = ai.analyze(df, taker=0.001, maker=0.0004, oos_frac=0.33)
    assert rows and all("taker" in r and "maker" in r for r in rows)
    for r in rows:
        for fee in ("taker", "maker"):
            for part in ("full", "oos"):
                assert {"n", "pnl", "pf", "sharpe"} <= set(r[fee][part])
