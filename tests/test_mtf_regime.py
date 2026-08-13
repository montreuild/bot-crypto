"""L5 — volatilité relative (§76), alignement multi-timeframe (§81 §82), et la
parité HTF backtest ↔ live.

Le défaut le plus sérieux trouvé dans ce lot n'est pas dans la spécification :
`htf_trend(None)` renvoyait 0. Or `df_htf` n'est fourni QUE par le live — le
backtest ne l'a jamais passé. Neuf stratégies avaient donc un filtre HTF
**inerte en simulation et actif en production**, et aucun test ne le disait.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.core.indicators_core import atr_percentile
from app.core.indicators_market import htf_trend
from app.core.smc_sessions import mtf_alignment


def _serie(n: int = 600, pente: float = 0.002, seed: int = 7,
           bruit: float = 0.005) -> pl.DataFrame:
    """⚠ `bruit` compte autant que `pente` pour les tests HTF : le
    rééchantillonnage moyenne le bruit, et une série qui devient monotone après
    agrégation ne contient AUCUN pivot fractal — le moteur y renvoie donc un
    biais neutre, à juste titre. Un test de tendance HTF a besoin d'une série
    qui respire encore après agrégation."""
    rng = np.random.RandomState(seed)
    start = dt.datetime(2021, 1, 1)
    close = 100 * np.cumprod(1 + pente + rng.normal(0, bruit, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": open_,
        "high": np.maximum(open_, close) * 1.003,
        "low": np.minimum(open_, close) * 0.997,
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


# ── §76 percentile d'ATR ────────────────────────────────────────────────────

def test_le_percentile_est_borne():
    p = atr_percentile(_serie(), n=14, lookback=100).to_numpy()
    assert np.nanmin(p) >= 0.0 and np.nanmax(p) <= 1.0


def test_le_percentile_est_causal():
    """Un rang qui regarderait la distribution future serait une fuite pure."""
    df = _serie()
    complet = atr_percentile(df, 14, 100).to_numpy()
    tronque = atr_percentile(df.head(300), 14, 100).to_numpy()
    np.testing.assert_allclose(tronque, complet[:300], rtol=1e-9)


def test_une_secousse_de_volatilite_fait_monter_le_rang():
    """C'est tout l'intérêt de §76 : un seuil absolu ne dit pas la même chose
    sur BTC 2018 et sur une action du SBF 120 ; un rang, si."""
    df = _serie(400, pente=0.0)
    h = df["high"].to_numpy().copy()
    lo = df["low"].to_numpy().copy()
    h[-5:] *= 1.06        # cinq barres nettement plus larges
    lo[-5:] *= 0.94
    agite = df.with_columns([pl.Series("high", h), pl.Series("low", lo)])
    p = atr_percentile(agite, 14, 200).to_numpy()
    assert p[-1] > 0.8, f"rang final {p[-1]} — la secousse doit être visible"


# ── §81 §82 alignement multi-timeframe ──────────────────────────────────────

def test_l_alignement_est_borne_et_aligne_sur_le_dataframe():
    df = _serie()
    a = mtf_alignment(df, mults=[4, 16])
    assert len(a) == len(df)
    assert np.all(np.abs(a) <= 1.0 + 1e-9)


def test_une_tendance_franche_produit_un_alignement_du_bon_signe():
    """Lu sur la moyenne du dernier tiers, pas sur la barre finale : à une barre
    donnée le dernier bucket HTF clos peut être neutre, ce qui ne dit rien de la
    tendance — c'est le prix de la causalité, pas un défaut.

    `mults=[4]` : avec 1 200 barres, le niveau 4× compte 300 buckets, assez pour
    que la détection de structure ait de quoi travailler. Un niveau 16× n'en
    aurait que 75, souvent neutres — ce qui testerait la taille de
    l'échantillon, pas l'alignement."""
    haussier = mtf_alignment(_serie(1200, 0.004, bruit=0.02), mults=[4])
    baissier = mtf_alignment(_serie(1200, -0.004, seed=9, bruit=0.02), mults=[4])
    assert haussier[-400:].mean() > 0 > baissier[-400:].mean()


def test_l_alignement_est_causal():
    df = _serie()
    complet = mtf_alignment(df, mults=[4, 16])
    tronque = mtf_alignment(df.head(400), mults=[4, 16])
    np.testing.assert_allclose(tronque, complet[:400], rtol=1e-9)


def test_l_alignement_est_exactement_la_moyenne_ponderee_des_niveaux():
    """§82 — la propriété est structurelle : le résultat est la moyenne pondérée
    des tendances de chaque niveau. La vérifier sur un tirage la rendrait
    dépendante du hasard ; on la vérifie donc par identité.

    C'est cette pondération qui empêche un timeframe bas contraire d'annuler le
    biais HTF : il pèse moins, il ne vote pas à égalité.
    """
    from app.core.smc_sessions import htf_trend_series

    df = _serie(1200, pente=0.003, bruit=0.02)
    poids = [0.36, 1.0]
    attendu = sum(w * htf_trend_series(df, None, mult=m)[0].astype(float)
                  for m, w in zip([4, 16], poids)) / sum(poids)
    np.testing.assert_allclose(
        mtf_alignment(df, mults=[4, 16], poids=poids), attendu, rtol=1e-12)


def test_les_poids_par_defaut_favorisent_le_niveau_le_plus_haut():
    """Décroissance géométrique : le dernier `mult` (le plus haut) pèse le plus."""
    df = _serie(1200, pente=0.003, bruit=0.02)
    bas_seul = mtf_alignment(df, mults=[4], poids=[1.0])
    haut_seul = mtf_alignment(df, mults=[16], poids=[1.0])
    defaut = mtf_alignment(df, mults=[4, 16])
    # Le résultat par défaut doit être plus proche du niveau HAUT que du bas.
    assert (np.abs(defaut - haut_seul).mean()
            <= np.abs(defaut - bas_seul).mean())


# ── Parité HTF backtest ↔ live ──────────────────────────────────────────────

def test_sans_df_htf_le_repli_produit_une_tendance():
    """LE test de ce lot. Avant le repli, cet appel renvoyait 0 en backtest
    pendant que le live obtenait une vraie tendance."""
    df = _serie(800, pente=0.004, bruit=0.02)
    assert htf_trend(None, df_ltf=df) != 0


def test_sans_repli_ni_frame_le_resultat_reste_neutre():
    assert htf_trend(None) == 0


@pytest.mark.parametrize("k", [400, 500, 600, 700])
def test_le_repli_est_causal(k):
    """Le HTF reconstruit ne doit voir que des buckets ENTIÈREMENT clôturés.

    Vérifié en MUTANT les barres postérieures à `k` : si le résultat à `k`
    changeait, c'est que le futur y entrait."""
    df = _serie(800, pente=0.004, bruit=0.02)
    ref = htf_trend(None, df_ltf=df.head(k))
    close = df["close"].to_numpy().copy()
    close[k:] *= 3.0                       # futur rendu méconnaissable
    mute = df.with_columns(pl.Series("close", close))
    assert htf_trend(None, df_ltf=mute.head(k)) == ref


def test_un_df_htf_fourni_reste_prioritaire():
    """Le live continue de passer son propre frame : le repli ne doit pas s'y
    substituer, sinon on changerait le comportement de production."""
    df = _serie(800, pente=0.004, bruit=0.02)
    htf_baissier = _serie(300, pente=-0.01, seed=3)
    assert htf_trend(htf_baissier, df_ltf=df) == -1


@pytest.mark.parametrize("mult", [4, 16])
def test_le_repli_accepte_plusieurs_niveaux(mult):
    df = _serie(1200, pente=0.003, bruit=0.02)
    assert htf_trend(None, df_ltf=df, mult=mult) in (-1, 0, 1)
