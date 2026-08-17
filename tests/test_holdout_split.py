"""Tranche holdout jamais vue par la recherche (#5).

`df_oos` servait quatre fois : classer les N essais, mesurer le gagnant,
mesurer la référence, et vérifier la consistance walk-forward. Après N
sélections, un score sur cette tranche est un maximum d'ordre N, pas une
estimation hors-échantillon.

Ce qui est vérifié ici : le holdout est bien découpé à la FIN, la recherche ne
le voit jamais (walk-forward compris), et l'absence d'historique suffisant
ramène EXACTEMENT à l'ancien découpage plutôt qu'à un holdout dégénéré.
"""
import polars as pl
import pytest

from app.core.is_oos import (
    HOLDOUT_FRACTION_DEFAULT,
    OOS_FRACTION_DEFAULT,
    split_is_oos,
    split_with_holdout,
)


def _df(n: int) -> pl.DataFrame:
    return pl.DataFrame({"close": [float(i) for i in range(n)]})


class TestDecoupage:
    def test_le_holdout_est_la_fin_de_lhistorique(self):
        df = _df(10_000)
        _, _, _, recherche, holdout = split_with_holdout(df, min_holdout_bars=300)
        assert holdout is not None
        assert len(holdout) == int(10_000 * HOLDOUT_FRACTION_DEFAULT)
        assert len(recherche) + len(holdout) == len(df)
        # contiguïté : la recherche s'arrête où le holdout commence
        assert float(recherche["close"][-1]) + 1 == float(holdout["close"][0])

    def test_la_recherche_ne_voit_jamais_le_holdout(self):
        df = _df(10_000)
        df_is, df_oos, _, recherche, holdout = split_with_holdout(
            df, min_holdout_bars=300)
        debut_holdout = float(holdout["close"][0])
        assert float(df_is["close"][-1]) < debut_holdout
        assert float(df_oos["close"][-1]) < debut_holdout
        # `recherche` remplace `df_full` pour le walk-forward : lui non plus.
        assert float(recherche["close"][-1]) < debut_holdout

    def test_le_decoupage_interne_reste_la_convention_existante(self):
        """Sur la partie recherche, IS/OOS est celui de split_is_oos, à l'identique."""
        df = _df(10_000)
        df_is, df_oos, split, recherche, _ = split_with_holdout(
            df, min_holdout_bars=300)
        a, b, s = split_is_oos(recherche)
        assert (len(df_is), len(df_oos), split) == (len(a), len(b), s)

    def test_proportions(self):
        df = _df(10_000)
        df_is, df_oos, _, _, holdout = split_with_holdout(df, min_holdout_bars=300)
        assert len(holdout) / len(df) == pytest.approx(HOLDOUT_FRACTION_DEFAULT, abs=0.01)
        reste = len(df) - len(holdout)
        assert len(df_oos) / reste == pytest.approx(OOS_FRACTION_DEFAULT, abs=0.01)


class TestRefusFranc:
    def test_historique_trop_court_retombe_sur_lancien_contrat(self):
        """Pas de holdout de 30 bougies : l'ancien découpage, à l'identique."""
        df = _df(1200)
        df_is, df_oos, split, recherche, holdout = split_with_holdout(
            df, min_holdout_bars=600)
        assert holdout is None
        a, b, s = split_is_oos(df)
        assert (len(df_is), len(df_oos), split) == (len(a), len(b), s)
        assert recherche is df

    def test_fraction_nulle_desactive(self):
        df = _df(10_000)
        _, _, _, recherche, holdout = split_with_holdout(df, holdout_fraction=0.0)
        assert holdout is None and recherche is df

    def test_holdout_plus_court_que_le_minimum_refuse(self):
        df = _df(4000)                       # 20 % = 800 barres
        _, _, _, _, holdout = split_with_holdout(df, min_holdout_bars=900)
        assert holdout is None

    def test_df_vide(self):
        assert split_with_holdout(_df(0)) == (None, None, 0, None, None)
        assert split_with_holdout(None) == (None, None, 0, None, None)


class TestRequiredTotalBars:
    def test_le_fetch_reserve_aussi_les_20_pct_de_holdout(self):
        """N-01 : required_total_bars dimensionne pour IS + OOS + holdout."""
        from app.core.is_oos import HOLDOUT_FRACTION_DEFAULT, OOS_FRACTION_DEFAULT
        from app.engine.optimizer_search import required_total_bars

        n = required_total_bars("unknown_strategy_xyz", "1h")
        # Fallback min_bars=220 + fenêtre OOS, divisé par 0.35 × 0.80.
        # Plus grand que l'ancienne formule (÷ 0.35 seulement) d'un facteur 1.25.
        from app.engine.optimizer_search import _oos_trade_window_bars
        oos_needed = 220 + _oos_trade_window_bars("1h")
        old = __import__("math").ceil(oos_needed / OOS_FRACTION_DEFAULT)
        new = __import__("math").ceil(
            oos_needed / (OOS_FRACTION_DEFAULT * (1.0 - HOLDOUT_FRACTION_DEFAULT)))
        assert n == new
        assert n == __import__("math").ceil(old / (1.0 - HOLDOUT_FRACTION_DEFAULT))
        assert n > old


class TestNonRegression:
    def test_split_is_oos_inchange(self):
        """La fonction historique ne doit pas avoir bougé d'un indice."""
        for n in (1000, 5000, 23_456):
            df = _df(n)
            _, _, split = split_is_oos(df)
            assert split == max(210 + 100, int(n * (1.0 - OOS_FRACTION_DEFAULT)))
