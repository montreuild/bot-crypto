"""
CapitalAllocator — Allocation du capital par slot (stratégie × timeframe).

Chaque slot "strategy::tf" (ex: "trend::1h", "breakout::4h") est une entité
indépendante avec son propre budget, son exposition courante et son historique P&L.

Règles :
  - Allocation initiale : 100% / N slots (parts égales)
  - Cap dur : 30% max par slot
  - Rééquilibrage hebdomadaire (lundi 00:00 UTC) basé sur profit_factor 7j
  - Corrélation : ≥75% même sens → bloquer nouvelles entrées ce sens
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Cap maximum par slot (30% du capital)
_MAX_SLOT_PCT = 0.30


@dataclass
class SlotBudget:
    slot_key: str           # "trend::1h"
    strategy: str           # "trend"
    tf: str                 # "1h"
    budget_pct: float       # 0.25 → 25% du capital
    used_notional: float    # exposition courante en USDC
    weekly_pnl: float       # P&L 7 derniers jours pour le rééquilibrage
    weekly_wins: int        # victoires 7j
    weekly_trades: int      # trades 7j
    weekly_gross_win: float # somme gains positifs 7j
    weekly_gross_loss: float # somme pertes absolues 7j


class CapitalAllocator:
    """
    Gère l'allocation du capital par slot strategy::tf.

    Usage dans LiveTrader :
        allocator = CapitalAllocator(capital=1000, active_per_tf=self._active_per_tf)
        ok, reason = allocator.can_allocate("trend::1h", notional=250)
        if ok:
            allocator.register_open("trend::1h", notional=250)
        # À la clôture :
        allocator.register_close("trend::1h", notional=250, pnl=12.5)
        # Chaque cycle :
        allocator.rebalance_if_due()
    """

    def __init__(self, capital: float, active_per_tf: Dict[str, List[dict]]):
        self.capital = capital
        self._slots: Dict[str, SlotBudget] = {}
        self._rebalance_next: float = self._next_monday_ts()
        self.rebuild_slots(active_per_tf)

    # ── Construction des slots ─────────────────────────────────────────────
    def rebuild_slots(self, active_per_tf: Dict[str, List[dict]]):
        """
        Reconstruit les slots depuis _active_per_tf.
        Appelé au démarrage et après hot-reload des stratégies.
        Préserve le budget des slots existants (pour ne pas écraser le rééquilibrage).
        """
        new_keys = set()
        for tf, entries in active_per_tf.items():
            for entry in entries:
                name = entry.get("name", "")
                if not name:
                    continue
                key = f"{name}::{tf}"
                new_keys.add(key)
                if key not in self._slots:
                    self._slots[key] = SlotBudget(
                        slot_key=key, strategy=name, tf=tf,
                        budget_pct=0.0,
                        used_notional=0.0,
                        weekly_pnl=0.0, weekly_wins=0,
                        weekly_trades=0, weekly_gross_win=0.0,
                        weekly_gross_loss=0.0,
                    )

        # Supprimer les slots qui ne sont plus actifs
        for key in list(self._slots.keys()):
            if key not in new_keys:
                del self._slots[key]

        self._equalize_budgets()
        logger.info(
            f"[Allocator] {len(self._slots)} slots : "
            + ", ".join(f"{k}={v.budget_pct:.0%}" for k, v in self._slots.items())
        )

    def _equalize_budgets(self):
        """Distribue 100% équitablement entre les slots, en respectant le cap."""
        n = len(self._slots)
        if n == 0:
            return
        per_slot = min(1.0 / n, _MAX_SLOT_PCT)
        for slot in self._slots.values():
            slot.budget_pct = round(per_slot, 4)

    # ── Allocation ─────────────────────────────────────────────────────────
    def can_allocate(self, slot_key: str, notional: float) -> tuple[bool, str]:
        """
        Vérifie si le slot peut prendre une nouvelle position du montant notionnel.
        Retourne (ok, reason).
        """
        slot = self._slots.get(slot_key)
        if slot is None:
            return False, f"Slot '{slot_key}' inconnu"

        max_exposure = self.capital * slot.budget_pct
        if slot.used_notional + notional > max_exposure * 1.05:  # tolérance 5%
            return False, (
                f"Budget slot {slot_key} épuisé "
                f"({slot.used_notional:.1f}+{notional:.1f} > {max_exposure:.1f} USDC)"
            )
        return True, ""

    def register_open(self, slot_key: str, notional: float):
        """Enregistre l'ouverture d'une position dans un slot."""
        slot = self._slots.get(slot_key)
        if slot:
            slot.used_notional = round(slot.used_notional + notional, 4)

    def register_close(self, slot_key: str, notional: float, pnl: float):
        """Enregistre la clôture d'une position + met à jour les stats hebdo."""
        slot = self._slots.get(slot_key)
        if slot:
            slot.used_notional = round(max(0.0, slot.used_notional - notional), 4)
            slot.weekly_pnl = round(slot.weekly_pnl + pnl, 6)
            slot.weekly_trades += 1
            if pnl > 0:
                slot.weekly_wins += 1
                slot.weekly_gross_win = round(slot.weekly_gross_win + pnl, 6)
            else:
                slot.weekly_gross_loss = round(slot.weekly_gross_loss + abs(pnl), 6)

    def check_correlation(self, side: str, open_positions: dict) -> tuple[bool, str]:
        """
        Vérifie si ≥75% des positions ouvertes sont dans le même sens.
        Si oui, bloque les nouvelles entrées de ce sens.
        """
        if not open_positions:
            return True, ""
        total = len(open_positions)
        same = sum(1 for p in open_positions.values() if p.get("side") == side)
        ratio = same / total
        if ratio >= 0.75:
            return False, (
                f"Corrélation trop élevée : {same}/{total} positions sont {side} ({ratio:.0%} ≥ 75%)"
            )
        return True, ""

    # ── Sync capital ───────────────────────────────────────────────────────
    def update_equity(self, capital: float):
        self.capital = capital

    # ── Rééquilibrage hebdomadaire ─────────────────────────────────────────
    def rebalance_if_due(self):
        if time.time() < self._rebalance_next:
            return
        self._rebalance_next = self._next_monday_ts()
        self._rebalance()

    def _rebalance(self):
        """
        Rééquilibrage basé sur le profit_factor des 7 derniers jours.
          - Top tiers (PF > 1.5)  → budget × 1.3
          - Bottom tiers (PF < 0.8) → budget × 0.75
          - Autres → budget × 1.0
        Normalise pour que la somme = 100%, puis applique le cap.
        """
        if not self._slots:
            return

        def _profit_factor(slot: SlotBudget) -> float:
            if slot.weekly_gross_loss > 0:
                return slot.weekly_gross_win / slot.weekly_gross_loss
            return 1.0 if slot.weekly_gross_win == 0 else 2.0

        pfs = {k: _profit_factor(v) for k, v in self._slots.items()}
        logger.info(f"[Allocator] Rééquilibrage hebdomadaire — PF : {pfs}")

        # Appliquer les multiplicateurs
        new_budgets: Dict[str, float] = {}
        for key, slot in self._slots.items():
            pf = pfs[key]
            if pf > 1.5:
                mult = 1.3
            elif pf < 0.8:
                mult = 0.75
            else:
                mult = 1.0
            new_budgets[key] = slot.budget_pct * mult

        # Normaliser
        total = sum(new_budgets.values())
        if total > 0:
            factor = 1.0 / total
            for key in new_budgets:
                new_budgets[key] = min(new_budgets[key] * factor, _MAX_SLOT_PCT)

        # Renormaliser après cap
        total2 = sum(new_budgets.values())
        if total2 > 0:
            factor2 = 1.0 / total2
            for key in new_budgets:
                new_budgets[key] = round(new_budgets[key] * factor2, 4)

        # Appliquer + reset stats hebdo
        for key, slot in self._slots.items():
            slot.budget_pct = new_budgets.get(key, slot.budget_pct)
            slot.weekly_pnl = 0.0
            slot.weekly_wins = 0
            slot.weekly_trades = 0
            slot.weekly_gross_win = 0.0
            slot.weekly_gross_loss = 0.0

        logger.info(
            "[Allocator] Nouveaux budgets : "
            + ", ".join(f"{k}={v.budget_pct:.0%}" for k, v in self._slots.items())
        )

    # ── Statut pour l'API ──────────────────────────────────────────────────
    def get_status(self) -> List[dict]:
        return [
            {
                "slot_key":      s.slot_key,
                "strategy":      s.strategy,
                "tf":            s.tf,
                "budget_pct":    round(s.budget_pct * 100, 1),
                "budget_usdc":   round(self.capital * s.budget_pct, 2),
                "used_notional": round(s.used_notional, 2),
                "used_pct":      round(
                    s.used_notional / max(self.capital * s.budget_pct, 1) * 100, 1
                ),
                "weekly_pnl":    round(s.weekly_pnl, 4),
                "weekly_trades": s.weekly_trades,
                "weekly_wins":   s.weekly_wins,
                "next_rebalance": datetime.fromtimestamp(
                    self._rebalance_next, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC"),
            }
            for s in self._slots.values()
        ]

    # ── Utilitaires ────────────────────────────────────────────────────────
    @staticmethod
    def _next_monday_ts() -> float:
        """Prochain lundi à 00:00 UTC."""
        now = datetime.now(timezone.utc)
        days_ahead = 7 - now.weekday()  # weekday: 0=lundi
        if days_ahead == 7:
            days_ahead = 0              # on est lundi → prochain lundi dans 7j
        # Si aujourd'hui lundi et pas encore passé minuit, c'est aujourd'hui
        next_monday = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_monday = next_monday.replace(
            day=now.day + days_ahead if days_ahead > 0 else now.day + 7
        )
        # Utiliser timedelta pour éviter les débordements de jour
        from datetime import timedelta
        delta_days = days_ahead if days_ahead > 0 else 7
        next_monday = (
            now.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=delta_days)
        )
        return next_monday.timestamp()
