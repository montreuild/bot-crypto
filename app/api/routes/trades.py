"""
Routes trades, statistiques et gestion du risque.

Endpoints :
  GET  /api/trades
  GET  /api/trades/export
  GET  /api/stats/daily
  GET  /api/risk
  POST /api/risk/reset-halt
"""
import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api import state
from app.api.helpers import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/trades", dependencies=[Depends(verify_api_key)])
def list_trades(limit: int = 100, offset: int = 0,
                symbol: str = None, strategy: str = None):
    """Retourne les trades paginés. Paramètres : limit, offset, symbol, strategy."""
    if not state.SessionLocal:
        raise HTTPException(503, "DB non initialisée")
    limit  = max(1, min(limit, 1000))
    offset = max(0, offset)
    session = state.SessionLocal()
    try:
        from app.core.database import Trade as _Trade
        q = session.query(_Trade)
        if symbol:   q = q.filter(_Trade.symbol   == symbol)
        if strategy: q = q.filter(_Trade.strategy == strategy)
        total = q.count()
        page  = q.order_by(_Trade.time.desc()).offset(offset).limit(limit).all()
        return {
            "total":  total,
            "offset": offset,
            "limit":  limit,
            "trades": [
                {"id": t.id, "time": str(t.time), "symbol": t.symbol, "side": t.side,
                 "strategy": t.strategy, "entry": t.entry, "exit": t.exit_price,
                 "pnl": t.pnl, "pnl_pct": t.pnl_pct, "fees": t.fees,
                 "status": t.status, "score": t.score, "reason": t.reason}
                for t in page
            ],
        }
    finally:
        session.close()


@router.get("/api/trades/export", dependencies=[Depends(verify_api_key)])
def export_trades(limit: int = 10000):
    """Export CSV des trades. limit = nombre max de trades (défaut 10 000, max 50 000)."""
    if not state.SessionLocal:
        raise HTTPException(503, "DB non initialisée")
    export_limit = max(1, min(limit, 50000))
    session = state.SessionLocal()
    try:
        from app.core.database import get_trades
        trades = get_trades(session, limit=export_limit)
        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow(["id", "time", "symbol", "side", "strategy",
                    "entry", "exit", "pnl", "fees", "status"])
        for t in trades:
            w.writerow([t.id, t.time, t.symbol, t.side, t.strategy,
                        t.entry, t.exit_price, t.pnl, t.fees, t.status])
        out.seek(0)
        return StreamingResponse(
            iter([out.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trades.csv"},
        )
    finally:
        session.close()


@router.get("/api/stats/daily", dependencies=[Depends(verify_api_key)])
def daily_stats(days: int = 30):
    if not state.SessionLocal:
        return []
    session = state.SessionLocal()
    try:
        from app.core.database import DailyStats
        rows = (session.query(DailyStats)
                .order_by(DailyStats.date.desc())
                .limit(days).all())
        return [{"date": r.date, "trades": r.trades, "wins": r.wins,
                 "pnl": r.pnl, "fees": r.fees,
                 "max_dd": r.max_dd, "equity_open": r.equity_open,
                 "equity_close": r.equity_close}
                for r in rows]
    finally:
        session.close()


@router.get("/api/risk", dependencies=[Depends(verify_api_key)])
def risk_status():
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    return state.trader.risk.status_dict()


@router.post("/api/risk/reset-halt", dependencies=[Depends(verify_api_key)])
def reset_halt():
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    state.trader.risk.reset_halt()
    state.trader.notif.reset_halt_notification()
    state.trader.notif.reset_dd_warning()
    return {"status": "reset", "message": "Circuit breaker réinitialisé"}
