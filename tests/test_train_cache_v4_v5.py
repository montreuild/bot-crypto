"""v4/v5/smc_ml_edge : alignement des features d'entraînement + cache.

Deux propriétés distinctes, la première étant une CORRECTION et non une
optimisation :

1. **Alignement.** ``_features_for_training`` servait ``X[:len(df)]`` — les
   premières barres du backtest — quelle que soit la fenêtre reçue. Or
   ``score()`` entraîne sur une fenêtre de QUEUE dès que le backtest dépasse
   ``warmup_bars * 2 + 1`` barres : au-delà, les features venaient du début de
   la série et les labels de la fin.

2. **Cache.** ``cached_train`` doit servir un deuxième entraînement identique,
   et surtout REFUSER de le servir quand ``adx_threshold`` change — ce
   paramètre pilote ici le calcul des features, pas seulement un seuil de
   décision.
"""
import numpy as np
import polars as pl
import pytest

from app.core import train_cache
from app.core.indicators import precompute_df
from app.strategies.scoring_statistique_opus_v4 import Strategy as V4
from app.strategies.scoring_statistique_opus_v4 import _build_features
from app.strategies.scoring_statistique_opus_v5 import Strategy as V5


def _make_df(n: int = 3000, seed: int = 11) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    t0 = np.datetime64("2024-01-01T00:00:00", "ms")
    return precompute_df(pl.DataFrame({
        "time": t0 + np.arange(n) * np.timedelta64(3_600_000, "ms"),
        "open": np.concatenate([[close[0]], close[:-1]]),
        "high": high, "low": low, "close": close,
        "volume": np.abs(rng.normal(500, 120, n)),
    }))


@pytest.mark.parametrize("cls", [V4, V5])
def test_offset_sert_les_barres_de_la_fenetre_recue(cls):
    """Une fenêtre de queue reçoit SES features, pas celles du début."""
    df = _make_df(3000)
    X = _build_features(df, 20.0)
    strat = cls()
    strat._bt_features_cache[20.0] = X
    strat._bt_features_len = len(df)

    n, off = 800, 2000
    tranche = df.slice(off, n)
    servi = strat._features_for_training(tranche, 20.0, off)
    assert np.array_equal(servi, X[off:off + n])

    # Et c'est bien une autre matrice que l'ancien comportement : sans quoi le
    # test passerait aussi sur le code d'avant, donc ne prouverait rien.
    assert not np.array_equal(X[off:off + n], X[:n])


@pytest.mark.parametrize("cls", [V4, V5])
def test_sans_offset_le_prefixe_reste_servi(cls):
    """Fenêtre de tête (offset 0) : comportement historique conservé."""
    df = _make_df(2000)
    X = _build_features(df, 20.0)
    strat = cls()
    strat._bt_features_cache[20.0] = X
    strat._bt_features_len = len(df)
    assert np.array_equal(strat._features_for_training(df.slice(0, 500), 20.0, 0),
                          X[:500])


@pytest.mark.parametrize("cls", [V4, V5])
def test_fenetre_hors_cache_rebuild(cls):
    """offset + longueur au-delà du cache → build direct, jamais un mauvais slice."""
    df = _make_df(1200)
    strat = cls()
    strat._bt_features_cache[20.0] = _build_features(df, 20.0)
    strat._bt_features_len = 600            # cache plus court que demandé
    servi = strat._features_for_training(df.slice(400, 500), 20.0, 400)
    assert servi is not None
    assert np.array_equal(servi, _build_features(df.slice(400, 500), 20.0))


@pytest.mark.parametrize("cls", [V4, V5])
def test_le_cache_sert_un_entrainement_identique(cls, monkeypatch):
    df = _make_df(1500)
    train_cache.clear()
    strat = cls()
    appels = []

    def _impl(train_df, tf_key, params):
        appels.append((len(train_df), params.get("adx_threshold")))
        # état minimal, sinon cached_train refuse un snapshot vide
        strat._amp_models[tf_key] = object()
        strat._dir_models[tf_key] = object()
        strat._trained_tfs.add(tf_key)
        return True

    monkeypatch.setattr(strat, "_train_impl", _impl)
    monkeypatch.setattr(strat, "_seed_bt_predictions", lambda *a, **k: None)

    assert strat._train(df, "BTC/USDC", 20.0, 0.30)
    assert strat._train(df, "BTC/USDC", 20.0, 0.30)
    assert len(appels) == 1, "le 2e entraînement identique devait être servi par le cache"


@pytest.mark.parametrize("cls", [V4, V5])
def test_adx_threshold_invalide_le_cache(cls, monkeypatch):
    """adx_threshold change les FEATURES : partager le modèle serait un faux."""
    df = _make_df(1500)
    train_cache.clear()
    strat = cls()
    appels = []

    def _impl(train_df, tf_key, params):
        appels.append(params.get("adx_threshold"))
        strat._amp_models[tf_key] = object()
        strat._dir_models[tf_key] = object()
        strat._trained_tfs.add(tf_key)
        return True

    monkeypatch.setattr(strat, "_train_impl", _impl)
    monkeypatch.setattr(strat, "_seed_bt_predictions", lambda *a, **k: None)

    strat._train(df, "BTC/USDC", 20.0, 0.30)
    strat._train(df, "BTC/USDC", 25.0, 0.30)
    assert appels == [20.0, 25.0], "deux seuils ADX = deux entraînements"
    assert "adx_threshold" in cls._TRAIN_PARAM_KEYS
