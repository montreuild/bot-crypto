"""Bloc de features SMC — causalité, stationnarité, contrat.

Le test qui porte tout est :func:`test_aucune_fuite_temporelle`. Une feature de
structure de marché est construite à partir d'entités (order blocks, FVG,
poches de liquidité) dont la date de CONFIRMATION diffère de la date de
FORMATION. Se tromper de date ne casse rien, ne lève aucune exception, et
produit un modèle dont l'AUC en validation est excellente et l'edge en direct
nul. Aucun test de valeur, de type ou de forme ne l'attrape.

La seule vérification qui l'attrape est celle-ci : recalculer les features sur
un PRÉFIXE de la série et exiger l'égalité avec le calcul sur la série
complète. Si une feature de la barre 3 000 change selon que l'on connaît ou non
la barre 5 000, elle lit le futur.

Ce test a effectivement trouvé deux défauts pendant l'écriture du module — un
ordre de clés qui rendait la validité d'un FVG dépendante de la longueur de la
série, et des compteurs sans péremption qui corrélaient à 0.96 avec l'indice de
barre. Les deux sont documentés dans `app/ml/features_smc.py`.
"""
import pathlib

import numpy as np
import polars as pl
import pytest

from app.ml.features_smc import SMC_FEATURE_NAMES, build_smc_features

PARQUET = pathlib.Path("data/ohlcv/BTC_USDC/1h.parquet")

#: Barres de rodage ignorées : les toutes premières barres n'ont pas encore
#: d'historique SMC et ne sont comparables d'aucun côté.
_RODAGE = 200

#: Tolérance de divergence pour les features de liquidité. Voir
#: :func:`test_aucune_fuite_temporelle` pour la raison — et pourquoi elle va
#: dans le sens conservateur.
_TOLERANCE_LIQ = 0.01


@pytest.fixture(scope="module")
def ohlcv():
    if not PARQUET.exists():
        pytest.skip("cache OHLCV absent")
    df = pl.read_parquet(PARQUET)
    if len(df) < 6000:
        pytest.skip(f"historique insuffisant ({len(df)} barres)")
    return df.tail(6000)


@pytest.fixture(scope="module")
def features(ohlcv):
    f = build_smc_features(ohlcv)
    assert f is not None, "build_smc_features a renvoyé None sur un jeu valide"
    return f


def test_contrat_de_sortie(ohlcv, features):
    """Mêmes lignes que l'entrée, toutes les colonnes annoncées, rien de plus."""
    assert len(features) == len(ohlcv)
    assert list(features.columns) == SMC_FEATURE_NAMES


def test_aucune_valeur_non_finie(features):
    """LightGBM traite les NaN comme une branche à part : une feature qui en
    contient apprend « absence de donnée » comme un signal. Le module doit
    donner une valeur finie partout, y compris quand aucune entité n'existe."""
    for nom in SMC_FEATURE_NAMES:
        col = np.asarray(features[nom].to_numpy(), dtype=float)
        assert np.isfinite(col).all(), f"{nom} contient {int((~np.isfinite(col)).sum())} valeurs non finies"


def test_aucune_feature_constante(features):
    """Une colonne constante ne porte aucune information et fausse le compte de
    features élaguées."""
    for nom in SMC_FEATURE_NAMES:
        col = np.asarray(features[nom].to_numpy(), dtype=float)
        assert float(np.std(col)) > 0.0, f"{nom} est constante"


def test_aucune_fuite_temporelle(ohlcv):
    """LE test. Les features de la barre i ne doivent pas dépendre des barres > i.

    Protocole : calculer sur ``df[:4000]``, calculer sur ``df[:6000]``, tronquer
    le second à 4000, exiger l'égalité.

    Exception admise et bornée — les deux features de distance à la liquidité.
    Le moteur SMC ré-agrège les poches de liquidité : un cluster {3263, 3271}
    formé en 3274 devient {3263, 3277, 3281} formé en 3284 quand des touches
    ultérieures apparaissent au même niveau. La poche existe donc en temps réel
    entre 3274 et 3284, et disparaît de l'analyse a posteriori.

    Le SENS de l'écart décide de sa gravité : c'est le calcul HORS-LIGNE qui
    voit MOINS que le direct, jamais plus. Un modèle entraîné là-dessus est
    donc pénalisé, pas avantagé — l'inverse d'une fuite. On borne à 1 % des
    barres pour que le jour où le moteur changerait de comportement, le test
    le dise.
    """
    K = 4000
    prefixe = build_smc_features(ohlcv.head(K))
    plein = build_smc_features(ohlcv)
    assert prefixe is not None and plein is not None

    divergences = {}
    for nom in SMC_FEATURE_NAMES:
        a = np.asarray(prefixe[nom].to_numpy(), dtype=float)[_RODAGE:]
        b = np.asarray(plein[nom].to_numpy(), dtype=float)[:K][_RODAGE:]
        taux = float((~np.isclose(a, b, rtol=1e-9, atol=1e-9)).mean())
        if taux > 0:
            divergences[nom] = taux

    exactes = [n for n in SMC_FEATURE_NAMES if n not in divergences]
    tolerees = {n: t for n, t in divergences.items() if n.startswith("smc_liq_")}
    interdites = {n: t for n, t in divergences.items() if not n.startswith("smc_liq_")}

    assert not interdites, (
        "features non causales (valeur de la barre i modifiée par des barres > i) : "
        + ", ".join(f"{n} sur {t:.1%} des barres" for n, t in sorted(interdites.items()))
    )
    for nom, taux in tolerees.items():
        assert taux < _TOLERANCE_LIQ, (
            f"{nom} diverge sur {taux:.1%} des barres, au-delà du 1 % attribuable "
            "à la ré-agrégation des poches de liquidité — le moteur SMC a changé "
            "de comportement, il faut re-diagnostiquer avant de relever le seuil"
        )
    assert len(exactes) >= len(SMC_FEATURE_NAMES) - 2


def test_les_compteurs_ne_sont_pas_des_horloges(features):
    """Une feature qui corrèle fortement avec l'indice de barre encode l'époque,
    pas le marché : elle gonfle l'AUC en validation et ne vaut rien en direct.

    C'était le cas de trois compteurs avant que `_intervalles` ne fasse périmer
    les zones (`smc_breaker_n_fresh` r=0.96, `smc_ob_bear_n` r=0.86,
    `smc_fvg_up_n` r=0.82). Ce test empêche la régression.
    """
    idx = np.arange(len(features), dtype=float)
    fautives = []
    for nom in SMC_FEATURE_NAMES:
        col = np.asarray(features[nom].to_numpy(), dtype=float)
        if float(np.std(col)) < 1e-12:
            continue
        r = abs(float(np.corrcoef(idx, col)[0, 1]))
        if r > 0.5:
            fautives.append((nom, r))
    assert not fautives, (
        "features corrélées à l'indice de barre (horloges déguisées) : "
        + ", ".join(f"{n} r={r:.2f}" for n, r in fautives)
    )


def test_serie_trop_courte_renvoie_none():
    """Un historique insuffisant doit donner « pas de bloc SMC », pas une
    exception : une recette sans SMC reste entraînable."""
    court = pl.DataFrame({
        "time": pl.datetime_range(
            pl.datetime(2024, 1, 1), pl.datetime(2024, 1, 2), "1h", eager=True
        )[:10],
        "open": [1.0] * 10, "high": [1.0] * 10,
        "low": [1.0] * 10, "close": [1.0] * 10, "volume": [1.0] * 10,
    })
    assert build_smc_features(court) is None


def test_colonnes_manquantes_renvoie_none():
    """Une frame sans OHLC ne doit pas lever."""
    assert build_smc_features(pl.DataFrame({"close": [1.0] * 100})) is None
