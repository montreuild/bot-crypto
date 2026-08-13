"""L2 (§2 §4 §27 §93) — l'économie du trade avant de le prendre.

Le dépôt facturait correctement mais **après coup** : la décision d'entrée
comparait un gain BRUT à un risque BRUT. L1 en a donné la démonstration
chiffrée — sur BTC 4 h, un profit factor de 1,147 pour un PnL net de −0,33 % :
chaque jambe supplémentaire est un fill de plus, et ce coût n'entrait dans
aucune décision.

Le test qui compte est `test_le_rr_net_annonce_correspond_au_r_realise` : si le
R/R calculé à l'entrée ne correspond pas à ce qu'un trade encaisse en touchant
exactement sa cible, le chiffre ne sert à rien.
"""
import pytest

from app.core.execution import venue_borrow_rate
from app.core.trade_economics import (
    Couts,
    economic_edge_ok,
    execution_score,
    funding_cost,
    net_rr,
    round_trip_cost,
)


class _VenuePerp:
    market_type = "perp"
    name = "perp-test"

    def effective_borrow_rate(self, defaut):
        return defaut


class _VenueMargin(_VenuePerp):
    market_type = "margin"


# ── §27 funding ─────────────────────────────────────────────────────────────

def test_le_funding_se_compte_par_periodes_discretes():
    """OKX règle toutes les 8 h : 24 h de détention = 3 périodes."""
    c = funding_cost(notional=10_000, funding_rate=0.0001, hours_held=24)
    assert c == pytest.approx(10_000 * 0.0001 * 3)


def test_un_funding_negatif_est_encaisse():
    """C'est la différence de nature avec un emprunt, qui ne peut que coûter.
    Un signe négatif en sortie est un gain, pas un bug."""
    assert funding_cost(10_000, -0.0002, 24) < 0


def test_une_detention_nulle_ne_coute_rien():
    assert funding_cost(10_000, 0.001, 0) == 0.0


def test_une_venue_perp_n_emprunte_plus():
    """§27 — facturer un perp au taux d'emprunt margin donnait un coût de
    portage toujours positif et de la mauvaise magnitude."""
    assert venue_borrow_rate(0.00072, _VenuePerp()) == 0.0
    assert venue_borrow_rate(0.00072, _VenueMargin()) == 0.00072


# ── §2 R/R net ──────────────────────────────────────────────────────────────

def test_les_couts_alourdissent_le_risque_et_allegent_le_gain():
    """Les deux effets vont dans le même sens, et c'est pourquoi un R/R brut de
    2,0 peut valoir bien moins en net sur un petit mouvement."""
    couts = Couts(frais_entree=1.0, frais_sortie=1.0, slippage=0.5, portage=0.5)
    brut = 2.0
    net = net_rr(entree=100.0, stop=90.0, cible=120.0, taille=1.0, couts=couts)
    assert net < brut
    # gain net (20 − 3) / risque net (10 + 3)
    assert net == pytest.approx(17.0 / 13.0)


def test_le_rr_net_annonce_correspond_au_r_realise():
    """LE test du lot. Un trade qui touche exactement sa cible doit encaisser le
    R/R qu'on lui avait annoncé — sinon le chiffre ne sert à rien."""
    entree, stop, cible, taille = 100.0, 95.0, 110.0, 2.0
    couts = round_trip_cost(entree, taille, fee_rate=0.001, spread_pct=0.0005)
    annonce = net_rr(entree, stop, cible, taille, couts)
    # Réalisation : gain brut moins tous les coûts, rapporté au risque encouru
    # (distance au stop) augmenté de ces mêmes coûts.
    gain_realise = (cible - entree) * taille - couts.total
    risque_encouru = (entree - stop) * taille + couts.total
    assert annonce == pytest.approx(gain_realise / risque_encouru, rel=1e-12)


def test_le_short_calcule_du_bon_cote():
    couts = Couts(0.5, 0.5, 0.0, 0.0)
    assert net_rr(100.0, 110.0, 90.0, 1.0, couts, side="short") > 0


def test_un_risque_brut_nul_ne_produit_pas_de_rr():
    assert net_rr(100.0, 100.0, 110.0, 1.0, Couts(0, 0, 0, 0)) == 0.0


def test_le_spread_est_paye_des_deux_cotes():
    """À l'entrée on achète au-dessus du milieu, à la sortie on vend en dessous."""
    c = round_trip_cost(100.0, 10.0, fee_rate=0.0, spread_pct=0.001)
    assert c.slippage == pytest.approx(1000.0 * 0.001 * 2)


def test_le_funding_remplace_l_emprunt_et_ne_s_y_ajoute_pas():
    """Un perp ne paie pas les deux."""
    perp = round_trip_cost(100.0, 10.0, heures_estimees=24,
                           borrow_rate_daily=0.001, funding_rate=0.0001)
    margin = round_trip_cost(100.0, 10.0, heures_estimees=24,
                             borrow_rate_daily=0.001)
    assert perp.portage != margin.portage
    assert perp.portage == pytest.approx(funding_cost(1000.0, 0.0001, 24))


# ── §4 economic edge ────────────────────────────────────────────────────────

def test_le_gain_doit_valoir_plusieurs_fois_son_cout():
    couts = Couts(1.0, 1.0, 0.0, 0.0)     # total 2
    assert economic_edge_ok(20.0, couts, multiple=5.0)
    assert not economic_edge_ok(8.0, couts, multiple=5.0)


def test_un_multiple_nul_desactive_le_filtre():
    """Sans ça, on ne pourrait pas mesurer ce que le filtre coûte."""
    assert economic_edge_ok(0.01, Couts(10, 10, 10, 10), multiple=0)


def test_le_filtre_est_transposable_entre_venues():
    """§4 — un seuil de gain ABSOLU (0,4 %) couvre largement les frais sur une
    venue à 0,08 % et pas du tout sur une action à commission fixe. Rapporté aux
    coûts réels, le même réglage tient sur les deux."""
    pas_cher = Couts(0.5, 0.5, 0.0, 0.0)
    cher = Couts(5.0, 5.0, 0.0, 0.0)
    gain = 8.0
    assert economic_edge_ok(gain, pas_cher, multiple=5.0)
    assert not economic_edge_ok(gain, cher, multiple=5.0)


# ── §93 qualité d'exécution ─────────────────────────────────────────────────

def test_un_spread_trop_large_refuse_le_trade():
    note, motif = execution_score(spread_pct=0.01, notional=1000,
                                  volume_quote=1e6, gain_attendu=50,
                                  spread_max=0.002)
    assert note == 0.0 and motif is not None


def test_la_note_est_bornee_et_decroit_avec_le_spread():
    etroit, _ = execution_score(0.0002, 1000, 1e7, 50, spread_max=0.002)
    large, _ = execution_score(0.0018, 1000, 1e7, 50, spread_max=0.002)
    assert 0.0 <= large < etroit <= 1.0
