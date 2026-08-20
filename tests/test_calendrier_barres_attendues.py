"""`expected_bars_between` — la primitive qui remplace les heuristiques de trous.

La détection de trous posait la question à l'envers : « quel écart tolérer ? »,
à quoi répondait une pile d'heuristiques (marge de fraîcheur `max_gap_seconds`,
prochaine ouverture, échantillonnage de l'intérieur du span). Aucune ne décrit
la réalité : elles se contentaient de s'en approcher, et la coïncidence
`3×tf ≈ un week-end` en journalier faisait le reste.

La bonne question est « combien de barres DEVRAIENT exister entre ces deux
horodatages ? ». Le calendrier connaît les séances : il sait y répondre.
"""
from datetime import datetime, timezone

import pytest

from app.core.market_calendar import ALWAYS_OPEN, get_calendar

H = 3600
J = 86400


def _u(*a):
    return datetime(*a, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def xpar():
    return get_calendar("XPAR")


# ── 24/7 : arithmétique pure ────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,tf,attendu", [
    (_u(2026, 4, 20, 10), _u(2026, 4, 20, 11), H, 0),
    (_u(2026, 4, 20, 10), _u(2026, 4, 20, 12), H, 1),
    (_u(2026, 4, 20, 10), _u(2026, 4, 20, 13), H, 2),
    (_u(2026, 4, 20, 10), _u(2026, 4, 20, 10), H, 0),   # identiques
    (_u(2026, 4, 20, 12), _u(2026, 4, 20, 10), H, 0),   # inversés
])
def test_marche_24_7(a, b, tf, attendu):
    assert ALWAYS_OPEN.expected_bars_between(a, b, tf) == attendu


# ── Marché à séances, intraday ──────────────────────────────────────────────
# XPAR en heure d'été : séance 07:00–15:30 UTC.

@pytest.mark.parametrize("a,b,attendu,motif", [
    (_u(2026, 4, 20, 10), _u(2026, 4, 20, 11), 0, "barres consécutives en séance"),
    (_u(2026, 4, 20, 10), _u(2026, 4, 20, 12), 1, "une barre manquante en séance"),
    (_u(2026, 4, 20, 15), _u(2026, 4, 21, 7), 0, "la nuit n'est pas un trou"),
    (_u(2026, 4, 17, 15), _u(2026, 4, 20, 7), 0, "le week-end n'est pas un trou"),
    (_u(2026, 4, 17, 15), _u(2026, 4, 20, 9), 2, "07:00 et 08:00 manquent au lundi"),
    (_u(2026, 4, 17, 16), _u(2026, 4, 20, 9), 2, "barre hors séance : pas de créneau consommé"),
])
def test_seances_intraday(xpar, a, b, attendu, motif):
    assert xpar.expected_bars_between(a, b, H) == attendu, motif


def test_une_barre_hors_seance_ne_masque_pas_l_ouverture_suivante(xpar):
    """Régression : compter les secondes ouvertes DEPUIS `a` ajoutait le
    reliquat de sa séance et faussait l'arrondi — deux barres manquantes
    étaient comptées comme une seule. On part du créneau qui suit `a`."""
    assert xpar.expected_bars_between(
        _u(2026, 4, 17, 15), _u(2026, 4, 20, 9), H) == 2


# ── Marché à séances, journalier ────────────────────────────────────────────
# Les bougies 1d du dépôt sont horodatées à 23:00 UTC (minuit Paris) : le span
# vendredi→lundi contient donc un jour ouvré. Compter les SÉANCES — et non les
# jours calendaires — est ce qui rend le calcul insensible à cette convention.

@pytest.mark.parametrize("a,b,attendu,motif", [
    (_u(2026, 4, 20, 22), _u(2026, 4, 21, 22), 0, "deux séances consécutives"),
    (_u(2026, 4, 17, 22), _u(2026, 4, 20, 22), 0, "week-end : aucune séance sautée"),
    (_u(2026, 4, 20, 22), _u(2026, 4, 22, 22), 1, "mardi manquant"),
    (_u(2026, 4, 20, 22), _u(2026, 4, 23, 22), 2, "mardi et mercredi manquants"),
])
def test_seances_journalier(xpar, a, b, attendu, motif):
    assert xpar.expected_bars_between(a, b, J) == attendu, motif


def test_les_trois_calendriers_exposent_la_primitive():
    """Contrat du protocole : la détection de trous en dépend pour tout
    symbole, quel que soit le calendrier résolu."""
    for cal in (ALWAYS_OPEN, get_calendar("XPAR")):
        assert hasattr(cal, "expected_bars_between"), cal
        assert isinstance(
            cal.expected_bars_between(_u(2026, 4, 20, 10),
                                      _u(2026, 4, 20, 12), H), int)
