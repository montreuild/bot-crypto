"""
Audit logging — traçabilité des actions sensibles.

Stocke les événements d'audit dans une table SQLite dédiée `audit_events`.
Permet de retracer "qui a fait quoi et quand" : start/stop bot, apply params,
change strategies, reset circuit breaker, etc.

Usage direct :
    from app.core.audit_log import audit_log
    audit_log("bot.stop", actor="user", ip="127.0.0.1", details={"reason": "manual"})

Usage via décorateur (route FastAPI) :
    @audit_action("bot.start")
    async def bot_start(): ...

Usage via middleware (auto-log all POST/DELETE) :
    # Dans main.py :
    app.add_middleware(AuditLogMiddleware)
"""
import json
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Optional, cast

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


# ── Modèle ORM ──────────────────────────────────────────────────────────────

try:
    from app.core.database import Base
except Exception:
    Base = None

if Base is not None:
    class AuditEvent(Base):
        __tablename__ = "audit_events"

        id = Column(Integer, primary_key=True, autoincrement=True)
        ts = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
        action = Column(String(128), nullable=False, index=True)
        actor = Column(String(64), nullable=False, default="anonymous")
        ip = Column(String(45), nullable=False, default="")
        method = Column(String(10), nullable=False, default="")
        path = Column(String(256), nullable=False, default="")
        details = Column(Text, default="{}")
        status_code = Column(Integer, default=0)

        __table_args__ = (
            Index("ix_audit_events_action_ts", "action", "ts"),
        )

    _engine = None
    _SessionLocal = None

    def _init_audit_db(engine):
        """Crée la table audit_events si elle n'existe pas."""
        global _engine, _SessionLocal
        _engine = engine
        _SessionLocal = sessionmaker(bind=engine)
        try:
            Base.metadata.create_all(engine, tables=[AuditEvent.__table__])
            logger.info("[AuditLog] Table audit_events prête")
        except Exception as e:
            logger.warning(f"[AuditLog] create_all KO : {e}")

    def audit_log(
        action: str,
        actor: str = "anonymous",
        ip: str = "",
        method: str = "",
        path: str = "",
        details: Optional[dict] = None,
        status_code: int = 0,
    ) -> None:
        """Enregistre un événement d'audit. Non bloquant (jamais critique)."""
        if _SessionLocal is None:
            return
        try:
            with _SessionLocal() as session:
                evt = AuditEvent(
                    action=action[:128],
                    actor=actor[:64],
                    ip=ip[:45],
                    method=method[:10],
                    path=path[:256],
                    details=json.dumps(details or {}, default=str),
                    status_code=status_code,
                )
                session.add(evt)
                session.commit()
        except Exception as e:
            logger.debug(f"[AuditLog] write KO : {e}")

    def get_audit_events(
        limit: int = 100,
        offset: int = 0,
        action_filter: str = "",
        actor_filter: str = "",
    ) -> dict:
        """Retourne les événements d'audit paginés avec filtres optionnels."""
        if _SessionLocal is None:
            return {"events": [], "total": 0, "limit": limit, "offset": offset}
        try:
            with _SessionLocal() as session:
                q = session.query(AuditEvent)
                if action_filter:
                    q = q.filter(AuditEvent.action.ilike(f"%{action_filter}%"))
                if actor_filter:
                    q = q.filter(AuditEvent.actor.ilike(f"%{actor_filter}%"))
                total = q.count()
                rows = (
                    q.order_by(AuditEvent.ts.desc())
                    .offset(offset)
                    .limit(min(limit, 1000))
                    .all()
                )
                return {
                    "events": [
                        {
                            "id": r.id,
                            "ts": r.ts.isoformat() if r.ts else None,
                            "action": r.action,
                            "actor": r.actor,
                            "ip": r.ip,
                            "method": r.method,
                            "path": r.path,
                            "details": json.loads(cast(str, r.details)) if r.details else {},
                            "status_code": r.status_code,
                        }
                        for r in rows
                    ],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }
        except Exception as e:
            logger.warning(f"[AuditLog] read KO : {e}")
            return {"events": [], "total": 0, "limit": limit, "offset": offset, "error": str(e)}

    def audit_action(action_name: str):
        """Décorateur pour logger automatiquement une route FastAPI."""
        def decorator(fn):
            @wraps(fn)
            async def wrapper(*args, **kwargs):
                request = kwargs.get("request")
                ip = ""
                method = ""
                path = ""
                if request:
                    ip = getattr(request.client, "host", "") if request.client else ""
                    method = request.method
                    path = request.url.path
                actor = "user"  # pourrait être étendu avec un système d'auth
                try:
                    result = await fn(*args, **kwargs)
                    audit_log(action_name, actor=actor, ip=ip, method=method, path=path,
                              details=kwargs, status_code=200)
                    return result
                except Exception as e:
                    audit_log(action_name, actor=actor, ip=ip, method=method, path=path,
                              details={"error": str(e)[:200]}, status_code=500)
                    raise
            return wrapper
        return decorator

else:
    # Fallback si Base n'est pas disponible (ne devrait pas arriver en prod)
    def audit_log(*_a, **_kw):  # type: ignore[misc]
        pass
    def get_audit_events(*_a, **_kw):  # type: ignore[misc]
        return {"events": [], "total": 0}
    def audit_action(_name):  # type: ignore[misc]
        def deco(fn):
            @wraps(fn)
            async def w(*a, **k): return await fn(*a, **k)
            return w
        return deco
    def _init_audit_db(_e): pass
