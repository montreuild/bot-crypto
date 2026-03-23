"""
Routes contrôle du bot (démarrage / arrêt / gestion CBs).

Endpoints :
  POST /api/bot/start
  POST /api/bot/stop
  POST /api/circuit-breakers/reset/{slot_key}
"""
import threading as _threading
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/bot/start", dependencies=[Depends(verify_api_key)])
def bot_start():
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    if state.trader.running:
        return {"status": "already_running"}
    t = _threading.Thread(target=state.trader.start, daemon=True)
    t.start()
    return {"status": "started"}


@router.post("/api/bot/stop", dependencies=[Depends(verify_api_key)])
def bot_stop(close_positions: bool = False):
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    n_open = len(state.trader.open_positions)
    state.trader.stop(close_positions=close_positions)
    return {
        "status":           "stopped",
        "close_positions":  close_positions,
        "positions_closed": n_open if close_positions else 0,
        "positions_kept":   n_open if not close_positions else 0,
    }


@router.post("/api/circuit-breakers/reset/{slot_key:path}", dependencies=[Depends(verify_api_key)])
def reset_slot_circuit_breaker(slot_key: str):
    """
    Réinitialise manuellement la pause d'un slot.
    slot_key format: "trend::1h" (encodé en URL)
    """
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    state.trader.risk.reset_slot_pause(slot_key)
    return {
        "status":   "reset",
        "slot_key": slot_key,
        "message":  f"Pause du slot '{slot_key}' réinitialisée",
    }
