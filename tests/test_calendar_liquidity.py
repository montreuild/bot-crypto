"""SMC-03 — liquidité calendaire PDH/PDL/PWH/PWL (causale, ancrée 00:00 UTC)."""
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from app.core import smc


def _hourly_df(start: datetime, n: int, base: float = 100.0) -> pl.DataFrame:
    times = [start + timedelta(hours=k) for k in range(n)]
    rng = np.random.default_rng(7)
    close = base + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.2, n)
    vol = rng.uniform(10, 100, n)
    return pl.DataFrame({
        "time": pl.Series(times).cast(pl.Datetime("ms")),
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
    })


def test_pdh_pdl_causal():
    # Lundi 00:00 UTC, 4 jours complets en 1h.
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)   # lundi
    df = _hourly_df(start, 24 * 4)
    cal = smc.calendar_liquidity_levels(df)
    assert cal is not None
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    # Jour 1 (barres 0-23) : aucun jour précédent → NaN.
    assert np.isnan(cal["pdh"][:24]).all()
    assert np.isnan(cal["pdl"][:24]).all()
    # Jour 2 (barres 24-47) : PDH/PDL = extrêmes du jour 1, constants.
    assert np.allclose(cal["pdh"][24:48], h[:24].max())
    assert np.allclose(cal["pdl"][24:48], l[:24].min())
    # Jour 3 : extrêmes du jour 2 uniquement (pas du jour 1 ni du jour 3).
    assert np.allclose(cal["pdh"][48:72], h[24:48].max())
    assert np.allclose(cal["pdl"][48:72], l[24:48].min())


def test_pwh_pwl_weekly_monday_anchor():
    # Deux semaines complètes + 1 jour, départ un lundi.
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)   # lundi
    df = _hourly_df(start, 24 * 15)
    cal = smc.calendar_liquidity_levels(df)
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    week1 = slice(0, 24 * 7)
    week2 = slice(24 * 7, 24 * 14)
    # Semaine 1 : aucune semaine précédente.
    assert np.isnan(cal["pwh"][week1]).all()
    # Semaine 2 : PWH/PWL = extrêmes de la semaine 1, constants.
    assert np.allclose(cal["pwh"][week2], h[week1].max())
    assert np.allclose(cal["pwl"][week2], l[week1].min())
    # Lundi de la semaine 3 : extrêmes de la semaine 2.
    assert np.allclose(cal["pwh"][24 * 14:], h[week2].max())


def test_no_time_column_returns_none():
    df = pl.DataFrame({"open": [1.0], "high": [2.0], "low": [0.5],
                       "close": [1.5], "volume": [10.0]})
    assert smc.calendar_liquidity_levels(df) is None


def test_flag_off_is_default_and_aux_none():
    from app.strategies.smart_money import Strategy
    strat = Strategy()
    p = strat._p({})
    assert p["use_calendar_liquidity"] is False
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    df = _hourly_df(start, 300)
    res, aux = strat._analyze_cached(df, p)
    assert aux["cal"] is None
    # Flag actif → niveaux présents.
    p_on = strat._p({"smart_money": {"use_calendar_liquidity": True}})
    res2, aux2 = strat._analyze_cached(df, p_on)
    assert aux2["cal"] is not None and "pdh" in aux2["cal"]


def test_flag_on_score_smoke():
    from app.strategies.smart_money import Strategy
    strat = Strategy()
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    df = _hourly_df(start, 400)
    for mode in (True, "targets", "sweeps"):
        sig = strat.score(df, {"smart_money": {"use_calendar_liquidity": mode}})
        assert isinstance(sig, dict) and "side" in sig
