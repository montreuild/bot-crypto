"""
Route WebSocket temps réel pour le frontend Next.js.

Endpoint : /ws

Protocole :
- Connexion : ws://localhost:8000/ws
- Auth (S1-04) : la connexion doit provenir de localhost OU présenter la clé
  API. Le cookie HttpOnly ``api_key`` (posé par le proxy Next) est vérifié
  EN PREMIER — il n'apparaît jamais dans l'URL, les logs serveur ou les
  devtools réseau. Le query param ``?api_key=xxx`` n'est honoré que si
  ``ALLOW_WS_QUERY_KEY=1`` (S-03) : sinon il fuit dans les journaux nginx
  et l'historique du navigateur.

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

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.api import state
from app.api.helpers import verify_api_key
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


def _allow_ws_query_key() -> bool:
    """S-03 : le repli ``?api_key=`` est opt-in (fuit dans les logs d'accès)."""
    return os.environ.get("ALLOW_WS_QUERY_KEY", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# ── Auth WebSocket ───────────────────────────────────────────────────
def _check_ws_auth(websocket: WebSocket, api_key_query: Optional[str]) -> bool:
    """Vérifie l'auth pour une connexion WebSocket.

    Règle (alignée sur verify_api_key) :
    - Si aucune clé API n'est configurée → seulement localhost autorisé.
    - Sinon → cookie HttpOnly ``api_key`` vérifié en premier (jamais dans
      l'URL/les logs). ``?api_key=xxx`` seulement si ``ALLOW_WS_QUERY_KEY=1``.
    """
    cfg = (state.cfg or {}).get("web", {})
    configured_key = cfg.get("api_key", "")

    client_host = websocket.client.host if websocket.client else ""

    if not configured_key:
        if _is_local_ws_client(client_host):
            return True
        logger.warning(f"[WS] Connexion refusée depuis {client_host} (no API key)")
        return False

    token = websocket.cookies.get("api_key") or ""
    if not token and api_key_query and _allow_ws_query_key():
        token = api_key_query
        logger.warning(
            "[WS] Auth via ?api_key= (ALLOW_WS_QUERY_KEY=1) depuis %s — "
            "la clé apparaît dans les journaux d'accès",
            client_host or "unknown",
        )
    if not token or len(token) > 256:
        return False
    return hmac.compare_digest(token, configured_key)


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
    api_key: Optional[str] = Query(default=None),
):
    """Endpoint WebSocket temps réel.

    Usage frontend :
        const ws = new WebSocket('ws://localhost:8000/ws');
        ws.onmessage = (e) => {
            const { type, ts, data } = JSON.parse(e.data);
            console.log(type, data);
        };

    Le navigateur envoie automatiquement le cookie HttpOnly ``api_key`` (posé
    par le proxy Next) — aucun paramètre à ajouter à l'URL. ``?api_key=``
    n'est honoré que si ``ALLOW_WS_QUERY_KEY=1``.
    """
    # Auth
    if not _check_ws_auth(websocket, api_key):
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
@router.get("/api/ws/status", dependencies=[Depends(verify_api_key)])
async def ws_status():
    """Status du hub WebSocket (debug, monitoring)."""
    return {
        "subscribers": event_hub.subscriber_count,
        "history_size": len(event_hub._history),
        "channels": list(CHANNELS),
    }
