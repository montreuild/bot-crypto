"""ARCH-03 — contrat du schéma produit par `build_features`.

`build_features` est le premier hub du dépôt : 354 arêtes sortantes vers
`app/core` (indicateurs, SMC, dérivés). Rien ne reliait jusqu'ici un changement
d'indicateur au vecteur de features, donc aux modèles entraînés : une colonne
renommée ou réordonnée passait sans qu'aucun test ne bronche, et se voyait à
l'inférence, sur un modèle déjà publié.

Le hash du schéma rend ce lien visible. Il n'interdit rien — il oblige à
constater le changement et à décider si les modèles en place restent valides.
"""
import datetime as dt
import hashlib

import numpy as np
import polars as pl
import pytest

from app.ml.backend.features import build_features


def _ohlcv(n: int = 400) -> pl.DataFrame:
    """Série déterministe : un contrat de schéma ne doit pas dépendre d'un tirage."""
    rng = np.random.RandomState(7)
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.004, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    debut = dt.datetime(2021, 1, 1)
    return pl.DataFrame({
        "time": [debut + dt.timedelta(hours=i) for i in range(n)],
        "open": open_,
        "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.002, n))),
        "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.002, n))),
        "close": close,
        "volume": np.abs(rng.normal(500, 50, n)),
    })


@pytest.fixture(scope="module")
def feats() -> pl.DataFrame:
    df = build_features(_ohlcv())
    assert df is not None, "400 barres doivent suffire (plancher documenté : 210)"
    return df


#: Schéma attendu. À mettre à jour dans le MÊME commit que le changement de
#: features, après avoir vérifié que les modèles publiés restent utilisables.
NB_COLONNES = 462
HASH_SCHEMA = "ea49a3998169d9c0"


def _hash(noms: list[str]) -> str:
    return hashlib.sha256("\n".join(noms).encode()).hexdigest()[:16]


def test_le_schema_produit_est_stable(feats):
    noms = feats.columns
    assert (len(noms), _hash(noms)) == (NB_COLONNES, HASH_SCHEMA), (
        f"le schéma de build_features a changé : {len(noms)} colonnes, "
        f"hash {_hash(noms)}. Une feature a été ajoutée, retirée, renommée ou "
        f"déplacée — souvent via un indicateur de app/core. Vérifier que les "
        f"modèles déjà publiés restent utilisables, puis actualiser "
        f"NB_COLONNES et HASH_SCHEMA."
    )


def test_l_ordre_des_colonnes_compte(feats):
    """Le hash est sensible à l'ordre, et c'est voulu : LightGBM indexe les
    features par position. Deux colonnes permutées donnent un modèle qui
    prédit sur les mauvaises valeurs sans jamais lever."""
    permute = feats.columns[:]
    permute[10], permute[11] = permute[11], permute[10]
    assert _hash(permute) != HASH_SCHEMA


def test_les_colonnes_ohlcv_ouvrent_le_frame(feats):
    """Contrat d'entrée normalisé : la sortie commence par time+OHLCV, quel que
    soit l'appelant — c'est ce qui rend un cache FeatureStore partageable."""
    assert feats.columns[:6] == ["time", "open", "high", "low", "close", "volume"]


def test_aucune_colonne_en_double(feats):
    """Un doublon fausserait `select_feature_columns` sans rien signaler."""
    noms = feats.columns
    doublons = {n for n in noms if noms.count(n) > 1}
    assert not doublons, doublons


def test_le_schema_ne_depend_pas_des_colonnes_supplementaires(feats):
    """L'entrée est normalisée à time+OHLCV : une colonne technique passée par
    erreur ne doit ni survivre dans la sortie, ni être retenue comme feature."""
    brut = _ohlcv()
    decore = brut.with_columns(pl.lit(1.0).alias("_colonne_parasite"))
    autre = build_features(decore)
    assert autre is not None
    assert "_colonne_parasite" not in autre.columns
    assert autre.columns == feats.columns


def test_sous_le_plancher_de_barres_rien_n_est_produit(feats):
    """210 barres est le plancher documenté : en dessous, `None` plutôt qu'un
    frame partiellement rempli qui entraînerait un modèle sur du vide."""
    assert build_features(_ohlcv(100)) is None
