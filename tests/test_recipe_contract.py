"""La recette comme objet de premier ordre — invariants du contrat.

Ce fichier verrouille ce que la recette-fichier est censée garantir, et que
l'ancien « recipe == nom de stratégie » ne garantissait pas :

  * **Chargeabilité** — toute recette référencée existe et est valide.
  * **Identité** — le hash couvre TOUT ce qui change le modèle, notamment
    ``features.params`` (fracture d : ``adx_threshold`` et ``lookahead``
    étaient balayés par l'optimiseur ET changeaient le modèle, sans entrer
    dans la clé du registre).
  * **Partage** — même recette ⇒ même clé de registre (ML-02 §5.5), ce qui
    était inapplicable tant que la clé était le nom de stratégie.
  * **Cohérence** — chaque recette déclare un format de persistance qu'un
    prédicteur sait réellement lire.
"""
import dataclasses
import pathlib

import pytest

from app.ml.predictor import _BY_PERSISTENCE
from app.ml.recipe import (
    KNOWN_PERSISTENCE,
    Recipe,
    available_recipes,
    load_recipe,
    primary_recipe,
    strategy_models,
)
from app.ml.scoring import resolve_gate_spec, resolve_recipe_name

ALL = available_recipes()


def _ml_strategy_names():
    import ast
    out = []
    for f in sorted(pathlib.Path("app/strategies").glob("*.py")):
        if f.stem == "__init__":
            continue
        src = f.read_text(encoding="utf-8")
        if "models:" in src and "Dict[str, str]" in src:
            try:
                ast.parse(src)
            except SyntaxError:
                continue
            out.append(f.stem)
    return out


ML_STRATEGIES = _ml_strategy_names()


# ─────────────────────────────────────────────────────────────────────────────
#  Chargeabilité et validité
# ─────────────────────────────────────────────────────────────────────────────
def test_there_is_at_least_one_recipe():
    assert ALL, "aucune recette dans recipes/ — les liaisons ne résoudront rien"


@pytest.mark.parametrize("name", ALL)
def test_every_recipe_loads(name):
    r = load_recipe(name)
    assert isinstance(r, Recipe) and r.name == name


@pytest.mark.parametrize("name", ALL)
def test_every_recipe_declares_a_readable_persistence(name):
    """Une recette qui déclare un format sans prédicteur serait inchargeable —
    l'erreur doit être impossible, pas découverte au premier entraînement."""
    r = load_recipe(name)
    assert r.persistence in KNOWN_PERSISTENCE
    assert r.persistence == "proxy" or r.persistence in _BY_PERSISTENCE


@pytest.mark.parametrize("name", ALL)
def test_every_recipe_declares_at_least_one_head(name):
    assert load_recipe(name).heads


def test_load_recipe_is_strict_about_missing_files():
    """Une recette absente est une erreur de configuration : un défaut
    silencieux entraînerait « quelque chose » sous un nom qui promet autre
    chose."""
    with pytest.raises(FileNotFoundError):
        load_recipe("recette_qui_nexiste_pas")


def test_load_recipe_rejects_name_mismatch(tmp_path):
    """Le nom de fichier est la clé du registre : un ``recipe:`` interne
    différent créerait deux vérités pour une seule lignée d'artefacts."""
    d = tmp_path / "recipes"
    d.mkdir()
    (d / "vrai_nom.yaml").write_text("recipe: autre_nom\nversion: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fait foi"):
        load_recipe("vrai_nom", recipes_dir=str(d))


def test_unknown_persistence_is_rejected_at_load(tmp_path):
    d = tmp_path / "recipes"
    d.mkdir()
    (d / "r.yaml").write_text("persistence: format_bidon\n", encoding="utf-8")
    with pytest.raises(ValueError, match="persistence"):
        load_recipe("r", recipes_dir=str(d))


# ─────────────────────────────────────────────────────────────────────────────
#  Identité — le hash couvre ce qui change le modèle
# ─────────────────────────────────────────────────────────────────────────────
def test_all_recipe_hashes_are_distinct():
    hashes = {n: load_recipe(n).hash() for n in ALL}
    assert len(set(hashes.values())) == len(hashes), f"collision de hash : {hashes}"


def test_features_params_enter_the_hash():
    """FRACTURE (d) : ``adx_threshold`` est dans ``param_space`` de
    ``scoring_statistique_opus_v4/v5`` ET c'est un argument du builder de
    features. Deux essais d'optimisation produisaient donc des modèles
    différents sous la même clé et le même hash."""
    a = load_recipe("stat48_v4")
    b = dataclasses.replace(a, features_params={**a.features_params, "adx_threshold": 25.0})
    assert a.hash() != b.hash()


def test_lookahead_enters_the_hash():
    """Même fracture côté ``ml_dynamic_threshold`` : ``lookahead`` est
    l'horizon de labellisation ET un paramètre optimisé."""
    a = load_recipe("dyn_threshold_v1")
    b = dataclasses.replace(a, features_params={**a.features_params, "lookahead": 5})
    assert a.hash() != b.hash()


def test_hyperparameters_enter_the_hash():
    a = load_recipe("omnibus_v4_multi")
    b = dataclasses.replace(a, hp={**a.hp, "num_leaves": 63})
    assert a.hash() != b.hash()


def test_version_bump_starts_a_new_lineage():
    a = load_recipe("omnibus_v4_multi")
    assert a.hash() != dataclasses.replace(a, version=a.version + 1).hash()


def test_gate_thresholds_do_NOT_enter_the_hash():
    """Le gate est une décision d'EXPLOITATION : relever un plancher de
    promotion ne change pas le modèle produit, donc ne doit pas ouvrir une
    nouvelle lignée d'artefacts."""
    a = load_recipe("omnibus_v4_multi")
    b = dataclasses.replace(a, gate={**a.gate, "auc_floor": 0.70})
    assert a.hash() == b.hash()


def test_description_does_not_enter_the_hash():
    a = load_recipe("omnibus_v4_multi")
    assert a.hash() == dataclasses.replace(a, description="autre texte").hash()


def test_hash_is_stable_across_calls():
    assert load_recipe("omnibus_v4_multi").hash() == load_recipe("omnibus_v4_multi").hash()


# ─────────────────────────────────────────────────────────────────────────────
#  Partage — ML-02 §5.5 devient exécutable
# ─────────────────────────────────────────────────────────────────────────────
def test_v11_and_v12_share_one_recipe():
    """``v12`` hérite du routing de ``v11`` avec une recette strictement
    identique : il réentraînait pourtant sa propre lignée. Une recette
    partagée = un entraînement, un gate, une alerte de fraîcheur."""
    assert resolve_recipe_name("opus_omnibus_v11") == resolve_recipe_name("opus_omnibus_v12")


def test_single_horizon_generations_share_one_recipe():
    names = {resolve_recipe_name(s) for s in
             ("opus_omnibus_v7", "opus_omnibus_v10_retrained", "opus_stat_retrained_v4")}
    assert names == {"omnibus_v4_single"}


def test_distinct_models_keep_distinct_recipes():
    """Élagage activé ou non produit des modèles différents : recettes
    distinctes, donc lignées d'artefacts distinctes."""
    a = resolve_recipe_name("opus_omnibus_v11")
    b = resolve_recipe_name("opus_omnibus_v11_followsetup")
    assert a != b
    assert load_recipe(a).hp["prune_features"] is True
    assert load_recipe(b).hp["prune_features"] is False


# ─────────────────────────────────────────────────────────────────────────────
#  Liaison stratégie → recette
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("strategy", ML_STRATEGIES)
def test_every_ml_strategy_binds_to_an_existing_recipe(strategy):
    import importlib
    cls = importlib.import_module(f"app.strategies.{strategy}").Strategy
    name = primary_recipe(cls)
    assert name, f"{strategy} déclare `models` mais aucune recette principale"
    assert name in ALL, f"{strategy} -> {name!r} introuvable dans recipes/"


def test_yaml_models_block_overrides_the_class_attribute():
    """C'est ce qui permet de rebrancher une stratégie sur une autre recette
    sans toucher au code — donc de remplacer une famille de fichiers par une
    famille de liaisons."""
    import app.strategies.opus_omnibus_v11 as v11
    assert primary_recipe(v11.Strategy) == "omnibus_v4_multi"
    over = primary_recipe(v11.Strategy, {"models": {"signal": "omnibus_v4_single"}})
    assert over == "omnibus_v4_single"


def test_strategy_without_models_has_no_recipe():
    """Une stratégie classique (sans modèle) doit rester intacte : pas de
    recette, et surtout pas d'erreur."""
    import app.strategies.breakout as brk
    assert primary_recipe(brk.Strategy) is None
    assert strategy_models(brk.Strategy) == {}


def test_resolve_recipe_name_refuses_to_fall_back_to_the_strategy_name():
    """FRACTURE (b) : retomber sur le nom de stratégie ressusciterait le
    défaut qu'on vient de corriger — une lignée d'artefacts par consommateur."""
    import app.strategies.breakout as brk
    with pytest.raises(ValueError, match="ne déclare aucune recette"):
        resolve_recipe_name(brk.Strategy)


# ─────────────────────────────────────────────────────────────────────────────
#  Le gate lit la recette, plus un attribut de classe
# ─────────────────────────────────────────────────────────────────────────────
def test_gate_spec_comes_from_the_recipe_file():
    assert resolve_gate_spec("opus_omnibus_v11")["label_horizons"] == [1, 3, 6]
    assert resolve_gate_spec("opus_omnibus_v7")["label_horizons"] == [1]


def test_dyn_threshold_recipe_declares_its_own_metric():
    """Sans ``metric: auc_dir``, le gate arbitrerait ``ml_dynamic_threshold``
    sur une amplitude que sa recette ne produit pas, et conclurait à un faux
    « indisponible »."""
    assert resolve_gate_spec("ml_dynamic_threshold")["metric"] == "auc_dir"


def test_no_strategy_still_hardcodes_a_gate_spec():
    """Deux déclarations de la même convention finissent toujours par
    diverger — c'est l'origine du décalage 0.732 vs 0.702."""
    offenders = [f.stem for f in pathlib.Path("app/strategies").glob("*.py")
                 if "\n    gate_spec" in f.read_text(encoding="utf-8")]
    assert offenders == [], f"gate_spec en dur (doublon de la recette) : {offenders}"
