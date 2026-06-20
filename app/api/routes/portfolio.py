"""Routes Phase 4 — portefeuille, bots (cycle de vie + cône MC), notifications, réglages.

Sert les vues « fonds piloté » : santé du portefeuille + allocation (réelle et
shadow), kanban des bots par état avec le cône Monte-Carlo vs réel (Phase 0),
fil d'activité (notifications 3 niveaux) et presets de risque.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Presets de risque (Réglages) ─────────────────────────────────────────────
_RISK_PRESETS = {
    "prudent": {
        "label": "Prudent",
        "trading": {"risk_per_trade": 0.005, "max_positions": 3,
                    "daily_drawdown_limit": 0.03, "max_drawdown_global": 0.15},
        "risk": {"equity_kill_switch_dd": 0.25},
    },
    "equilibre": {
        "label": "Équilibré",
        "trading": {"risk_per_trade": 0.01, "max_positions": 5,
                    "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20},
        "risk": {"equity_kill_switch_dd": 0.35},
    },
    "agressif": {
        "label": "Agressif",
        "trading": {"risk_per_trade": 0.02, "max_positions": 8,
                    "daily_drawdown_limit": 0.08, "max_drawdown_global": 0.30},
        "risk": {"equity_kill_switch_dd": 0.45},
    },
}


def _trader():
    return getattr(state, "trader", None)


# ── Fil d'activité (notifications 3 niveaux) ─────────────────────────────────
@router.get("/api/notifications", dependencies=[Depends(verify_api_key)])
def get_notifications(limit: int = 50, level: str = "info"):
    tr = _trader()
    notif = getattr(tr, "notif", None) if tr else None
    if notif is None or not hasattr(notif, "recent"):
        return {"notifications": [], "levels": ["info", "warning", "critical"]}
    return {
        "notifications": notif.recent(limit=limit, min_level=level),
        "levels": ["info", "warning", "critical"],
    }


# ── OOS tracker brut (cône MC vs réel) ───────────────────────────────────────
@router.get("/api/oos-tracker", dependencies=[Depends(verify_api_key)])
def get_oos_tracker():
    from app.core.oos_tracker import load_oos_tracker
    return {"slots": load_oos_tracker()}


# ── Bots : cycle de vie + identité + verdict réel vs simulation ──────────────
def _edge_significant(edge: dict, edge_min: int, max_worst: float) -> bool:
    """Réplique du gate d'edge (slot_lifecycle) pour l'affichage : borne basse
    du cône > 0, plancher de trades backtest, garde-fou de queue."""
    if not edge or not edge.get("available"):
        return False
    ci = edge.get("ci_low_pct")
    n = int(edge.get("n", 0) or 0)
    worst = edge.get("worst_trade_pct")
    if ci is None or ci <= 0 or n < edge_min:
        return False
    if worst is not None and worst < -max_worst:
        return False
    return True


@router.get("/api/bots", dependencies=[Depends(verify_api_key)])
def get_bots():
    from app.core.oos_tracker import load_oos_tracker
    oos = load_oos_tracker()
    tr = _trader()
    cfg = state.cfg or {}
    lc = cfg.get("lifecycle", {}) or {}
    edge_min = int(lc.get("edge_min_trades", 20))
    max_worst = float(lc.get("max_worst_trade_pct", 50.0))
    manual_set = set(lc.get("manual_active", []) or [])

    # États du cycle de vie : snapshot live, sinon base.
    states = {}
    counts = {}
    reopt = []
    if tr and getattr(tr, "_lifecycle_snapshot", None):
        snap = tr._lifecycle_snapshot
        states = snap.get("states", {})
        counts = snap.get("counts", {})
        reopt = snap.get("reopt_queue", [])
    else:
        try:
            from app.core.database import get_current_lifecycle_states, session_scope
            if tr:
                with session_scope(tr.SessionLocal) as s:
                    states = get_current_lifecycle_states(s)
        except Exception:
            states = {}

    # Identités de bot + budgets allocateur.
    identities = {}
    budgets = {}
    if tr:
        try:
            for ident in tr.get_bot_identities():
                identities[ident["slot_key"]] = ident
        except Exception:
            pass
        try:
            for s in tr.allocator.get_status():
                budgets[s["slot_key"]] = s
        except Exception:
            pass

    # Union des slots connus (oos ∪ states ∪ budgets).
    # Overlay des forçages manuels (droit de veto) : état affiché = actif.
    for key in manual_set:
        states[key] = "candidat" if key not in states else states[key]
        states[key] = "actif"

    keys = set(oos) | set(states) | set(budgets) | set(identities) | manual_set
    bots = []
    for key in sorted(keys):
        rec = oos.get(key, {})
        contract = rec.get("contract", {}) or {}
        edge = rec.get("edge", {}) or {}
        strat, _, tf = key.partition("::")
        bots.append({
            "slot_key":     key,
            "strategy":     rec.get("strategy", strat),
            "timeframe":    rec.get("timeframe", tf),
            "state":        states.get(key, "candidat"),
            "identity":     identities.get(key),
            "budget":       budgets.get(key),
            "sim":          rec.get("sim"),
            "monte_carlo":  rec.get("monte_carlo"),
            "edge":         edge,
            "edge_significant": _edge_significant(edge, edge_min, max_worst),
            "manual_active": key in manual_set,
            "live":         rec.get("live"),
            "contract":     contract,
            "verdict":      contract.get("verdict"),
            "in_band":      contract.get("in_band"),
            "run_date":     rec.get("run_date"),
        })

    # Recompter après overlay manuel (les forçages comptent comme actifs).
    counts = {st: sum(1 for b in bots if b["state"] == st)
              for st in ("candidat", "essai", "actif", "retire")}

    return {"bots": bots, "counts": counts, "reopt_queue": reopt,
            "states": states,
            "thresholds": {"edge_min_trades": edge_min,
                           "max_worst_trade_pct": max_worst,
                           "fidelity_min_fills": int(lc.get("fidelity_min_fills", 2))}}


# ── Bypass manuel : forcer un bot en ACTIF (droit de veto utilisateur) ───────
@router.post("/api/bots/{slot_key:path}/force-active",
             dependencies=[Depends(verify_api_key)])
def force_active(slot_key: str, enabled: bool = True):
    """Force (``enabled=true``) ou libère (``false``) l'activation manuelle d'un
    bot. Persisté dans ``config.yaml`` (lifecycle.manual_active) et appliqué au
    cycle de vie en cours s'il tourne."""
    cfg = state.cfg or {}
    lc = cfg.setdefault("lifecycle", {})
    manual = [k for k in lc.get("manual_active", []) if k != slot_key]
    if enabled:
        manual.append(slot_key)
    manual = sorted(set(manual))
    lc["manual_active"] = manual

    try:
        from app.api.routes.config import _save_yaml
        _save_yaml(lambda d: d.setdefault("lifecycle", {}).update(
            {"manual_active": manual}))
    except Exception as e:
        logger.warning(f"[bots] sauvegarde manual_active KO : {e}")

    tr = _trader()
    if tr and getattr(tr, "_lifecycle", None):
        tr._lifecycle.set_manual_active(slot_key, enabled)

    logger.info(f"[bots] {slot_key} : forçage manuel ACTIF = {enabled}")
    return {"slot_key": slot_key, "manual_active": enabled, "all": manual}


# ── Forcer un forward-test (recalcul de l'edge) pour un bot ──────────────────
@router.post("/api/bots/{slot_key:path}/forward-test",
             dependencies=[Depends(verify_api_key)])
def run_bot_forward_test(slot_key: str):
    """Relance immédiatement le forward-test glissant d'un seul bot : re-backteste
    ses params figés (edge sur fenêtre longue + fidélité sur fenêtre courte),
    réécrit ``data/oos_tracker.json`` et, si le trader tourne, ré-évalue le cycle
    de vie pour que l'état se mette à jour tout de suite."""
    cfg = state.cfg or {}
    strat, _, tf = slot_key.partition("::")
    if not strat or not tf:
        raise HTTPException(400, "slot_key attendu au format 'strategy::timeframe'")
    ft = cfg.get("forward_test", {}) or {}
    lookback = int(ft.get("lookback_days", 45))
    edge_lb = int(ft.get("edge_lookback_days", 100))
    symbol = ft.get("symbol", "BTC/USDC")

    tr = _trader()
    if tr and getattr(tr, "scanner", None):
        fetch = tr.scanner.fetch_ohlcv
        session_factory = tr.SessionLocal
    else:
        try:
            from app.api.routes.backtest import _get_bt_exchange
            from app.core.candle_store import get_store
            exch = _get_bt_exchange(cfg)

            def fetch(sym, timeframe, limit=500):
                return get_store().fetch(exch, sym, timeframe, total=limit,
                                         prefer_cache=True)
        except Exception as e:
            raise HTTPException(503, f"Impossible d'initialiser les données : {e}")
        session_factory = state.SessionLocal

    from app.core.oos_tracker import run_forward_test
    try:
        res = run_forward_test(cfg, fetch, {tf: [{"name": strat}]}, session_factory,
                               symbol=symbol, lookback_days=lookback,
                               edge_lookback_days=edge_lb)
    except Exception as e:
        logger.error(f"[bots] forward-test {slot_key} KO : {e}", exc_info=True)
        raise HTTPException(500, f"Forward-test échoué : {e}")

    rec = res.get(slot_key) or {}
    # Ré-évalue le cycle de vie maintenant si le trader tourne (sinon l'edge est
    # recalculée et visible, mais la transition d'état attend le trader).
    state_changed = False
    if tr and getattr(tr, "_lifecycle_enabled", False):
        try:
            tr._lifecycle_thread()
            state_changed = True
        except Exception as e:
            logger.debug(f"[bots] ré-évaluation lifecycle KO : {e}")

    return {
        "slot_key": slot_key,
        "ran": bool(rec),
        "edge": rec.get("edge"),
        "trader_running": bool(tr),
        "state_reevaluated": state_changed,
    }


# ── Portefeuille : santé + allocation réelle/shadow + activité ───────────────
@router.get("/api/portfolio", dependencies=[Depends(verify_api_key)])
def get_portfolio():
    tr = _trader()
    cfg = state.cfg or {}
    if not tr:
        return {
            "running": False,
            "capital": cfg.get("trading", {}).get("capital", 0),
            "allocation": [], "shadow_allocation": {}, "lifecycle": {},
            "risk": {}, "activity": [],
        }
    st = tr.status
    notif = getattr(tr, "notif", None)
    activity = notif.recent(limit=20) if notif and hasattr(notif, "recent") else []
    return {
        "running":           tr.running,
        "paper_mode":        cfg.get("trading", {}).get("paper_mode", True),
        "capital":           st.get("capital"),
        "total_pnl":         st.get("total_pnl"),
        "total_pnl_pct":     st.get("total_pnl_pct"),
        "win_rate":          st.get("win_rate"),
        "open_positions":    st.get("positions", []),
        "allocation":        st.get("capital_allocation", []),
        "shadow_allocation": st.get("shadow_allocation", {}),
        "continuous_allocation": bool(getattr(getattr(tr, "allocator", None),
                                              "continuous_allocation", False)),
        "lifecycle":         st.get("lifecycle", {}),
        "bots":              st.get("bots", []),
        "risk": {
            "halted":             st.get("halted"),
            "halt_reason":        st.get("halt_reason"),
            "kill_switch":        st.get("kill_switch"),
            "veto_mode":          st.get("veto_mode"),
            "veto_shadow_blocks": st.get("veto_shadow_blocks"),
            "global_dd_pct":      st.get("global_dd_pct"),
            "daily_pnl_pct":      st.get("daily_pnl_pct"),
        },
        "activity": activity,
    }


# ── Réglages : presets de risque + mode expert ───────────────────────────────
@router.get("/api/settings/presets", dependencies=[Depends(verify_api_key)])
def get_presets():
    cfg = state.cfg or {}
    ui = cfg.get("ui", {}) or {}
    return {
        "presets": {k: {"label": v["label"], **v["trading"], **v["risk"]}
                    for k, v in _RISK_PRESETS.items()},
        "current": ui.get("risk_preset"),
        "expert_mode": bool(ui.get("expert_mode", False)),
    }


@router.post("/api/settings/risk-preset", dependencies=[Depends(verify_api_key)])
def set_risk_preset(preset: str):
    if preset not in _RISK_PRESETS:
        raise HTTPException(400, f"Preset inconnu : {preset}")
    p = _RISK_PRESETS[preset]
    from app.api.routes.config import _save_yaml

    def _apply(disk):
        disk.setdefault("trading", {}).update(p["trading"])
        disk.setdefault("risk", {}).update(p["risk"])
        disk.setdefault("ui", {})["risk_preset"] = preset
    _save_yaml(_apply)

    # Met à jour la config en mémoire (sans redémarrage).
    if state.cfg:
        state.cfg.setdefault("trading", {}).update(p["trading"])
        state.cfg.setdefault("risk", {}).update(p["risk"])
        state.cfg.setdefault("ui", {})["risk_preset"] = preset
    logger.info(f"[Settings] Preset de risque appliqué : {preset}")
    return {"applied": preset, **p["trading"], **p["risk"],
            "note": "Pris en compte au prochain (re)démarrage du trader pour le RiskManager."}


@router.post("/api/settings/expert-mode", dependencies=[Depends(verify_api_key)])
def set_expert_mode(enabled: bool = False):
    from app.api.routes.config import _save_yaml
    _save_yaml(lambda disk: disk.setdefault("ui", {}).update({"expert_mode": enabled}))
    if state.cfg:
        state.cfg.setdefault("ui", {})["expert_mode"] = enabled
    return {"expert_mode": enabled}
