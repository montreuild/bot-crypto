"""Routes configuration RISK — circuit breakers par slot.

Issu du split ARCH-013 de ``app/api/routes/config.py`` (684 lignes → 4 routers).
Endpoint :
- POST /api/config/risk
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import verify_api_key
from app.api.routes._config_helpers import _save_yaml
from app.api.schemas import RiskConfigBody

logger = logging.getLogger(__name__)
router = APIRouter()


# ── POST /api/config/risk ─────────────────────────────────────────────────

@router.post("/api/config/risk", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_risk_config(request: Request, body: RiskConfigBody):
    """Met à jour les circuit breakers par slot (SEC-03 : ``RiskConfigBody``)."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    changed = body.model_dump(exclude_none=True)
    for key, val in changed.items():
        state.cfg.setdefault("risk", {})[key] = val
        # Applique à chaud sur le RiskManager
        if state.trader and hasattr(state.trader.risk, f"_{key}"):
            setattr(state.trader.risk, f"_{key}", val)

    try:
        _save_yaml(lambda d: d.setdefault("risk", {}).update(changed))
        saved = True
    except Exception as e:
        logger.warning(f"[config/risk] sauvegarde YAML KO : {e}")
        saved = False
    return {"changed": changed, "saved_to_disk": saved,
            "trader_updated": state.trader is not None}
