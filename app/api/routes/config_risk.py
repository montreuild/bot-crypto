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

logger = logging.getLogger(__name__)
router = APIRouter()


# ── POST /api/config/risk ─────────────────────────────────────────────────

@router.post("/api/config/risk", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_risk_config(
    request:                 Request,
    consecutive_loss_limit:  int   = None,
    slot_daily_dd_limit:     float = None,
    win_rate_floor:          float = None,
    volatility_threshold:    float = None,
    consecutive_pause_secs:  int   = None,
):
    """Met à jour la configuration des circuit breakers par slot."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    # ── Validation des bornes ──
    if consecutive_loss_limit is not None and not (1 <= consecutive_loss_limit <= 20):
        raise HTTPException(400, "consecutive_loss_limit doit être entre 1 et 20")
    if slot_daily_dd_limit is not None and not (0.0 < slot_daily_dd_limit <= 0.5):
        raise HTTPException(400, "slot_daily_dd_limit doit être entre 0 (exclus) et 0.5")
    if win_rate_floor is not None and not (0.0 <= win_rate_floor <= 1.0):
        raise HTTPException(400, "win_rate_floor doit être entre 0 et 1")
    if volatility_threshold is not None and not (0.0 < volatility_threshold <= 1.0):
        raise HTTPException(400, "volatility_threshold doit être entre 0 (exclus) et 1.0")
    if consecutive_pause_secs is not None and not (60 <= consecutive_pause_secs <= 86400):
        raise HTTPException(400, "consecutive_pause_secs doit être entre 60 et 86400 (1 min — 24h)")
    changed = {}
    mapping = {
        "consecutive_loss_limit":  consecutive_loss_limit,
        "slot_daily_dd_limit":     slot_daily_dd_limit,
        "win_rate_floor":          win_rate_floor,
        "volatility_threshold":    volatility_threshold,
        "consecutive_pause_secs":  consecutive_pause_secs,
    }
    for key, val in mapping.items():
        if val is not None:
            state.cfg.setdefault("risk", {})[key] = val
            changed[key] = val
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
