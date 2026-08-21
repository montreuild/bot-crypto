"""Un geste explicite de l'opérateur refait la mesure, il ne relit pas la conclusion.

Trois mémos évitent de reposer une question dont la réponse est connue :
créneaux confirmés absents (persistant), « historique épuisé » et cooldown de
recousage (6 h, mémoire vive). `refetch` n'effaçait que le premier, et
`backfill-equities` aucun : un symbole déclaré épuisé par le live dans les 6 h
précédentes était sauté, en log DEBUG — donc le bouton disait « refais » sans
refaire.
"""
import time

import polars as pl
import pytest
from starlette.testclient import TestClient

from app.api import state
from app.api.helpers import verify_api_key
from app.api.main import app
from app.core import ohlcv_absents as _abs
from app.core.candle_store import CandleStore


@pytest.fixture
def client():
    app.dependency_overrides[verify_api_key] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


def test_oublier_memos_efface_les_trois(tmp_path):
    store = CandleStore(base_dir=str(tmp_path))
    path = store._path("AC.PA", "1d")
    path.parent.mkdir(parents=True, exist_ok=True)

    _abs.ajouter(path, "AC.PA", "1d", [1_600_000_000_000])
    store._mark_exhausted("AC.PA", "1d", 1_500_000_000_000)
    store._mark_gaps_unfillable("AC.PA", "1d", 7)

    assert _abs.charger(path, "AC.PA", "1d")
    assert store._history_exhausted("AC.PA", "1d", 1_500_000_000_000)
    assert store._gaps_on_cooldown("AC.PA", "1d", 7)

    store.oublier_memos("AC.PA", "1d")

    assert not _abs.charger(path, "AC.PA", "1d")
    assert not store._history_exhausted("AC.PA", "1d", 1_500_000_000_000)
    assert not store._gaps_on_cooldown("AC.PA", "1d", 7)


def test_oublier_memos_ne_touche_pas_les_autres_symboles(tmp_path):
    """Le geste porte sur un couple (symbole, tf), pas sur le store entier."""
    store = CandleStore(base_dir=str(tmp_path))
    store._mark_exhausted("AC.PA", "1d", 1)
    store._mark_exhausted("BNP.PA", "1d", 1)
    store._mark_exhausted("AC.PA", "1h", 1)

    store.oublier_memos("AC.PA", "1d")

    assert not store._history_exhausted("AC.PA", "1d", 1)
    assert store._history_exhausted("BNP.PA", "1d", 1)
    assert store._history_exhausted("AC.PA", "1h", 1)


class _StoreEspion(CandleStore):
    """Vrai store (mémos réels), fetch neutralisé."""

    def __init__(self, base_dir):
        super().__init__(base_dir=str(base_dir))
        self.appels = []

    def fetch(self, exchange, symbol, tf, total, prefer_cache=False):
        self.appels.append((symbol, tf))
        return pl.DataFrame({"time": [1, 2], "close": [1.0, 2.0]})


def _attendre(client, job_id, timeout=10.0):
    fin = time.time() + timeout
    while time.time() < fin:
        job = client.get(f"/api/data/backfill-status/{job_id}").json()
        if job.get("status") in ("done", "error"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} jamais terminé")


def test_refetch_efface_le_memo_epuise(client, monkeypatch, tmp_path):
    espion = _StoreEspion(tmp_path)
    espion._mark_exhausted("AC.PA", "1d", 42)
    monkeypatch.setattr(state, "cfg", {"scanner": {"symbols": ["AC.PA"],
                                                   "timeframes": ["1d"]}})
    monkeypatch.setattr("app.core.exchange.create_exchange", lambda _cfg: object())
    monkeypatch.setattr("app.api.routes.data.get_store", lambda: espion)

    r = client.post("/api/data/refetch?symbol=AC.PA&tf=1d")
    assert r.status_code == 200, r.text
    assert espion.appels == [("AC.PA", "1d")]
    assert not espion._history_exhausted("AC.PA", "1d", 42)


def test_backfill_efface_le_memo_epuise(client, monkeypatch, tmp_path):
    espion = _StoreEspion(tmp_path)
    espion._mark_exhausted("BNP.PA", "1d", 42)
    monkeypatch.setattr(state, "cfg", {"scanner": {"universe": ["sbf120"]}})
    monkeypatch.setattr("app.core.universe.load_universe", lambda _u: ["BNP.PA"])
    monkeypatch.setattr("app.core.exchange.create_exchange", lambda _cfg: object())
    monkeypatch.setattr("app.api.routes.data.get_store", lambda: espion)

    r = client.post("/api/data/backfill-equities?tf=1d&years=2")
    assert r.status_code == 200, r.text
    job = _attendre(client, r.json()["job_id"])

    assert job["status"] == "done", job.get("error")
    assert espion.appels == [("BNP.PA", "1d")]
    assert not espion._history_exhausted("BNP.PA", "1d", 42)
