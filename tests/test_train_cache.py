"""Tests du cache process-wide d'entraînements ML (app/core/train_cache.py)."""
import polars as pl
import pytest

from app.core import train_cache


class _FakeStrategy:
    """Reproduit la forme d'état des stratégies opus_* retrained."""
    _TRAIN_STATE_ATTRS = ("_amp_models", "_medians", "_best_auc_per_tf")

    def __init__(self):
        self._amp_models = {}
        self._medians = {}
        self._best_auc_per_tf = {}
        self._trained_tfs = set()
        self._best_auc = 0.0
        self.train_calls = 0

    def _train_impl(self, df, tf_key, params):
        self.train_calls += 1
        self._amp_models[tf_key] = f"model_{self.train_calls}"
        self._medians[tf_key] = {"f1": 0.5}
        self._best_auc_per_tf[tf_key] = 0.7
        self._trained_tfs.add(tf_key)
        return True


def _df(n=100, start=0):
    from datetime import datetime, timedelta
    t0 = datetime(2024, 1, 1)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=start + i) for i in range(n)],
        "close": [100.0 + i for i in range(n)],
    })


@pytest.fixture(autouse=True)
def _clean_cache():
    train_cache.clear()
    yield
    train_cache.clear()


def _run(strategy, df, params):
    return train_cache.cached_train(
        strategy, df, "1h", params, strategy._train_impl,
        strategy._TRAIN_STATE_ATTRS, ("n_estimators",))


def test_cache_hit_restores_state_on_other_instance():
    df = _df()
    a, b = _FakeStrategy(), _FakeStrategy()
    assert _run(a, df, {"n_estimators": 100})
    assert a.train_calls == 1
    assert _run(b, df, {"n_estimators": 100})
    assert b.train_calls == 0          # pas de réentraînement
    assert b._amp_models["1h"] == "model_1"
    assert "1h" in b._trained_tfs
    assert b._best_auc == pytest.approx(0.7)


def test_different_train_params_miss():
    df = _df()
    a = _FakeStrategy()
    _run(a, df, {"n_estimators": 100})
    _run(a, df, {"n_estimators": 200})
    assert a.train_calls == 2


def test_decision_params_ignored_in_key():
    df = _df()
    a, b = _FakeStrategy(), _FakeStrategy()
    _run(a, df, {"n_estimators": 100, "score_threshold": 0.5})
    _run(b, df, {"n_estimators": 100, "score_threshold": 0.9})
    assert b.train_calls == 0          # seuil de décision ≠ clé du cache


def test_different_window_miss():
    a = _FakeStrategy()
    _run(a, _df(100), {"n_estimators": 100})
    _run(a, _df(120), {"n_estimators": 100})
    assert a.train_calls == 2


def test_failed_train_not_cached():
    df = _df()
    a = _FakeStrategy()
    a._train_impl = lambda d, tf, p: False
    assert not train_cache.cached_train(
        a, df, "1h", {}, a._train_impl, a._TRAIN_STATE_ATTRS, ())
    assert train_cache.stats()["entries"] == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Régression : un cache hit ne doit JAMAIS rendre une stratégie « entraînée
#  sans modèle » (découvert en fusionnant v10_retrained dans v11)
# ─────────────────────────────────────────────────────────────────────────────
class _StratUnderscoreAttrs:
    """Stratégie qui expose son état AVEC underscore — cas de V11/V12, qui
    héritent des ``_TRAIN_STATE_ATTRS`` de ``TrainState`` (sans underscore)."""

    def __init__(self):
        self._amp_models = {}
        self._feature_cols = {}
        self._best_auc_per_tf = {}
        self._trained_tfs = set()
        self._best_auc = 0.0

    def _train_impl(self, df, tf_key, params):
        self._amp_models[tf_key] = f"booster-{tf_key}"
        self._feature_cols[tf_key] = ["f0", "f1"]
        self._best_auc_per_tf[tf_key] = 0.61
        self._trained_tfs.add(tf_key)
        return True


# Noms SANS underscore, comme TrainState.STATE_ATTRS.
_STATE_ATTRS_NO_UNDERSCORE = ("amp_models", "feature_cols", "best_auc_per_tf")


def test_cache_hit_restores_the_model_not_just_the_trained_flag():
    """Le snapshot cherchait ``amp_models`` sur une stratégie qui expose
    ``_amp_models`` : il repartait vide, et le hit suivant marquait le TF
    entraîné SANS aucun modèle. En optimisation (plusieurs essais dans un même
    process), tous les essais après le premier produisaient donc « Modèle
    indisponible » et zéro signal."""
    train_cache.clear()
    df = _df()
    params = {"n_estimators": 10}

    a = _StratUnderscoreAttrs()
    assert train_cache.cached_train(a, df, "1h", params, a._train_impl,
                                    _STATE_ATTRS_NO_UNDERSCORE, ("n_estimators",))
    assert a._amp_models["1h"] == "booster-1h"

    b = _StratUnderscoreAttrs()
    assert train_cache.cached_train(b, df, "1h", params, b._train_impl,
                                    _STATE_ATTRS_NO_UNDERSCORE, ("n_estimators",))
    assert train_cache.stats()["hits"] == 1, "le 2e appel doit être un hit"
    assert "1h" in b._trained_tfs
    assert b._amp_models.get("1h") == "booster-1h", \
        "hit sans modèle restauré : la stratégie se croit entraînée à vide"
    assert b._feature_cols.get("1h") == ["f0", "f1"]


class _StratNoState:
    """Aucun attribut d'état reconnaissable — le snapshot ne peut rien capter."""

    def __init__(self):
        self._trained_tfs = set()
        self.calls = 0

    def _train_impl(self, df, tf_key, params):
        self.calls += 1
        self._trained_tfs.add(tf_key)
        return True


def test_empty_snapshot_is_never_cached():
    """Garde-fou indépendant du nommage : ne rien mettre en cache est toujours
    correct, mettre en cache un état vide ne l'est jamais."""
    train_cache.clear()
    df = _df()
    a = _StratNoState()
    train_cache.cached_train(a, df, "1h", {}, a._train_impl, ("amp_models",), ())
    assert train_cache.stats()["entries"] == 0
    assert train_cache.stats()["empty_snapshots"] == 1

    # Le second appel doit RÉELLEMENT réentraîner, pas récupérer un état vide.
    b = _StratNoState()
    train_cache.cached_train(b, df, "1h", {}, b._train_impl, ("amp_models",), ())
    assert b.calls == 1
