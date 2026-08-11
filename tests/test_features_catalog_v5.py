"""Catalogue `v5_smc@1` et câblage `labels.params`.

Deux choses sont vérifiées ici, et toutes deux relèvent du même risque : un
réglage qui a l'air branché mais ne l'est pas.

1. Les bascules d'ablation (`smc`, `regime`) doivent réellement changer les
   colonnes construites. Une bascule inerte ferait croire à une mesure
   d'ablation alors que les quatre variantes seraient identiques.
2. `labels.params` doit atteindre le schéma de labellisation ET entrer dans le
   hash de la recette. Sans cela, une recette décrivant des barrières à 1.5 ATR
   entraînerait sur les barrières par défaut, en silence.
"""
import pathlib

import polars as pl
import pytest

from app.ml.features_catalog import (
    REGIME_FEATURE_NAMES,
    available_catalogs,
    build,
)
from app.ml.features_smc import SMC_FEATURE_NAMES

PARQUET = pathlib.Path("data/ohlcv/BTC_USDC/1h.parquet")


@pytest.fixture(scope="module")
def ohlcv():
    if not PARQUET.exists():
        pytest.skip("cache OHLCV absent")
    df = pl.read_parquet(PARQUET)
    if len(df) < 3000:
        pytest.skip(f"historique insuffisant ({len(df)} barres)")
    return df.tail(3000)


def test_le_catalogue_est_enregistre():
    assert "v5_smc@1" in available_catalogs()


def test_les_bascules_changent_vraiment_les_colonnes(ohlcv):
    """Le cœur du dispositif d'ablation."""
    base = build("v5_smc@1", ohlcv, {"smc": False, "regime": False})
    regime = build("v5_smc@1", ohlcv, {"smc": False, "regime": True})
    smc = build("v5_smc@1", ohlcv, {"smc": True, "regime": False})
    tout = build("v5_smc@1", ohlcv, {"smc": True, "regime": True})

    assert len(regime.names) == len(base.names) + len(REGIME_FEATURE_NAMES)
    assert len(smc.names) == len(base.names) + len(SMC_FEATURE_NAMES)
    assert len(tout.names) == len(base.names) + len(REGIME_FEATURE_NAMES) + len(SMC_FEATURE_NAMES)


def test_sans_bascule_le_catalogue_egale_v4(ohlcv):
    """`smc=False, regime=False` doit reproduire EXACTEMENT `v4_polars@1` :
    c'est la référence de l'ablation, si elle dérive les écarts ne veulent
    plus rien dire."""
    v4 = build("v4_polars@1", ohlcv)
    v5_nu = build("v5_smc@1", ohlcv, {"smc": False, "regime": False})
    assert v5_nu.names == v4.names


def test_pas_de_collision_de_noms(ohlcv):
    """Une colonne SMC qui porterait le nom d'une colonne v4 en masquerait une
    silencieusement au moment du `select`."""
    fs = build("v5_smc@1", ohlcv, {})
    assert len(fs.names) == len(set(fs.names))


def test_les_lignes_restent_alignees(ohlcv):
    """Le concat horizontal n'a de sens que si les blocs ont le même nombre de
    lignes que les features — sinon chaque colonne SMC serait décalée."""
    fs = build("v5_smc@1", ohlcv, {})
    assert len(fs) == len(ohlcv)
    assert fs.matrix().shape == (len(ohlcv), len(fs.names))


def test_le_regime_est_un_one_hot_exclusif(ohlcv):
    """Les quatre indicatrices doivent sommer à 1 sur chaque barre : un régime
    et un seul. Si la somme dérive, c'est que `classify_regime` a gagné un code
    que le bloc ne connaît pas — et ce régime deviendrait invisible."""
    fs = build("v5_smc@1", ohlcv, {"smc": False, "regime": True})
    indicatrices = [n for n in REGIME_FEATURE_NAMES if n.startswith("regime_is_")]
    somme = fs.frame.select(indicatrices).to_numpy().sum(axis=1)
    assert (somme == 1).all(), (
        f"{int((somme != 1).sum())} barres sans régime unique — "
        f"codes non couverts par le one-hot"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  labels.params
# ─────────────────────────────────────────────────────────────────────────────
def test_label_params_atteint_l_entrainement():
    """Le bloc `labels.params:` doit ressortir dans `train_params()`, qui est
    ce que `recipe_trainer` transmet au labelleur."""
    from app.ml.recipe import load_recipe

    r = load_recipe("omnibus_smc_tb")
    p = r.train_params()
    assert p.get("tb_tp_atr") == 1.5
    assert p.get("tb_sl_atr") == 1.0
    assert p.get("tb_max_bars") == 12


def test_label_params_entre_dans_le_hash():
    """Deux jeux de barrières définissent deux cibles différentes. Partager une
    empreinte les ferait partager une lignée d'artefacts, donc comparer par le
    gate des candidats qui ne répondent pas à la même question."""
    import dataclasses

    from app.ml.recipe import load_recipe

    r = load_recipe("omnibus_smc_tb")
    autre = dataclasses.replace(
        r, label_params={**r.label_params, "tb_tp_atr": 3.0})
    assert autre.hash() != r.hash()


def test_les_trois_recettes_sont_entrainables():
    """`supports()` renvoie la RAISON du refus — on l'affiche telle quelle."""
    from app.ml.recipe_trainer import supports

    for nom in ("omnibus_full", "omnibus_smc", "omnibus_smc_tb"):
        assert supports(nom) is None, f"{nom} : {supports(nom)}"


def test_omnibus_smc_ne_differe_que_par_le_catalogue():
    """La comparaison `omnibus_smc` contre `omnibus_full` n'est interprétable
    que si le catalogue est le SEUL écart. Ce test l'impose."""
    from app.ml.recipe import load_recipe

    plein, smc = load_recipe("omnibus_full"), load_recipe("omnibus_smc")
    assert smc.features_catalog != plein.features_catalog
    assert smc.label_scheme == plein.label_scheme
    assert smc.label_horizons == plein.label_horizons
    assert smc.amp_top_pct == plein.amp_top_pct
    assert smc.heads == plein.heads
    assert smc.min_bars == plein.min_bars
    assert smc.gate == plein.gate


def test_omnibus_smc_tb_ne_differe_que_par_la_cible():
    """Même exigence pour la troisième tête : seuls le schéma, ses paramètres
    et la liste des têtes changent par rapport à `omnibus_smc`."""
    from app.ml.recipe import load_recipe

    smc, tb = load_recipe("omnibus_smc"), load_recipe("omnibus_smc_tb")
    assert tb.features_catalog == smc.features_catalog
    assert tb.features_params == smc.features_params
    assert tb.label_scheme == "triple_barrier" != smc.label_scheme
    assert tb.heads == ["amp", "dir", "tb"]
    assert tb.label_horizons == smc.label_horizons
    assert tb.amp_top_pct == smc.amp_top_pct
