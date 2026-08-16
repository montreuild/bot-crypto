"""bb_squeeze : troncature et série mémoïsée ≡ calcul de référence (PERF).

Deux raccourcis se superposent dans cette fonction, et chacun doit être exact :

1. la **troncature** historique à ``bb_period + lookback + 2`` barres, qui rend
   l'appel O(1) pour le live et pour toute stratégie non câblée ;
2. la **série mémoïsée** (``full_df`` + ``cache``), qui supprime l'appel polars
   par barre en backtest — 65 % du profil de ``breakout`` après la correction
   de ``htf_trend``.

Les trois formes sont comparées barre à barre à l'implémentation naïve
(rolling sur toute la série, sans troncature ni cache).
"""
import numpy as np
import polars as pl
import pytest

from app.core.indicators_causal import bb_squeeze_series
from app.core.indicators_core import bb_squeeze


def _make_df(n: int = 800, seed: int = 5) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    # Volatilité franchement variable : sans alternance compression/expansion,
    # le squeeze serait constant et le test ne prouverait rien.
    vol = 0.002 + 0.008 * (1 + np.sin(np.arange(n) / 37.0)) / 2
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 1, n) * vol))
    t0 = np.datetime64("2024-01-01T00:00:00", "ms")
    return pl.DataFrame({
        "time": t0 + np.arange(n) * np.timedelta64(3_600_000, "ms"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.abs(rng.normal(500, 120, n)),
    })


def _reference(close: pl.Series, lookback: int, bb_period: int,
               quantile: float) -> bool:
    """bb_squeeze SANS troncature — la définition, telle qu'on l'écrirait."""
    if len(close) < bb_period + lookback:
        return False
    sma = close.rolling_mean(bb_period)
    std = close.rolling_std(bb_period)
    width = 4 * std / sma.clip(lower_bound=1e-9)
    cur = width[-1]
    if cur is None:
        return False
    past = width[-(lookback + 1):-1].drop_nulls()
    return len(past) >= 5 and float(cur) <= float(past.quantile(quantile))


PARAMS = [(15, 20, 0.30), (10, 20, 0.30), (20, 30, 0.30), (15, 20, 0.50)]


@pytest.mark.parametrize("lookback,bb_period,quantile", PARAMS)
def test_troncature_exacte(lookback, bb_period, quantile):
    """La troncature à bb_period+lookback+2 ne change aucune décision."""
    df = _make_df(500)
    close = df["close"]
    for i in range(len(df)):
        assert bb_squeeze(close[:i + 1], lookback, bb_period, quantile) == \
            _reference(close[:i + 1], lookback, bb_period, quantile), f"barre {i}"


@pytest.mark.parametrize("lookback,bb_period,quantile", PARAMS)
def test_serie_memoisee_exacte(lookback, bb_period, quantile):
    """La série vaut, à chaque indice, le calcul de référence sur le préfixe."""
    df = _make_df(500)
    close = df["close"]
    serie = bb_squeeze_series(df, lookback, bb_period, quantile)
    assert serie is not None
    for i in range(len(df)):
        assert bool(serie[i]) == _reference(close[:i + 1], lookback, bb_period,
                                            quantile), f"barre {i}"


def test_la_serie_nest_pas_constante():
    """Garde-fou : un test qui compare deux « False partout » ne prouve rien."""
    serie = bb_squeeze_series(_make_df(800), 15, 20, 0.30)
    assert serie.any() and not serie.all()


def test_chemin_memoise_de_bb_squeeze_identique():
    df = _make_df(400)
    close = df["close"]
    cache: dict = {}
    for i in range(len(df)):
        assert bb_squeeze(close[:i + 1], 15, 20, full_df=df, cache=cache) == \
            bb_squeeze(close[:i + 1], 15, 20)
    assert len(cache) == 1, "la série ne doit être construite qu'une fois"


def test_fenetre_non_prefixe_repasse_par_le_calcul_direct():
    df = _make_df(300)
    autre = _make_df(300, seed=42)
    cache: dict = {}
    assert bb_squeeze(autre["close"][:250], 15, 20, full_df=df, cache=cache) == \
        bb_squeeze(autre["close"][:250], 15, 20)


def test_lookback_sous_le_plancher_de_significativite():
    """``len(past) >= 5`` : sous 5 largeurs, la réponse est False partout."""
    df = _make_df(200)
    serie = bb_squeeze_series(df, 4, 20, 0.30)
    assert not serie.any()
    for i in range(len(df)):
        assert bool(serie[i]) == bb_squeeze(df["close"][:i + 1], 4, 20)
