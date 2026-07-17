"""
Event Hub — pub/sub en mémoire pour diffusion temps réel via WebSocket.

Permet au LiveTrader (thread synchrone) de publier des événements qui seront
consommés par les clients WebSocket connectés (frontend Next.js).

Architecture :
  LiveTrader (thread synchrone)
        │
        ▼
   event_hub.publish(event_dict)   # non-bloquant
        │
        ▼
  EventHub._subscribers (list[asyncio.Queue])
        │
        ▼
  WebSocket handler (async) ──► navigateur client

Thread-safety : les subscribers sont des asyncio.Queue, mais publish() peut
être appelé depuis n'importe quel thread (synchrone). On utilise
loop.call_soon_threadsafe pour éviter les warnings "Task was destroyed but it
is pending!" et garantir que put_nowait s'exécute dans la bonne loop.
"""
import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventHub:
    """Hub de publication/subscription d'événements temps réel.

    - Un seul hub global (instance `event_hub` ci-dessous).
    - Chaque client WebSocket appelle `subscribe()` → reçoit une asyncio.Queue.
    - Le LiveTrader appelle `publish(event)` → l'événement est diffusé à tous.
    - Historique optionnel (ring buffer) pour permettre aux nouveaux clients de
      recevoir les N derniers événements (ex: rafraîchir la page sans rien perdre).
    """

    def __init__(self, history_size: int = 100):
        self._subscribers: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._history: deque = deque(maxlen=history_size)

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """À appeler au démarrage de l'app FastAPI pour fixer la loop."""
        self._loop = loop

    def subscribe(self, replay_history: bool = True) -> asyncio.Queue:
        """Crée une nouvelle queue abonnée aux événements.

        Si replay_history=True, les événements récents sont rejoués dans la queue
        (pour qu'un client qui se reconnecte ne rate rien).
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        if replay_history:
            for evt in self._history:
                try:
                    q.put_nowait(evt)
                except asyncio.QueueFull:
                    break
        logger.info(f"[EventHub] +1 subscriber (total={len(self._subscribers)})")
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Retire une queue (à appeler à la déconnexion du client)."""
        if q in self._subscribers:
            self._subscribers.remove(q)
            logger.info(f"[EventHub] -1 subscriber (total={len(self._subscribers)})")

    def publish(self, event: Dict[str, Any]) -> None:
        """Publie un événement à tous les subscribers.

        Non-bloquant : si une queue est pleine (client lent), on drop l'event.
        Peut être appelé depuis n'importe quel thread (sync ou async).
        """
        # Normalisation : tout event a un timestamp
        if "ts" not in event:
            event = {**event, "ts": _utcnow_iso()}

        # Historique
        self._history.append(event)

        if not self._subscribers:
            return

        # Si on est dans la loop async, put_nowait direct
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._dispatch, event)
        else:
            # Loop pas encore démarrée (démarrage du bot) : on skip
            pass

    def _dispatch(self, event: Dict[str, Any]) -> None:
        """Distribution effective (doit tourner dans la loop async)."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Client lent : on drop l'event le plus ancien et on retente
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Singleton global
event_hub = EventHub(history_size=100)


# ── Helpers typés pour publier des événements cohérents ─────────────────────

def publish_trade_opened(
    slot_key: str,
    symbol: str,
    side: str,
    size: float,
    entry_price: float,
    stop_price: float,
    strategy: str,
    timeframe: str,
    score: float = 0.0,
    reason: str = "",
) -> None:
    """Publie un événement d'ouverture de position."""
    event_hub.publish({
        "type": "trade.opened",
        "data": {
            "slot_key": slot_key,
            "symbol": symbol,
            "side": side,
            "size": size,
            "entry": entry_price,
            "stop": stop_price,
            "strategy": strategy,
            "timeframe": timeframe,
            "score": score,
            "reason": reason,
        },
    })


def publish_trade_closed(
    slot_key: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    fees: float = 0.0,
    reason: str = "",
    duration_bars: int = 0,
) -> None:
    """Publie un événement de clôture de position."""
    event_hub.publish({
        "type": "trade.closed",
        "data": {
            "slot_key": slot_key,
            "symbol": symbol,
            "side": side,
            "entry": entry_price,
            "exit": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "fees": fees,
            "reason": reason,
            "duration_bars": duration_bars,
        },
    })


def publish_signal(
    slot_key: str,
    symbol: str,
    timeframe: str,
    side: str,
    score: float,
    accepted: bool,
    reason: str = "",
) -> None:
    """Publie un signal généré par le pipeline (accepté ou rejeté)."""
    event_hub.publish({
        "type": "signal.generated",
        "data": {
            "slot_key": slot_key,
            "symbol": symbol,
            "timeframe": timeframe,
            "side": side,
            "score": score,
            "accepted": accepted,
            "reason": reason,
        },
    })


def publish_risk_event(
    event_subtype: str,
    severity: str = "warning",
    **fields: Any,
) -> None:
    """Publie un événement de risque (circuit breaker, drawdown, margin)."""
    event_hub.publish({
        "type": f"risk.{event_subtype}",
        "data": {"severity": severity, **fields},
    })


def publish_cycle_update(
    cycle: int,
    capital: float,
    open_positions: int,
    scan_duration_ms: float = 0.0,
) -> None:
    """Publie une mise à jour de cycle (heartbeat temps réel)."""
    event_hub.publish({
        "type": "cycle.update",
        "data": {
            "cycle": cycle,
            "capital": capital,
            "open_positions": open_positions,
            "scan_duration_ms": scan_duration_ms,
        },
    })


def publish_ticker(
    symbol: str,
    price: float,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    change_pct: Optional[float] = None,
) -> None:
    """Publie un ticker prix (pour mise à jour temps réel des positions)."""
    event_hub.publish({
        "type": "ticker.update",
        "data": {
            "symbol": symbol,
            "price": price,
            "bid": bid,
            "ask": ask,
            "change_pct": change_pct,
        },
    })
