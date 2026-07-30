"""ML-19 — les stratégies bespoke écrivent par le chemin RECETTE.

``_train`` déléguait déjà à ``recipe_trainer`` depuis ML-14, mais l'ÉCRITURE
restait celle de la classe : ``save_lgb_with_scaler`` produit un meta.json
``format_version: 1`` sans liste de features ni médianes. Conséquence mesurable
et mesurée ci-dessous : le scorer générique ne peut pas reconstruire la matrice
d'entrée du holdout, retourne ``unsupported_format``, et le gate refuse de
trancher — « comparaison manuelle requise ». Une recette entraînée
automatiquement mais impossible à promouvoir automatiquement.

``TrainedRecipe.save`` écrit toujours features + médianes. La bascule consiste
donc à GARDER l'objet rendu par ``recipe_trainer.train`` au lieu d'en extraire
les seuls boosters.

Périmètre : ``scoring_statistique_opus_v4`` et ``v5``, les deux recettes dont
l'équivalence a été mesurée (écart 0.000000, ML-14).
``ml_dynamic_threshold`` reste dehors — son ``_train`` n'a jamais été migré
(il entraîne encore via son propre ``_train_lgbm``), donc l'équivalence n'y
est PAS acquise et la bascule y serait un pari, pas une décision.
"""
import datetime as dt
import json
import warnings

import numpy as np
import polars as pl
import pytest

warnings.filterwarnings("ignore")
pytest.importorskip("lightgbm")

STRATEGIES = ["scoring_statistique_opus_v4", "scoring_statistique_opus_v5"]


@pytest.fixture(scope="module")
def ohlcv():
    from app.core.indicators import precompute_df
    rng = np.random.RandomState(5)
    n = 3000
    times = [dt.datetime(2023, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    close = 100 * np.cumprod(1 + rng.normal(0.0002, 0.012, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return precompute_df(pl.DataFrame({
        "time": times, "open": open_, "close": close,
        "high": np.maximum(open_, close) * 1.004,
        "low": np.minimum(open_, close) * 0.996,
        "volume": rng.uniform(100, 1000, n),
    }))


def _fitted(name: str, df: pl.DataFrame):
    import importlib
    strat = importlib.import_module(f"app.strategies.{name}").Strategy()
    strat.fit(df, params={strat.name: {}})
    assert strat.is_trained, f"{name} : fit() n'a produit aucun modèle"
    return strat


@pytest.mark.parametrize("name", STRATEGIES)
class TestRecipeWritePath:
    def test_artifact_carries_features_and_medians(self, name, ohlcv, tmp_path):
        """Le cœur de ML-19 : sans ces deux listes, le holdout n'est pas
        reconstructible et l'artefact est illisible par qui ne connaît pas la
        stratégie qui l'a produit."""
        strat = _fitted(name, ohlcv)
        prefix = str(tmp_path / f"{name}_1h")
        strat.save_model(prefix)

        meta = json.loads((tmp_path / f"{name}_1h.meta.json").read_text(encoding="utf-8"))
        assert meta["format_version"] == 2, "toujours l'ancien format d'écriture"
        assert len(meta["features"]) > 0
        assert len(meta["medians"]) == len(meta["features"])
        assert meta["tf"] == "1h", "le TF écrit est celui DEMANDÉ, pas la clé interne"

    def test_the_gate_can_now_score_it(self, name, ohlcv, tmp_path):
        """La conséquence qui justifie la bascule. On ne teste pas la VALEUR
        de l'AUC — les données sont une marche aléatoire, il n'y a rien à
        apprendre — mais le fait que le scorer rende des métriques au lieu de
        déclarer forfait."""
        import app.ml.policy as policy
        from app.ml.recipe import load_recipe, primary_recipe

        strat = _fitted(name, ohlcv)
        prefix = str(tmp_path / f"{name}_1h")
        strat.save_model(prefix)

        recipe = primary_recipe(type(strat))
        r = load_recipe(recipe)
        gc_ = policy.GateConfig.from_params({**r.gate_spec(), **r.gate_params()})
        m = policy.score_holdout(prefix, ohlcv.tail(1200), strategy=recipe,
                                 gate_cfg=gc_, params=r.train_params())
        assert not m.get("unsupported_format"), (
            "le scorer générique refuse encore l'artefact — la bascule "
            "d'écriture n'a pas pris effet")
        assert m.get("auc_amp") is not None

    def test_legacy_format_was_indeed_unscorable(self, name, ohlcv, tmp_path):
        """Le contre-factuel. Sans lui, le test précédent pourrait passer sur
        un scorer devenu tolérant, et on croirait avoir corrigé un défaut qui
        n'existait plus."""
        import app.ml.policy as policy
        from app.ml.recipe import load_recipe, primary_recipe

        strat = _fitted(name, ohlcv)
        strat._trained_recipes.clear()          # force le chemin historique
        prefix = str(tmp_path / f"{name}_legacy_1h")
        strat.save_model(prefix)

        meta = json.loads((tmp_path / f"{name}_legacy_1h.meta.json").read_text(encoding="utf-8"))
        assert meta["format_version"] == 1
        assert "features" not in meta

        recipe = primary_recipe(type(strat))
        r = load_recipe(recipe)
        gc_ = policy.GateConfig.from_params({**r.gate_spec(), **r.gate_params()})
        m = policy.score_holdout(prefix, ohlcv.tail(1200), strategy=recipe,
                                 gate_cfg=gc_, params=r.train_params())
        assert m.get("unsupported_format") is True

    def test_round_trip_still_works(self, name, ohlcv, tmp_path):
        """Non-régression : le nouveau format doit rester chargeable par le
        ``load_model`` de la stratégie, qui lit toujours les mêmes fichiers."""
        import importlib
        strat = _fitted(name, ohlcv)
        prefix = str(tmp_path / f"{name}_1h")
        strat.save_model(prefix)

        reloaded = importlib.import_module(f"app.strategies.{name}").Strategy()
        assert reloaded.load_model(prefix)
        assert reloaded.is_trained

    def test_a_reloaded_model_falls_back_to_the_legacy_writer(self, name, ohlcv, tmp_path):
        """Un modèle rechargé n'a pas de ``TrainedRecipe`` en mémoire. Le repli
        doit écrire quelque chose plutôt que rien — sinon un aller-retour
        charger/sauvegarder perdrait le modèle en silence."""
        import importlib
        strat = _fitted(name, ohlcv)
        first = str(tmp_path / f"{name}_1h")
        strat.save_model(first)

        reloaded = importlib.import_module(f"app.strategies.{name}").Strategy()
        assert reloaded.load_model(first)
        second = str(tmp_path / f"{name}_again_1h")
        reloaded.save_model(second)
        assert (tmp_path / f"{name}_again_1h.meta.json").exists()
        assert (tmp_path / f"{name}_again_1h.amp.lgb").exists()

    def test_reset_forgets_the_trained_recipe(self, name, ohlcv, tmp_path):
        """Sans ce nettoyage, ``save_model`` après un ``reset_model`` écrirait
        le modèle de l'entraînement PRÉCÉDENT."""
        strat = _fitted(name, ohlcv)
        assert strat._trained_recipes
        strat.reset_model()
        assert not strat._trained_recipes
