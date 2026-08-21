"""PERF-02 — la détection de trous ne rescanne plus tout l'historique.

Le rapport de trous était recalculé sur le fichier entier à CHAQUE sauvegarde,
alors qu'une sauvegarde ajoute des barres à la fin. Mesuré avant correctif :
509 ms de détection contre 59 ms d'écriture parquet sur sept fichiers — 90 % du
coût de `_save`. Le pire cas est l'actions en journalier, 21 µs/barre contre
1 µs en crypto : la marche de séances coûte vingt fois plus.

Le risque d'un cache est de rendre un résultat faux. Ces tests portent donc
d'abord sur l'ÉQUIVALENCE avec le scan complet.
"""
import datetime as dt

import polars as pl
import pytest

from app.core.candle_store import CandleStore
from app.core.ohlcv_gaps import calendar_for_symbol, detect_ohlcv_gaps

UTC = dt.timezone.utc


def _serie(n=200, retirer=(), depart=0):
    debut = dt.datetime(2026, 7, 1, tzinfo=UTC) + dt.timedelta(minutes=15 * depart)
    idx = [i for i in range(n) if i not in set(retirer)]
    return pl.DataFrame({
        "time":   [debut + dt.timedelta(minutes=15 * i) for i in idx],
        "open":   [1.0] * len(idx), "high": [1.0] * len(idx),
        "low":    [1.0] * len(idx), "close": [1.0] * len(idx),
        "volume": [1.0] * len(idx),
    })


def _cle(g):
    return [(x["index"], x["gap_bars"]) for x in g]


@pytest.fixture
def store(tmp_path):
    return CandleStore(base_dir=str(tmp_path))


CAL = calendar_for_symbol("BTC/USDC")


@pytest.mark.parametrize("retirer", [(), (50,), (50, 51), (10, 80, 150), (3, 197)])
def test_l_incremental_donne_le_meme_resultat_que_le_scan_complet(store, tmp_path, retirer):
    p = tmp_path / "15m.parquet"
    df = _serie(retirer=retirer)
    store._gaps_incrementaux(p, df[:-1], "15m", CAL, set())      # amorce le cache
    inc = store._gaps_incrementaux(p, df, "15m", CAL, set())     # +1 barre
    assert _cle(inc) == _cle(detect_ohlcv_gaps(df, "15m", calendar=CAL, absents=set()))


def test_un_trou_cree_par_l_ajout_est_detecte(store, tmp_path):
    """Le seul trou qu'un ajout peut créer est à la jonction."""
    p = tmp_path / "15m.parquet"
    base = _serie(n=100)
    store._gaps_incrementaux(p, base, "15m", CAL, set())
    tardive = base["time"][-1] + dt.timedelta(minutes=15 * 4)
    suite = pl.concat([base, pl.DataFrame({
        "time": [tardive], "open": [1.0], "high": [1.0],
        "low": [1.0], "close": [1.0], "volume": [1.0]})])
    inc = store._gaps_incrementaux(p, suite, "15m", CAL, set())
    assert _cle(inc) == _cle(detect_ohlcv_gaps(suite, "15m", calendar=CAL, absents=set()))
    assert len(inc) == 1 and inc[0]["gap_bars"] == 3


def test_un_prefixe_modifie_force_un_scan_complet(store, tmp_path):
    """`_backfill_gaps` insère au MILIEU : le cache ne doit pas s'appliquer,
    sinon le trou comblé resterait signalé pour toujours."""
    p = tmp_path / "15m.parquet"
    troue = _serie(retirer=(50,))
    store._gaps_incrementaux(p, troue, "15m", CAL, set())
    comble = _serie()                                   # la barre 50 revient
    inc = store._gaps_incrementaux(p, comble, "15m", CAL, set())
    assert inc == [], "le trou comblé doit disparaître"


def test_un_fichier_raccourci_force_un_scan_complet(store, tmp_path):
    p = tmp_path / "15m.parquet"
    store._gaps_incrementaux(p, _serie(n=200), "15m", CAL, set())
    court = _serie(n=100, retirer=(50,))
    inc = store._gaps_incrementaux(p, court, "15m", CAL, set())
    assert _cle(inc) == _cle(detect_ohlcv_gaps(court, "15m", calendar=CAL, absents=set()))


def test_sans_changement_le_resultat_est_stable(store, tmp_path):
    p = tmp_path / "15m.parquet"
    df = _serie(retirer=(50, 120))
    a = store._gaps_incrementaux(p, df, "15m", CAL, set())
    b = store._gaps_incrementaux(p, df, "15m", CAL, set())
    assert _cle(a) == _cle(b) == _cle(detect_ohlcv_gaps(df, "15m", calendar=CAL, absents=set()))


def test_deux_fichiers_ne_partagent_pas_leur_cache(store, tmp_path):
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    store._gaps_incrementaux(a, _serie(retirer=(50,)), "15m", CAL, set())
    assert store._gaps_incrementaux(b, _serie(), "15m", CAL, set()) == []


def test_les_creneaux_absents_sont_respectes(store, tmp_path):
    """Le cache ne doit pas figer un résultat calculé sans le registre."""
    from app.core.ohlcv_gaps import creneaux_manquants
    p = tmp_path / "15m.parquet"
    df = _serie(retirer=(50,))
    g = store._gaps_incrementaux(p, df, "15m", CAL, set())
    assert len(g) == 1
    absents = set(creneaux_manquants(g[0]["time_before"], g[0]["time_after"], 900.0, CAL))
    store._gaps_cache.clear()
    assert store._gaps_incrementaux(p, df, "15m", CAL, absents) == []
