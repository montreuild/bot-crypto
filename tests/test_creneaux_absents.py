"""Créneaux confirmés absents à la source.

Sur une valeur peu liquide, un quart d'heure sans transaction ne produit
aucune bougie : le marché était ouvert, personne n'a traité. Le calendrier ne
peut pas le savoir — seule la source le peut, en répondant. Ces tests portent
sur ce qu'on a le droit de déduire de sa réponse.
"""
import datetime as dt

import polars as pl
import pytest

from app.core import ohlcv_absents as ABS
from app.core.ohlcv_gaps import (
    calendar_for_symbol,
    completeness_from_gaps,
    creneaux_manquants,
    detect_ohlcv_gaps,
)

TF_MS = 15 * 60 * 1000


@pytest.fixture(autouse=True)
def _propre():
    ABS._reset_for_tests()
    yield
    ABS._reset_for_tests()


def _serie(n=40, retirer=()):
    """Série 15m continue en séance, moins les indices demandés."""
    debut = dt.datetime(2026, 7, 1, 8, 0, tzinfo=dt.timezone.utc)
    idx = [i for i in range(n) if i not in set(retirer)]
    return pl.DataFrame({
        "time":   [debut + dt.timedelta(minutes=15 * i) for i in idx],
        "open":   [1.0] * len(idx), "high": [1.0] * len(idx),
        "low":    [1.0] * len(idx), "close": [1.0] * len(idx),
        "volume": [1.0] * len(idx),
    })


def _absents_de(df, gaps, symbole="BTC/USDC"):
    """Le calendrier est désormais obligatoire : sans lui on énumérait
    l'horloge, week-ends compris."""
    cal = calendar_for_symbol(symbole)
    return [t for g in gaps
            for t in creneaux_manquants(g["time_before"], g["time_after"],
                                        900.0, cal)]


# ── Ce que la détection fait des créneaux confirmés ─────────────────────────

def test_un_creneau_confirme_absent_n_est_plus_un_trou():
    df = _serie(retirer=[10])
    gaps = detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC")
    assert len(gaps) == 1
    absents = set(_absents_de(df, gaps))
    assert detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC", absents=absents) == []


def test_la_completude_ignore_les_creneaux_confirmes():
    """Le cœur du correctif : un fichier complet à 100 % de ce qui EXISTE
    affichait 92 % parce qu'on comptait des bougies qui n'ont jamais été."""
    df = _serie(n=40, retirer=[10, 20, 30])
    gaps = detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC")
    avant = completeness_from_gaps(len(df), gaps) * 100
    assert avant < 100.0
    absents = set(_absents_de(df, gaps))
    apres = completeness_from_gaps(
        len(df), detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC", absents=absents)) * 100
    assert apres == 100.0


def test_un_creneau_non_confirme_reste_un_trou():
    """Confirmer un créneau ne doit pas en masquer un autre."""
    df = _serie(retirer=[10, 25])
    gaps = detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC")
    assert len(gaps) == 2
    partiel = set(_absents_de(df, gaps[:1]))
    restants = detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC", absents=partiel)
    assert len(restants) == 1


def test_un_trou_partiellement_confirme_garde_ses_barres_restantes():
    df = _serie(retirer=[10, 11, 12])
    gaps = detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC")
    assert gaps[0]["gap_bars"] == 3
    un_seul = {_absents_de(df, gaps)[0]}
    reste = detect_ohlcv_gaps(df, "15m", symbol="BTC/USDC", absents=un_seul)
    assert reste[0]["gap_bars"] == 2


# ── Le registre ─────────────────────────────────────────────────────────────

def test_le_registre_survit_au_processus(tmp_path):
    p = tmp_path / "15m.parquet"
    p.write_bytes(b"")
    assert ABS.ajouter(p, "OPM.PA", "15m", [1000, 2000]) == 2
    ABS._reset_for_tests()                       # simule un redémarrage
    assert ABS.charger(p, "OPM.PA", "15m") == {1000, 2000}
    assert ABS.chemin(p).name == "15m.absent.json"


def test_ajouter_est_idempotent(tmp_path):
    p = tmp_path / "15m.parquet"
    assert ABS.ajouter(p, "S", "15m", [1, 2, 3]) == 3
    assert ABS.ajouter(p, "S", "15m", [2, 3]) == 0
    assert ABS.charger(p, "S", "15m") == {1, 2, 3}


def test_oublier_efface_le_registre(tmp_path):
    """Un refetch explicite doit pouvoir tout redemander."""
    p = tmp_path / "15m.parquet"
    ABS.ajouter(p, "S", "15m", [1, 2])
    ABS.oublier(p, "S", "15m")
    assert ABS.charger(p, "S", "15m") == set()
    assert not ABS.chemin(p).exists()


def test_le_registre_est_borne(tmp_path):
    p = tmp_path / "15m.parquet"
    ABS.ajouter(p, "S", "15m", range(ABS._MAX_ABSENTS + 500))
    assert len(ABS.charger(p, "S", "15m")) <= ABS._MAX_ABSENTS


def test_la_route_de_refetch_reinitialise_le_registre():
    import inspect

    from app.api.routes import data as R
    src = inspect.getsource(R.data_refetch)
    assert "oublier" in src, (
        "un refetch explicite doit redemander les créneaux confirmés absents"
    )


# ── La borne de largeur ─────────────────────────────────────────────────────

def test_seuls_les_micro_trous_sont_confirmables():
    """Mesuré sur le parc : côté actions aucun trou ne dépasse 4 barres ; le
    seul gros trou crypto en fait 3 939 — de l'historique manquant, que
    l'exchange détient peut-être. Le confirmer absent l'effacerait pour de bon."""
    from app.core.candle_store import _MAX_ABSENT_GAP_BARS

    assert 4 < _MAX_ABSENT_GAP_BARS < 100


def test_le_backfill_ecarte_les_gros_trous():
    import inspect

    from app.core.candle_store import CandleStore
    src = inspect.getsource(CandleStore._fill_detected_gaps)
    assert "_MAX_ABSENT_GAP_BARS" in src


def test_fetch_span_rend_le_span_couvert():
    """On ne confirme une absence que là où la source a RÉPONDU. Yahoo plafonne
    le 15 m à 60 jours : au-delà il ne dit pas « ça n'existe pas », il ne dit
    rien."""
    import inspect

    from app.core.candle_store import CandleStore
    src = inspect.getsource(CandleStore._fetch_span)
    assert "couvert" in src
    assert src.count("return") >= 2
