"""API-04 — le backfill actions écrit réellement dans le store.

La boucle appelait `YFinanceProvider.fetch_bars`, méthode inexistante :
l'AttributeError tombait dans l'`except Exception` par symbole, le job
finissait « done » avec `bars: 0, ok: False` partout, et aucun Parquet
n'était écrit. Le job avait donc l'air de tourner sans rien backfiller.

Le test vérifie les deux moitiés du contrat : le store est bien sollicité
(le fetch persiste), et le job rapporte des bougies.
"""
import time

import polars as pl
import pytest
from starlette.testclient import TestClient

from app.api import state
from app.api.helpers import verify_api_key
from app.api.main import app


@pytest.fixture
def client():
    app.dependency_overrides[verify_api_key] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


class _StoreEspion:
    def __init__(self):
        self.appels = []

    def fetch(self, exchange, symbol, tf, total, prefer_cache=False):
        self.appels.append((symbol, tf, total))
        return pl.DataFrame({"time": [1, 2, 3], "close": [1.0, 2.0, 3.0]})


def _attendre(client, job_id, timeout=10.0):
    fin = time.time() + timeout
    while time.time() < fin:
        r = client.get(f"/api/data/backfill-status/{job_id}")
        assert r.status_code == 200, r.text
        job = r.json()
        if job.get("status") in ("done", "error"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} toujours {job.get('status')} après {timeout}s")


def test_backfill_equities_passe_par_le_store(client, monkeypatch):
    espion = _StoreEspion()
    monkeypatch.setattr(state, "cfg", {"scanner": {"universe": ["sbf120"]}})
    monkeypatch.setattr("app.core.universe.load_universe",
                        lambda _u: ["BNP.PA", "AC.PA"])
    monkeypatch.setattr("app.core.exchange.create_exchange", lambda _cfg: object())
    monkeypatch.setattr("app.api.routes.data.get_store", lambda: espion)

    r = client.post("/api/data/backfill-equities?tf=1d&years=2")
    assert r.status_code == 200, r.text
    job = _attendre(client, r.json()["job_id"])

    assert job["status"] == "done", job.get("error")
    assert [a[0] for a in espion.appels] == ["BNP.PA", "AC.PA"]
    assert all(a[1] == "1d" and a[2] == 730 for a in espion.appels)
    assert [r["bars"] for r in job["results"]] == [3, 3]
    assert all(r["ok"] for r in job["results"])


def test_backfill_equities_refuse_sans_univers(client, monkeypatch):
    monkeypatch.setattr(state, "cfg", {"scanner": {"universe": []}})
    r = client.post("/api/data/backfill-equities")
    assert r.status_code == 400
    assert "univers" in r.json()["error"].lower()
