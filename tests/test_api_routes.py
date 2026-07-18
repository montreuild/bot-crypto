"""Tests des routes FastAPI via TestClient (TEST-03).

Aucune route n'était testée avec le TestClient (grep = 0) : erreurs 500,
routes cassées ou paramètres ignorés (ex. parsing slot_key 3-parties) ne se
voyaient qu'en usage réel. `verify_api_key` bloque par défaut tout appel
« non local » (TestClient n'a pas une IP 127.0.0.1) — la fixture `client`
neutralise cette dépendance pour les tests fonctionnels ; un test dédié
vérifie séparément, sans le contournement, que le blocage est bien actif.
"""

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


# ── /api/stats/fees (FIN-06) ─────────────────────────────────────────────

class TestFeeBreakdown:
    @pytest.fixture(autouse=True)
    def _session_local(self, monkeypatch, tmp_path):
        db = tmp_path / "fees.db"
        _, session_local = init_db(f"sqlite:///{db}")
        monkeypatch.setattr(state, "SessionLocal", session_local, raising=False)
        self._session_local = session_local

    def test_shape_and_defaults(self, client):
        r = client.get("/api/stats/fees")
        assert r.status_code == 200
        assert r.json() == {"taker": 0.0, "maker": 0.0, "borrow": 0.0, "stop": 0.0}

    def test_reflects_saved_trades(self, client):
        from app.core.database import Trade, session_scope
        with session_scope(self._session_local) as sess:
            sess.add(Trade(symbol="BTC/USDC", side="long", strategy="t",
                           entry=100.0, exit_price=101.0, size=1.0, notional=100.0,
                           pnl=1.0, fees=0.5, fee_taker=0.5, fee_maker=0.0,
                           borrow_cost=0.05, exit_reason="stop_loss",
                           status="closed", timeframe="1h"))
            sess.commit()
        r = client.get("/api/stats/fees")
        body = r.json()
        assert body["taker"] == pytest.approx(0.5)
        assert body["borrow"] == pytest.approx(0.05)
        assert body["stop"] == pytest.approx(0.5)


def test_fees_without_session_local_returns_zeros(client, monkeypatch):
    monkeypatch.setattr(state, "SessionLocal", None, raising=False)
    r = client.get("/api/stats/fees")
    assert r.status_code == 200
    assert r.json() == {"taker": 0.0, "maker": 0.0, "borrow": 0.0, "stop": 0.0}


# ── Auth : le contournement de test ne doit pas masquer un vrai trou ─────

def test_protected_route_rejects_unauthenticated_non_local_request():
    # Sans dependency_overrides ni api_key configurée, verify_api_key ne doit
    # laisser passer QUE les requêtes locales — TestClient n'en est pas une.
    assert verify_api_key not in app.dependency_overrides
    raw_client = TestClient(app)
    r = raw_client.get("/api/data/status")
    assert r.status_code == 403


# ── CORS (S0-03) ──────────────────────────────────────────────────────────
# DELETE /api/optimize/job existe (routes/optimizer.py) mais allow_methods
# ne listait que GET/POST — un navigateur bloquait le preflight en prod.

def test_cors_preflight_allows_delete():
    raw_client = TestClient(app)
    r = raw_client.options(
        "/api/optimize/job",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )
    assert r.status_code == 200
    allowed = r.headers.get("access-control-allow-methods", "")
    assert "DELETE" in allowed


# ── Rate limiting par endpoint (SEC-04) ──────────────────────────────────────
# Le reste de la suite désactive le limiter (tests/conftest.py::_disable_rate_
# limiting, car de nombreux tests appellent les fonctions de route directement
# sans objet Request réel) — ce test le réactive localement pour vérifier
# qu'une limite PLUS STRICTE que le default_limits global (300/minute) est
# bien appliquée à un endpoint sensible.

def test_per_route_limit_returns_429(client):
    original = state.limiter.enabled
    state.limiter.enabled = True
    state.limiter.reset()
    try:
        # /api/config/notifications/test est décoré @state.limiter.limit("5/minute").
        statuses = [client.post("/api/config/notifications/test").status_code
                    for _ in range(6)]
    finally:
        state.limiter.reset()
        state.limiter.enabled = original
    assert 429 not in statuses[:5], statuses
    assert statuses[5] == 429, statuses
