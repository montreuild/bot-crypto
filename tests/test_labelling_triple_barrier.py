"""Schéma `triple_barrier` — la cible qui pose la question du trader.

Ces tests s'appuient sur des séries CONSTRUITES dont on connaît la réponse à
l'avance. C'est nécessaire : sur des données réelles, on ne peut que constater
qu'un label « a l'air raisonnable », ce qui ne distingue pas une implémentation
juste d'une implémentation qui se trompe d'une barre ou de convention.

Le montage donne un ATR exactement égal à 1.0 : bougies plates de corps nul,
amplitude 1.0, pas de gap — donc True Range = 1.0 à chaque barre et moyenne
mobile = 1.0. Les barrières tombent alors sur des valeurs rondes
(`tb_tp_atr=1.5` → cible à +1.5, `tb_sl_atr=1.0` → stop à −1.0), et chaque
assertion peut être calculée de tête.
"""
import numpy as np
import polars as pl
import pytest

from app.ml.labelling import build

PRIX = 100.0
#: Au-delà de `tb_atr_period`, l'ATR du montage vaut exactement 1.0.
RODAGE = 40
PARAMS = {
    "label_horizons": [1, 2],
    "amp_top_pct": 0.30,
    "tb_tp_atr": 1.5,
    "tb_sl_atr": 1.0,
    "tb_max_bars": 5,
    "tb_atr_period": 14,
}


def _serie(n: int = 80) -> dict:
    """Bougies plates : clôture PRIX, mèches ±0.5 → True Range = 1.0 partout."""
    return {
        "open": [PRIX] * n,
        "high": [PRIX + 0.5] * n,
        "low": [PRIX - 0.5] * n,
        "close": [PRIX] * n,
    }


def _frame(d: dict) -> pl.DataFrame:
    return pl.DataFrame(d)


def _labels(d: dict, **surcharges):
    return build("triple_barrier", _frame(d), {**PARAMS, **surcharges})


def test_atr_du_montage_vaut_bien_un():
    """Si ce test tombe, tous les autres deviennent illisibles : leurs seuils
    sont écrits en dur en supposant ATR = 1.0."""
    from app.ml.labelling import _atr_causal

    d = _serie()
    atr = _atr_causal(np.array(d["high"]), np.array(d["low"]),
                      np.array(d["close"]), 14)
    assert atr[RODAGE] == pytest.approx(1.0)


def test_cible_touchee_avant_stop():
    """Cible à 101.5 : une mèche haute à 102 trois barres plus tard la touche."""
    d = _serie()
    d["high"][RODAGE + 3] = 102.0
    lab = _labels(d)
    assert lab.y["tb"][RODAGE] == 1


def test_stop_touche_avant_cible():
    """Stop à 99.0 : une mèche basse à 98.5 le touche — la position est perdue,
    même si le prix montait ensuite."""
    d = _serie()
    d["low"][RODAGE + 2] = 98.5
    d["high"][RODAGE + 4] = 105.0      # remontée POSTÉRIEURE : ne doit rien changer
    lab = _labels(d)
    assert lab.y["tb"][RODAGE] == 0


def test_premier_touche_gagne():
    """Cible ET stop touchés, mais à des barres différentes : c'est le PREMIER
    qui décide. Ici la cible arrive en t+1, le stop en t+3."""
    d = _serie()
    d["high"][RODAGE + 1] = 102.0
    d["low"][RODAGE + 3] = 98.0
    assert _labels(d).y["tb"][RODAGE] == 1


def test_convention_pessimiste_sur_une_meme_bougie():
    """Quand UNE MÊME bougie touche la cible et le stop, l'OHLC ne dit pas
    lequel est venu en premier. On tranche pour le stop.

    Le parti pris inverse produirait des labels optimistes, donc un modèle qui
    annonce un taux de réussite que l'exécution réelle ne tiendra pas — une
    erreur qui ne se voit qu'en production.
    """
    d = _serie()
    d["high"][RODAGE + 2] = 102.0      # cible atteinte
    d["low"][RODAGE + 2] = 98.0        # stop atteint, même bougie
    assert _labels(d).y["tb"][RODAGE] == 0


def test_expiration_compte_comme_un_echec():
    """Aucune barrière touchée avant la verticale : le trade n'aurait pas payé,
    donc 0 — regroupé avec le stop, et distingué dans `stats`."""
    lab = _labels(_serie())
    assert lab.y["tb"][RODAGE] == 0
    assert lab.stats["tb_pct_expire"] == pytest.approx(100.0)
    assert lab.stats["tb_pct_cible"] == pytest.approx(0.0)


def test_la_barriere_verticale_borne_la_recherche():
    """Une cible touchée APRÈS `tb_max_bars` ne compte pas : la position aurait
    déjà été fermée."""
    d = _serie()
    d["high"][RODAGE + 5 + 1] = 110.0
    assert _labels(d).y["tb"][RODAGE] == 0


def test_barrieres_proportionnelles_a_la_volatilite():
    """Le même mouvement en POURCENTAGE ne donne pas le même label selon la
    volatilité ambiante — c'est tout l'intérêt de barrières en ATR.

    Série calme (ATR 1.0) : +1.6 dépasse la cible à +1.5 → gagné.
    Série agitée (ATR 4.0) : le même +1.6 est loin de la cible à +6.0 → non.
    """
    calme = _serie()
    calme["high"][RODAGE + 2] = PRIX + 1.6
    assert _labels(calme).y["tb"][RODAGE] == 1

    n = 80
    agitee = {"open": [PRIX] * n, "close": [PRIX] * n,
              "high": [PRIX + 2.0] * n, "low": [PRIX - 2.0] * n}
    agitee["high"][RODAGE + 2] = PRIX + 1.6   # sous la mèche habituelle
    assert _labels(agitee).y["tb"][RODAGE] == 0


def test_toutes_les_tetes_sont_alignees():
    """`Labels` promet des cibles alignées sur les `n` premières lignes. `tb`
    tronque plus court qu'`amp`/`dir` (barrière verticale plus longue que le
    plus grand horizon) : sans harmonisation, les têtes n'ont pas la même
    longueur et l'appelant doit y penser à la place du schéma."""
    lab = _labels(_serie())
    assert lab.heads == ["amp", "dir", "tb"]
    for tete, y in lab.y.items():
        assert len(y) == lab.n, f"tête {tete} : {len(y)} valeurs pour n={lab.n}"


def test_les_dernieres_barres_ne_sont_pas_labellisables():
    """`n` doit exclure les `tb_max_bars` dernières barres : au-delà,
    « non résolu » voudrait dire « série terminée », pas « expiré »."""
    n = 80
    lab = _labels(_serie(n))
    assert lab.n <= n - PARAMS["tb_max_bars"]


def test_frame_sans_mèches_refuse_de_labelliser():
    """Un label de CHEMIN ne se construit pas sur les seules clôtures : mieux
    vaut refuser que produire une cible silencieusement fausse."""
    sans = pl.DataFrame({"close": [PRIX] * 80})
    assert build("triple_barrier", sans, PARAMS) is None


def test_les_statistiques_couvrent_les_trois_issues():
    d = _serie()
    d["high"][RODAGE + 1] = 102.0
    st = _labels(d).stats
    total = st["tb_pct_cible"] + st["tb_pct_stop"] + st["tb_pct_expire"]
    assert total == pytest.approx(100.0, abs=0.05)
    assert st["scheme"] == "triple_barrier"
    assert st["tb_tp_atr"] == 1.5 and st["tb_sl_atr"] == 1.0
