"""Recette `omnibus_full` — contrat et entraînabilité.

Une recette est un fichier YAML : rien n'empêche de la casser d'une virgule, et
l'erreur ne se voit qu'au moment où un entraînement échoue, en tâche de fond,
plusieurs minutes plus tard. Ces tests la font échouer tout de suite.

Ils verrouillent aussi les deux valeurs qui portent le gain mesuré contre
`omnibus_v4_multi` (horizon 2 présent, `amp_top_pct` à 0.20) : les changer est
légitime, mais doit être un geste conscient et re-mesuré, pas un effet de bord.
"""
import pytest

from app.ml.recipe import load_recipe
from app.ml.recipe_trainer import supports

RECETTE = "omnibus_full"


@pytest.fixture(scope="module")
def recette():
    return load_recipe(RECETTE)


def test_la_recette_est_entrainable():
    """`supports()` renvoie la RAISON du refus — on l'affiche telle quelle."""
    raison = supports(RECETTE)
    assert raison is None, f"recette non entraînable : {raison}"


def test_catalogue_et_schema_sont_enregistres(recette):
    from app.ml.features_catalog import available_catalogs
    from app.ml.labelling import available_schemes

    assert recette.features_catalog in available_catalogs()
    assert recette.label_scheme in available_schemes()


def test_les_deux_tetes_sont_declarees(recette):
    """`amp_dir_quantile` produit deux cibles ; n'en déclarer qu'une
    amputerait la recette de la moitié de son signal."""
    assert set(recette.heads) == {"amp", "dir"}


def test_reglages_porteurs_du_gain_mesure(recette):
    """Les deux seuls réglages qui battent `omnibus_v4_multi`.

    Mesuré sur 4 jeux (BTC/ETH × 1h/30m), gain sur les deux têtes à chaque fois.
    Les modifier sans re-mesurer ferait perdre le gain sans que rien ne le dise.
    """
    assert 2 in recette.label_horizons, (
        "l'horizon 2 porte l'essentiel du gain sur la tête direction"
    )
    assert 12 not in recette.label_horizons, (
        "l'horizon 12 a été mesuré nuisible (AUC amp 0.6921 contre 0.7006)"
    )
    assert recette.amp_top_pct == pytest.approx(0.20), (
        "0.20 bat 0.25 et 0.30 sur BTC (+0.015 d'AUC amp)"
    )


def test_calibration_desactivee_en_1h(recette):
    """Report d'une mesure existante : l'isotone dégrade l'ECE de +461 % en 1h,
    le set de validation y contenant trop peu de positifs."""
    assert recette.hp.get("calibrate") is True
    assert recette.hp_by_tf.get("1h", {}).get("calibrate") is False


def test_elagage_actif(recette):
    """437 colonnes brutes pour quelques milliers de lignes : sans élagage, le
    modèle a de quoi apprendre du bruit."""
    assert recette.hp.get("prune_features") is True


def test_gate_explicite(recette):
    params = recette.gate_params()
    assert params["gate_metric"] == "auc_amp"
    assert params["gate_auc_floor"] == pytest.approx(0.55)
    assert params["gate_holdout_bars"] == 1400


def test_le_hash_change_si_un_reglage_du_modele_change(recette):
    """Garde-fou de lignée : deux réglages différents ne doivent jamais
    partager une même famille d'artefacts."""
    import dataclasses

    autre = dataclasses.replace(recette, amp_top_pct=0.30)
    assert autre.hash() != recette.hash()

    # `gate` et `description` sont des décisions d'exploitation : les changer ne
    # change pas le modèle produit, donc pas le hash.
    meme = dataclasses.replace(recette, description="autre texte")
    assert meme.hash() == recette.hash()


@pytest.mark.slow
def test_entrainement_reel_produit_un_modele_au_dessus_du_plancher(tmp_path):
    """Le seul test qui prouve que la recette sert à quelque chose.

    Ignoré si le cache OHLCV n'est pas disponible (CI sans données).
    """
    pytest.importorskip("lightgbm")
    import pathlib

    import polars as pl

    from app.ml.recipe_trainer import train

    parquet = pathlib.Path("data/ohlcv/BTC_USDC/1h.parquet")
    if not parquet.exists():
        pytest.skip("cache OHLCV absent")
    df = pl.read_parquet(parquet)
    if len(df) < 5000:
        pytest.skip(f"historique insuffisant ({len(df)} barres)")

    entraine = train(RECETTE, df, "1h")
    assert entraine is not None, "train() a renvoyé None"

    meta = entraine.train_meta or {}
    assert meta.get("auc_amp", 0) >= 0.55, (
        f"AUC amp {meta.get('auc_amp')} sous le plancher du gate — "
        "la recette ne serait jamais promue"
    )
    # L'élagage doit retirer quelque chose des 437 colonnes, sinon il ne sert à
    # rien et le réglage `prune_features` ment.
    assert meta.get("n_kept_features", 0) < meta.get("n_features", 0)
