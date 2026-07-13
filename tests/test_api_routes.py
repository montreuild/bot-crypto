"""Tests des routes FastAPI via TestClient (TEST-03).

Aucune route n'était testée avec le TestClient (grep = 0) : erreurs 500,
routes cassées ou paramètres ignorés (ex. parsing slot_key 3-parties) ne se
voyaient qu'en usage réel. `verify_api_key` bloque par défaut tout appel
« non local » (TestClient n'a pas une IP 127.0.0.1) — la fixture `client`
neutralise cette dépendance pour les tests fonctionnels ; un test dédié
vérifie séparément, sans le contournement, que le blocage est bien actif.
"""
import os
import tempfile

import pytest
from starlette.testclient import TestClient

from app.api import state
from app.api.helpers import verify_api_key
from app.api.main import app
from app.core.database import init_db
from app.live.live_trader import LiveTrader


@pytest.fixture
def client():
    """TestClient avec verify_api_key neutralisé (tests fonctionnels des routes,
    pas de la couche auth elle-même — cf. test_protected_route_rejects...)."""
    app.dependency_overrides[verify_api_key] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_api_key, None)


class MockExchange:
    """Exchange minimal en mémoire — aucun appel réseau."""

    def fetch_ticker(self, symbol):
        return {"last": 100.0}

    def fetch_positions(self):
        return []

    def fetch_balance(self):
        return {"free": {"USDC": 1000.0}, "total": {"USDC": 1000.0}}


def _make_trader_cfg(db_path: str) -> dict:
    return {
        "trading": {
            "capital": 1000.0, "timeframes": ["1h"], "scan_interval": 60,
            "score_threshold": 0.5, "paper_mode": True,
        },
        "database": {"url": f"sqlite:///{db_path}"},
        "exchange": {"name": "okx", "margin": False},
        "strategies": {"enabled": ["trend_rider"]},
        "strategy_params": {}, "optimizer_results": {}, "scanner": {}, "risk": {},
        "notifications": {}, "optimizer": {"enabled": False},
        "forward_test": {"enabled": False}, "lifecycle": {"enabled": False},
        "capital_allocator": {},
    }


# ── /api/data/status, /api/data/refetch ──────────────────────────────────

def test_data_status_returns_dataset_list(client):
    r = client.get("/api/data/status")
    assert r.status_code == 200
    body = r.json()
    assert "datasets" in body
    assert isinstance(body["datasets"], list)


def test_data_refetch_without_symbol_and_no_scanner_config_is_400(client, monkeypatch):
    monkeypatch.setattr(state, "cfg", {}, raising=False)
    r = client.post("/api/data/refetch")
    assert r.status_code == 400
    assert "symbole" in r.json()["error"].lower()


# ── /api/scanner/fast_analysis ───────────────────────────────────────────

def test_fast_analysis_insufficient_data_returns_400(client):
    r = client.get(
        "/api/scanner/fast_analysis",
        params={"symbol": "ZZZNONEXISTENT/USDC", "tf": "1h"},
    )
    assert r.status_code == 400
    assert "insuffisantes" in r.json()["error"].lower()


# ── /api/portfolio ────────────────────────────────────────────────────────

def test_portfolio_without_running_trader_returns_default_shape(client, monkeypatch):
    monkeypatch.setattr(state, "trader", None, raising=False)
    monkeypatch.setattr(state, "cfg", {}, raising=False)
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["allocation"] == []


def test_portfolio_with_trader_exposes_symbol_per_bot(client, monkeypatch, tmp_path):
    cfg = _make_trader_cfg(str(tmp_path / "live.db"))
    trader = LiveTrader(cfg, MockExchange())
    monkeypatch.setattr(state, "trader", trader, raising=False)
    monkeypatch.setattr(state, "cfg", cfg, raising=False)

    r = client.get("/api/portfolio")
    assert r.status_code == 200
    bots = r.json()["bots"]
    assert bots, "au moins un bot attendu (trend_rider::1h::BTC/USDC en fallback)"
    assert bots[0]["symbol"] == "BTC/USDC"
    assert bots[0]["slot_key"] == "trend_rider::1h::BTC/USDC"


# ── /api/strategy/{slot_key}/performance — parsing 2 et 3 parties ───────

class TestStrategyPerformance:
    @pytest.fixture(autouse=True)
    def _session_local(self, monkeypatch, tmp_path):
        db = tmp_path / "perf.db"
        _, session_local = init_db(f"sqlite:///{db}")
        monkeypatch.setattr(state, "SessionLocal", session_local, raising=False)

    def test_two_part_slot_key(self, client):
        r = client.get("/api/strategy/trend_rider::4h/performance")
        assert r.status_code == 200
        body = r.json()
        assert body["strategy"] == "trend_rider"
        assert body["tf"] == "4h"
        assert body["slot_key"] == "trend_rider::4h"
        assert body["total_trades"] == 0

    def test_three_part_slot_key_with_symbol(self, client):
        r = client.get("/api/strategy/trend_rider::4h::BTC%2FUSDC/performance")
        assert r.status_code == 200
        body = r.json()
        assert body["strategy"] == "trend_rider"
        assert body["tf"] == "4h"
        assert body["slot_key"] == "trend_rider::4h::BTC/USDC"

    def test_invalid_slot_key_format_is_400(self, client):
        r = client.get("/api/strategy/badformat/performance")
        assert r.status_code == 400


def test_strategy_performance_without_session_local_is_503(client, monkeypatch):
    monkeypatch.setattr(state, "SessionLocal", None, raising=False)
    r = client.get("/api/strategy/trend_rider::4h/performance")
    assert r.status_code == 503


# ── Auth : le contournement de test ne doit pas masquer un vrai trou ─────

def test_protected_route_rejects_unauthenticated_non_local_request():
    # Sans dependency_overrides ni api_key configurée, verify_api_key ne doit
    # laisser passer QUE les requêtes locales — TestClient n'en est pas une.
    assert verify_api_key not in app.dependency_overrides
    raw_client = TestClient(app)
    r = raw_client.get("/api/data/status")
    assert r.status_code == 403
