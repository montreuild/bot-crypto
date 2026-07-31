"""Les paramètres d'ENTRAÎNEMENT doivent atteindre le backend.

Deux chemins mènent à l'entraînement d'une stratégie MLBackend :
``fit()`` (live, gate, runner) et ``score()`` → ``_train()`` (backtest inline).
Les deux doivent résoudre les paramètres contre les ``_DEFAULTS`` de la
stratégie, sinon le backend applique silencieusement les valeurs de SON
constructeur — et une stratégie qui déclare ``calibrate: False`` ou
``label_horizons: [1]`` s'entraîne quand même avec autre chose.

Découvert en fusionnant opus_omnibus_v10_retrained dans V11 : le preset
s'entraînait avec les réglages de V11, en contradiction avec ses propres
défauts ET avec la recette qu'il déclare.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

import app.ml.backend as bk  # noqa: E402
import app.strategies.opus_omnibus_v10_retrained as v10  # noqa: E402
import app.strategies.opus_omnibus_v11 as v11  # noqa: E402

TRAIN_KEYS = ("label_horizons", "calibrate", "prune_features")


def _ohlcv(n=1800, seed=5):
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pl.DataFrame({
        "time": [dt.datetime(2024, 1, 1) + dt.timedelta(hours=i) for i in range(n)],
        "open": open_, "high": close * 1.004, "low": close * 0.996,
        "close": close, "volume": rng.uniform(100, 1000, n),
    })


@pytest.fixture
def spy(monkeypatch):
    seen = {}
    orig = bk.MLBackend._train_impl_wrapper

    def _spy(self, df, tf_key, params):
        seen[self.name] = {k: params.get(k) for k in TRAIN_KEYS}
        return True          # pas besoin d'entraîner réellement

    monkeypatch.setattr(bk.MLBackend, "_train_impl_wrapper", _spy)
    yield seen
    monkeypatch.setattr(bk.MLBackend, "_train_impl_wrapper", orig)


def test_score_path_resolves_training_params_from_defaults(spy):
    """``score()`` n'appelle pas ``fit()`` : c'est le chemin qui perdait les
    défauts, donc celui qu'il faut verrouiller."""
    s = v10.Strategy()
    s.score(_ohlcv(), params={"opus_omnibus_v10_retrained": {
        "warmup_bars": 750, "retrain_every": 10 ** 9, "enable_hour_filter": False}})
    got = spy.get("opus_omnibus_v10_retrained")
    assert got is not None, "l'entraînement n'a pas été déclenché"
    assert got["label_horizons"] == [1]
    assert got["calibrate"] is False
    assert got["prune_features"] is False


def test_fit_path_resolves_training_params_from_defaults(spy):
    s = v10.Strategy()
    s.fit(_ohlcv(), params={"opus_omnibus_v10_retrained": {}})
    got = spy.get("opus_omnibus_v10_retrained")
    assert got is not None
    assert got["label_horizons"] == [1]
    assert got["calibrate"] is False


def test_explicit_params_still_win_over_defaults(spy):
    """Les défauts comblent les trous ; ils n'écrasent pas un choix explicite."""
    s = v10.Strategy()
    s.score(_ohlcv(), params={"opus_omnibus_v10_retrained": {
        "warmup_bars": 750, "retrain_every": 10 ** 9, "enable_hour_filter": False,
        "calibrate": True, "label_horizons": [1, 3]}})
    got = spy["opus_omnibus_v10_retrained"]
    assert got["calibrate"] is True
    assert got["label_horizons"] == [1, 3]


def test_v11_keeps_its_own_multi_horizon_settings(spy):
    """La correction ne doit pas déteindre sur V11."""
    s = v11.Strategy()
    s.score(_ohlcv(), params={"opus_omnibus_v11": {
        "warmup_bars": 750, "retrain_every": 10 ** 9, "enable_hour_filter": False}})
    got = spy["opus_omnibus_v11"]
    assert got["label_horizons"] == [1, 3, 6]
    assert got["calibrate"] is True
    assert got["prune_features"] is True


def test_preset_training_params_match_its_recipe():
    """Un preset dont les défauts contredisent sa recette produirait un modèle
    gaté sur une cible qu'il n'a pas apprise."""
    from app.ml.recipe import load_recipe, primary_recipe
    for mod in (v10, v11):
        cls = mod.Strategy
        recipe = load_recipe(primary_recipe(cls))
        assert list(cls._DEFAULTS["label_horizons"]) == list(recipe.label_horizons)
        assert cls._DEFAULTS["calibrate"] == recipe.hp.get("calibrate", True)
        assert cls._DEFAULTS["prune_features"] == recipe.hp.get("prune_features", True)
