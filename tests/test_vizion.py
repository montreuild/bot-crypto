"""Tests unitaires — stratégie vizion (modèle ICT strict + SMT générique)."""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.strategies.vizion import Strategy


def _df(n=500, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = np.maximum(200 + 0.3 * t + 12 * np.sin(2 * np.pi * t / 80)
                       + rng.normal(0, 0.6, n).cumsum() * 0.2, 1.0)
    o = np.concatenate([[close[0]], close[:-1]])
    h = np.maximum(o, close) + np.abs(rng.normal(0, 0.4, n))
    lo = np.minimum(o, close) - np.abs(rng.normal(0, 0.4, n))
    times = [datetime(2022, 1, 1) + timedelta(hours=4 * i) for i in range(n)]
    return pl.DataFrame({"time": times, "open": o, "high": h, "low": lo,
                         "close": close, "volume": rng.uniform(1e3, 5e3, n)})


def test_contract_insufficient():
    out = Strategy().score(_df(50))
    assert out["side"] == "none" and out["name"] == "vizion"


def test_prepare_runs_and_cache():
    s = Strategy(); s._bt_params = {"vizion": {"use_smt": False}}
    s.prepare_for_backtest(_df(500, seed=2))
    assert isinstance(s._sig, dict)          # tourne sans erreur (peut être vide)
    for sig in s._sig.values():
        assert sig["side"] in ("long", "short")
        assert sig["stop_hint"] > 0 and 0.5 <= sig["score"] <= 1.0


def test_smt_graceful_without_data():
    """SMT dégradé proprement quand le corrélé est absent → pas de crash."""
    s = Strategy()
    p = s._p({"vizion": {"use_smt": True, "smt_correlate_path": "/does/not/exist.parquet"}})
    assert s._load_smt(_df(120), p) is None
    p2 = s._p({"vizion": {"use_smt": False}})
    assert s._load_smt(_df(120), p2) is None


def test_smt_generic_with_file(tmp_path):
    """SMT GÉNÉRIQUE : un corrélé quelconque (fichier) est aligné et exploité."""
    df = _df(300, seed=3)
    corr = df.select(["time", "close"]).with_columns(
        (pl.col("close") * 1.01).alias("close"))
    path = tmp_path / "corr.parquet"
    corr.write_parquet(path)
    s = Strategy()
    p = s._p({"vizion": {"use_smt": True, "smt_correlate_path": str(path)}})
    smt = s._load_smt(df, p)
    assert smt is not None and len(smt) == df.height
    assert set(np.unique(smt)).issubset({-1, 0, 1})


def test_gates_monotone():
    """Chaque case de la checklist ne peut que RÉDUIRE le nombre de signaux."""
    df = _df(600, seed=5)

    def count(**ov):
        s = Strategy(); s._bt_params = {"vizion": {"use_smt": False, **ov}}
        s.prepare_for_backtest(df)
        return len(s._sig or {})

    base = count(require_sweep=False, require_fvg=False, require_htf_ob=False)
    strict = count(require_sweep=True, require_fvg=True, require_htf_ob=True)
    assert strict <= base
