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
@router.get("/api/bots", dependencies=[Depends(verify_api_key)])
def get_bots():
    from app.core.oos_tracker import load_oos_tracker
    oos = load_oos_tracker()
    tr = _trader()

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
    keys = set(oos) | set(states) | set(budgets) | set(identities)
    bots = []
    for key in sorted(keys):
        rec = oos.get(key, {})
        contract = rec.get("contract", {}) or {}
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
            "live":         rec.get("live"),
            "contract":     contract,
            "verdict":      contract.get("verdict"),
            "in_band":      contract.get("in_band"),
            "run_date":     rec.get("run_date"),
        })

    return {"bots": bots, "counts": counts, "reopt_queue": reopt,
            "states": states}


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
