"""L4 — hiérarchie de liquidité et qualité des zones (§15, §65–§67, §77–§86, §91).

Le moteur SMC ne distinguait pas un swing local de 15 minutes du plus haut de la
semaine précédente : les deux étaient des « pools », et le ciblage prenait la
PREMIÈRE cible éligible, donc toujours la plus proche. L0 a mesuré ce que ça
donne — la cible n'est atteinte que 28 à 36 % du temps.

Ce module classe et note ce qui existe déjà. Les tests vérifient que les notes
sont bornées, que le classement est ordonné comme la spécification le demande,
et que les fonctions restent causales.
"""
import datetime as dt

import numpy as np
import polars as pl
import pytest

from app.core.smc_quality import (
    CLASSES,
    POIDS_CLASSE,
    classe_liquidite,
    dealing_range,
    inducement,
    irl_erl,
    meilleure_cible,
    opens_calendaires,
    qualite_balayage,
    qualite_displacement,
    qualite_order_block,
    rang_fvg,
    taux_mitigation,
    valeur_attendue,
)

# ── §77 hiérarchie ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("source,attendu", [
    ("pwh", "PREV_WEEK"), ("pwl", "PREV_WEEK"),
    ("pdh", "PREV_DAY"), ("pdl", "PREV_DAY"),
    ("pmh", "HTF_EXTERNAL"),
    ("asian_high", "SESSION"),
    ("swing", "SWING"),
    ("void", "INTERNAL"), ("std_dev", "INTERNAL"), ("", "INTERNAL"),
])
def test_la_source_determine_la_classe(source, attendu):
    assert classe_liquidite(source) == attendu


def test_les_poids_respectent_l_ordre_de_la_specification():
    """§77 — HTF externe > semaine > jour > session > swing > interne. Un ordre
    inversé ferait préférer un gap interne au plus haut de la semaine."""
    poids = [POIDS_CLASSE[c] for c in CLASSES]
    assert poids == sorted(poids, reverse=True)


def test_la_valeur_attendue_croit_avec_le_gain_et_avec_la_classe():
    loin = valeur_attendue(120.0, 100.0, 10.0, "SWING")
    pres = valeur_attendue(110.0, 100.0, 10.0, "SWING")
    assert loin > pres
    noble = valeur_attendue(110.0, 100.0, 10.0, "PREV_WEEK")
    assert noble > pres


def test_un_risque_nul_ne_produit_pas_de_valeur():
    assert valeur_attendue(120.0, 100.0, 0.0, "SWING") == 0.0


# ── §78/§79 choix de cible ──────────────────────────────────────────────────

def test_la_cible_choisie_maximise_la_valeur_attendue():
    """Une poche hebdomadaire un peu plus loin doit battre un swing tout
    proche — c'est exactement ce que le ciblage historique ne savait pas
    faire, puisqu'il prenait la première venue."""
    cibles = [(102.0, "swing"), (108.0, "pwh")]
    best = meilleure_cible(cibles, entree=100.0, risque=2.0, side="long",
                           rr_min=0.5, gain_min_pct=0.1)
    assert best["source"] == "pwh"
    assert best["classe"] == "PREV_WEEK"


def test_les_cibles_ineligibles_sont_ecartees():
    """L'éligibilité (gain minimal, R/R) ne change pas : seul le choix change."""
    assert meilleure_cible([(100.5, "pwh")], 100.0, 2.0, "long",
                           rr_min=2.0, gain_min_pct=0.4) is None


def test_le_short_choisit_du_bon_cote():
    best = meilleure_cible([(98.0, "swing"), (92.0, "pwl")], 100.0, 2.0,
                           "short", rr_min=0.5, gain_min_pct=0.1)
    assert best["source"] == "pwl" and best["price"] < 100.0


def test_a_poids_egaux_le_classement_redevient_par_gain():
    """Propriété de repli : le mécanisme doit être neutralisable, sinon on ne
    peut pas isoler son effet dans une ablation."""
    plat = {c: 1.0 for c in CLASSES}
    best = meilleure_cible([(102.0, "pwh"), (110.0, "swing")], 100.0, 2.0,
                           "long", 0.5, 0.1, proba=plat)
    assert best["source"] == "swing"


# ── §67 dealing range, §66 IRL/ERL ──────────────────────────────────────────

def _res_minimal() -> dict:
    return {
        "_all_swings": [
            {"index": 10, "confirmed_at": 13, "kind": "low", "price": 90.0,
             "swept_at": None},
            {"index": 20, "confirmed_at": 23, "kind": "high", "price": 110.0,
             "swept_at": None},
        ],
        "_all_fvgs": [
            {"index": 25, "top": 106.0, "bottom": 104.0, "filled_at": None},
        ],
        "_atr_arr": np.full(60, 2.0),
    }


def test_le_dealing_range_expose_sa_provenance():
    """§67 — ne pas prendre le dernier high/low sans contexte : la source rend
    le choix lisible dans le journal."""
    rng = dealing_range(_res_minimal(), i=30)
    assert rng["high"] == 110.0 and rng["low"] == 90.0
    assert rng["mid"] == 100.0 and rng["source"] == "swing"


def test_le_dealing_range_ignore_les_swings_non_confirmes():
    """Causalité : un pivot n'existe qu'une fois ses barres de droite connues."""
    assert dealing_range(_res_minimal(), i=11) is None


def test_irl_et_erl_sont_distingues():
    """§66 — un FVG interne n'est pas le bord du range. Les confondre revient à
    poser un TP sur une inefficience en croyant viser un extrême."""
    cibles = irl_erl(_res_minimal(), i=30, prix=100.0, side="long")
    assert cibles["internal_target"] == 105.0     # milieu du FVG
    assert cibles["external_target"] == 110.0     # haut du range


# ── §65 inducement ──────────────────────────────────────────────────────────

def test_l_inducement_retient_le_swing_interne_non_balaye():
    ind = inducement(_res_minimal(), i=30, side="long", prix=100.0)
    assert ind == 90.0


def test_un_swing_deja_balaye_n_est_pas_un_inducement():
    res = _res_minimal()
    res["_all_swings"][0]["swept_at"] = 15
    assert inducement(res, i=30, side="long", prix=100.0) is None


# ── §83 §84 qualité balayage / displacement ─────────────────────────────────

def _barres(n=5):
    o = np.full(n, 100.0)
    h = np.full(n, 101.0)
    lo = np.full(n, 99.0)
    c = np.full(n, 100.0)
    return o, h, lo, c


def test_un_balayage_profond_et_vite_repris_note_plus_haut():
    o, h, lo, c = _barres()
    # Balayage propre : perce loin sous le niveau, clôture bien au-dessus.
    h[2], lo[2], o[2], c[2] = 101.0, 95.0, 100.0, 100.5
    propre = qualite_balayage({"index": 2, "kind": "sell_side", "level": 99.0},
                              h, lo, c, o, atr=2.0)
    # Balayage mou : effleure et clôture au ras.
    o2, h2, lo2, c2 = _barres()
    h2[2], lo2[2], o2[2], c2[2] = 100.2, 98.9, 100.0, 99.05
    mou = qualite_balayage({"index": 2, "kind": "sell_side", "level": 99.0},
                           h2, lo2, c2, o2, atr=2.0)
    assert propre > mou
    assert 0.0 <= mou <= propre <= 1.0


def test_la_note_de_displacement_est_bornee_et_recompense_la_structure():
    o, h, lo, c = _barres()
    o[2], c[2], h[2], lo[2] = 100.0, 103.0, 103.1, 99.9
    sans = qualite_displacement(2, o, h, lo, c, atr=2.0, casse_structure=False)
    avec = qualite_displacement(2, o, h, lo, c, atr=2.0, casse_structure=True)
    assert 0.0 <= sans <= avec <= 1.0
    assert avec > sans, "casser la structure doit valoir un bonus (§84)"


def test_un_atr_nul_ne_produit_pas_de_note():
    o, h, lo, c = _barres()
    assert qualite_displacement(2, o, h, lo, c, atr=0.0) == 0.0


# ── §15 mitigation, §85 rang FVG, §86 qualité OB ────────────────────────────

def test_le_taux_de_mitigation_est_continu():
    """`mitigated_at` est un booléen daté : une zone touchée à 10 % et une
    touchée à 90 % étaient indiscernables."""
    h = np.full(10, 100.0)
    lo = np.full(10, 100.0)
    lo[5] = 97.0          # descend à mi-hauteur d'une zone [96, 98]
    t = taux_mitigation({"top": 98.0, "bottom": 96.0, "index": 2,
                         "kind": "bullish"}, h, lo, i=8)
    assert 0.0 < t < 1.0


def test_le_fvg_du_mss_recoit_le_meilleur_rang():
    """§85 — le gap créé par le displacement qui a PROVOQUÉ le MSS est le point
    d'entrée du modèle ; rien ne le distinguait d'un gap quelconque."""
    res = {"_atr_arr": np.full(50, 2.0)}
    fvg = {"index": 30, "top": 102.0, "bottom": 101.0}
    assert rang_fvg(fvg, res, mss_bars={30}) == "MSS_FVG"
    assert rang_fvg(fvg, res, mss_bars=set()) == "CONTINUATION"


def test_un_micro_fvg_est_identifie_comme_tel():
    res = {"_atr_arr": np.full(50, 4.0)}
    assert rang_fvg({"index": 30, "top": 100.5, "bottom": 100.0}, res) == "MICRO"


def test_la_qualite_d_un_order_block_est_bornee():
    fort = qualite_order_block({"broke_structure": True, "strength": 2,
                                "invalidated_at": None}, {}, True, 0.0)
    faible = qualite_order_block({"broke_structure": False, "strength": 1,
                                  "invalidated_at": 40}, {}, False, 0.9)
    assert 0.0 <= faible < fort <= 1.0


# ── §91 ouvertures calendaires ──────────────────────────────────────────────

def test_les_ouvertures_sont_constantes_sur_la_periode():
    start = dt.datetime(2021, 1, 4)         # un lundi
    n = 72
    df = pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": np.arange(n, dtype=float) + 100.0,
        "high": np.arange(n, dtype=float) + 101.0,
        "low": np.arange(n, dtype=float) + 99.0,
        "close": np.arange(n, dtype=float) + 100.5,
        "volume": np.full(n, 1.0),
    })
    out = opens_calendaires(df)
    jour = out["daily_open"]
    assert len(set(jour[:24])) == 1, "l'ouverture du jour doit être constante"
    assert jour[0] != jour[24], "elle doit changer au jour suivant"


def test_les_ouvertures_sont_causales():
    """L'ouverture d'une période est connue dès sa PREMIÈRE barre — contrairement
    à ses extrêmes, qui ne le sont qu'à sa clôture."""
    start = dt.datetime(2021, 1, 4)
    n = 48
    df = pl.DataFrame({
        "time": [start + dt.timedelta(hours=i) for i in range(n)],
        "open": np.arange(n, dtype=float) + 100.0,
        "high": np.arange(n, dtype=float) + 101.0,
        "low": np.arange(n, dtype=float) + 99.0,
        "close": np.arange(n, dtype=float) + 100.5,
        "volume": np.full(n, 1.0),
    })
    complet = opens_calendaires(df)["daily_open"]
    tronque = opens_calendaires(df.head(30))["daily_open"]
    np.testing.assert_allclose(tronque, complet[:30])


def test_dataframe_vide_ne_plante_pas():
    df = pl.DataFrame({"time": [], "open": [], "high": [], "low": [],
                       "close": [], "volume": []})
    assert len(opens_calendaires(df)["daily_open"]) == 0
