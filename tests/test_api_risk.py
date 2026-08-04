"""S12 — /api/risk, /api/risk/diagnostics, POST /api/risk/envelopes.

Regle d'affichage que ces payloads servent (§6) : un montant de risque va
TOUJOURS avec sa base. Un risque en devise sans son enveloppe n'est pas
interpretable -- c'est ce qui a laisse passer la divergence backtest 1 000 EUR
/ live 90 EUR sans que rien ne la signale.
"""
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


@pytest.fixture
def cfg(monkeypatch):
    """Config S12 minimale, sans trader : les routes doivent repondre en
    lecture seule (l'API tourne aussi bot arrete)."""
    c = {
        "trading": {"paper_mode": True, "timeframes": ["1h"]},
        "venues": {"default": "okx-t",
                   "defs": {"okx-t": {"max_leverage": 1, "quote_currency": "USDC"}},
                   "assign": {}},
        "risk": {"profile": "p", "profiles": {"p": 0.025},
                 "min_slot_weight": 0.05,
                 "envelopes": {"okx-t": {
                     "capital": 1000.0, "max_symbol_exposure_pct": 1.0,
                     "symbol_risk_pct": 0.05, "venue_risk_pct": 0.05}}},
        "strategies": {"enabled": []},
    }
    monkeypatch.setattr(state, "cfg", c)
    monkeypatch.setattr(state, "trader", None)
    return c


class TestGetRisk:
    def test_returns_the_three_nested_levels(self, client, cfg):
        r = client.get("/api/risk")
        assert r.status_code == 200
        body = r.json()
        assert set(body) >= {"venues", "symbols", "slots",
                             "total_risk_engaged", "rejections"}

    def test_every_engaged_amount_comes_with_its_base(self, client, cfg, monkeypatch):
        """L'invariant d'affichage : jamais un risque sans son enveloppe."""
        from app.core.risk_envelope import envelopes_for_active_slots
        envs = envelopes_for_active_slots(
            cfg, {"1h": [{"name": "trend_rider", "symbol": "BTC/USDC"}]})

        class _T:
            envelopes = envs
            ledger = None
            rejections = None
            _active_per_tf = {}
        monkeypatch.setattr(state, "trader", _T())

        body = client.get("/api/risk").json()
        for level in ("venues", "symbols"):
            for row in body[level]:
                assert "risk_budget" in row and "risk_engaged" in row
                assert "envelope" in row
        for slot in body["slots"]:
            assert {"envelope", "risk_amount", "risk_engaged", "weight"} <= set(slot)

    def test_venue_usage_ratio_is_reported(self, client, cfg, monkeypatch):
        from app.core.risk_envelope import envelopes_for_active_slots
        envs = envelopes_for_active_slots(
            cfg, {"1h": [{"name": "trend_rider", "symbol": "BTC/USDC"}]})

        class _T:
            envelopes = envs
            ledger = None
            rejections = None
            _active_per_tf = {}
        monkeypatch.setattr(state, "trader", _T())
        for v in client.get("/api/risk").json()["venues"]:
            assert 0.0 <= v["risk_pct_used"] <= 1.0

    def test_declared_venues_appear_even_without_a_single_active_slot(self, client, cfg):
        """Trouve en verifiant l'ecran : le niveau VENUE vient de la CONFIG, pas
        des slots actifs. Une venue declaree a une enveloppe et un budget de
        risque des qu'elle existe -- bot arrete ou aucun slot promu. Ne la
        montrer qu'a partir des slots rendait l'ecran vide au repos, soit la
        meme lecture trompeuse que S12 supprime, prise a l'envers."""
        body = client.get("/api/risk").json()          # aucun trader, aucun slot
        assert body["slots"] == []
        assert [v["venue"] for v in body["venues"]] == ["okx-t"]
        venue = body["venues"][0]
        assert venue["envelope"] == 1000.0
        assert venue["risk_budget"] == 50.0            # 5 % de 1 000
        assert venue["risk_engaged"] == 0.0

    def test_the_editable_config_block_is_served(self, client, cfg):
        """L'onglet Reglages edite des valeurs SAISIES ; /api/config ne sert pas
        `risk.envelopes`, donc /api/risk le porte explicitement."""
        body = client.get("/api/risk").json()
        assert body["envelopes_config"]["okx-t"]["symbol_risk_pct"] == 0.05

    def test_without_config_it_says_so(self, client, monkeypatch):
        monkeypatch.setattr(state, "cfg", None)
        assert client.get("/api/risk").status_code == 503


class TestDiagnostics:
    def test_reports_severity_counts(self, client, cfg):
        r = client.get("/api/risk/diagnostics")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"diagnostics", "errors", "warnings"}
        assert body["errors"] == len([d for d in body["diagnostics"]
                                      if d["severity"] == "error"])

    def test_each_diagnostic_carries_a_code_and_a_scope(self, client, cfg, monkeypatch):
        from app.core.risk_envelope import envelopes_for_active_slots
        envs = envelopes_for_active_slots(
            cfg, {"1h": [{"name": "trend_rider", "symbol": "BTC/USDC"}]})

        class _T:
            envelopes = envs
            ledger = None
            rejections = None
            _active_per_tf = {}
        monkeypatch.setattr(state, "trader", _T())
        for d in client.get("/api/risk/diagnostics").json()["diagnostics"]:
            assert d["severity"] in ("error", "warning")
            assert d["code"] and d["scope"] and d["message"]


class TestPostEnvelopes:
    def test_a_valid_edit_is_applied_and_persisted_attempt_is_reported(
            self, client, cfg, monkeypatch):
        """Edition multi-cles coherente : monter le budget symbole exige de
        monter aussi celui de la venue, sinon le premier serait inatteignable."""
        monkeypatch.setattr("app.api.routes.risk._save_yaml",
                            lambda fn: fn({"risk": {"envelopes": {}}}))
        r = client.post("/api/risk/envelopes",
                        json={"okx-t": {"symbol_risk_pct": 0.06, "venue_risk_pct": 0.08}})
        assert r.status_code == 200
        assert r.json()["envelopes"]["okx-t"]["symbol_risk_pct"] == 0.06
        assert cfg["risk"]["envelopes"]["okx-t"]["venue_risk_pct"] == 0.08

    def test_raising_the_symbol_budget_alone_is_refused(self, client, cfg):
        """Le budget symbole ne peut pas depasser celui de la venue qui le
        contient -- l'API applique la meme regle que le chargement."""
        r = client.post("/api/risk/envelopes", json={"okx-t": {"symbol_risk_pct": 0.06}})
        assert r.status_code == 400
        assert "venue_risk_pct" in r.json()["detail"]

    def test_an_out_of_bounds_value_is_refused(self, client, cfg):
        r = client.post("/api/risk/envelopes", json={"okx-t": {"symbol_risk_pct": 0.5}})
        assert r.status_code == 400
        assert "symbol_risk_pct" in r.json()["detail"]

    def test_an_unknown_venue_is_refused(self, client, cfg):
        r = client.post("/api/risk/envelopes", json={"inconnue": {"capital": 500}})
        assert r.status_code == 400

    def test_a_budget_below_the_risk_profile_is_refused(self, client, cfg):
        """La garantie mono-slot vaut AUSSI par l'API : sans ca, l'edition
        deviendrait un chemin de contournement de la validation, et le bot
        demarrerait sans jamais trader."""
        r = client.post("/api/risk/envelopes", json={"okx-t": {"symbol_risk_pct": 0.02}})
        assert r.status_code == 400
        assert "un seul slot actif" in r.json()["detail"]

    def test_the_refused_edit_leaves_the_config_untouched(self, client, cfg):
        before = dict(cfg["risk"]["envelopes"]["okx-t"])
        client.post("/api/risk/envelopes", json={"okx-t": {"symbol_risk_pct": 0.02}})
        assert cfg["risk"]["envelopes"]["okx-t"] == before

    def test_a_structurally_impossible_edit_is_refused(self, client, cfg, monkeypatch):
        """Capital ecrase a 1 EUR : plus aucun slot ne peut franchir le
        notionnel minimum de la venue -> 400, pas une config morte."""
        cfg["venues"]["defs"]["okx-t"]["min_notional"] = 200.0

        from app.core.risk_envelope import envelopes_for_active_slots
        envs = envelopes_for_active_slots(
            cfg, {"1h": [{"name": "trend_rider", "symbol": "BTC/USDC"}]})

        class _T:
            envelopes = envs
            ledger = None
            rejections = None
            _active_per_tf = {}
        monkeypatch.setattr(state, "trader", _T())

        r = client.post("/api/risk/envelopes", json={"okx-t": {"capital": 1.0}})
        assert r.status_code == 400
        assert "impossible" in r.json()["detail"]

    def test_an_empty_body_is_refused(self, client, cfg):
        assert client.post("/api/risk/envelopes", json={}).status_code == 400
