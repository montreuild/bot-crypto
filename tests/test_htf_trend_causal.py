"""htf_trend : chemin mémoïsé (full_df + cache) ≡ repli barre à barre (PERF).

``htf_trend(df_htf=None, df_ltf=window)`` reconstruit toute l'agrégation HTF à
chaque appel — O(n) par barre, O(n²) sur un backtest, et 82 % du profil des
sept stratégies qui l'appellent. Le chemin mémoïsé calcule la série complète
une fois puis l'indexe en O(1).

Ces tests valident la seule propriété qui autorise ce raccourci : **égalité
stricte barre à barre** avec le chemin d'origine, sur toute la fenêtre et
jusque dans les zones de garde (moins de 40 barres, moins de 12 buckets, EMA
trop courte), plus le repli explicite sur grille irrégulière.
"""
import numpy as np
import polars as pl
import pytest

from app.core.indicators_causal import htf_trend_ema_series
from app.core.indicators_market import htf_trend


def _make_df(n: int = 900, seed: int = 3, step_ms: int = 3_600_000) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    t0 = np.datetime64("2024-01-01T00:00:00", "ms")
    return pl.DataFrame({
        "time": t0 + np.arange(n) * np.timedelta64(step_ms, "ms"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": np.abs(rng.normal(500, 120, n)),
    })


@pytest.mark.parametrize("ema_period,mult", [(50, 4), (50, 6), (20, 4), (200, 4)])
def test_serie_vectorisee_egale_repli_barre_a_barre(ema_period, mult):
    """La série complète vaut, à chaque indice, le calcul sur le préfixe."""
    df = _make_df(700)
    serie = htf_trend_ema_series(df, ema_period, mult)
    assert serie is not None, "grille régulière : le chemin rapide doit s'appliquer"

    for i in range(len(df)):
        attendu = htf_trend(None, ema_period, df_ltf=df[:i + 1], mult=mult)
        assert int(serie[i]) == attendu, (
            f"barre {i} (ema={ema_period}, mult={mult}) : "
            f"série={int(serie[i])} vs repli={attendu}"
        )


def test_chemin_memoise_de_htf_trend_identique():
    """``full_df``+``cache`` sur htf_trend : mêmes valeurs que sans."""
    df = _make_df(600)
    cache: dict = {}
    for i in range(len(df)):
        window = df[:i + 1]
        assert (htf_trend(None, df_ltf=window, full_df=df, cache=cache)
                == htf_trend(None, df_ltf=window))
    assert len(cache) == 1, "la série ne doit être construite qu'une fois"


def test_le_cache_ne_sert_pas_une_fenetre_non_prefixe():
    """Une fenêtre qui n'est pas un préfixe causal repasse par le calcul direct."""
    df = _make_df(400)
    autre = _make_df(400, seed=99)
    cache: dict = {}
    # ``autre[:300]`` n'est pas un préfixe de ``df`` → aucun raccourci possible.
    assert (htf_trend(None, df_ltf=autre[:300], full_df=df, cache=cache)
            == htf_trend(None, df_ltf=autre[:300]))


def test_grille_irreguliere_repli_sans_perte():
    """Bougie manquante : la série est refusée, htf_trend reste exact."""
    df = _make_df(400)
    troue = pl.concat([df[:200], df[201:]])       # une bougie sautée
    assert htf_trend_ema_series(troue, 50, 4) is None
    cache: dict = {}
    for i in (250, 300, 398):
        assert (htf_trend(None, df_ltf=troue[:i + 1], full_df=troue, cache=cache)
                == htf_trend(None, df_ltf=troue[:i + 1]))


def test_serie_courte_et_peu_de_buckets():
    """Zones de garde : moins de 40 barres, puis moins de 12 buckets HTF."""
    court = _make_df(30)
    assert htf_trend_ema_series(court, 50, 4) is None   # n < 40 → refus

    df = _make_df(120)
    serie = htf_trend_ema_series(df, 50, 4)
    assert serie is not None
    # 120 barres / buckets de 4 → 30 buckets, mais EMA50 exige 53 buckets
    # clôturés : la tendance reste neutre partout, comme le repli.
    assert set(np.unique(serie)) == {0}
    for i in range(len(df)):
        assert int(serie[i]) == htf_trend(None, df_ltf=df[:i + 1])


def test_live_prioritaire_sur_le_cache():
    """``df_htf`` fourni (live) : le cache ne doit jamais s'interposer."""
    df = _make_df(500)
    df_htf = _make_df(300, seed=11)
    cache: dict = {}
    attendu = htf_trend(df_htf)
    assert htf_trend(df_htf, df_ltf=df, full_df=df, cache=cache) == attendu
    assert cache == {}, "chemin live : aucune série ne doit être construite"
