"""PERF-05 : prepare_for_backtest ne re-scanne plus toute la série.

cProfile (BTC 1h, 8 k / 32 k barres) : 73 % du backtest était
``prepare_for_backtest`` — ``trendline_value_at``, ``_premium_discount_at``
et les checkers parcouraient toute la liste d'entités à chaque barre
d'événement (O(events × entités)).
"""
from __future__ import annotations

import numpy as np

from app.core import smc
from app.core.smc.geometry import _premium_discount_at
from app.strategies.smart_money import Strategy
from tests.test_smc import _random_df


def _naive_pd(swings, trend_arr, h, lo, c, i):
    return _premium_discount_at(swings, trend_arr, h, lo, c, i,
                               confirmed_at=None)


def _naive_tl(swings, i, kind):
    want = "low" if kind == "support" else "high"
    a = b = None
    for sw in reversed(swings):
        if sw["kind"] != want or sw["confirmed_at"] > i:
            continue
        if b is None:
            b = sw
        else:
            a = sw
            break
    if a is None or b is None or b["index"] == a["index"]:
        return None
    slope = (b["price"] - a["price"]) / (b["index"] - a["index"])
    return float(b["price"] + slope * (i - b["index"]))


def test_perf05_indexes_partition_entities():
    df = _random_df(900)
    res = smc.analyze(df)
    for items, key, field in (
        (res["_all_sweeps"], "_sweeps_at", "index"),
        (res["_all_obs"], "_obs_at", "touched_at"),
        (res["_all_breakers"], "_breakers_at", "touched_at"),
        (res["_all_rejections"], "_rejections_at", "touched_at"),
    ):
        idx = res[key]
        assert isinstance(idx, dict)
        seen = []
        for k, group in idx.items():
            for ent in group:
                assert ent.get(field) == k
                seen.append(id(ent))
        expected = [id(e) for e in items if e.get(field) is not None]
        assert sorted(seen) == sorted(expected)
    assert len(res["_swing_confirmed_at"]) == len(res["_all_swings"])
    if len(res["_all_swings"]) > 1:
        assert np.all(np.diff(res["_swing_confirmed_at"]) >= 0)


def test_perf05_geometry_matches_naive_scan():
    df = _random_df(1100, seed=11)
    res = smc.analyze(df)
    h = df["high"].to_numpy().astype(float)
    lo = df["low"].to_numpy().astype(float)
    c = df["close"].to_numpy().astype(float)
    swings = res["_all_swings"]
    for i in range(40, len(df), 9):
        got = smc.premium_discount_at(res, h, lo, c, i)
        naive = _naive_pd(swings, res["_trend_arr"], h, lo, c, i)
        assert got == naive, f"premium_discount i={i}"
        for kind in ("support", "resistance"):
            assert smc.trendline_value_at(res, i, kind) == _naive_tl(
                swings, i, kind), f"trendline {kind} i={i}"


def test_perf05_signal_at_index_equals_full_scan():
    df = _random_df(700, seed=3)
    strat = Strategy()
    p = strat._p(None)
    res = smc.analyze(df, strat._smc_params(p))
    aux = strat._build_aux(df, p, res)
    close = df["close"].to_numpy().astype(float)
    open_ = df["open"].to_numpy().astype(float)
    low = df["low"].to_numpy().astype(float)
    high = df["high"].to_numpy().astype(float)
    events = sorted(
        {ev["index"] for ev in res["_all_sweeps"] if ev["rejected"]}
        | {ob["touched_at"] for ob in res["_all_obs"]
           if ob["touched_at"] is not None}
    )

    def _fp(sig):
        if sig is None:
            return None
        return (sig["side"], sig.get("setup"), round(float(sig["score"]), 5))

    indexed = {
        i: _fp(strat._signal_at(res, i, open_, high, low, close, aux, p))
        for i in events
    }
    res["_sweeps_at"] = None
    res["_obs_at"] = None
    res["_breakers_at"] = None
    res["_rejections_at"] = None
    res["_swing_confirmed_at"] = None
    scanned = {
        i: _fp(strat._signal_at(res, i, open_, high, low, close, aux, p))
        for i in events
    }
    assert indexed == scanned
