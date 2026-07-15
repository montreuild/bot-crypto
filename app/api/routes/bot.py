"""Routes contrôle du bot — start, stop, reset circuit breaker."""
import threading as _threading
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import verify_api_key
from app.core.audit_log import audit_log

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/bot/start", dependencies=[Depends(verify_api_key)])
def bot_start(request: Request):
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    if state.trader.running:
        return {"status": "already_running"}
    audit_log("bot.start", ip=request.client.host if request.client else "",
              method="POST", path="/api/bot/start")
    t = _threading.Thread(target=state.trader.start, daemon=True)
    t.start()
    return {"status": "started"}


@router.post("/api/bot/stop", dependencies=[Depends(verify_api_key)])
def bot_stop(request: Request, close_positions: bool = False):
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    n_open = len(state.trader.open_positions)
    audit_log("bot.stop", ip=request.client.host if request.client else "",
              method="POST", path="/api/bot/stop",
              details={"close_positions": close_positions, "n_open": n_open})
    state.trader.stop(close_positions=close_positions)
    return {
        "status":           "stopped",
        "close_positions":  close_positions,
        "positions_closed": n_open if close_positions else 0,
        "positions_kept":   n_open if not close_positions else 0,
    }


@router.post("/api/circuit-breakers/reset/{slot_key:path}", dependencies=[Depends(verify_api_key)])
def reset_slot_circuit_breaker(slot_key: str, request: Request):
    """Réinitialise la pause d'un slot (format: strategy::tf)."""
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    audit_log("circuit_breaker.reset", ip=request.client.host if request.client else "",
              method="POST", path=f"/api/circuit-breakers/reset/{slot_key}",
              details={"slot_key": slot_key})
    state.trader.risk.reset_slot_pause(slot_key)
    return {
        "status":   "reset",
        "slot_key": slot_key,
        "message":  f"Pause du slot '{slot_key}' réinitialisée",
    }
