"""S4-03 — optimizer.ml_final_train_mode : "full" (IS+OOS, défaut historique
inchangé) vs "is_only" (cohérent avec le score OOS rapporté par l'optimiseur,
entraîne uniquement sur l'IS)."""
import sys
import types

import polars as pl
import pytest

from app.engine.auto_optimizer import _save_ml_model_post_opt


class _FakeMLStrategy:
    """Stub minimal : enregistre le DataFrame reçu par fit() sans entraîner
    de vrai modèle (le test porte sur LE CHOIX du DataFrame, pas le ML)."""
    model_dir = "/tmp"

    def __init__(self):
        self.fit_df = None
        self._trained_tfs = {"1h"}

    def fit(self, df, params=None):
        self.fit_df = df

    def save_model(self, path):
        pass


@pytest.fixture
def fake_strategy_module(tmp_path, monkeypatch):
    """Injecte un faux module app.strategies.__ml_leakage_test dans sys.modules."""
    mod_name = "app.strategies.__ml_leakage_test"
    mod = types.ModuleType(mod_name)
    instance_holder = {}

    class _Tracked(_FakeMLStrategy):
        model_dir = str(tmp_path)

        def __init__(self):
            super().__init__()
            instance_holder["instance"] = self

    mod.Strategy = _Tracked
    monkeypatch.setitem(sys.modules, mod_name, mod)
    yield "__ml_leakage_test", instance_holder
    monkeypatch.delitem(sys.modules, mod_name, raising=False)


def _make_df(n=5, tag="full"):
    return pl.DataFrame({
        "time": list(range(n)), "open": [1.0] * n, "high": [1.0] * n,
        "low": [1.0] * n, "close": [1.0] * n, "volume": [1.0] * n,
        "_pre_atr14": [0.1] * n,  # évite le precompute réel dans le test
        "_tag": [tag] * n,
    })


def test_full_mode_trains_on_df_full_by_default(fake_strategy_module):
    name, holder = fake_strategy_module
    df_full = _make_df(tag="full")
    df_is   = _make_df(tag="is")

    _save_ml_model_post_opt(name, {}, df_full, "1h", df_is=df_is, train_mode="full")

    fit_df = holder["instance"].fit_df
    assert fit_df["_tag"][0] == "full"


def test_is_only_mode_trains_on_df_is(fake_strategy_module):
    name, holder = fake_strategy_module
    df_full = _make_df(tag="full")
    df_is   = _make_df(tag="is")

    _save_ml_model_post_opt(name, {}, df_full, "1h", df_is=df_is, train_mode="is_only")

    fit_df = holder["instance"].fit_df
    assert fit_df["_tag"][0] == "is"


def test_is_only_mode_falls_back_to_full_when_df_is_missing(fake_strategy_module):
    """Sans df_is fourni (ex. appelant qui ne l'a pas), le mode is_only ne
    doit pas planter — repli sur df_full plutôt que sur None."""
    name, holder = fake_strategy_module
    df_full = _make_df(tag="full")

    _save_ml_model_post_opt(name, {}, df_full, "1h", df_is=None, train_mode="is_only")

    fit_df = holder["instance"].fit_df
    assert fit_df["_tag"][0] == "full"
