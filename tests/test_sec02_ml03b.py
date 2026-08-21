"""SEC-02 (registre de jetons WS borné) et ML-03b (trace de fit non mesurable)."""
import threading
import time

import pytest

from app.api import ws_tickets as WT
from app.ml import fit_trace as FT


@pytest.fixture(autouse=True)
def _propre():
    WT._reset_for_tests()
    FT._reset_for_tests()
    yield
    WT._reset_for_tests()
    FT._reset_for_tests()


# ── SEC-02 ──────────────────────────────────────────────────────────────────

def test_un_jeton_est_a_usage_unique():
    jeton, _ = WT.issue_ticket()
    assert WT.consume_ticket(jeton) is True
    assert WT.consume_ticket(jeton) is False


def test_un_jeton_expire_est_refuse():
    jeton, _ = WT.issue_ticket(ttl=0.01)
    time.sleep(0.05)
    assert WT.consume_ticket(jeton) is False


@pytest.mark.parametrize("jeton", [None, "", "x" * 129])
def test_les_jetons_invalides_sont_refuses_sans_recherche(jeton):
    assert WT.consume_ticket(jeton) is False


def test_le_registre_est_borne():
    """Un client emballé ne doit pas faire croître le registre sans fin — la
    purge ne retire que les jetons EXPIRÉS, donc à fort débit tout reste
    valide et rien n'était libéré."""
    for _ in range(WT._MAX_TICKETS + 200):
        WT.issue_ticket()
    assert len(WT._tickets) <= WT._MAX_TICKETS


def test_l_eviction_garde_les_jetons_les_plus_recents():
    """Évincer le plus proche de l'expiration : un jeton perdu coûte une
    reconnexion, un refus couperait le temps réel."""
    WT._reset_for_tests()
    vieux, _ = WT.issue_ticket(ttl=0.5)
    for _ in range(WT._MAX_TICKETS):
        WT.issue_ticket(ttl=300.0)
    assert WT.consume_ticket(vieux) is False, "le plus ancien doit avoir été évincé"
    assert len(WT._tickets) <= WT._MAX_TICKETS


def test_les_jetons_expires_sont_liberes_a_l_emission():
    for _ in range(50):
        WT.issue_ticket(ttl=0.01)
    time.sleep(0.05)
    avant = len(WT._tickets)
    for _ in range(WT._MAX_TICKETS):
        WT.issue_ticket()
    assert len(WT._tickets) <= WT._MAX_TICKETS
    assert avant == 50


def test_la_route_declare_une_limite_de_debit():
    """Le plafond du registre borne la mémoire ; la limite de débit borne
    l'usage. Sans elle, un client authentifié bogué sature le registre en
    boucle et chaque émission paie une purge complète sous verrou."""
    import inspect

    from app.api.routes import ws as R
    src = inspect.getsource(R)
    i_route = src.index('"/api/ws/ticket"')
    assert "limiter.limit" in src[i_route:i_route + 400], (
        "POST /api/ws/ticket doit porter une limite de débit"
    )


# ── ML-03b ──────────────────────────────────────────────────────────────────

def test_une_trace_normale_est_mesurable():
    FT.start()
    FT.record(None, site="t")
    out = FT.stop(series_len=100)
    assert out["mesurable"] is True
    assert out["fits_non_traces"] == 0
    assert out["n_fits"] == 1


def test_un_fit_dans_un_autre_thread_rend_la_trace_non_mesurable():
    """Le cœur de ML-03b : l'enregistreur est par thread. Un entraînement
    délégué ailleurs ne produisait AUCUNE trace — `n_fits=0` et
    `any_full_series=False` — soit une absence de mesure présentée comme une
    absence de fuite."""
    FT.start()
    t = threading.Thread(target=lambda: FT.record(None, site="ailleurs"))
    t.start()
    t.join()
    out = FT.stop(series_len=100)
    assert out["n_fits"] == 0
    assert out["any_full_series"] is False       # inchangé…
    assert out["fits_non_traces"] == 1           # …mais on sait que c'est faux
    assert out["mesurable"] is False


def test_le_compteur_ne_retient_que_la_fenetre_de_la_trace():
    """Un fit hors trace AVANT `start()` ne doit pas salir la mesure suivante."""
    FT.record(None, site="avant")
    FT.start()
    out = FT.stop(series_len=100)
    assert out["fits_non_traces"] == 0
    assert out["mesurable"] is True


def test_la_serie_complete_reste_detectee():
    FT.start()
    FT.record(_Faux(120), site="cached_train")
    out = FT.stop(series_len=100)
    assert out["any_full_series"] is True
    assert out["mesurable"] is True


class _Faux:
    def __init__(self, n): self._n = n
    def __len__(self): return self._n
    @property
    def columns(self): return []
