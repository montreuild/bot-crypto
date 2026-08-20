"""Routes Phase 4 — portefeuille, bots (cycle de vie + cône MC), notifications, réglages.

Sert les vues « fonds piloté » : santé du portefeuille + allocation (réelle et
shadow), kanban des bots par état avec le cône Monte-Carlo vs réel (Phase 0),
fil d'activité (notifications 3 niveaux) et presets de risque.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import verify_api_key
from app.api.schemas import PortfolioResponse
from app.core.bot_identity import parse_slot_key
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.core.risk.envelope import base_drift
from app.core.risk.envelope import trade_risk_pct as _trade_risk_pct

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Presets de risque (Réglages) — modèle S12 ────────────────────────────────
# Ne plus écrire ``trading.risk_per_trade`` / ``max_positions`` (clés legacy
# supprimées par les enveloppes : sizing = risk.profile × enveloppe de slot).
# Mapping UI → clé ``risk.profile`` (profiles dans config/risk.yaml) :
#   prudent → prudent (1 %), equilibre → normal (2,5 %), agressif → agressif (5 %)

_LEGACY_TRADING_KEYS = (
    "risk_per_trade", "max_positions", "max_longs", "max_shorts", "capital",
)

# Budgets de risque par profil (symbol_risk ≥ profile, venue_risk ≥ symbol_risk).
# `max_symbol_exposure_pct` n'est PAS dans le template : c'est une allocation
# multi-symboles (ex. 2 titres → 0.5 chacun), indépendante du profil de risque.
# L'UI le laisse toujours éditable ; un preset ne l'écrase plus à 100 %.
_ENVELOPE_RISK_BY_PROFILE = {
    "prudent":  {"symbol_risk_pct": 0.02, "venue_risk_pct": 0.02},
    "normal":   {"symbol_risk_pct": 0.05, "venue_risk_pct": 0.05},
    "agressif": {"symbol_risk_pct": 0.05, "venue_risk_pct": 0.05},
}

_RISK_PRESETS = {
    "prudent": {
        "label": "Prudent",
        "description": "Risque minimal — profil 1 % de l'enveloppe de slot",
        "profile": "prudent",
        "trading": {
            "daily_drawdown_limit": 0.03,
            "max_drawdown_global": 0.15,
        },
        "risk": {
            "profile": "prudent",
            "equity_kill_switch_dd": 0.25,
        },
    },
    "equilibre": {
        "label": "Équilibré",
        "description": "Profil normal — 2,5 % de l'enveloppe de slot",
        "profile": "normal",
        "trading": {
            "daily_drawdown_limit": 0.05,
            "max_drawdown_global": 0.20,
        },
        "risk": {
            "profile": "normal",
            "equity_kill_switch_dd": 0.35,
        },
    },
    "agressif": {
        "label": "Agressif",
        "description": "Risque élevé — profil 5 % de l'enveloppe de slot",
        "profile": "agressif",
        "trading": {
            "daily_drawdown_limit": 0.08,
            "max_drawdown_global": 0.30,
        },
        "risk": {
            "profile": "agressif",
            "equity_kill_switch_dd": 0.45,
        },
    },
    "personnalise": {
        "label": "Personnalisé",
        "description": "Édition libre des budgets de perte par venue (symbol/venue risk)",
        "profile": None,
        "trading": {},
        "risk": {},
    },
}

# Clé UI (preset) ← profil moteur
_PROFILE_TO_PRESET = {
    "prudent": "prudent",
    "normal": "equilibre",
    "agressif": "agressif",
}


def _strip_legacy_trading_keys(trading: dict) -> None:
    """Retire les clés trading invalidées par S12 (évite de re-polluer le YAML)."""
    if not isinstance(trading, dict):
        return
    for k in _LEGACY_TRADING_KEYS:
        trading.pop(k, None)


def _preset_public_view(key: str, p: dict) -> dict:
    """Payload API pour une carte preset — sans champs legacy."""
    profile = (p.get("risk") or {}).get("profile")
    rate = (
        _trade_risk_pct({"risk": {"profile": profile}})
        if profile else None
    )
    return {
        "key": key,
        "label": p["label"],
        "description": p.get("description", ""),
        "profile": profile,
        "custom": key == "personnalise",
        "trade_risk_pct": rate,
        "daily_drawdown_limit": (p.get("trading") or {}).get("daily_drawdown_limit"),
        "max_drawdown_global": (p.get("trading") or {}).get("max_drawdown_global"),
        "equity_kill_switch_dd": (p.get("risk") or {}).get("equity_kill_switch_dd"),
        "envelope_risk": _ENVELOPE_RISK_BY_PROFILE.get(profile) if profile else None,
    }


def _resolve_current_preset(cfg: dict) -> str | None:
    """Preset UI courant : ui.risk_preset si valide, sinon dérivé de risk.profile."""
    ui = (cfg or {}).get("ui") or {}
    current = ui.get("risk_preset")
    if current in _RISK_PRESETS:
        return current
    profile = ((cfg or {}).get("risk") or {}).get("profile") or "normal"
    return _PROFILE_TO_PRESET.get(str(profile), "equilibre")


def _apply_envelope_risk_template(risk_block: dict, profile: str) -> None:
    """Aligne les budgets de risque de chaque venue sur le template S12 du profil.

    Ne touche pas au ``capital`` de venue (choix d'allocation).
    """
    tmpl = _ENVELOPE_RISK_BY_PROFILE.get(profile)
    if not tmpl:
        return
    envs = risk_block.setdefault("envelopes", {})
    if not isinstance(envs, dict):
        return
    for _name, env in envs.items():
        if not isinstance(env, dict):
            continue
        env["symbol_risk_pct"] = tmpl["symbol_risk_pct"]
        env["venue_risk_pct"] = tmpl["venue_risk_pct"]
        # Ne pas toucher max_symbol_exposure_pct (allocation multi-symboles).


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
    # D6 : `force_active` est la clé canonique ; `manual_active` reste lue pour
    # les configs non migrées.
    forced_set = set(lc.get("force_active") or lc.get("manual_active") or [])

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

    # Identités de bot + enveloppes (S12 : le « budget » d'un slot EST son
    # poids dans l'enveloppe de son symbole).
    identities = {}
    budgets = {}
    if tr:
        try:
            for ident in tr.get_bot_identities():
                identities[ident["slot_key"]] = ident
        except Exception:
            pass
        for key, env in (getattr(tr, "envelopes", None) or {}).items():
            budgets[key] = {"slot_key": key, "budget_pct": round(env.weight, 4),
                           "envelope": round(env.slot_envelope, 4),
                           "risk_amount": round(env.slot_risk_amount, 4),
                           "currency": env.currency}

    # Union des slots connus (oos ∪ states ∪ budgets).
    # Overlay des forçages manuels (droit de veto) : état affiché = actif.
    for key in forced_set:
        states[key] = "candidat" if key not in states else states[key]
        states[key] = "actif"

    keys = set(oos) | set(states) | set(budgets) | set(identities) | forced_set
    bots = []
    for key in sorted(keys):
        rec = oos.get(key, {})
        contract = rec.get("contract", {}) or {}
        edge = rec.get("edge", {}) or {}
        strat, tf, sym = parse_slot_key(key)
        bots.append({
            "slot_key":     key,
            "strategy":     rec.get("strategy", strat),
            "timeframe":    rec.get("timeframe", tf),
            "symbol":       rec.get("symbol", sym),
            "state":        states.get(key, "candidat"),
            "identity":     identities.get(key),
            "budget":       budgets.get(key),
            "sim":          rec.get("sim"),
            "monte_carlo":  rec.get("monte_carlo"),
            "edge":         edge,
            "edge_significant": _edge_significant(edge, edge_min, max_worst),
            # S12 §5.2 : l'échelle économique sur laquelle cette edge a été
            # mesurée, et sa dérive par rapport à l'enveloppe courante. Une
            # expectancy en % ne dit rien sans la base qui l'a produite.
            "base":         rec.get("base"),
            "base_drift":   (base_drift(rec.get("base"), tr.envelopes[key])
                             if tr and key in (getattr(tr, "envelopes", None) or {})
                             else None),
            # D6 : `force_active` est le nom canonique ; `manual_active` reste
            # émis à l'identique le temps que les clients migrent.
            "force_active":  key in forced_set,
            "manual_active": key in forced_set,
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
@state.limiter.limit("30/minute")
def force_active(request: Request, slot_key: str, enabled: bool = True):
    """Force (``enabled=true``) ou libère (``false``) l'activation manuelle d'un
    bot. Persisté dans ``config.yaml`` (lifecycle.force_active) et appliqué au
    cycle de vie en cours s'il tourne.

    D6 : la clé écrite est ``force_active``. Une éventuelle ``manual_active``
    héritée est reprise puis SUPPRIMÉE du fichier, pour ne pas laisser deux
    listes de forçage cohabiter — elles divergeraient au premier écart.
    """
    cfg = state.cfg or {}
    lc = cfg.setdefault("lifecycle", {})
    current = lc.get("force_active")
    if current is None:
        current = lc.get("manual_active") or []
    forced = [k for k in current if k != slot_key]
    if enabled:
        forced.append(slot_key)
    forced = sorted(set(forced))
    lc["force_active"] = forced
    lc.pop("manual_active", None)

    def _write(d: dict) -> None:
        section = d.setdefault("lifecycle", {})
        section["force_active"] = forced
        section.pop("manual_active", None)

    try:
        from app.api.routes._config_helpers import _save_yaml
        _save_yaml(_write)
    except Exception as e:
        logger.warning(f"[bots] sauvegarde force_active KO : {e}")

    tr = _trader()
    if tr and getattr(tr, "_lifecycle", None):
        tr._lifecycle.set_force_active(slot_key, enabled)

    logger.info(f"[bots] {slot_key} : forçage manuel ACTIF = {enabled}")
    return {"slot_key": slot_key, "force_active": enabled,
            "manual_active": enabled, "all": forced}


# ── Forcer un forward-test (recalcul de l'edge) pour un bot ──────────────────
@router.post("/api/bots/{slot_key:path}/forward-test",
             dependencies=[Depends(verify_api_key)])
@state.limiter.limit("10/minute")
def run_bot_forward_test(request: Request, slot_key: str):
    """Relance immédiatement le forward-test glissant d'un seul bot : re-backteste
    ses params figés (edge sur fenêtre longue + fidélité sur fenêtre courte),
    réécrit ``data/oos_tracker.json`` et, si le trader tourne, ré-évalue le cycle
    de vie pour que l'état se mette à jour tout de suite."""
    cfg = state.cfg or {}
    strat, tf, sym = parse_slot_key(slot_key)
    if not strat or not tf:
        raise HTTPException(400, "slot_key attendu au format 'strategy::timeframe[::symbol]'")
    ft = cfg.get("forward_test", {}) or {}
    lookback = int(ft.get("lookback_days", 45))
    edge_lb = int(ft.get("edge_lookback_days", 100))
    symbol = sym or ft.get("symbol", DEFAULT_CONFIG_SYMBOL)

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

    from app.engine.forward_test import run_forward_test
    try:
        res = run_forward_test(cfg, fetch, {tf: [{"name": strat, "symbol": sym}]},
                               session_factory, symbol=symbol, lookback_days=lookback,
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
        "sim": rec.get("sim"),
        "contract": rec.get("contract"),
        "run_date": rec.get("run_date"),
        "trader_running": bool(tr),
        "state_reevaluated": state_changed,
    }


# ── Portefeuille : santé + allocation réelle/shadow + activité ───────────────
@router.get("/api/portfolio", dependencies=[Depends(verify_api_key)],
            response_model=PortfolioResponse)
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
        "presets": {k: _preset_public_view(k, v) for k, v in _RISK_PRESETS.items()},
        "current": _resolve_current_preset(cfg),
        "active_profile": (cfg.get("risk") or {}).get("profile", "normal"),
        "active_trade_risk_pct": _trade_risk_pct(cfg) if cfg else None,
        "expert_mode": bool(ui.get("expert_mode", False)),
        "note": (
            "Le sizing utilise risk.profile × enveloppe de slot (S12). "
            "Les clés trading.risk_per_trade / max_positions ne sont plus écrites."
        ),
    }


@router.post("/api/settings/risk-preset", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def set_risk_preset(request: Request, preset: str):
    if preset not in _RISK_PRESETS:
        raise HTTPException(400, f"Preset inconnu : {preset}")
    p = _RISK_PRESETS[preset]
    from app.api.routes._config_helpers import _save_yaml

    is_custom = preset == "personnalise"
    profile = (p.get("risk") or {}).get("profile")

    def _apply(disk):
        trading = disk.setdefault("trading", {})
        if p.get("trading"):
            trading.update(p["trading"])
        _strip_legacy_trading_keys(trading)
        risk = disk.setdefault("risk", {})
        if p.get("risk"):
            risk.update(p["risk"])
        if profile and not is_custom:
            _apply_envelope_risk_template(risk, profile)
        disk.setdefault("ui", {})["risk_preset"] = preset
    _save_yaml(_apply)

    if state.cfg:
        trading = state.cfg.setdefault("trading", {})
        if p.get("trading"):
            trading.update(p["trading"])
        _strip_legacy_trading_keys(trading)
        risk = state.cfg.setdefault("risk", {})
        if p.get("risk"):
            risk.update(p["risk"])
        if profile and not is_custom:
            _apply_envelope_risk_template(risk, profile)
        state.cfg.setdefault("ui", {})["risk_preset"] = preset

    logger.info(
        "[Settings] Preset risque %s → risk.profile=%s custom=%s",
        preset, profile, is_custom,
    )
    rate = _trade_risk_pct(state.cfg) if state.cfg else (
        _trade_risk_pct({"risk": {"profile": profile}}) if profile else None
    )
    return {
        "applied": preset,
        "profile": profile,
        "custom": is_custom,
        "trade_risk_pct": rate,
        **(p.get("trading") or {}),
        "equity_kill_switch_dd": (p.get("risk") or {}).get("equity_kill_switch_dd"),
        "envelopes_updated": bool(profile and not is_custom),
        "note": (
            "Mode personnalisé : budgets de risque par venue éditables."
            if is_custom else
            "risk.profile + budgets enveloppe (S12) appliqués ; capital venue inchangé."
        ),
    }


@router.post("/api/settings/expert-mode", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def set_expert_mode(request: Request, enabled: bool = False):
    from app.api.routes._config_helpers import _save_yaml
    _save_yaml(lambda disk: disk.setdefault("ui", {}).update({"expert_mode": enabled}))
    if state.cfg:
        state.cfg.setdefault("ui", {})["expert_mode"] = enabled
    return {"expert_mode": enabled}
