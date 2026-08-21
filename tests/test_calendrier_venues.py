"""Calendrier de place : cartographie des venues et cohérence compte/énumération.

Trois défauts trouvés en inspectant le registre d'absents d'une instance qui
tournait : des places européennes traitées comme des marchés 24/7, une marche
de sessions qui fabriquait des séances quand le calendrier ne couvrait pas la
période, et deux fonctions répondant différemment à la même question.
"""
import datetime as dt

import pytest

from app.core.market_calendar import _sessions_ouvertes_entre
from app.core.ohlcv_gaps import calendar_for_symbol, creneaux_manquants

UTC = dt.timezone.utc


# ── Cartographie des venues ─────────────────────────────────────────────────

@pytest.mark.parametrize("symbole,place", [
    ("AC.PA", "XPAR"), ("FDJUP.XC", "XPAR"),
    ("APAM.AS", "XAMS"), ("SOLB.BR", "XBRU"),
    ("ARRD.F", "XFRA"), ("SAP.DE", "XETR"), ("VOD.L", "XLON"),
])
def test_chaque_suffixe_a_sa_place(symbole, place):
    """`.BR` et `.XC` retombaient sur 24/7 : chaque week-end devenait un trou.
    Mesuré sur SOLB.BR — 2 755 créneaux « absents », tous les week-ends
    depuis 2001."""
    assert getattr(calendar_for_symbol(symbole), "name", "") == place


@pytest.mark.parametrize("symbole", ["BTC/USDC", "XRP/USDC", "ETH-USD", ""])
def test_sans_suffixe_connu_le_marche_est_24_7(symbole):
    assert type(calendar_for_symbol(symbole)).__name__ == "AlwaysOpenCalendar"


def test_aucune_action_du_parc_n_est_traitee_en_24_7():
    """Garde-fou : un symbole action tombant sur ALWAYS_OPEN rend sa
    complétude ininterprétable."""
    import pathlib
    racine = pathlib.Path(__file__).resolve().parents[1] / "data" / "ohlcv"
    if not racine.exists():
        pytest.skip("pas de cache OHLCV local")
    fautifs = [d.name for d in racine.iterdir()
               if d.is_dir() and "." in d.name
               and type(calendar_for_symbol(d.name)).__name__ == "AlwaysOpenCalendar"]
    assert fautifs == [], f"places sans calendrier : {sorted(set(fautifs))}"


# ── Marche de sessions ──────────────────────────────────────────────────────

def test_une_marche_qui_n_avance_pas_ne_fabrique_pas_de_seances():
    """`exchange_calendars` ne remonte qu'à 2006 ; avant, le repli 24/7 rend
    `session_end() is None` et la marche avançait d'une seconde par tour —
    398 séances pour un simple week-end de 2001."""
    class _Muet:
        name = "muet"
        def next_open(self, ts=None): return ts
        def session_end(self, ts=None): return None
        def is_open(self, ts=None): return True

    a = dt.datetime(2001, 6, 21, 22, 0, tzinfo=UTC)
    assert _sessions_ouvertes_entre(_Muet(), a, a + dt.timedelta(days=3)) == 0


# ── Compte et énumération ───────────────────────────────────────────────────

@pytest.mark.parametrize("symbole", ["AC.PA", "SOLB.BR", "BTC/USDC"])
@pytest.mark.parametrize("tf_s", [900, 3600])
def test_le_compte_est_exactement_la_longueur_de_l_enumeration(symbole, tf_s):
    """Les deux étaient calculés séparément et divergeaient sur tout week-end :
    0 barre attendue contre 63 créneaux énumérés. Confirmer ces 63 faisait
    passer un vrai trou en négatif — donc disparaître du rapport."""
    cal = calendar_for_symbol(symbole)
    a = dt.datetime(2026, 7, 3, 15, 0, tzinfo=UTC)
    for jours in (1, 3, 5):
        b = a + dt.timedelta(days=jours)
        assert (cal.expected_bars_between(a, b, tf_s)
                == len(cal.expected_slots_between(a, b, tf_s)))


def test_aucun_creneau_de_week_end_n_est_confirmable_sur_une_action():
    cal = calendar_for_symbol("AC.PA")
    a = dt.datetime(2026, 7, 3, 15, 0, tzinfo=UTC)     # vendredi, clôture
    b = dt.datetime(2026, 7, 6, 7, 0, tzinfo=UTC)      # lundi, ouverture
    assert creneaux_manquants(a, b, 3600.0, cal) == []


def test_le_crypto_enumere_tout_le_span():
    cal = calendar_for_symbol("BTC/USDC")
    a = dt.datetime(2026, 7, 3, 15, 0, tzinfo=UTC)
    b = a + dt.timedelta(hours=4)
    assert len(creneaux_manquants(a, b, 3600.0, cal)) == 3


def test_sans_calendrier_rien_n_est_confirmable():
    """Le repli doit être le silence, pas l'énumération d'horloge."""
    a = dt.datetime(2026, 7, 3, 15, 0, tzinfo=UTC)
    assert creneaux_manquants(a, a + dt.timedelta(days=3), 3600.0, None) == []


def test_le_journalier_n_est_pas_enumerable():
    """Une barre journalière porte minuit LOCAL (22 h ou 23 h UTC selon
    l'heure d'été) : deux candidats séparés par un changement d'heure
    désignent la même séance. Et une séance absente est une suspension ou de
    l'historique manquant, pas un créneau « non tradé »."""
    cal = calendar_for_symbol("AC.PA")
    a = dt.datetime(2026, 7, 3, 22, 0, tzinfo=UTC)
    assert creneaux_manquants(a, a + dt.timedelta(days=5), 86400.0, cal) == []
