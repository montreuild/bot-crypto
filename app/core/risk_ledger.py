"""RiskLedger — comptabilité thread-safe du risque et du notionnel engagés (S12).

Remplace ``CapitalAllocator`` : plus de « budget par slot » à équilibrer entre
N stratégies déclarées, seulement une réservation/libération sous enveloppe et
budget de risque (docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §4.2). Les anciens
garde-fous de capacité (``max_pyramiding``, ``max_positions``, corrélation
directionnelle) disparaissent : le budget de risque symbole/venue les rend
inutiles (§2.2) — n bots sur un même symbole ne peuvent jamais perdre plus que
le budget de ce symbole, quel que soit leur nombre.
"""
import threading
from dataclasses import dataclass
from typing import Dict

from app.core.risk_envelope import Envelope


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason_code: str = ""
    detail: str = ""


@dataclass
class _Reservation:
    venue: str
    symbol: str
    slot_key: str
    notional: float
    risk: float


def _symbol_key(venue: str, symbol: str) -> str:
    return f"{venue}::{symbol}"


# Garde de bruit flottant — PAS une tolérance de dépassement. Le risque
# réservé (|entrée − stop| × taille) et le plafond (enveloppe × taux) sont
# calculés par deux chemins différents : à l'égalité exacte, le dernier ulp
# suffit à faire refuser un trade conforme (mesuré : 200.00000000000003 > 200).
# L'ancien ×1.05 de CapitalAllocator était trois ordres de grandeur au-dessus
# et laissait passer de vrais dépassements ; 1e-9 relatif n'autorise rien
# d'économiquement observable.
_FP_EPS = 1e-9


def _exceeds(value: float, limit: float) -> bool:
    return value > limit * (1.0 + _FP_EPS)


class RiskLedger:
    """Réservation/libération atomiques sous enveloppe + budget de risque.

    Aucune tolérance de dépassement (les anciens ``×1.05`` de
    ``CapitalAllocator.can_allocate`` disparaissent) : ``reserve`` est
    atomique sous verrou, la réservation précède l'ordre.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._positions: Dict[str, _Reservation] = {}
        self._venue_notional: Dict[str, float] = {}
        self._venue_risk: Dict[str, float] = {}
        self._symbol_notional: Dict[str, float] = {}
        self._symbol_risk: Dict[str, float] = {}
        self._slot_notional: Dict[str, float] = {}
        self._slot_risk: Dict[str, float] = {}

    def reserve(self, env: Envelope, *, risk: float, notional: float,
               pos_key: str) -> Decision:
        """Règles dans l'ordre — le premier refus l'emporte (§4.2)."""
        with self._lock:
            # L-05 : une 2ᵉ réserve sur la même clé écrasait la 1ʳᵉ sans
            # décrémenter les agrégats — fuite permanente de budget.
            if pos_key in self._positions:
                return Decision(False, "deja_reserve",
                                f"clé {pos_key} déjà réservée")
            sym_key = _symbol_key(env.venue, env.symbol)
            cur_symbol_notional = self._symbol_notional.get(sym_key, 0.0)
            cur_symbol_risk     = self._symbol_risk.get(sym_key, 0.0)
            cur_venue_risk      = self._venue_risk.get(env.venue, 0.0)

            cur_slot_notional = self._slot_notional.get(env.slot_key, 0.0)
            cur_slot_risk = self._slot_risk.get(env.slot_key, 0.0)
            if _exceeds(notional, env.max_notional):
                return Decision(False, "enveloppe_slot",
                                f"notionnel {notional:.2f} > plafond slot {env.max_notional:.2f}")
            if _exceeds(cur_slot_notional + notional, env.max_notional):
                return Decision(False, "enveloppe_slot",
                                f"notionnel slot cumulé {cur_slot_notional + notional:.2f} "
                                f"> plafond slot {env.max_notional:.2f}")
            slot_risk_budget = getattr(env, "slot_risk_budget", None)
            if slot_risk_budget is None:
                slot_risk_budget = env.max_notional
            if slot_risk_budget and _exceeds(cur_slot_risk + risk, float(slot_risk_budget)):
                return Decision(False, "budget_slot",
                                f"risque slot {cur_slot_risk + risk:.2f} "
                                f"> budget {float(slot_risk_budget):.2f}")
            if _exceeds(cur_symbol_notional + notional, env.symbol_max_notional):
                return Decision(False, "enveloppe_slot",
                                f"notionnel symbole {cur_symbol_notional + notional:.2f} "
                                f"> enveloppe {env.symbol_max_notional:.2f}")
            # F-05 : _venue_notional était tenu à jour mais jamais comparé.
            # Sans ça, N symboles à plein levier dépassent max_leverage déclaré.
            cur_venue_notional = self._venue_notional.get(env.venue, 0.0)
            venue_max_notional = env.venue_envelope * max(env.max_leverage, 1.0)
            if _exceeds(cur_venue_notional + notional, venue_max_notional):
                return Decision(False, "enveloppe_venue",
                                f"notionnel venue {cur_venue_notional + notional:.2f} "
                                f"> enveloppe {venue_max_notional:.2f}")
            if _exceeds(cur_symbol_risk + risk, env.symbol_risk_budget):
                return Decision(False, "budget_symbole",
                                f"risque symbole {cur_symbol_risk + risk:.2f} "
                                f"> budget {env.symbol_risk_budget:.2f}")
            if _exceeds(cur_venue_risk + risk, env.venue_risk_budget):
                return Decision(False, "budget_venue",
                                f"risque venue {cur_venue_risk + risk:.2f} "
                                f"> budget {env.venue_risk_budget:.2f}")
            if env.min_notional > 0 and notional < env.min_notional:
                return Decision(False, "notionnel_min",
                                f"notionnel {notional:.2f} < minimum {env.min_notional:.2f}")

            self._positions[pos_key] = _Reservation(
                venue=env.venue, symbol=env.symbol, slot_key=env.slot_key,
                notional=notional, risk=risk,
            )
            self._venue_notional[env.venue]   = self._venue_notional.get(env.venue, 0.0) + notional
            self._venue_risk[env.venue]       = cur_venue_risk + risk
            self._symbol_notional[sym_key]    = cur_symbol_notional + notional
            self._symbol_risk[sym_key]        = cur_symbol_risk + risk
            self._slot_notional[env.slot_key] = self._slot_notional.get(env.slot_key, 0.0) + notional
            self._slot_risk[env.slot_key]     = self._slot_risk.get(env.slot_key, 0.0) + risk
            return Decision(True)

    def release(self, pos_key: str) -> None:
        with self._lock:
            r = self._positions.pop(pos_key, None)
            if r is None:
                return
            sym_key = _symbol_key(r.venue, r.symbol)
            self._venue_notional[r.venue]   = max(0.0, self._venue_notional.get(r.venue, 0.0) - r.notional)
            self._venue_risk[r.venue]       = max(0.0, self._venue_risk.get(r.venue, 0.0) - r.risk)
            self._symbol_notional[sym_key]  = max(0.0, self._symbol_notional.get(sym_key, 0.0) - r.notional)
            self._symbol_risk[sym_key]      = max(0.0, self._symbol_risk.get(sym_key, 0.0) - r.risk)
            self._slot_notional[r.slot_key] = max(0.0, self._slot_notional.get(r.slot_key, 0.0) - r.notional)
            self._slot_risk[r.slot_key]     = max(0.0, self._slot_risk.get(r.slot_key, 0.0) - r.risk)

    def update_risk(self, pos_key: str, risk: float) -> None:
        """Ajuste le risque engagé d'une position ouverte.

        Appelé quand le trailing remonte le stop : le risque réel
        (``|entrée − stop_courant| × taille``) diminue et libère du budget
        pour un autre bot (§2.1) — l'enveloppe, elle, reste inchangée.
        """
        with self._lock:
            r = self._positions.get(pos_key)
            if r is None:
                return
            delta = risk - r.risk
            r.risk = risk
            sym_key = _symbol_key(r.venue, r.symbol)
            self._venue_risk[r.venue]       = max(0.0, self._venue_risk.get(r.venue, 0.0) + delta)
            self._symbol_risk[sym_key]      = max(0.0, self._symbol_risk.get(sym_key, 0.0) + delta)
            self._slot_risk[r.slot_key]     = max(0.0, self._slot_risk.get(r.slot_key, 0.0) + delta)

    def resize(self, pos_key: str, *, risk: float, notional: float) -> None:
        """Réexprime une réservation existante — pyramidage.

        Les plafonds ne sont pas re-vérifiés : l'incrément l'a déjà été par un
        ``reserve`` dédié. L'appelant doit *agrandir d'abord, libérer ensuite*
        l'incrément provisoire, pour qu'un réservataire concurrent ne voie
        jamais plus de marge qu'il n'y en a réellement.
        """
        with self._lock:
            r = self._positions.get(pos_key)
            if r is None:
                return
            d_risk = risk - r.risk
            d_notional = notional - r.notional
            r.risk, r.notional = risk, notional
            sym_key = _symbol_key(r.venue, r.symbol)
            self._venue_risk[r.venue]       = max(0.0, self._venue_risk.get(r.venue, 0.0) + d_risk)
            self._symbol_risk[sym_key]      = max(0.0, self._symbol_risk.get(sym_key, 0.0) + d_risk)
            self._slot_risk[r.slot_key]     = max(0.0, self._slot_risk.get(r.slot_key, 0.0) + d_risk)
            self._venue_notional[r.venue]   = max(0.0, self._venue_notional.get(r.venue, 0.0) + d_notional)
            self._symbol_notional[sym_key]  = max(0.0, self._symbol_notional.get(sym_key, 0.0) + d_notional)
            self._slot_notional[r.slot_key] = max(0.0, self._slot_notional.get(r.slot_key, 0.0) + d_notional)

    def engaged(self) -> dict:
        """``{venue: {venue_name: {notional, risk}}, symbol: {...}, slot: {...}}``."""
        with self._lock:
            return {
                "venue": {k: {"notional": v, "risk": self._venue_risk.get(k, 0.0)}
                         for k, v in self._venue_notional.items()},
                "symbol": {k: {"notional": v, "risk": self._symbol_risk.get(k, 0.0)}
                          for k, v in self._symbol_notional.items()},
                "slot": {k: {"notional": v, "risk": self._slot_risk.get(k, 0.0)}
                        for k, v in self._slot_notional.items()},
            }

    def snapshot(self) -> dict:
        """Pour ``/api/risk`` — même contenu qu'``engaged()``, nom explicite
        côté consommateur API (§6.1)."""
        return self.engaged()
