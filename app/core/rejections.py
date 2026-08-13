"""Compteurs de refus de trade — mêmes codes, live et backtest (S12).

Sans ça, impossible de dire POURQUOI un backtest et son équivalent live
divergent (cf. docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §5.1) : le motif de
refus est l'information qui explique l'écart.
"""
import threading
from collections import Counter
from typing import Dict

REASONS = (
    "budget_symbole", "budget_venue", "enveloppe_slot", "notionnel_min",
    "levier", "venue", "risk", "slot_cb", "slot_disabled", "stop_invalide",
    # L6 (§97) — un événement de liquidité a déjà produit son trade primaire.
    "evenement_deja_trade",
)


class RejectionCounter:
    """Thread-safe. Compte les refus par motif, par slot et par symbole."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._by_reason: Counter = Counter()
        self._by_slot: Counter = Counter()
        self._by_symbol: Counter = Counter()

    def record(self, reason: str, *, venue: str = "", symbol: str = "",
              slot_key: str = "") -> None:
        with self._lock:
            self._total += 1
            self._by_reason[reason] += 1
            if slot_key:
                self._by_slot[slot_key] += 1
            if symbol:
                self._by_symbol[symbol] += 1

    def as_dict(self) -> Dict:
        with self._lock:
            return {
                "total": self._total,
                "par_motif": dict(self._by_reason),
                "par_slot": dict(self._by_slot),
                "par_symbole": dict(self._by_symbol),
            }

    def reset(self) -> None:
        with self._lock:
            self._total = 0
            self._by_reason.clear()
            self._by_slot.clear()
            self._by_symbol.clear()
