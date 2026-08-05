"""Diagnostics de faisabilité des enveloppes de risque (S12).

Une config peut être syntaxiquement valide et STRUCTURELLEMENT impossible :
c'est le défaut trouvé avant cette refonte (notionnel 200 € contre un plafond
de 95 €, refus systématique, jamais détecté). Le sizing est en forme fermée —
aucune donnée de marché n'est nécessaire pour savoir si un trade peut passer
(cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §6.2).

Découplage volontaire de ``_validate_risk_envelopes`` (app/core/config.py) :
ce module a besoin de connaître les slots actifs par symbole (nombre, edges),
une information de app/engine — que app/core ne peut pas importer (couche
plus basse, cf. ARCHITECTURE.md). ``diagnose()`` prend donc cette information
en paramètre explicite plutôt que de la recalculer ; l'appelant (lifecycle
live, route /api/risk/diagnostics) la construit depuis les slots actifs
réels. ``_validate_risk_envelopes`` couvre déjà, à lui seul, les erreurs de
forme pure (existence, bornes, cohérence budget) qui ne nécessitent aucune
énumération de slots.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.core.risk_envelope import slot_weights, trade_risk_pct

_DEFAULT_STOP_PCT_REFERENCE = 0.025


@dataclass(frozen=True)
class Diagnostic:
    severity: str      # "error" | "warning"
    code: str
    scope: str          # "venue:<nom>" | "symbol:<sym>" | "slot:<slot_key>"
    message: str
    values: dict


def n_slots_max(symbol_envelope: float, max_leverage: float, min_notional: float) -> float:
    """Nombre maximal de slots qu'un symbole peut porter sans qu'aucun ne
    devienne inatteignable — corollaire direct de la fenêtre de stops (§6.2).
    ``inf`` si la venue n'impose pas de notionnel minimum (cas crypto)."""
    if min_notional <= 0:
        return float("inf")
    return symbol_envelope * max(max_leverage, 1.0) / min_notional


def _venue_def(cfg: dict, venue_name: str) -> dict:
    return ((cfg.get("venues") or {}).get("defs") or {}).get(venue_name) or {}


def diagnose(cfg: dict, slots_by_symbol: Dict[Tuple[str, str], Dict[str, Optional[float]]],
            *, prices: Dict[str, float] = None) -> List[Diagnostic]:
    """Diagnostics de faisabilité.

    ``slots_by_symbol`` : ``{(venue, symbole): {slot_key: edge_ci_low|None}}``
    — un symbole absent de ce dict n'est simplement pas analysé (les règles
    qui en dépendent ne se déclenchent que sur les symboles fournis). Les
    règles purement config (pas de slots) tournent une fois par venue.
    """
    prices = prices or {}
    risk_cfg = cfg.get("risk", {}) or {}
    envelopes_cfg = risk_cfg.get("envelopes") or {}
    min_weight = float(risk_cfg.get("min_slot_weight", 0.05))
    stop_pct_ref = float((risk_cfg.get("diagnostics") or {}).get(
        "stop_pct_reference", _DEFAULT_STOP_PCT_REFERENCE))
    risk_pct = trade_risk_pct(cfg)

    out: List[Diagnostic] = []
    symbols_by_venue: Dict[str, List[str]] = {}
    for (venue, symbol) in slots_by_symbol:
        symbols_by_venue.setdefault(venue, []).append(symbol)

    for venue_name, env_cfg in envelopes_cfg.items():
        capital = float(env_cfg.get("capital", 0.0))
        max_expo = float(env_cfg.get("max_symbol_exposure_pct", 1.0))
        symbol_risk_pct = float(env_cfg.get("symbol_risk_pct", 0.02))
        venue_risk_pct = float(env_cfg.get("venue_risk_pct", 0.03))
        vdef = _venue_def(cfg, venue_name)
        venue_min_notional = float(vdef.get("min_notional", 0.0) or 0.0)
        max_leverage = float(vdef.get("max_leverage", 1.0) or 1.0)
        fee_min = float(vdef.get("fee_min", 0.0) or 0.0)
        lot_size = float(vdef.get("lot_size", 0.0) or 0.0)
        symbol_envelope = capital * max_expo

        symbols = symbols_by_venue.get(venue_name, [])
        n_symbols = len(symbols)

        if n_symbols > 0 and n_symbols * max_expo > 1.0:
            out.append(Diagnostic(
                "error", "enveloppe_venue_depassee", f"venue:{venue_name}",
                f"{n_symbols} symbole(s) x {max_expo:.0%} d'exposition > 100% "
                f"de l'enveloppe venue — réduire max_symbol_exposure_pct.",
                {"n_symbols": n_symbols, "max_symbol_exposure_pct": max_expo}))

        if n_symbols > 0 and venue_risk_pct >= n_symbols * symbol_risk_pct:
            out.append(Diagnostic(
                "warning", "decote_correlation_absente", f"venue:{venue_name}",
                f"venue_risk_pct ({venue_risk_pct:.1%}) >= {n_symbols} x "
                f"symbol_risk_pct ({symbol_risk_pct:.1%}) : le budget venue "
                f"ne contraint rien, aucune décote de corrélation.",
                {"venue_risk_pct": venue_risk_pct, "symbol_risk_pct": symbol_risk_pct,
                 "n_symbols": n_symbols}))

        if venue_min_notional > 0 and symbol_envelope * min_weight < venue_min_notional:
            # Expliquer le calcul pour l'UI (sinon le code technique est opaque).
            # enveloppe_symbole = capital_venue × max_symbol_exposure_pct
            # plancher_slot     = enveloppe_symbole × min_slot_weight
            floor_notional = symbol_envelope * min_weight
            out.append(Diagnostic(
                "warning", "min_slot_weight_insuffisant", f"venue:{venue_name}",
                f"Les plus petits slots ne pourront pas ouvrir d'ordre sur "
                f"{venue_name} : capital×exposition×plancher de poids "
                f"({capital:.0f} × {max_expo:.0%} × {min_weight:.0%} = "
                f"{floor_notional:.0f}) est sous le notionnel minimum de la "
                f"venue ({venue_min_notional:.0f}). "
                f"Remèdes : augmenter le capital de la venue, relever "
                f"l'exposition max / symbole, baisser min_slot_weight, ou "
                f"abaisser min_notional dans venues.defs.{venue_name}.",
                {"capital": capital, "max_symbol_exposure_pct": max_expo,
                 "symbol_envelope": symbol_envelope, "min_slot_weight": min_weight,
                 "floor_notional": floor_notional,
                 "min_notional": venue_min_notional}))

        for symbol in symbols:
            edges = slots_by_symbol[(venue_name, symbol)]
            n_slots = len(edges)
            n_max = n_slots_max(symbol_envelope, max_leverage, venue_min_notional)
            if n_max != float("inf") and n_slots > n_max:
                out.append(Diagnostic(
                    "error", "trop_de_slots", f"symbol:{symbol}",
                    f"{n_slots} slots > {n_max:.1f} maximum sur ce symbole — "
                    f"plafonner les slots ou augmenter le capital de la venue.",
                    {"n_slots": n_slots, "n_slots_max": n_max}))

            weights = slot_weights(edges, min_weight)
            for slot_key, w in weights.items():
                if w <= 0:
                    out.append(Diagnostic(
                        "warning", "slot_sans_edge", f"slot:{slot_key}",
                        "poids nul (edge non prouvée positive) — ce slot ne "
                        "tradera pas tant que son edge n'est pas mesurée.",
                        {"weight": w}))
                    continue

                slot_envelope = symbol_envelope * w
                max_notional = slot_envelope * max(max_leverage, 1.0)
                risk_amount = slot_envelope * risk_pct

                # Le slot risque, à lui seul, plus que ne l'autorise le budget
                # partagé au-dessus de lui : chaque trade sera refusé par
                # RiskLedger, quelles que soient la volatilité et la taille.
                # Mord dès que trade_risk_pct × poids dépasse symbol_risk_pct —
                # donc typiquement quand un symbole se retrouve à UN seul slot
                # actif (poids 1,0) avec un profil de risque plus agressif que
                # le budget du symbole.
                if risk_amount > symbol_envelope * symbol_risk_pct:
                    out.append(Diagnostic(
                        "error", "budget_symbole_insuffisant", f"slot:{slot_key}",
                        f"risque par trade ({risk_amount:.2f}) > budget de risque "
                        f"du symbole ({symbol_envelope * symbol_risk_pct:.2f}) : "
                        f"aucun trade ne passera. Baisser risk.profile ou monter "
                        f"symbol_risk_pct.",
                        {"risk_amount": risk_amount, "weight": w,
                         "symbol_risk_budget": symbol_envelope * symbol_risk_pct}))
                    continue

                if risk_amount > capital * venue_risk_pct:
                    out.append(Diagnostic(
                        "error", "budget_venue_insuffisant", f"slot:{slot_key}",
                        f"risque par trade ({risk_amount:.2f}) > budget de risque "
                        f"de la venue ({capital * venue_risk_pct:.2f}) : aucun "
                        f"trade ne passera.",
                        {"risk_amount": risk_amount,
                         "venue_risk_budget": capital * venue_risk_pct}))
                    continue

                if max_notional < venue_min_notional:
                    out.append(Diagnostic(
                        "error", "trade_impossible", f"slot:{slot_key}",
                        f"plafond notionnel ({max_notional:.2f}) < minimum venue "
                        f"({venue_min_notional:.2f}) : aucun trade ne passera "
                        f"jamais sur ce slot, quelle que soit la volatilité.",
                        {"max_notional": max_notional, "min_notional": venue_min_notional}))
                    continue

                price = prices.get(symbol)
                if price and lot_size > 0 and max_notional < price * lot_size:
                    out.append(Diagnostic(
                        "error", "lot_indivisible", f"slot:{slot_key}",
                        f"plafond notionnel ({max_notional:.2f}) n'achète pas "
                        f"une unité du lot ({price * lot_size:.2f}).",
                        {"max_notional": max_notional, "lot_value": price * lot_size}))

                lo = risk_amount / max_notional if max_notional > 0 else float("inf")
                hi = risk_amount / venue_min_notional if venue_min_notional > 0 else float("inf")
                if hi != float("inf") and lo > 0 and hi / lo < 1.5:
                    out.append(Diagnostic(
                        "warning", "fenetre_stops_etroite", f"slot:{slot_key}",
                        f"fenêtre de stops viables [{lo:.2%} — {hi:.2%}] trop "
                        f"étroite : la plupart des stops seront refusés ou "
                        f"plafonnés.",
                        {"stop_pct_low": lo, "stop_pct_high": hi}))

                if lo >= stop_pct_ref:
                    out.append(Diagnostic(
                        "warning", "plafond_notionnel_mordant", f"slot:{slot_key}",
                        f"au stop de référence ({stop_pct_ref:.2%}), le plafond "
                        f"notionnel mord déjà — le risque réel sera inférieur "
                        f"au risk_pct configuré dès qu'un stop est plus serré.",
                        {"stop_pct_low": lo, "stop_pct_reference": stop_pct_ref}))

                if fee_min > 0:
                    notional_typ = min(risk_amount / stop_pct_ref, max_notional) \
                        if stop_pct_ref > 0 else max_notional
                    if notional_typ > 0 and (2 * fee_min / notional_typ) > 0.01:
                        out.append(Diagnostic(
                            "warning", "frais_fixes_dominants", f"slot:{slot_key}",
                            f"2x plancher de courtage ({fee_min:.2f}) représente "
                            f"{2 * fee_min / notional_typ:.1%} du notionnel type "
                            f"— au-delà de 1%, le plancher mange l'edge.",
                            {"fee_min": fee_min, "notional_type": notional_typ}))

    return out
