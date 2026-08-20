"""Routes trades, statistiques journalières et risque."""
import csv
import io
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api import state
from app.api.helpers import verify_api_key
from app.api.schemas import RiskOverviewResponse, TradesListResponse
from app.core.bot_identity import build_slot_key
from app.core.database import session_scope
from app.core.risk_gate import _default_venue_capital

logger = logging.getLogger(__name__)
router = APIRouter()

# V4-C : accepte le slot 3-parties ``strategy::tf::SYMBOL`` (ex.
# trend_rider::4h::ETH/USDC) — l'ancienne regex 2-parties renvoyait 400 sur
# les actions budget/toggle/reset de l'écran Bots depuis la refonte per-symbole.
_SLOT_KEY_RE = re.compile(
    r'^[a-z_][a-z0-9_]*::[0-9a-z]+(::[A-Za-z0-9/:\-]+)?$')


def _validate_slot_key(slot_key: str) -> None:
    if not _SLOT_KEY_RE.match(slot_key):
        raise HTTPException(400, "Format slot_key invalide")


@router.get("/api/trades", dependencies=[Depends(verify_api_key)],
            response_model=TradesListResponse)
def list_trades(limit: int = 100, offset: int = 0,
                symbol: str | None = None, strategy: str | None = None):
    """Retourne les trades paginés avec filtres optionnels symbol/strategy."""
    if not state.SessionLocal:
        raise HTTPException(503, "DB non initialisée")
    limit  = max(1, min(limit, 1000))
    offset = max(0, offset)
    with session_scope(state.SessionLocal) as session:
        from app.core.database import Trade as _Trade
        q = session.query(_Trade)
        if symbol:
            q = q.filter(_Trade.symbol   == symbol)
        if strategy:
            q = q.filter(_Trade.strategy == strategy)
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


@router.get("/api/stats/fees", dependencies=[Depends(verify_api_key)])
def fee_breakdown(days: int | None = None):
    """FIN-06 : compteur de frais par catégorie — taker/maker/borrow/stop.

    ``days`` optionnel (défaut : toute l'historique) limite aux trades clos
    dans les N derniers jours, comme ``/api/stats/daily``.
    """
    if not state.SessionLocal:
        return {"taker": 0.0, "maker": 0.0, "borrow": 0.0, "stop": 0.0}
    from datetime import datetime, timedelta, timezone

    from app.core.database import get_fee_breakdown
    since = (datetime.now(timezone.utc) - timedelta(days=days)) if days else None
    with session_scope(state.SessionLocal) as session:
        return get_fee_breakdown(session, since=since)


@router.get("/api/risk", dependencies=[Depends(verify_api_key)],
            response_model=RiskOverviewResponse)
def risk_status():
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    return state.trader.risk.status_dict()


@router.post("/api/risk/reset-halt", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("10/minute")
def reset_halt(request: Request, force: bool = False):
    """Réinitialise le HALT global.

    ``force=true`` est requis pour acquitter un **kill-switch** d'équité (veto
    catastrophe persistant) — sans quoi un simple reset le laisse actif.
    """
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    was_kill = state.trader.risk._kill_switch_tripped
    if was_kill and not force:
        raise HTTPException(
            409, "Kill-switch actif — acquittement explicite requis (force=true)."
        )
    state.trader.risk.reset_halt(force=force)
    state.trader.notif.reset_halt_notification()
    state.trader.notif.reset_dd_warning()
    # Lève aussi le kill-switch fichier éventuel posé par le watchdog dead-man.
    if force:
        try:
            from app.live.watchdog import clear_kill_switch
            clear_kill_switch()
        except Exception:
            pass
    return {"status": "reset", "force": force,
            "message": "Kill-switch acquitté" if was_kill else "Circuit breaker réinitialisé"}


# ── Gestion des slots ─────────────────────────────────────────────────────

@router.get("/api/slots", dependencies=[Depends(verify_api_key)])
def list_slots():
    """Slots avec leur enveloppe et l'état de leur coupe-circuit.

    S12 : le « budget » d'un slot EST son poids dans l'enveloppe de son
    symbole — il n'y a plus de budget à rééquilibrer en parallèle des poids.
    Le détail des trois niveaux emboîtés est servi par ``/api/risk``.
    """
    if not state.cfg:
        raise HTTPException(503, "Configuration non chargée")

    from app.core.risk_envelope import envelopes_for_active_slots

    envelopes = (getattr(state.trader, "envelopes", None) if state.trader else None)
    if not envelopes:
        active = getattr(state.trader, "_active_per_tf", None) or {}
        envelopes = envelopes_for_active_slots(state.cfg, active)

    disabled = set((state.cfg.get("risk") or {}).get("disabled_slots") or [])
    slot_states = ({s["slot_key"]: s for s in state.trader.risk.get_slot_states()}
                   if state.trader else {})
    engaged = (state.trader.ledger.snapshot()["slot"]
               if state.trader and getattr(state.trader, "ledger", None) else {})

    slots = []
    for key, env in sorted(envelopes.items()):
        cb = slot_states.get(key, {})
        slots.append({
            "slot_key": key, "venue": env.venue, "symbol": env.symbol,
            "currency": env.currency,
            "weight": round(env.weight, 4),
            "envelope": round(env.slot_envelope, 4),
            "risk_amount": round(env.slot_risk_amount, 4),
            "risk_engaged": round(engaged.get(key, {}).get("risk", 0.0), 4),
            "enabled": key not in disabled,
            "paused": cb.get("paused", False),
            "pause_reason": cb.get("pause_reason", ""),
            "consecutive_losses": cb.get("consecutive_losses", 0),
            "win_rate_15t": cb.get("win_rate_15t", 100.0),
            "daily_pnl": cb.get("daily_pnl", 0.0),
        })
    return {
        "capital": (round(state.trader.capital_display, 2) if state.trader
                    else _default_venue_capital(state.cfg)),
        "slots": slots,
    }


@router.post("/api/slots/{slot_key:path}/toggle", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def toggle_slot(request: Request, slot_key: str, enabled: bool = True):
    """Active ou désactive un slot (``risk.disabled_slots``).

    Seule clé rescapée de l'ancien bloc ``capital_allocator`` : un slot n'a
    plus besoin d'être « connu » d'un registre pour trader, seule une
    désactivation explicite le bloque."""
    _validate_slot_key(slot_key)
    if not state.cfg:
        raise HTTPException(503, "Configuration non chargée")

    disabled = set((state.cfg.get("risk") or {}).get("disabled_slots") or [])
    if enabled:
        disabled.discard(slot_key)
    else:
        disabled.add(slot_key)
    ordered = sorted(disabled)
    state.cfg.setdefault("risk", {})["disabled_slots"] = ordered
    try:
        from app.api.routes._config_helpers import _save_yaml
        _save_yaml(lambda d: d.setdefault("risk", {}).update({"disabled_slots": ordered}))
        saved = True
    except Exception as e:
        logger.warning(f"[slots] sauvegarde YAML KO : {e}")
        saved = False
    return {"status": "toggled", "slot_key": slot_key, "enabled": enabled,
            "disabled_slots": ordered, "saved_to_disk": saved}


@router.post("/api/slots/{slot_key:path}/reset", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def reset_slot(request: Request, slot_key: str):
    """Réinitialise le circuit breaker d'un slot."""
    _validate_slot_key(slot_key)
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    state.trader.risk.reset_slot_pause(slot_key)
    return {
        "status":   "reset",
        "slot_key": slot_key,
        "message":  f"Pause du slot '{slot_key}' réinitialisée",
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
    """Retourne les derniers résultats OOS de l'optimiseur.

    Les résultats sont persistés par l'optimiseur dans ``strategies/{nom}.yaml``
    (et fusionnés dans ``optimizer_results`` au chargement). Comme une optimisation
    lancée pendant la session écrit sur disque sans rafraîchir le ``state.cfg`` en
    mémoire, on relit la config depuis le disque ici afin que la page Audit OOS
    affiche toujours les **derniers** OOS appliqués.
    """
    if not state.cfg:
        raise HTTPException(503, "Config non initialisée")
    try:
        from app.core.config import load_config as _reload_cfg
        _fresh = _reload_cfg("config.yaml")
        if _fresh.get("optimizer_results"):
            state.cfg["optimizer_results"] = _fresh["optimizer_results"]
    except Exception as e:
        logger.warning(f"[audit/results] relecture config disque KO : {e}")
    opt_results = state.cfg.get("optimizer_results", {})
    rows = []
    for strategy, tfs in opt_results.items():
        if not isinstance(tfs, dict):
            continue
        for tf, data in tfs.items():
            if not isinstance(data, dict):
                continue
            # ``oos_score`` peut être absent / None (run échoué ou en cours)
            # Schéma par symbole (V4-C) : une entrée peut être héritée (plate)
            # ou un mapping {symbole: entrée} — une ligne par symbole.
            from app.core.param_resolution import _is_legacy_tf_entry
            entries = ([("", data)] if _is_legacy_tf_entry(data)
                       else [(sym, d) for sym, d in data.items()
                             if isinstance(d, dict)])
            for sym, entry in entries:
                raw_score = entry.get("oos_score")
                try:
                    oos_score = round(float(raw_score), 4) if raw_score is not None else 0.0
                except (TypeError, ValueError):
                    oos_score = 0.0
                rows.append({
                    "strategy":  strategy,
                    "tf":        tf,
                    "symbol":    sym,
                    "slot_key":  build_slot_key(strategy, tf, sym),
                    "run_date":  entry.get("run_date", ""),
                    "oos_score": oos_score,
                    "params":    entry.get("params", {}),
                })
    rows.sort(key=lambda r: r["oos_score"], reverse=True)

    # Dernier backtest réel par slot (strategy::tf) — persisté par /api/backtest.
    try:
        from app.core.backtest_history import load_backtest_history
        backtests = load_backtest_history()
    except Exception as e:
        logger.warning(f"[audit/results] historique backtests KO : {e}")
        backtests = {}

    return {"results": rows, "total": len(rows), "backtests": backtests}


@router.get("/api/strategy/{slot_key:path}/performance", dependencies=[Depends(verify_api_key)])
def strategy_performance(slot_key: str):
    """Stats détaillées pour un slot strategy::tf[::symbol] (ex: trend::1h,
    trend_rider::4h::ETH/USDC)."""
    if not state.SessionLocal:
        raise HTTPException(503, "DB non initialisée")

    from app.core.bot_identity import parse_slot_key
    strategy_name, tf, symbol = parse_slot_key(slot_key)
    if not strategy_name or not tf:
        raise HTTPException(400, "Format slot_key invalide. Attendu: "
                                 "strategy::tf[::symbol] (ex: trend::1h)")

    with session_scope(state.SessionLocal) as session:
        from app.core.database import Trade as _Trade
        q = (session.query(_Trade)
             .filter(_Trade.strategy == strategy_name)
             .filter(_Trade.timeframe == tf))
        if symbol:
            q = q.filter(_Trade.symbol == symbol)
        q = (q.order_by(_Trade.time.desc())
              .limit(500))
        trades_raw = q.all()
        total    = len(trades_raw)
        wins     = sum(1 for t in trades_raw if (t.pnl or 0) > 0)
        total_pnl = sum(float(t.pnl or 0) for t in trades_raw)
        gross_win = sum(float(t.pnl) for t in trades_raw if (t.pnl or 0) > 0)
        gross_loss = abs(sum(float(t.pnl) for t in trades_raw if (t.pnl or 0) < 0))

        win_rate = round(wins / total * 100, 1) if total > 0 else 0.0
        pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

        # R-01 : même plancher F-02 que health_mixin / BacktestResult.
        from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES
        pnls = [float(t.pnl or 0) for t in trades_raw]
        sharpe = None
        max_dd = 0.0
        if len(pnls) >= MIN_SIGNIFICANT_TRADES:
            import numpy as _np
            arr = _np.array(pnls)
            std = float(_np.std(arr))
            if std > 0:
                sharpe = round(float(_np.mean(arr)) / std * _np.sqrt(252), 3)
        if len(pnls) >= 2:
            import numpy as _np
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
