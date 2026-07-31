"""OBS-01 — métriques Prometheus.

Ce que ces tests protègent, dans l'ordre de ce qui casserait le plus vite :

1. **La cardinalité.** C'est le mode de panne d'une instrumentation
   Prometheus, et il est silencieux : tout marche en développement, puis la
   mémoire du serveur de métriques enfle en production pendant des semaines.
   Un libellé qui recopierait un chemin brut — donnée non bornée, contrôlée
   par l'appelant — doit faire échouer un test, pas une alerte à 3h du matin.
2. **L'innocuité.** Le module est branché sur le chemin chaud du trading
   (``EventHub.publish``). Un événement malformé doit produire une métrique
   manquante, jamais une exception qui remonte dans le trader.
3. **La dégradation propre.** ``prometheus-client`` est optionnel : sans lui
   le bot démarre et trade, ``/metrics`` répond 503 en disant quoi installer.
"""
import pytest

from app.core import metrics

pytestmark = pytest.mark.skipif(not metrics.AVAILABLE,
                                reason="prometheus-client absent")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.api.main import app
    return TestClient(app)


def _sample(name: str, **labels) -> float:
    """Valeur courante d'une série, 0.0 si elle n'existe pas encore."""
    v = metrics.REGISTRY.get_sample_value(name, labels or None)
    return float(v) if v is not None else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Exposition
# ─────────────────────────────────────────────────────────────────────────────
class TestEndpoint:
    def test_metrics_is_served_in_prometheus_text_format(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "cryptobot_" in r.text

    def test_requests_are_counted_by_route_template(self, client):
        before = _sample("cryptobot_http_requests_total",
                         method="GET", route="/health", status="200")
        client.get("/health")
        after = _sample("cryptobot_http_requests_total",
                        method="GET", route="/health", status="200")
        assert after == before + 1

    def test_unmatched_paths_share_one_label(self, client):
        """Le cœur de la protection de cardinalité : un scan d'URL inexistantes
        — ou un bot qui teste ``/wp-admin`` toute la nuit — ne doit pas créer
        une série temporelle par chemin essayé."""
        for path in ("/nexiste-pas", "/nexiste-pas-non-plus", "/wp-admin.php"):
            client.get(path)
        body = client.get("/metrics").text
        assert 'route="__unmatched__"' in body
        for path in ("/nexiste-pas", "/wp-admin.php"):
            assert f'route="{path}"' not in body

    def test_latency_histogram_is_populated(self, client):
        client.get("/health")
        assert _sample("cryptobot_http_request_duration_seconds_count",
                       method="GET", route="/health") > 0


# ─────────────────────────────────────────────────────────────────────────────
#  Métier — via le hub d'événements
# ─────────────────────────────────────────────────────────────────────────────
class TestDomainEvents:
    def test_trade_opened_is_counted_from_the_hub(self):
        """Le branchement est dans ``EventHub.publish``, pas dans les
        appelants : publier suffit à mesurer."""
        from app.core.events import publish_trade_opened
        labels = dict(strategy="s_test", symbol="X/Y", side="long", timeframe="1h")
        before = _sample("cryptobot_trades_opened_total", **labels)
        publish_trade_opened("slot", "X/Y", "long", 1.0, 100.0, 95.0, "s_test", "1h")
        assert _sample("cryptobot_trades_opened_total", **labels) == before + 1

    @pytest.mark.parametrize("pnl,outcome", [(5.0, "win"), (-5.0, "loss"), (0.0, "flat")])
    def test_close_is_classified_by_outcome(self, pnl, outcome):
        from app.core.events import publish_trade_closed
        labels = dict(symbol="O/Q", side="long", outcome=outcome, reason="tp")
        before = _sample("cryptobot_trades_closed_total", **labels)
        publish_trade_closed("slot", "O/Q", "long", 100.0, 100.0 + pnl,
                             pnl, pnl, fees=0.1, reason="tp")
        assert _sample("cryptobot_trades_closed_total", **labels) == before + 1

    def test_cycle_update_sets_gauges(self):
        from app.core.events import publish_cycle_update
        publish_cycle_update(7, 4242.0, 3, scan_duration_ms=1500.0)
        assert _sample("cryptobot_capital") == 4242.0
        assert _sample("cryptobot_open_positions") == 3.0

    def test_a_malformed_event_never_raises(self):
        """L'observabilité ne doit pas pouvoir interrompre un trade."""
        for bad in ({}, {"type": "trade.closed"},
                    {"type": "trade.closed", "data": {"pnl": "pas un nombre"}},
                    {"type": "cycle.update", "data": None},
                    {"type": None, "data": []}):
            metrics.record_event(bad)          # ne doit rien lever


# ─────────────────────────────────────────────────────────────────────────────
#  Dégradation
# ─────────────────────────────────────────────────────────────────────────────
class TestGracefulDegradation:
    def test_helpers_are_noops_without_the_library(self, monkeypatch):
        monkeypatch.setattr(metrics, "AVAILABLE", False)
        metrics.record_event({"type": "trade.opened", "data": {}})
        payload, _ = metrics.render()
        assert payload is None

    def test_endpoint_explains_how_to_fix_it(self, client, monkeypatch):
        monkeypatch.setattr(metrics, "AVAILABLE", False)
        r = client.get("/metrics")
        assert r.status_code == 503
        assert "prometheus-client" in r.json()["detail"]


def test_timed_measures_a_block():
    import time
    with metrics.timed() as t:
        time.sleep(0.02)
    assert t.seconds >= 0.02
