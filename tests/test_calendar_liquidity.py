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
    lo = df["low"].to_numpy()
    # Jour 1 (barres 0-23) : aucun jour précédent → NaN.
    assert np.isnan(cal["pdh"][:24]).all()
    assert np.isnan(cal["pdl"][:24]).all()
    # Jour 2 (barres 24-47) : PDH/PDL = extrêmes du jour 1, constants.
    assert np.allclose(cal["pdh"][24:48], h[:24].max())
    assert np.allclose(cal["pdl"][24:48], lo[:24].min())
    # Jour 3 : extrêmes du jour 2 uniquement (pas du jour 1 ni du jour 3).
    assert np.allclose(cal["pdh"][48:72], h[24:48].max())
    assert np.allclose(cal["pdl"][48:72], lo[24:48].min())


def test_pwh_pwl_weekly_monday_anchor():
    # Deux semaines complètes + 1 jour, départ un lundi.
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)   # lundi
    df = _hourly_df(start, 24 * 15)
    cal = smc.calendar_liquidity_levels(df)
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    week1 = slice(0, 24 * 7)
    week2 = slice(24 * 7, 24 * 14)
    # Semaine 1 : aucune semaine précédente.
    assert np.isnan(cal["pwh"][week1]).all()
    # Semaine 2 : PWH/PWL = extrêmes de la semaine 1, constants.
    assert np.allclose(cal["pwh"][week2], h[week1].max())
    assert np.allclose(cal["pwl"][week2], lo[week1].min())
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


def test_require_inducement_default_off_and_smoke():
    """SMC-11 : défaut off ; flag on → score() tourne et filtre (smoke)."""
    from app.strategies.smart_money import Strategy
    strat = Strategy()
    assert strat.fixed_params["require_inducement"] is False
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    df = _hourly_df(start, 400)
    sig = strat.score(df, {"smart_money": {"require_inducement": True}})
    assert isinstance(sig, dict) and "side" in sig


def test_smc04_05_06_07_defaults_off_and_smoke():
    """SMC-04/05/06/07 : défauts off ; chaque flag on → score() tourne."""
    from app.strategies.smart_money import Strategy
    fp = Strategy.fixed_params
    assert fp["judas_bonus"] is False and fp["judas_filter"] is False
    assert fp["tp_std_dev"] is False and fp["use_bpr"] is False
    assert fp["sb_bonus"] is False and fp["sb_filter"] is False
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    df = _hourly_df(start, 400)
    for flag in ("judas_bonus", "judas_filter", "tp_std_dev", "use_bpr",
                 "sb_bonus", "sb_filter"):
        sig = Strategy().score(df, {"smart_money": {flag: True}})
        assert isinstance(sig, dict) and "side" in sig, flag


def test_vizion_entry_at_ce_default_off():
    from app.strategies.vizion import Strategy as V
    assert V.fixed_params["entry_at_ce"] is False


def test_fvg_overlap_ce():
    from app.core import ict
    fvgs = [{"kind": "bullish", "index": 5, "filled_at": None,
             "mitigated_at": None, "top": 110.0, "bottom": 100.0}]
    assert ict.fvg_overlap_ce(fvgs, 10, "bullish", 95.0, 105.0) == 105.0
    assert ict.fvg_overlap_ce(fvgs, 10, "bearish", 95.0, 105.0) is None
    assert ict.fvg_overlap_ce(fvgs, 3, "bullish", 95.0, 105.0) is None


def test_smc12_ipda_mode():
    """SMC-12 : mode IPDA causal — range = max/min glissant sur lookback."""
    from app.core import smc
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    df = _hourly_df(start, 350)
    res = smc.analyze(df)
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    c = df["close"].to_numpy()
    i = 300
    pd_ipda = smc.premium_discount_at(res, h, lo, c, i, mode="ipda",
                                      ipda_lookback=20)
    assert pd_ipda is not None
    assert pd_ipda["range_high"] == round(float(h[281:301].max()), 8)
    assert pd_ipda["range_low"] == round(float(lo[281:301].min()), 8)
    # Défaut "swing" inchangé (même dict que l'appel historique).
    assert smc.premium_discount_at(res, h, lo, c, i) == \
        smc.premium_discount_at(res, h, lo, c, i, mode="swing")


def test_smc13_subtype_additive():
    """SMC-13 : subtype additif — mitigation par défaut, ob si structure cassée."""
    from app.core import smc
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    df = _hourly_df(start, 350)
    res = smc.analyze(df)
    obs = res["_all_obs"]
    if obs:
        assert all(ob.get("subtype") in ("ob", "mitigation") for ob in obs)
        # cohérence : strength 2 ⟺ subtype "ob"
        for ob in obs:
            if ob["strength"] >= 2:
                assert ob["subtype"] == "ob"


def test_smc12_13_14_defaults_and_smoke():
    from app.strategies.smart_money import Strategy
    fp = Strategy.fixed_params
    assert fp["pd_mode"] == "swing" and fp["mitigation_mode"] == "off"
    assert fp["amd_session_anchored"] is False
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    df = _hourly_df(start, 400)
    for extra in ({"pd_mode": "ipda"}, {"mitigation_mode": "exclude"},
                  {"mitigation_mode": "penalize"},
                  {"amd_bonus": True, "amd_session_anchored": True}):
        sig = Strategy().score(df, {"smart_money": extra})
        assert isinstance(sig, dict) and "side" in sig, extra
