"""Recette `omnibus_smc` — contrat et entraînabilité.

Même rôle que `test_recipe_omnibus_full.py` : une recette est un fichier YAML
qu'une virgule suffit à casser, et l'erreur ne se voit qu'au moment où un
entraînement échoue en tâche de fond, plusieurs minutes plus tard.

Le test qui compte vraiment est `test_entrainement_reel_bat_la_reference_sur_dir`
(marqué `slow`) : il re-mesure le gain annoncé par la recette au lieu de faire
confiance à sa description. Une documentation qui ment est pire qu'une absence
de documentation.
"""
import pytest

from app.ml.recipe import load_recipe
from app.ml.recipe_trainer import supports

RECETTE = "omnibus_smc"


@pytest.fixture(scope="module")
def recette():
    return load_recipe(RECETTE)


def test_la_recette_est_entrainable():
    raison = supports(RECETTE)
    assert raison is None, f"recette non entraînable : {raison}"


def test_catalogue_et_schema_sont_enregistres(recette):
    from app.ml.features_catalog import available_catalogs
    from app.ml.labelling import available_schemes

    assert recette.features_catalog == "v5_smc@1"
    assert recette.features_catalog in available_catalogs()
    assert recette.label_scheme in available_schemes()


def test_les_deux_blocs_sont_actives_explicitement(recette):
    """Les bascules valent `true` par défaut dans le constructeur, mais une
    valeur implicite n'entre pas lisiblement dans le hash de la recette — et
    une ablation doit être explicite."""
    assert recette.features_params.get("smc") is True
    assert recette.features_params.get("regime") is True


def test_les_bascules_entrent_dans_le_hash(recette):
    """Sans cela, les quatre variantes d'ablation partageraient une lignée
    d'artefacts et le gate comparerait des modèles différents sous une même
    identité."""
    import dataclasses

    sans_smc = dataclasses.replace(
        recette, features_params={**recette.features_params, "smc": False})
    assert sans_smc.hash() != recette.hash()


def test_seul_le_catalogue_differe_d_omnibus_full(recette):
    """La comparaison n'est interprétable que si le catalogue est le SEUL
    écart. Changer un réglage de labels ici rendrait le gain mesuré
    inattribuable."""
    plein = load_recipe("omnibus_full")
    assert recette.features_catalog != plein.features_catalog
    assert recette.label_scheme == plein.label_scheme
    assert recette.label_horizons == plein.label_horizons
    assert recette.amp_top_pct == plein.amp_top_pct
    assert recette.heads == plein.heads
    assert recette.min_bars == plein.min_bars
    assert recette.gate == plein.gate


def test_elagage_actif(recette):
    """464 colonnes pour quelques milliers de lignes : sans élagage, le modèle
    a de quoi apprendre du bruit."""
    assert recette.hp.get("prune_features") is True


def test_calibration_desactivee_en_1h(recette):
    assert recette.hp.get("calibrate") is True
    assert recette.hp_by_tf.get("1h", {}).get("calibrate") is False


@pytest.mark.slow
def test_entrainement_reel_bat_la_reference_sur_dir(tmp_path):
    """Re-mesure le gain annoncé plutôt que de croire la description.

    Deux entraînements sur le MÊME jeu, seule la bascule `smc` change. Le seuil
    de +0.03 est délibérément à moitié du gain mesuré (+0.0756 sur ce jeu) :
    on veut attraper une régression qui casse le bloc, pas faire échouer le
    test sur la variance normale de LightGBM.
    """
    pytest.importorskip("lightgbm")
    import pathlib

    import polars as pl

    from app.ml.recipe_trainer import train

    parquet = pathlib.Path("data/ohlcv/BTC_USDC/1h.parquet")
    if not parquet.exists():
        pytest.skip("cache OHLCV absent")
    df = pl.read_parquet(parquet)
    if len(df) < 20000:
        pytest.skip(f"historique insuffisant ({len(df)} barres)")

    sans = train(RECETTE, df, "1h", params={"smc": False, "regime": False})
    avec = train(RECETTE, df, "1h", params={"smc": True, "regime": True})
    assert sans is not None and avec is not None

    auc_sans = (sans.train_meta or {}).get("auc_dir", 0.0)
    auc_avec = (avec.train_meta or {}).get("auc_dir", 0.0)
    assert auc_avec - auc_sans > 0.03, (
        f"le bloc SMC n'apporte plus que {auc_avec - auc_sans:+.4f} d'AUC dir "
        f"({auc_sans:.4f} -> {auc_avec:.4f}) ; +0.0756 avaient été mesurés. "
        f"Soit une régression du bloc, soit les features ne sont plus câblées."
    )
    # Le bloc doit aussi ÊTRE UTILISÉ, pas seulement présent : au moins une
    # colonne SMC doit survivre à l'élagage.
    from app.ml.features_smc import SMC_FEATURE_NAMES

    gardees = set((avec.train_meta or {}).get("kept_features") or [])
    assert gardees & set(SMC_FEATURE_NAMES), (
        "aucune colonne SMC retenue par l'élagage — le bloc est construit mais "
        "n'apporte rien au modèle"
    )
