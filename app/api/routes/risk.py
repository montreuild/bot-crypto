"""Routes ENVELOPPES DE RISQUE (S12).

- GET  /api/risk               — enveloppes et risque engagé, venue → symbole → slot
- GET  /api/risk/diagnostics   — faisabilité analytique de la configuration
- POST /api/risk/envelopes     — édition des enveloppes (refusée si un
                                 diagnostic `error` en résulterait)

Règle d'affichage que ces payloads servent (cf.
docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §6) : **toujours accompagner un montant
de risque de sa base**. Un risque en devise sans son enveloppe n'est pas
interprétable — c'est précisément ce qui a laissé passer la divergence
backtest 1 000 € / live 90 € sans que rien ne la signale.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import verify_api_key
from app.api.routes._config_helpers import _save_yaml
from app.api.schemas import RiskEnvelopesBody
from app.core.risk_diagnostics import diagnose
from app.core.risk_envelope import envelopes_for_active_slots

logger = logging.getLogger(__name__)
router = APIRouter()

# Bornes reprises de ``_validate_risk_envelopes`` : une enveloppe écrite par
# l'API doit satisfaire exactement les mêmes règles que celle chargée du disque,
# sinon l'API devient un chemin de contournement de la validation.
_BOUNDS = {
    "max_symbol_exposure_pct": (0.0, 1.0),
    "symbol_risk_pct":         (0.0, 0.10),
    "venue_risk_pct":          (0.0, 0.20),
}


def _current_envelopes() -> dict:
    """``{slot_key: Envelope}`` — celles du trader si vivant, sinon recalculées.

    Le trader tient le cache à jour à chaque cycle de vie ; hors trader (API
    seule, tests), on résout à la volée pour que la lecture reste possible."""
    if state.trader is not None and getattr(state.trader, "envelopes", None):
        return state.trader.envelopes
    active = getattr(state.trader, "_active_per_tf", None) or {}
    return envelopes_for_active_slots(state.cfg or {}, active)


def _engaged() -> dict:
    if state.trader is not None and getattr(state.trader, "ledger", None):
        return state.trader.ledger.snapshot()
    return {"venue": {}, "symbol": {}, "slot": {}}


def _slots_by_symbol(envelopes: dict, edges: dict) -> dict:
    out: dict = {}
    for slot_key, env in envelopes.items():
        out.setdefault((env.venue, env.symbol), {})[slot_key] = edges.get(slot_key)
    return out


def _edges() -> dict:
    try:
        from app.core.oos_tracker import load_oos_tracker
        return {k: (r.get("edge", {}) or {}).get("ci_low_pct")
                for k, r in load_oos_tracker().items()}
    except Exception as e:      # pragma: no cover — lecture disque best-effort
        logger.debug(f"[api/risk] lecture oos_tracker KO : {e}")
        return {}


# ── GET /api/risk ──────────────────────────────────────────────────────────

@router.get("/api/risk", dependencies=[Depends(verify_api_key)])
def get_risk():
    """Enveloppes et risque engagé aux trois niveaux."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")

    envelopes = _current_envelopes()
    engaged   = _engaged()
    edges     = _edges()

    venues, symbols = {}, {}
    slots = []

    # Le niveau VENUE vient de la config, pas des slots actifs : une venue a
    # une enveloppe et un budget de risque dès qu'elle est déclarée, même bot
    # arrêté ou sans slot promu. Ne l'afficher qu'à partir des slots rendait
    # l'écran vide au repos — soit exactement la lecture trompeuse que cette
    # refonte supprime, prise à l'envers.
    env_cfg = (state.cfg.get("risk") or {}).get("envelopes") or {}
    venue_defs = ((state.cfg.get("venues") or {}).get("defs") or {})
    for name, e in env_cfg.items():
        capital = float(e.get("capital", 0.0))
        venues[name] = {
            "venue": name,
            "currency": (venue_defs.get(name) or {}).get("quote_currency", ""),
            "envelope": round(capital, 4),
            "notional_engaged": round(engaged["venue"].get(name, {}).get("notional", 0.0), 4),
            "risk_budget": round(capital * float(e.get("venue_risk_pct", 0.0)), 4),
            "risk_engaged": round(engaged["venue"].get(name, {}).get("risk", 0.0), 4),
        }
    for slot_key, env in sorted(envelopes.items()):
        sym_key = f"{env.venue}::{env.symbol}"
        symbols.setdefault(sym_key, {
            "venue": env.venue, "symbol": env.symbol, "currency": env.currency,
            "envelope": round(env.symbol_envelope, 4),
            "notional_engaged": round(engaged["symbol"].get(sym_key, {}).get("notional", 0.0), 4),
            "risk_budget": round(env.symbol_risk_budget, 4),
            "risk_engaged": round(engaged["symbol"].get(sym_key, {}).get("risk", 0.0), 4),
        })
        slots.append({
            "slot_key": slot_key, "venue": env.venue, "symbol": env.symbol,
            "currency": env.currency, "weight": round(env.weight, 4),
            "envelope": round(env.slot_envelope, 4),
            "max_notional": round(env.max_notional, 4),
            "risk_amount": round(env.slot_risk_amount, 4),
            "risk_engaged": round(engaged["slot"].get(slot_key, {}).get("risk", 0.0), 4),
            "notional_engaged": round(engaged["slot"].get(slot_key, {}).get("notional", 0.0), 4),
            "edge_ci_low": edges.get(slot_key),
        })

    for v in venues.values():
        v["risk_pct_used"] = (round(v["risk_engaged"] / v["risk_budget"], 4)
                              if v["risk_budget"] > 0 else 0.0)

    rejections = ({} if state.trader is None or not getattr(state.trader, "rejections", None)
                  else state.trader.rejections.as_dict())
    return {
        "venues": sorted(venues.values(), key=lambda x: x["venue"]),
        "symbols": sorted(symbols.values(), key=lambda x: (x["venue"], x["symbol"])),
        "slots": slots,
        "total_risk_engaged": round(sum(v["risk_engaged"] for v in venues.values()), 4),
        "rejections": rejections,
        # Bloc éditable tel qu'il est sur disque — /api/config ne le sert pas,
        # et l'onglet Réglages a besoin des valeurs SAISIES, pas seulement des
        # montants dérivés.
        "envelopes_config": env_cfg,
    }


# ── GET /api/risk/diagnostics ──────────────────────────────────────────────

@router.get("/api/risk/diagnostics", dependencies=[Depends(verify_api_key)])
def get_risk_diagnostics():
    """Faisabilité analytique : ce qui ne pourra jamais passer, et pourquoi."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    envelopes = _current_envelopes()
    diags = diagnose(state.cfg, _slots_by_symbol(envelopes, _edges()))
    return {
        "diagnostics": [
            {"severity": d.severity, "code": d.code, "scope": d.scope,
             "message": d.message, "values": d.values}
            for d in diags
        ],
        "errors": sum(1 for d in diags if d.severity == "error"),
        "warnings": sum(1 for d in diags if d.severity == "warning"),
    }


# ── POST /api/risk/envelopes ───────────────────────────────────────────────

@router.post("/api/risk/envelopes", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("20/minute")
def update_envelopes(request: Request, body: RiskEnvelopesBody):
    """Met à jour ``risk.envelopes`` (SEC-03 : ``RiskEnvelopesBody``).

    Refuse en 400 si l'édition produirait une configuration structurellement
    impossible — un slot dont aucun ordre ne pourrait jamais passer. Laisser
    écrire une telle config par l'API annulerait la validation du chargement :
    le bot démarrerait et ne traderait simplement plus.
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        envelopes = body.as_envelopes()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not envelopes:
        raise HTTPException(400, "corps attendu : {venue: {capital, ...}}")

    known = set((state.cfg.get("risk") or {}).get("envelopes") or {})
    for venue, env in envelopes.items():
        if not isinstance(env, dict):
            raise HTTPException(400, f"risk.envelopes.{venue} doit être un objet")
        if venue not in known:
            raise HTTPException(400, f"venue inconnue : '{venue}'")
        if "capital" in env and float(env["capital"]) <= 0:
            raise HTTPException(400, f"risk.envelopes.{venue}.capital doit être > 0")
        for key, (lo, hi) in _BOUNDS.items():
            if key in env and not (lo < float(env[key]) <= hi):
                raise HTTPException(
                    400, f"risk.envelopes.{venue}.{key} doit être dans ]{lo}, {hi}]"
                )

    # Validation sur une copie : la config en mémoire n'est modifiée qu'une fois
    # la nouvelle version jugée cohérente ET faisable.
    import copy

    candidate = copy.deepcopy(state.cfg)
    target = candidate.setdefault("risk", {}).setdefault("envelopes", {})
    for venue, env in envelopes.items():
        target.setdefault(venue, {}).update(env)

    from app.core.config import _validate_risk_envelopes
    try:
        _validate_risk_envelopes(candidate)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    blocking = [d for d in diagnose(candidate, _slots_by_symbol(_current_envelopes(), _edges()))
                if d.severity == "error"]
    if blocking:
        raise HTTPException(400, "configuration structurellement impossible : "
                                 + " | ".join(d.message for d in blocking))

    applied = candidate["risk"]["envelopes"]
    state.cfg.setdefault("risk", {})["envelopes"] = applied
    try:
        _save_yaml(lambda d: d.setdefault("risk", {}).setdefault("envelopes", {}).update(applied))
        saved = True
    except Exception as e:
        logger.warning(f"[api/risk] sauvegarde YAML KO : {e}")
        saved = False
    return {"envelopes": applied, "saved_to_disk": saved}
