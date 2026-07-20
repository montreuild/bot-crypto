"""SMC-01 — SMT évaluée à la barre d'origine de la zone (flag smt_at_origin)."""
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from app.strategies.smart_money import Strategy


def _df(n: int = 400, seed: int = 11, base: float = 100.0) -> pl.DataFrame:
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    times = [start + timedelta(hours=k) for k in range(n)]
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(0, 0.6, n))
    high = close + rng.uniform(0.1, 1.2, n)
    low = close - rng.uniform(0.1, 1.2, n)
    open_ = close + rng.normal(0, 0.25, n)
    vol = rng.uniform(10, 100, n)
    return pl.DataFrame({
        "time": pl.Series(times).cast(pl.Datetime("ms")),
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
    })


def test_default_off():
    assert Strategy().fixed_params["smt_at_origin"] is False


def test_no_ref_bar_leak_and_smoke(tmp_path):
    # Corrélé synthétique (parquet) → smt_series active réellement le SMT.
    corr = _df(400, seed=22, base=50.0)
    path = tmp_path / "corr.parquet"
    corr.write_parquet(path)

    df = _df(400, seed=11)
    strat = Strategy()
    params = {"smart_money": {
        "smt_correlate_path": str(path),
        "smt_filter": True, "smt_bonus": True, "smt_at_origin": True,
    }}
    sig = strat.score(df, params)
    assert isinstance(sig, dict) and "_smt_ref_bar" not in sig

    # Passe signal par signal : aucun ne doit porter la clé privée.
    strat2 = Strategy()
    p_full = strat2._p(params)
    res, aux = strat2._analyze_cached(df, p_full)
    o = df["open"].to_numpy().astype(float)
    h = df["high"].to_numpy().astype(float)
    lo = df["low"].to_numpy().astype(float)
    c = df["close"].to_numpy().astype(float)
    for i in range(260, len(df)):
        s = strat2._signal_at(res, i, o, h, lo, c, aux, p_full)
        if s is not None:
            assert "_smt_ref_bar" not in s


def test_off_identical_to_baseline(tmp_path):
    """smt_at_origin=False (défaut) → signaux identiques au comportement
    historique (SMT à la barre courante), à params SMT égaux."""
    corr = _df(400, seed=22, base=50.0)
    path = tmp_path / "corr.parquet"
    corr.write_parquet(path)
    df = _df(400, seed=11)

    def signals(extra):
        strat = Strategy()
        p_full = strat._p({"smart_money": {
            "smt_correlate_path": str(path), "smt_filter": True, **extra}})
        res, aux = strat._analyze_cached(df, p_full)
        o = df["open"].to_numpy().astype(float)
        h = df["high"].to_numpy().astype(float)
        lo = df["low"].to_numpy().astype(float)
        c = df["close"].to_numpy().astype(float)
        return [(i, s["side"], round(s["score"], 6))
                for i in range(260, len(df))
                for s in [strat._signal_at(res, i, o, h, lo, c, aux, p_full)]
                if s is not None]

    assert signals({}) == signals({"smt_at_origin": False})
