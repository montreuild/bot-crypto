"""Enveloppes de risque emboîtées venue → symbole → slot (S12).

Modèle pur, sans état, sans I/O — cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md.

Deux plafonds à chaque niveau : une *enveloppe* (devise, borne le notionnel)
et un *budget de risque* (devise, borne la perte). Le sizing du slot dérive
de l'enveloppe du slot, elle-même dérivée du poids par confiance d'edge —
c'est ce qui remplace le partage à parts égales entre N stratégies déclarées.
"""
from dataclasses import dataclass, replace
from typing import Dict, List, Optional

from app.core.bot_identity import Venue

_DEFAULT_PROFILES = {"prudent": 0.01, "normal": 0.025, "agressif": 0.05}


@dataclass(frozen=True)
class Envelope:
    venue: str
    symbol: str
    slot_key: str
    currency: str
    venue_envelope: float
    venue_risk_budget: float
    symbol_envelope: float
    symbol_risk_budget: float
    slot_envelope: float        # base économique du bot — backtest ET live
    slot_risk_amount: float     # slot_envelope × trade_risk_pct
    max_leverage: float
    min_notional: float          # de la venue — porté ici pour RiskLedger/diagnostics
    trade_risk_pct: float
    weight: float                # poids du slot dans son symbole

    @property
    def max_notional(self) -> float:
        return self.slot_envelope * max(self.max_leverage, 1.0)

    @property
    def symbol_max_notional(self) -> float:
        """Plafond de notionnel agrégé du symbole.

        Le levier s'applique ici comme au niveau du slot : les *enveloppes*
        sont libellées en capital, le *notionnel* en exposition. Comme
        ``Σ slot_envelope = symbol_envelope``, ce plafond vaut exactement la
        somme des ``max_notional`` de ses slots — sans quoi le plafond slot
        autoriserait, à levier > 1, ce que le plafond symbole refuserait
        (deux bases pour une même grandeur : le défaut que S12 supprime)."""
        return self.symbol_envelope * max(self.max_leverage, 1.0)


def slot_weights(edges: Dict[str, Optional[float]], min_weight: float = 0.05) -> Dict[str, float]:
    """Poids par confiance d'edge (borne basse de l'IC d'expectancy).

    - Aucun slot n'a d'edge mesurée (tous ``None``) → répartition égale
      (bootstrap : donner leur chance à des bots jamais évalués).
    - Dès qu'au moins une edge est mesurée, un slot non mesuré (``None``) ou
      à edge ≤ 0 reçoit un poids nul — il ne trade pas tant que son edge
      n'est pas prouvée positive.
    - Plancher ``min_weight`` appliqué puis poids renormalisés, uniquement
      parmi les slots à poids non nul.
    """
    if not edges:
        return {}
    if all(v is None for v in edges.values()):
        n = len(edges)
        weights = {k: 1.0 / n for k in edges}
    else:
        raw = {k: (max(v, 0.0) if v is not None else 0.0) for k, v in edges.items()}
        total = sum(raw.values())
        weights = ({k: 0.0 for k in edges} if total <= 0
                   else {k: raw[k] / total for k in edges})
    return _apply_weight_floor(weights, min_weight)


def _apply_weight_floor(weights: Dict[str, float], min_weight: float) -> Dict[str, float]:
    active = {k: w for k, w in weights.items() if w > 0}
    if not active or min_weight <= 0:
        return weights
    n = len(active)
    if min_weight * n >= 1.0:
        # Plancher inatteignable pour tous → égalité entre slots actifs.
        eq = 1.0 / n
        return {k: (eq if k in active else 0.0) for k in weights}
    remaining = 1.0 - min_weight * n
    total_active = sum(active.values())
    return {
        k: (min_weight + (w / total_active) * remaining if k in active else 0.0)
        for k, w in weights.items()
    }


def trade_risk_pct(cfg: dict) -> float:
    """Taux de risque par trade, en % de l'enveloppe du slot (``risk.profile``)."""
    risk_cfg = cfg.get("risk", {}) or {}
    profile = risk_cfg.get("profile", "normal")
    profiles = risk_cfg.get("profiles") or _DEFAULT_PROFILES
    return float(profiles.get(profile, _DEFAULT_PROFILES["normal"]))


def resolve_envelope(cfg: dict, venue: Venue, symbol: str, slot_key: str, *,
                     peers: List[str], edges: Dict[str, Optional[float]]) -> Envelope:
    """Résout l'enveloppe d'un slot.

    ``peers`` : slot_keys du même symbole (``slot_key`` inclus) — le poids se
    répartit entre eux, pas entre tous les slots déclarés. ``edges`` :
    ``{slot_key: edge_ci_low|None}`` pour ces mêmes peers.
    """
    risk_cfg = cfg.get("risk", {}) or {}
    env_cfg = (risk_cfg.get("envelopes") or {}).get(venue.name, {})

    venue_capital = float(env_cfg.get("capital", 0.0))
    max_expo_pct = float(env_cfg.get("max_symbol_exposure_pct", 1.0))
    symbol_risk_pct = float(env_cfg.get("symbol_risk_pct", 0.02))
    venue_risk_pct = float(env_cfg.get("venue_risk_pct", 0.03))
    risk_pct = trade_risk_pct(cfg)
    min_weight = float(risk_cfg.get("min_slot_weight", 0.05))

    symbol_envelope = venue_capital * max_expo_pct
    weights = slot_weights({k: edges.get(k) for k in peers}, min_weight)
    weight = weights.get(slot_key, 0.0)
    slot_envelope = symbol_envelope * weight

    return Envelope(
        venue=venue.name, symbol=symbol, slot_key=slot_key,
        currency=venue.quote_currency,
        venue_envelope=venue_capital,
        venue_risk_budget=venue_capital * venue_risk_pct,
        symbol_envelope=symbol_envelope,
        symbol_risk_budget=symbol_envelope * symbol_risk_pct,
        slot_envelope=slot_envelope,
        slot_risk_amount=slot_envelope * risk_pct,
        max_leverage=venue.max_leverage,
        min_notional=venue.min_notional,
        trade_risk_pct=risk_pct,
        weight=weight,
    )


def envelopes_for_active_slots(cfg: dict, active_per_tf: dict, *,
                               edges: Optional[Dict[str, Optional[float]]] = None,
                               default_symbol: str = "") -> Dict[str, Envelope]:
    """``{slot_key: Envelope}`` pour tous les slots actifs.

    Source unique de la résolution : le live (reconstruction périodique), le
    forward-test (base d'edge) et ``/api/risk`` doivent voir exactement les
    mêmes enveloppes — sinon la base sur laquelle une edge est mesurée cesse
    d'être celle qui trade, et la garde de dérive du §5.2 ne veut plus rien
    dire. Les poids se répartissent par symbole, jamais globalement (§2.3).
    """
    from app.core.bot_identity import build_slot_key, resolve_venue

    edges = edges or {}
    grouped: Dict[tuple, list] = {}
    for tf, slots in (active_per_tf or {}).items():
        for slot in slots or []:
            name = slot.get("name")
            if not name:
                continue
            symbol = slot.get("symbol") or default_symbol
            slot_key = build_slot_key(name, tf, symbol)
            venue = resolve_venue(cfg, name, tf, symbol)
            grouped.setdefault((venue.name, symbol), []).append((slot_key, venue))

    out: Dict[str, Envelope] = {}
    for (_venue_name, symbol), members in grouped.items():
        peers = [k for k, _ in members]
        peer_edges = {k: edges.get(k) for k in peers}
        for slot_key, venue in members:
            out[slot_key] = resolve_envelope(cfg, venue, symbol, slot_key,
                                             peers=peers, edges=peer_edges)
    return out


def envelope_base(env: Envelope, as_of: str = "") -> dict:
    """Empreinte de l'échelle économique ayant servi à mesurer une edge (§5.2).

    Une expectancy n'a de sens qu'accompagnée de la base sur laquelle elle a
    été mesurée : c'est ce qui a laissé passer la divergence backtest 1 000 € /
    live 90 € sans que rien ne la signale."""
    return {"venue": env.venue, "slot_envelope": round(env.slot_envelope, 4),
            "trade_risk_pct": env.trade_risk_pct, "currency": env.currency,
            "as_of": as_of}


def base_drift(recorded_base: dict, env: Envelope) -> Optional[float]:
    """Dérive relative entre la base enregistrée et l'enveloppe courante.

    ``None`` si aucune base n'a été enregistrée (bot antérieur à S12) : c'est
    une information manquante, pas une dérive nulle — l'appelant décide."""
    if not recorded_base:
        return None
    previous = float(recorded_base.get("slot_envelope") or 0.0)
    if previous <= 0:
        return None
    return abs(env.slot_envelope - previous) / previous


def with_reference_envelope(env: Envelope, reference_capital: float) -> Envelope:
    """Bascule sur l'enveloppe d'étude (§5.1) : fixe, indépendante du poids —
    « ce bot est-il promouvable » (enveloppe réelle) contre « cette stratégie
    vaut-elle quelque chose » (échelle de référence commune à tous les bots)."""
    return replace(env, slot_envelope=reference_capital,
                   slot_risk_amount=reference_capital * env.trade_risk_pct)
