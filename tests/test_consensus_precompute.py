"""Tests — pré-calcul des votes de signal_consensus (prepare_for_backtest).

Vérifie que :
  1. ``prepare_for_backtest`` remplit le cache de votes bruts indexé par barre ;
  2. ``score()`` consomme ce cache (lookup O(1)) au lieu de réévaluer les
     sous-stratégies — chemin réutilisé par les trials de l'optimiseur ;
  3. faire varier les paramètres de consensus (min_consensus / score_threshold /
     consensus_bonus) ré-agrège le MÊME cache sans le recalculer ;
  4. sans prepare (mode live), ``score()`` reste fonctionnel via le fallback.
"""
import datetime as dt
import os
import sys

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.strategies.signal_consensus import Strategy


def _ohlcv(n=900, seed=7):
    rng = np.random.default_rng(seed)
    t0 = dt.datetime(2024, 1, 1)
    times = [t0 + dt.timedelta(hours=i) for i in range(n)]
    price = 100 + np.cumsum(rng.normal(0, 1.2, n))
    return pl.DataFrame({
        "time": times,
        "open": price,
        "high": price + np.abs(rng.normal(0, 1, n)),
        "low":  price - np.abs(rng.normal(0, 1, n)),
        "close": price,
        "volume": rng.uniform(100, 300, n),
    })


def test_precompute_populates_cache():
    df = _ohlcv()
    s = Strategy()
    s._bt_symbol = ""
    s._bt_tf = "1h"
    s.prepare_for_backtest(df)
    # Une entrée par barre à partir de min_bars_required.
    expected = len(df) - (s.min_bars_required(None) - 1)
    assert len(s._bt_votes) == expected
    # Chaque entrée = un vote brut (name, weight, side, score) par sous-stratégie.
    sample = next(iter(s._bt_votes.values()))
    assert len(sample) == len(s._sub_strategies)
    for name, weight, side, score in sample:
        assert isinstance(name, str)
        assert side in ("long", "short", "none", "error")
        assert 0.0 <= score <= 1.0


def test_cached_score_uses_lookup_not_recompute():
    df = _ohlcv()
    s = Strategy()
    s._bt_symbol = ""
    s.prepare_for_backtest(df)

    # Sabote les sous-stratégies : si score() les réévaluait, il planterait.
    s._sub_strategies = [("boom", _Boom())]

    r = s.score(df, None)
    assert r["name"] == "signal_consensus"
    assert r["side"] in ("long", "short", "none")


def test_consensus_params_reaggregate_same_cache():
    df = _ohlcv()
    s = Strategy()
    s._bt_symbol = ""
    s.prepare_for_backtest(df)
    cache_id = id(s._bt_votes)

    def count_sides(params):
        sides = {"long": 0, "short": 0, "none": 0}
        for i in range(s.min_bars_required(None), len(df)):
            sides[s.score(df[: i + 1], params)["side"]] += 1
        return sides

    loose = count_sides({"signal_consensus": {"min_consensus": 1, "score_threshold": 0.45}})
    strict = count_sides({"signal_consensus": {"min_consensus": 3, "score_threshold": 0.60}})

    # Le cache n'est jamais reconstruit entre deux paramétrages.
    assert id(s._bt_votes) == cache_id
    # Un consensus plus strict émet au plus autant de signaux qu'un consensus lâche.
    assert (strict["long"] + strict["short"]) <= (loose["long"] + loose["short"])


def test_live_fallback_without_prepare():
    df = _ohlcv()
    s = Strategy()  # pas de prepare_for_backtest → pas de cache
    assert s._bt_votes == {}
    r = s.score(df, None)
    assert r["name"] == "signal_consensus"
    assert r["side"] in ("long", "short", "none")


class _Boom:
    """Sous-stratégie qui explose si on l'évalue — détecte un recompute indu."""
    def score(self, *a, **k):
        raise AssertionError("score() ne doit pas réévaluer les sous-stratégies en cache")
