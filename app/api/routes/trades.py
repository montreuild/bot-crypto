"""Routes trades, statistiques journalières et risque."""
import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api import state
from app.api.helpers import verify_api_key
from app.core.database import session_scope

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/trades", dependencies=[Depends(verify_api_key)])
def list_trades(limit: int = 100, offset: int = 0,
                symbol: str = None, strategy: str = None):
    """Retourne les trades paginés avec filtres optionnels symbol/strategy."""
    if not state.SessionLocal:
        raise HTTPException(503, "DB non initialisée")
    limit  = max(1, min(limit, 1000))
    offset = max(0, offset)
    with session_scope(state.SessionLocal) as session:
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


@router.get("/api/trades/export", dependencies=[Depends(verify_api_key)])
def export_trades(limit: int = 10000):
    """Export CSV des trades (max 50 000)."""
    if not state.SessionLocal:
        raise HTTPException(503, "DB non initialisée")
    export_limit = max(1, min(limit, 50000))
    with session_scope(state.SessionLocal) as session:
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


@router.get("/api/stats/daily", dependencies=[Depends(verify_api_key)])
def daily_stats(days: int = 30):
    if not state.SessionLocal:
        return []
    with session_scope(state.SessionLocal) as session:
        from app.core.database import DailyStats
        rows = (session.query(DailyStats)
                .order_by(DailyStats.date.desc())
                .limit(days).all())
        return [{"date": r.date, "trades": r.trades, "wins": r.wins,
                 "pnl": r.pnl, "fees": r.fees,
                 "max_dd": r.max_dd, "equity_open": r.equity_open,
                 "equity_close": r.equity_close}
                for r in rows]


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


@router.get("/api/capital-allocation", dependencies=[Depends(verify_api_key)])
def capital_allocation():
    """Retourne l'allocation budgétaire par slot (strategy::tf)."""
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    return {
        "capital": round(state.trader.capital_display, 2),
        "slots":   state.trader.allocator.get_status(),
    }


@router.get("/api/circuit-breakers", dependencies=[Depends(verify_api_key)])
def circuit_breakers():
    """Retourne l'état de tous les circuit breakers (globaux + par slot)."""
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    return {
        "global_halted":    state.trader.risk.halted,
        "halt_reason":      state.trader.risk.halt_reason,
        "volatility_brake": state.trader.risk.volatility_brake_active,
        "circuit_breakers": state.trader.risk.get_circuit_breakers_status(),
        "slot_states":      state.trader.risk.get_slot_states(),
    }


@router.get("/api/audit/results", dependencies=[Depends(verify_api_key)])
def audit_results():
    """Retourne les résultats de l'optimiseur depuis config.yaml."""
    if not state.cfg:
        raise HTTPException(503, "Config non initialisée")
    opt_results = state.cfg.get("optimizer_results", {})
    rows = []
    for strategy, tfs in opt_results.items():
        if not isinstance(tfs, dict):
            continue
        for tf, data in tfs.items():
            if not isinstance(data, dict):
                continue
            rows.append({
                "strategy":  strategy,
                "tf":        tf,
                "slot_key":  f"{strategy}::{tf}",
                "run_date":  data.get("run_date", ""),
                "oos_score": round(float(data.get("oos_score", 0)), 4),
                "params":    data.get("params", {}),
            })
    rows.sort(key=lambda r: r["oos_score"], reverse=True)
    return {"results": rows, "total": len(rows)}


@router.get("/api/strategy/{slot_key:path}/performance", dependencies=[Depends(verify_api_key)])
def strategy_performance(slot_key: str):
    """Stats détaillées pour un slot strategy::tf (ex: trend::1h)."""
    if not state.SessionLocal:
        raise HTTPException(503, "DB non initialisée")

    parts = slot_key.split("::")
    if len(parts) != 2:
        raise HTTPException(400, "Format slot_key invalide. Attendu: strategy::tf (ex: trend::1h)")

    strategy_name, tf = parts[0], parts[1]
    with session_scope(state.SessionLocal) as session:
        from app.core.database import Trade as _Trade
        q = (session.query(_Trade)
             .filter(_Trade.strategy == strategy_name)
             .filter(_Trade.timeframe == tf)
             .order_by(_Trade.time.desc())
             .limit(500))
        trades_raw = q.all()
        total    = len(trades_raw)
        wins     = sum(1 for t in trades_raw if (t.pnl or 0) > 0)
        total_pnl = sum(float(t.pnl or 0) for t in trades_raw)
        gross_win = sum(float(t.pnl) for t in trades_raw if (t.pnl or 0) > 0)
        gross_loss = abs(sum(float(t.pnl) for t in trades_raw if (t.pnl or 0) < 0))

        win_rate = round(wins / total * 100, 1) if total > 0 else 0.0
        pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

        # Sharpe + Max DD
        pnls = [float(t.pnl or 0) for t in trades_raw]
        sharpe = 0.0
        max_dd = 0.0
        if len(pnls) >= 3:
            import numpy as _np
            arr = _np.array(pnls)
            std = float(_np.std(arr))
            if std > 0:
                sharpe = round(float(_np.mean(arr)) / std * _np.sqrt(252), 3)
            if len(pnls) >= 2:
                eq   = _np.cumsum(pnls)
                peak = _np.maximum.accumulate(eq)
                max_dd = round(float(_np.min((eq - peak) / (peak + 1e-9) * 100)), 2)

        slot_state = None
        if state.trader:
            for s in state.trader.risk.get_slot_states():
                if s["slot_key"] == slot_key:
                    slot_state = s
                    break

        return {
            "slot_key":      slot_key,
            "strategy":      strategy_name,
            "tf":            tf,
            "total_trades":  total,
            "wins":          wins,
            "win_rate":      win_rate,
            "total_pnl":     round(total_pnl, 4),
            "profit_factor": pf,
            "sharpe":        sharpe,
            "max_drawdown":  max_dd,
            "slot_state":    slot_state,
            "recent_trades": [
                {"time": str(t.time), "symbol": t.symbol, "side": t.side,
                 "entry": t.entry, "exit": t.exit_price,
                 "pnl": t.pnl, "pnl_pct": t.pnl_pct}
                for t in trades_raw[:20]
            ],
        }
