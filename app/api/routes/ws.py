"""
Route WebSocket temps réel pour le frontend Next.js.

Endpoint : /ws

Protocole :
- Connexion : ws://localhost:8000/ws
- Auth (S1-04) : la connexion doit provenir de localhost OU présenter la clé
  API. Le cookie HttpOnly ``api_key`` (posé par le proxy Next) est vérifié
  EN PREMIER — il n'apparaît jamais dans l'URL. ``?api_key=`` n'est plus
  honoré (SEC-02). Un ``POST /api/ws/ticket`` authentifié délivre un jeton
  éphémère à usage unique (``?ticket=``), valable 30 s.

Messages serveur → client (tous au format JSON) :

  { "type": "trade.opened",    "ts": "...", "data": { ... } }
  { "type": "trade.closed",    "ts": "...", "data": { ... } }
  { "type": "signal.generated","ts": "...", "data": { ... } }
  { "type": "risk.circuit_breaker","ts":"...","data":{ "severity":"critical", ... } }
  { "type": "risk.drawdown_warning","ts":"...","data":{ "severity":"warning", ... } }
  { "type": "cycle.update",    "ts": "...", "data": { ... } }
  { "type": "ticker.update",   "ts": "...", "data": { ... } }
  { "type": "connected",       "ts": "...", "data": { "subscribers": N, "history_size": N } }

Messages client → serveur (optionnel) :
  { "type": "ping" }       → serveur répond { "type": "pong" }
  { "type": "subscribe",   "channels": ["trades","signals","risk"] }
                            → filtre les events reçus (par défaut : tous)
"""
import asyncio
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Set

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect

from app.api import state
from app.api.helpers import verify_api_key
from app.api.ws_tickets import consume_ticket, issue_ticket
from app.core.events import event_hub

logger = logging.getLogger(__name__)
router = APIRouter()

_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_local_ws_client(host: str) -> bool:
    """True si la connexion est considérée locale (SEC-004).

    ``testclient`` (hostname Starlette TestClient) n'est accepté que sous
    pytest — jamais dans un process de production.
    """
    if host in _LOCALHOST_HOSTS:
        return True
    return host == "testclient" and bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Auth WebSocket ───────────────────────────────────────────────────
def _check_ws_auth(websocket: WebSocket, ticket: Optional[str]) -> bool:
    """Cookie HttpOnly d'abord ; sinon jeton éphémère ``?ticket=`` (SEC-02)."""
    cfg = (state.cfg or {}).get("web", {})
    configured_key = cfg.get("api_key", "")

    client_host = websocket.client.host if websocket.client else ""

    if not configured_key:
        if _is_local_ws_client(client_host):
            return True
        logger.warning(f"[WS] Connexion refusée depuis {client_host} (no API key)")
        return False

    token = websocket.cookies.get("api_key") or ""
    if token and len(token) <= 256 and hmac.compare_digest(token, configured_key):
        return True
    if consume_ticket(ticket):
        return True
    return False


# ── Channels disponibles ──────────────────────────────────────────────────
CHANNELS = {"trades", "signals", "risk", "cycle", "ticker"}


def _event_channel(event_type: str) -> str:
    """Mappe un event_type à son channel."""
    if event_type.startswith("trade."):
        return "trades"
    if event_type.startswith("signal."):
        return "signals"
    if event_type.startswith("risk."):
        return "risk"
    if event_type.startswith("cycle."):
        return "cycle"
    if event_type.startswith("ticker."):
        return "ticker"
    return ""


# ── Endpoint WebSocket ───────────────────────────────────────────────────
@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    ticket: Optional[str] = Query(default=None),
):
    """Endpoint WebSocket temps réel.

    Le navigateur envoie le cookie HttpOnly ``api_key``. ``?api_key=`` n'est
    plus honoré. Un client sans cookie passe par ``POST /api/ws/ticket``.
    """
    if not _check_ws_auth(websocket, ticket):
        await websocket.close(code=4403, reason="Forbidden")
        return

    await websocket.accept()

    # Subscribe au hub
    queue = event_hub.subscribe(replay_history=True)

    # Message de bienvenue
    try:
        await websocket.send_json({
            "type": "connected",
            "ts": _utcnow_iso(),
            "data": {
                "subscribers": event_hub.subscriber_count,
                "history_size": len(event_hub._history),
                "channels": list(CHANNELS),
                "server_time": _utcnow_iso(),
            },
        })
    except Exception:
        pass

    # Lancement des deux tâches : lecture client + écriture hub
    subscribed_channels: Set[str] = set(CHANNELS)  # par défaut : tout

    async def read_client():
        """Lit les messages du client (ping, subscribe, etc.)."""
        nonlocal subscribed_channels
        try:
            while True:
                msg = await websocket.receive_json()
                msg_type = msg.get("type")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "ts": _utcnow_iso()})
                elif msg_type == "subscribe":
                    requested = set(msg.get("channels", []))
                    subscribed_channels = requested & CHANNELS
                    await websocket.send_json({
                        "type": "subscribed",
                        "data": {"channels": list(subscribed_channels)},
                    })
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"[WS] read_client error: {e}")

    async def write_hub():
        """Pousse les événements du hub vers le client."""
        try:
            while True:
                event = await queue.get()
                # Filtre par channel
                chan = _event_channel(event.get("type", ""))
                if chan and chan not in subscribed_channels:
                    continue
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"[WS] write_hub error: {e}")

    # Lance les deux tâches en parallèle
    tasks = [
        asyncio.create_task(read_client()),
        asyncio.create_task(write_hub()),
    ]
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.debug(f"[WS] gather error: {e}")
    finally:
        for t in tasks:
            t.cancel()
        event_hub.unsubscribe(queue)
        logger.info(f"[WS] client déconnecté (restant={event_hub.subscriber_count})")


# ── Endpoint REST pour debug ───────────────────────────────────────────────
@router.post("/api/ws/ticket", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("60/minute")
def ws_ticket(request: Request):
    """Jeton WS à usage unique, 30 s — jamais la clé API permanente.

    SEC-02 : le débit est plafonné assez haut pour ne jamais gêner une
    reconnexion légitime — le frontend redemande un jeton à chaque tentative,
    avec recul exponentiel — mais assez bas pour qu'un client emballé ne
    remplisse pas le registre.
    """
    token, ttl = issue_ticket()
    return {"ticket": token, "expires_in": ttl}


@router.get("/api/ws/status", dependencies=[Depends(verify_api_key)])
async def ws_status():
    """Status du hub WebSocket (debug, monitoring)."""
    return {
        "subscribers": event_hub.subscriber_count,
        "history_size": len(event_hub._history),
        "channels": list(CHANNELS),
    }
