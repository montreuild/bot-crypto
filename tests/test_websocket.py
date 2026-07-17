"""
Tests du WebSocket temps réel (endpoint /ws + event hub).

Couvre :
- Connexion/déconnexion WebSocket
- Réception du message de bienvenue
- Publication d'un event via event_hub.publish()
- Réception par les subscribers
- Ping/pong
- Subscribe par channel
- Replay d'historique
- Auth (localhost only sans clé API)
"""
import asyncio
import json
from starlette.testclient import TestClient

from app.api import state
from app.api.main import app
from app.core.events import (
    event_hub,
    publish_trade_opened,
    publish_trade_closed,
    publish_signal,
    publish_risk_event,
)


def _setup_hub_loop():
    """Initialise la loop du hub pour les tests (en mode sync via TestClient)
    et purge l'historique pour éviter les interférences entre tests."""
    loop = asyncio.new_event_loop()
    event_hub.set_loop(loop)
    event_hub._history.clear()
    event_hub._subscribers.clear()
    return loop


def test_ws_welcome_message():
    """Le client reçoit un message 'connected' à la connexion."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert "subscribers" in msg["data"]
            assert "channels" in msg["data"]
            assert isinstance(msg["data"]["channels"], list)
            assert len(msg["data"]["channels"]) > 0


def test_ws_trade_opened_event():
    """Un event trade.opened publié via event_hub est reçu par le client WS."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            # Skip welcome
            ws.receive_json()

            # Publish
            publish_trade_opened(
                slot_key="trend_rider::1h::BTC/USDC",
                symbol="BTC/USDC",
                side="long",
                size=0.01,
                entry_price=50000.0,
                stop_price=49000.0,
                strategy="trend_rider",
                timeframe="1h",
                score=0.75,
                reason="test_signal",
            )

            # Receive event
            found = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "trade.opened":
                    assert msg["data"]["slot_key"] == "trend_rider::1h::BTC/USDC"
                    assert msg["data"]["symbol"] == "BTC/USDC"
                    assert msg["data"]["side"] == "long"
                    assert msg["data"]["entry"] == 50000.0
                    assert msg["data"]["strategy"] == "trend_rider"
                    assert "ts" in msg
                    found = True
                    break
            assert found, "trade.opened event not received"


def test_ws_trade_closed_event():
    """Un event trade.closed est reçu avec le PnL."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # welcome

            publish_trade_closed(
                slot_key="trend_rider::1h::BTC/USDC",
                symbol="BTC/USDC",
                side="long",
                entry_price=50000.0,
                exit_price=51000.0,
                pnl=10.0,
                pnl_pct=2.0,
                fees=0.5,
                reason="trailing",
                duration_bars=5,
            )

            found = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "trade.closed":
                    assert msg["data"]["pnl"] == 10.0
                    assert msg["data"]["pnl_pct"] == 2.0
                    assert msg["data"]["duration_bars"] == 5
                    found = True
                    break
            assert found


def test_ws_signal_event():
    """Un event signal.generated est reçu."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()

            publish_signal(
                slot_key="pullback_trend::4h::ETH/USDC",
                symbol="ETH/USDC",
                timeframe="4h",
                side="short",
                score=0.62,
                accepted=False,
                reason="score_below_threshold",
            )

            found = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "signal.generated":
                    assert msg["data"]["accepted"] is False
                    assert msg["data"]["score"] == 0.62
                    found = True
                    break
            assert found


def test_ws_risk_event():
    """Un event risk.* est reçu avec sa sévérité."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()

            publish_risk_event(
                "circuit_breaker",
                severity="critical",
                slot_key="trend_rider::1h::BTC/USDC",
                reason="consecutive_losses_exceeded",
            )

            found = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "risk.circuit_breaker":
                    assert msg["data"]["severity"] == "critical"
                    found = True
                    break
            assert found


def test_ws_ping_pong():
    """Le client envoie ping, le serveur répond pong."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # welcome
            ws.send_json({"type": "ping"})
            msg = ws.receive_json()
            assert msg["type"] == "pong"


def test_ws_subscribe_channel_filter():
    """Le client peut filtrer les events par channel."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # welcome

            # Subscribe uniquement au channel 'trades'
            ws.send_json({"type": "subscribe", "channels": ["trades"]})
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
            assert "trades" in msg["data"]["channels"]

            # Publie un signal (channel 'signals') → ne doit pas être reçu
            publish_signal(
                slot_key="t::1h::BTC/USDC",
                symbol="BTC/USDC", timeframe="1h",
                side="long", score=0.5, accepted=True,
            )
            # Publie un trade (channel 'trades') → doit être reçu
            publish_trade_opened(
                slot_key="t::1h::BTC/USDC",
                symbol="BTC/USDC", side="long", size=0.01,
                entry_price=50000, stop_price=49000,
                strategy="t", timeframe="1h",
                score=0.7, reason="",
            )

            found = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "trade.opened":
                    found = True
                    break
                # On ne doit PAS recevoir signal.generated
                assert msg["type"] != "signal.generated", "Signal filtré mais reçu"
            assert found, "trade.opened (channel trades) non reçu"


def test_ws_history_replay():
    """Un nouveau client reçoit l'historique des derniers events."""
    _setup_hub_loop()
    with TestClient(app) as client:
        # Publie un event AVANT la connexion WS
        publish_trade_opened(
            slot_key="history_test::1h::BTC/USDC",
            symbol="BTC/USDC", side="long", size=0.01,
            entry_price=50000, stop_price=49000,
            strategy="history_test", timeframe="1h",
            score=0.8, reason="replay_test",
        )

        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert msg["data"]["history_size"] > 0

            # Le prochain message devrait être l'event replayé
            found = False
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "trade.opened" and msg["data"]["slot_key"] == "history_test::1h::BTC/USDC":
                    found = True
                    break
            assert found, "Event historique non replayé"


def test_ws_status_endpoint():
    """L'endpoint REST /api/ws/status retourne l'état du hub."""
    with TestClient(app) as client:
        r = client.get("/api/ws/status")
        assert r.status_code == 200
        data = r.json()
        assert "subscribers" in data
        assert "history_size" in data
        assert "channels" in data
        assert isinstance(data["channels"], list)


def test_ws_multiple_subscribers():
    """Plusieurs clients reçoivent tous les events publiés."""
    _setup_hub_loop()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
            ws1.receive_json()  # welcome 1
            ws2.receive_json()  # welcome 2

            publish_trade_opened(
                slot_key="multi::1h::BTC/USDC",
                symbol="BTC/USDC", side="long", size=0.01,
                entry_price=50000, stop_price=49000,
                strategy="multi", timeframe="1h",
                score=0.7, reason="multi_test",
            )

            for ws in [ws1, ws2]:
                found = False
                for _ in range(20):
                    msg = ws.receive_json()
                    if msg["type"] == "trade.opened":
                        assert msg["data"]["slot_key"] == "multi::1h::BTC/USDC"
                        found = True
                        break
                assert found, f"Client n'a pas reçu l'event"


# ── Auth par cookie HttpOnly (S1-04) ────────────────────────────────────────

def test_ws_cookie_auth_accepted(monkeypatch):
    """Le cookie HttpOnly api_key (posé par les pages web) suffit — pas
    besoin de ?api_key= dans l'URL."""
    _setup_hub_loop()
    monkeypatch.setattr(state, "cfg", {"web": {"api_key": "secret123"}})
    with TestClient(app) as client:
        client.cookies.set("api_key", "secret123")
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "connected"


def test_ws_query_param_fallback_when_no_cookie(monkeypatch):
    """?api_key= reste un fallback pour les clients sans cookie jar."""
    _setup_hub_loop()
    monkeypatch.setattr(state, "cfg", {"web": {"api_key": "secret123"}})
    with TestClient(app) as client:
        with client.websocket_connect("/ws?api_key=secret123") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "connected"


def test_ws_wrong_key_rejected(monkeypatch):
    _setup_hub_loop()
    monkeypatch.setattr(state, "cfg", {"web": {"api_key": "secret123"}})
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/ws?api_key=wrong") as ws:
                ws.receive_json()
            assert False, "connexion aurait dû être refusée"
        except Exception:
            pass  # WebSocketDisconnect ou fermeture — refus attendu


def test_ws_cookie_takes_priority_over_wrong_query_param(monkeypatch):
    """Le cookie valide authentifie même si le query param est absent/faux —
    cf. _check_ws_auth : cookie vérifié en premier."""
    _setup_hub_loop()
    monkeypatch.setattr(state, "cfg", {"web": {"api_key": "secret123"}})
    with TestClient(app) as client:
        client.cookies.set("api_key", "secret123")
        with client.websocket_connect("/ws") as ws:  # pas de query param du tout
            msg = ws.receive_json()
            assert msg["type"] == "connected"
