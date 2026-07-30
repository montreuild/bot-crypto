"""Routes configuration NOTIFICATIONS — lecture, mise à jour, test.

Issu du split ARCH-013 de ``app/api/routes/config.py`` (684 lignes → 4 routers).
Endpoints :
- GET  /api/config/notifications
- POST /api/config/notifications
- POST /api/config/notifications/test
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import verify_api_key
from app.api.routes._config_helpers import _save_yaml

logger = logging.getLogger(__name__)
router = APIRouter()


# ── GET /api/config/notifications ─────────────────────────────────────────

@router.get("/api/config/notifications", dependencies=[Depends(verify_api_key)])
def get_notifications_config():
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    notif = dict(state.cfg.get("notifications", {}))

    def _mask(token: str) -> str:
        if not token:
            return ""
        return token[:4] + "****" + token[-3:] if len(token) > 8 else "****"

    sensitive = ["telegram_bot_token", "twilio_auth_token", "twilio_account_sid",
                 "whatsapp_token", "email_password"]
    for k in sensitive:
        if k in notif and notif[k]:
            notif[k] = _mask(str(notif[k]))
    return notif


# ── POST /api/config/notifications ────────────────────────────────────────

@router.post("/api/config/notifications", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_notifications_config(
    request:                Request,
    telegram_enabled:       bool  = None,
    telegram_bot_token:     str   = None,
    telegram_chat_id:       str   = None,
    whatsapp_enabled:       bool  = None,
    whatsapp_number:        str   = None,
    whatsapp_token:         str   = None,
    email_enabled:          bool  = None,
    email_smtp:             str   = None,
    email_port:             int   = None,
    email_user:             str   = None,
    email_password:         str   = None,
    email_to:               str   = None,
    min_pnl_to_notify:      float = None,
    position_loss_warn_pct: float = None,
):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    notif = state.cfg.setdefault("notifications", {})
    mapping = {
        "telegram_enabled":       telegram_enabled,
        "telegram_bot_token":     telegram_bot_token,
        "telegram_chat_id":       telegram_chat_id,
        "whatsapp_enabled":       whatsapp_enabled,
        "whatsapp_number":        whatsapp_number,
        "whatsapp_token":         whatsapp_token,
        "email_enabled":          email_enabled,
        "email_smtp":             email_smtp,
        "email_port":             email_port,
        "email_user":             email_user,
        "email_password":         email_password,
        "email_to":               email_to,
        "min_pnl_to_notify":      min_pnl_to_notify,
        "position_loss_warn_pct": position_loss_warn_pct,
    }
    changed = {k: v for k, v in mapping.items() if v is not None}
    notif.update(changed)

    if state.trader:
        from app.core.notifications import Notifier
        state.trader.notif = Notifier(state.cfg)
        state.trader.risk.attach_notifier(state.trader.notif)

    try:
        def _upd(d):
            n = d.setdefault("notifications", {})
            n.update({k: v for k, v in changed.items()
                      if "password" not in k and "token" not in k})
            for k in ["telegram_bot_token", "whatsapp_token", "email_password"]:
                if k in changed:
                    n[k] = changed[k]
        _save_yaml(_upd)
        saved = True
    except Exception as e:
        logger.warning(f"[config/notifications] sauvegarde YAML KO : {e}")
        saved = False
    return {"changed": list(changed.keys()), "saved_to_disk": saved}


# ── POST /api/config/notifications/test ──────────────────────────────────

@router.post("/api/config/notifications/test", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("5/minute")
def test_notification(request: Request):
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    state.trader.notif.send("🔔 Test de notification depuis le bot", async_=False)
    return {"status": "sent"}
