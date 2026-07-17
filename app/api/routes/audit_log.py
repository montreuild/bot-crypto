"""Route audit log — traçabilité des actions sensibles."""
import logging

from fastapi import APIRouter, Depends, Query

from app.api.helpers import verify_api_key
from app.core.audit_log import get_audit_events

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/audit/log", dependencies=[Depends(verify_api_key)])
def audit_log_list(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action: str = Query("", description="Filtre sur l'action (substring)"),
    actor: str = Query("", description="Filtre sur l'acteur (substring)"),
):
    """Retourne les événements d'audit paginés.

    Filtres optionnels :
    - `action` : substring sur le nom d'action (ex: "bot." pour toutes les actions bot)
    - `actor` : substring sur l'acteur
    """
    return get_audit_events(limit=limit, offset=offset, action_filter=action, actor_filter=actor)


@router.get("/api/audit/log/stats", dependencies=[Depends(verify_api_key)])
def audit_log_stats():
    """Statistiques rapides : compte par action, dernière activité."""
    from app.core.audit_log import _SessionLocal, AuditEvent
    from sqlalchemy import func
    if _SessionLocal is None:
        return {"by_action": {}, "total": 0, "last_event": None}
    try:
        with _SessionLocal() as session:
            counts = (
                session.query(AuditEvent.action, func.count(AuditEvent.id))
                .group_by(AuditEvent.action)
                .order_by(func.count(AuditEvent.id).desc())
                .limit(20)
                .all()
            )
            total = session.query(func.count(AuditEvent.id)).scalar() or 0
            last = session.query(AuditEvent).order_by(AuditEvent.ts.desc()).first()
            return {
                "by_action": {action: count for action, count in counts},
                "total": total,
                "last_event": {
                    "ts": last.ts.isoformat() if last and last.ts else None,
                    "action": last.action if last else None,
                } if last else None,
            }
    except Exception as e:
        logger.warning(f"[audit/stats] KO : {e}")
        return {"by_action": {}, "total": 0, "last_event": None, "error": str(e)}
