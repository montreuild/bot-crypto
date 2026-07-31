"""``MLBackendMixin`` — une seule mécanique ML pour toutes les stratégies.

Avant : chaque stratégie ML portait sa propre copie de l'entraînement, de la
persistance, de l'inférence et de l'état par timeframe — 2 396 lignes de
plomberie sur 10 742 (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §1.3).

Ce fichier verrouille que la factorisation tient : les stratégies délèguent
réellement (pas de copie réintroduite), l'état vit dans le backend, et les
deux corrections de bugs portées par le mixin ne peuvent pas être perdues.
"""
import pytest

pytest.importorskip("lightgbm")

from app.ml.backend.mixin import MLBackendMixin  # noqa: E402

MIGRATED = [
    "opus_omnibus_v11",
    "opus_omnibus_v7",
    "opus_omnibus_v11_followsetup",
    "opus_stat_retrained_v4",
    "opus_omnibus_v10_retrained",   # preset de V11
]


def _cls(name):
    import importlib
    return importlib.import_module(f"app.strategies.{name}").Strategy


@pytest.mark.parametrize("name", MIGRATED)
def test_strategy_uses_the_shared_mixin(name):
    assert issubclass(_cls(name), MLBackendMixin), (
        f"{name} ne délègue plus au mixin — une plomberie locale a-t-elle été "
        f"réintroduite ?"
    )


@pytest.mark.parametrize("name", MIGRATED)
def test_state_lives_in_the_backend_not_the_strategy(name):
    """L'état ML doit être UNE seule fois, dans le backend. Des dicts locaux
    en doublon divergeraient du backend sans que rien ne le signale."""
    s = _cls(name)()
    assert hasattr(s, "ml"), f"{name} n'a pas de backend"
    assert s._amp_models is s.ml.state.amp_models
    assert s._feature_cols is s.ml.state.feature_cols
    assert s._trained_tfs is s.ml.state.trained_tfs
    assert s._lock is s.ml._lock


@pytest.mark.parametrize("name", MIGRATED)
def test_no_local_train_impl_remains(name):
    """``_train_impl`` local = code d'entraînement dupliqué. Sa suppression a
    été validée par ``scripts/check_train_impl_equivalence.py`` (écart
    0.000000) ; le réintroduire annulerait cette garantie."""
    cls = _cls(name)
    assert "_train_impl" not in vars(cls), (
        f"{name} redéfinit _train_impl — l'équivalence avec MLBackend n'est "
        f"plus garantie"
    )


def test_train_merges_defaults_under_params():
    """Correction de bug portée par le mixin : ``score()`` entraîne par
    ``_train()`` et ne passe pas tous les paramètres d'entraînement. Sans la
    fusion des ``_DEFAULTS``, le backend appliquait les valeurs de son
    constructeur — une stratégie déclarant ``calibrate: False`` s'entraînait
    quand même en calibré."""
    import inspect
    src = inspect.getsource(MLBackendMixin._train)
    assert "self._DEFAULTS" in src, "la fusion des défauts a disparu de _train"


@pytest.mark.parametrize("name", MIGRATED)
def test_recipe_flags_match_the_declared_recipe(name):
    """Les drapeaux du backend ne doivent pas contredire la recette : ils ne
    sont qu'un repli, mais un repli qui ment est pire que pas de repli."""
    from app.ml.recipe import load_recipe, primary_recipe
    cls = _cls(name)
    recipe = load_recipe(primary_recipe(cls))
    if recipe.persistence == "proxy":
        pytest.skip("recette sans artefact")
    assert cls.ml_calibrate == recipe.hp.get("calibrate", True)
    assert cls.ml_prune_features == recipe.hp.get("prune_features", True)
    assert cls.ml_multi_horizon == (len(recipe.label_horizons) > 1)
