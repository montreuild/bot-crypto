"""LAB-C — la relation recette ↔ stratégie devient une donnée.

Les deux vocabulaires sont disjoints : aucun des noms de recette n'est un nom
de stratégie. L'écran les empilait sans dire ce qui les relie, et le dialogue
d'entraînement envoyait l'un dans le champ de l'autre. La relation existait
côté serveur (`resolve_recipe_name`) sans être exposée par aucune route.
"""
import pytest

from app.api.routes.ml import _recette_de, _strategies_par_recette
from app.api.schemas import MlRecipe, MLStrategyInfo


def test_les_deux_vocabulaires_sont_bien_disjoints():
    """Le constat lui-même, verrouillé : si un jour un nom coïncide, la
    juxtaposition redevient lisible et ce lot perd sa raison d'être."""
    from app.api.helpers import _discover_strategies
    from app.ml.recipe import available_recipes

    communs = set(available_recipes()) & set(_discover_strategies())
    assert not communs, f"noms communs : {sorted(communs)}"


def test_une_strategie_ml_declare_sa_recette():
    assert _recette_de("opus_omnibus_v11") == "omnibus_v4_multi"
    assert _recette_de("ml_dynamic_threshold") == "dyn_threshold_v1"


def test_une_strategie_sans_recette_rend_none_au_lieu_de_lever():
    """`resolve_recipe_name` lève — c'est correct pour l'entraînement, pas
    pour un inventaire qui doit rendre la ligne quand même."""
    assert _recette_de("smart_money") is None
    assert _recette_de("nom_qui_n_existe_pas") is None


def test_la_relation_inverse_est_complete_et_triee():
    par_recette = _strategies_par_recette()
    assert par_recette, "aucune liaison — test vacant"
    assert par_recette["omnibus_v4_multi"] == ["opus_omnibus_v11", "opus_omnibus_v12"]
    for recette, strategies in par_recette.items():
        assert strategies == sorted(strategies), recette
        for s in strategies:
            assert _recette_de(s) == recette, (s, recette)


@pytest.mark.parametrize("modele,champ", [
    (MLStrategyInfo, "recipe"),
    (MlRecipe, "used_by"),
])
def test_le_lien_fait_partie_du_contrat_publie(modele, champ):
    """Dans le schéma, donc dans generated.ts, donc vérifié par tsc."""
    from pathlib import Path

    assert champ in modele.model_fields
    ts = Path("frontend/src/types/generated.ts").read_text(encoding="utf-8")
    bloc = ts.split(f"export interface {modele.__name__} {{", 1)[1].split("}", 1)[0]
    assert champ in bloc, f"{champ} absent de {modele.__name__}"
