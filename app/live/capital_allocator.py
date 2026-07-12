"""
CapitalAllocator — allocation du capital par slot (strategy::tf).

Modes d'allocation :
  - equal       : répartition égale entre tous les slots actifs
  - manual      : budgets définis manuellement par l'utilisateur
  - performance : rééquilibrage automatique basé sur le profit_factor
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from app.core.bot_identity import parse_slot_key, build_slot_key

logger = logging.getLogger(__name__)

# Defaults (can be overridden via config)
_DEFAULT_MAX_SLOT_PCT = 0.50
_DEFAULT_MIN_TRADES_FOR_REBALANCE = 3
_DEFAULT_REBALANCE_INTERVAL = "weekly"  # "daily" or "weekly"
_DEFAULT_MAX_SYMBOL_EXPOSURE_PCT = 0.25  # max 25% of capital on a single symbol
_DEFAULT_MAX_PYRAMIDING = 2  # max positions per symbol (pyramiding)
_VALID_MODES = ("equal", "manual", "performance")


def _lookup_legacy(mapping, slot_key: str) -> Optional[str]:
    """Résout ``slot_key`` (``strategy::tf::symbol``) dans ``mapping`` (dict ou
    set de clés persistées dans config.yaml), avec repli sur la clé héritée à
    2 parties ``strategy::tf`` si la clé exacte à 3 parties est absente.

    Cf. OPS-01 : avant la refonte per-symbole, ``capital_allocator.slot_budgets``
    et ``lifecycle.manual_active`` ne portaient pas la dimension symbole. Une
    clé 2 parties sans match exact s'applique donc à TOUS les slots de préfixe
    ``strategy::tf::``. La clé exacte à 3 parties, si présente, a toujours
    priorité sur la clé héritée.

    Retourne la clé effectivement trouvée dans ``mapping`` (exacte ou héritée),
    ou ``None`` si aucune des deux ne matche.
    """
    if slot_key in mapping:
        return slot_key
    strategy, tf, symbol = parse_slot_key(slot_key)
    if symbol:
        legacy_key = f"{strategy}::{tf}"
        if legacy_key in mapping:
            return legacy_key
    return None


def _legacy_keys(mapping) -> List[str]:
    """Liste les clés à 2 parties (``strategy::tf``, sans symbole) présentes
    dans ``mapping`` — utilisé uniquement pour le log de compatibilité au
    chargement (cf. OPS-01)."""
    return sorted(k for k in mapping if k.count("::") == 1)


@dataclass
class SlotBudget:
    slot_key: str           # "trend::1h::BTC/USDC"
    strategy: str           # "trend"
    tf: str                 # "1h"
    symbol: str             # "BTC/USDC"
    enabled: bool           # slot activé/désactivé
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

    Trois modes d'allocation :
      - equal       : 100% répartis équitablement (par défaut)
      - manual      : budgets fixés par l'utilisateur, normalisés à 100%
      - performance : rééquilibrage automatique basé sur le profit_factor

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

    def __init__(self, capital: float, active_per_tf: Dict[str, List[dict]],
                 cfg: dict = None, session_factory=None):
        self.capital = capital
        self._session_factory = session_factory
        alloc_cfg = (cfg or {}).get("capital_allocator", {})
        self._max_slot_pct = float(alloc_cfg.get("max_slot_pct", _DEFAULT_MAX_SLOT_PCT))
        self._min_trades_for_rebalance = int(alloc_cfg.get("min_trades_for_rebalance", _DEFAULT_MIN_TRADES_FOR_REBALANCE))
        self._rebalance_interval = alloc_cfg.get("rebalance_interval", _DEFAULT_REBALANCE_INTERVAL)
        self._max_symbol_exposure_pct = float(alloc_cfg.get("max_symbol_exposure_pct", _DEFAULT_MAX_SYMBOL_EXPOSURE_PCT))
        self._max_pyramiding = int(alloc_cfg.get("max_pyramiding", _DEFAULT_MAX_PYRAMIDING))
        self._mode: str = alloc_cfg.get("mode", "equal")
        if self._mode not in _VALID_MODES:
            self._mode = "equal"
        # Sizing par bot (Phase 1) : si True, chaque bot dimensionne sur SON budget
        # (budget × levier) au lieu de l'équité globale → fidélité au backtest.
        self._per_bot_sizing = bool(alloc_cfg.get("per_bot_sizing", False))
        # Allocation continue pilotée par le score (Phase 2) — paramètres SHADOW.
        self._reserve_pct      = float(alloc_cfg.get("reserve_pct", 0.10))
        self._min_notional_usdc = float(alloc_cfg.get("min_notional_usdc", 10.0))
        self._max_budget_step  = float(alloc_cfg.get("max_budget_step", 0.25))  # ±25%
        self._active_floor     = int(alloc_cfg.get("active_floor", 2))
        # Si True, l'allocation continue est RÉELLEMENT appliquée (sinon shadow only).
        self._continuous_allocation = bool(alloc_cfg.get("continuous_allocation", False))
        self._custom_budgets: Dict[str, float] = {
            k: float(v) for k, v in alloc_cfg.get("slot_budgets", {}).items()
        }
        self._disabled_slots: set = set(alloc_cfg.get("disabled_slots", []))
        self._slots: Dict[str, SlotBudget] = {}
        self._rebalance_next: float = self._next_rebalance_ts()
        # Callback optionnel appelé après chaque _apply_mode() : persist_fn(budgets: dict)
        self._persist_callback: Optional[Callable[[dict], None]] = None
        # Compatibilité OPS-01 : clés héritées à 2 parties (sans symbole),
        # appliquées par préfixe à tous les slots concernés (cf. _lookup_legacy).
        legacy_budgets = _legacy_keys(self._custom_budgets)
        legacy_disabled = _legacy_keys(self._disabled_slots)
        if legacy_budgets or legacy_disabled:
            logger.info(
                "[Allocator] Clés héritées 2-parties détectées (appliquées par "
                f"préfixe à tous les symboles) : slot_budgets={legacy_budgets}, "
                f"disabled_slots={legacy_disabled}"
            )
        self.rebuild_slots(active_per_tf)
        # Reprise des stats hebdo + planning de rebalance après redémarrage.
        self._restore_weekly_stats()

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
                symbol = entry.get("symbol", "")
                key = build_slot_key(name, tf, symbol)
                new_keys.add(key)
                if key not in self._slots:
                    self._slots[key] = SlotBudget(
                        slot_key=key, strategy=name, tf=tf, symbol=symbol,
                        enabled=_lookup_legacy(self._disabled_slots, key) is None,
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

        self._apply_mode()

        logger.info(
            f"[Allocator] {len(self._slots)} slots (mode={self._mode}) : "
            + ", ".join(
                f"{k}={'OFF' if not v.enabled else f'{v.budget_pct:.0%}'}"
                for k, v in self._slots.items()
            )
        )

    def set_persist_callback(self, callback: Callable[[dict], None]) -> None:
        """
        Enregistre un callback appelé après chaque _apply_mode() pour persister les budgets.
        callback(budgets: dict) où budgets = {slot_key: budget_pct} pour les slots actifs.
        """
        self._persist_callback = callback

    def _apply_mode(self):
        """Applique le mode d'allocation actuel aux budgets des slots."""
        if self._mode == "manual":
            self._apply_manual_budgets()
        else:
            # equal et performance : commencer par une distribution égale
            self._equalize_budgets()
            # En mode manual, les custom budgets sont appliqués dans _apply_manual_budgets
            # Pour equal/performance, on restaure quand même les custom budgets persistés
            if self._mode == "performance":
                for slot in self._slots.values():
                    if not slot.enabled:
                        continue
                    found_key = _lookup_legacy(self._custom_budgets, slot.slot_key)
                    if found_key is not None:
                        slot.budget_pct = max(
                            0.01, min(self._max_slot_pct, self._custom_budgets[found_key])
                        )
        # Persister les budgets calculés si un callback est enregistré
        if self._persist_callback is not None:
            budgets = {k: round(v.budget_pct, 4) for k, v in self._slots.items() if v.enabled}
            try:
                self._persist_callback(budgets)
            except Exception as e:
                logger.warning(f"[Allocator] Persistance budgets KO : {e}")

    def _equalize_budgets(self):
        """Distribue 100% équitablement entre les slots actifs, en respectant le cap."""
        active = [s for s in self._slots.values() if s.enabled]
        n = len(active)
        if n == 0:
            return
        per_slot = min(1.0 / n, self._max_slot_pct)
        for slot in self._slots.values():
            slot.budget_pct = round(per_slot, 4) if slot.enabled else 0.0

    def _apply_manual_budgets(self):
        """Applique les budgets manuels. Slots sans budget explicite reçoivent une part égale du reste."""
        active = [s for s in self._slots.values() if s.enabled]
        if not active:
            for s in self._slots.values():
                s.budget_pct = 0.0
            return

        # Désactiver les slots non actifs
        for s in self._slots.values():
            if not s.enabled:
                s.budget_pct = 0.0

        # Appliquer les budgets custom (clé exacte 3-parties, sinon repli sur la
        # clé héritée 2-parties "strategy::tf" — cf. OPS-01/_lookup_legacy).
        used = 0.0
        unset_keys = []
        for s in active:
            found_key = _lookup_legacy(self._custom_budgets, s.slot_key)
            if found_key is not None:
                pct = max(0.01, min(self._max_slot_pct, self._custom_budgets[found_key]))
                s.budget_pct = round(pct, 4)
                used += s.budget_pct
            else:
                unset_keys.append(s.slot_key)

        # Distribuer le reste aux slots sans budget manuel
        remaining = max(0.0, 1.0 - used)
        if unset_keys:
            per_unset = min(remaining / len(unset_keys), self._max_slot_pct)
            for key in unset_keys:
                self._slots[key].budget_pct = round(per_unset, 4)

        # Si la somme > 1.0, normaliser proportionnellement
        self._normalize_budgets()

    def _normalize_budgets(self):
        """Normalise les budgets pour que la somme des slots actifs = 100%."""
        active = [s for s in self._slots.values() if s.enabled]
        total = sum(s.budget_pct for s in active)
        if total > 0 and abs(total - 1.0) > 0.001:
            factor = 1.0 / total
            for s in active:
                s.budget_pct = round(min(s.budget_pct * factor, self._max_slot_pct), 4)
            # Renormaliser après cap
            total2 = sum(s.budget_pct for s in active)
            if total2 > 0 and abs(total2 - 1.0) > 0.001:
                factor2 = 1.0 / total2
                for s in active:
                    s.budget_pct = round(s.budget_pct * factor2, 4)

    # ── Allocation ─────────────────────────────────────────────────────────
    def is_slot_enabled(self, slot_key: str) -> tuple[bool, str]:
        """
        Vérifie si le slot est activé (enabled/disabled).
        Retourne (ok, reason). Indépendant de la logique budget.
        """
        slot = self._slots.get(slot_key)
        if slot is None:
            return False, f"Slot '{slot_key}' inconnu"
        if not slot.enabled:
            return False, f"Slot '{slot_key}' désactivé"
        return True, ""

    def can_allocate(self, slot_key: str, notional: float) -> tuple[bool, str]:
        """
        Vérifie si le slot dispose du budget nécessaire pour une nouvelle position.
        Retourne (ok, reason). Ne vérifie PAS l'état enabled/disabled (voir is_slot_enabled).
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
            # Persiste les stats hebdo (survie au redémarrage pour le rebalance).
            self._persist_weekly_stats()

    def check_correlation(self, side: str, open_positions: dict,
                          symbol: str = "") -> tuple[bool, str]:
        """
        Vérifie si ≥75% des positions ouvertes sont dans le même sens.
        Also checks max symbol exposure and pyramiding limits.
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
        # Max exposure per symbol
        if symbol:
            symbol_notional = sum(
                p.get("notional", 0) for p in open_positions.values()
                if p.get("symbol") == symbol
            )
            max_symbol = self.capital * self._max_symbol_exposure_pct
            if symbol_notional >= max_symbol:
                return False, (
                    f"Exposition symbole {symbol} max atteinte "
                    f"({symbol_notional:.0f}/{max_symbol:.0f} USDC)"
                )
            # Pyramiding limit
            symbol_count = sum(
                1 for p in open_positions.values()
                if p.get("symbol") == symbol
            )
            if symbol_count >= self._max_pyramiding:
                return False, (
                    f"Pyramiding max atteint pour {symbol} "
                    f"({symbol_count}/{self._max_pyramiding})"
                )
        return True, ""

    @property
    def per_bot_sizing(self) -> bool:
        return self._per_bot_sizing

    def slot_budget_usdc(self, slot_key: str) -> float:
        """Budget courant d'un slot en USDC (0 si inconnu/désactivé)."""
        slot = self._slots.get(slot_key)
        if not slot or not slot.enabled:
            return 0.0
        return round(self.capital * slot.budget_pct, 4)

    # ── Sync capital ───────────────────────────────────────────────────────
    def update_equity(self, capital: float):
        self.capital = capital

    # ── Rééquilibrage ──────────────────────────────────────────────────────
    def rebalance_if_due(self):
        if self._mode != "performance":
            return
        if time.time() < self._rebalance_next:
            return
        self._rebalance_next = self._next_rebalance_ts()
        self._rebalance()

    def force_rebalance(self):
        """Force un rééquilibrage immédiat (appelé via API)."""
        if self._mode == "performance":
            self._rebalance()
        elif self._mode == "equal":
            self._equalize_budgets()
        # En mode manual, on ne rééquilibre pas automatiquement
        logger.info(f"[Allocator] Rééquilibrage forcé (mode={self._mode})")

    def _rebalance(self):
        """
        Rééquilibrage basé sur le profit_factor des 7 derniers jours.
          - Top tiers (PF > 1.5)  → budget × 1.3
          - Bottom tiers (PF < 0.8) → budget × 0.75
          - Autres → budget × 1.0
        Normalise pour que la somme = 100%, puis applique le cap.
        """
        active = [s for s in self._slots.values() if s.enabled]
        if not active:
            return

        def _profit_factor(slot: SlotBudget) -> float:
            if slot.weekly_gross_loss > 0:
                return slot.weekly_gross_win / slot.weekly_gross_loss
            return 1.0 if slot.weekly_gross_win == 0 else 2.0

        pfs = {s.slot_key: _profit_factor(s) for s in active}
        logger.info(f"[Allocator] Rééquilibrage — PF : {pfs}")

        new_budgets: Dict[str, float] = {}
        for slot in active:
            key = slot.slot_key
            if slot.weekly_trades < self._min_trades_for_rebalance:
                logger.debug(
                    f"[Allocator] Slot {key} ignoré pour rééquilibrage "
                    f"({slot.weekly_trades} trades < {self._min_trades_for_rebalance} min)"
                )
                new_budgets[key] = slot.budget_pct
                continue
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
                new_budgets[key] = min(new_budgets[key] * factor, self._max_slot_pct)

        # Renormaliser après cap
        total2 = sum(new_budgets.values())
        if total2 > 0:
            factor2 = 1.0 / total2
            for key in new_budgets:
                new_budgets[key] = round(new_budgets[key] * factor2, 4)

        # Appliquer + reset stats hebdo
        for slot in self._slots.values():
            if slot.enabled:
                target = new_budgets.get(slot.slot_key, slot.budget_pct)
                # Garde-fou de rebalance : ne jamais retirer de collatéral d'un
                # bot à position ouverte. On plancherise le budget au notionnel
                # déjà engagé (doc §5, Phase 2).
                if slot.used_notional > 0 and self.capital > 0:
                    floor = slot.used_notional / self.capital
                    if target < floor:
                        logger.info(
                            f"[Allocator] {slot.slot_key} : budget plancherisé à "
                            f"{floor:.1%} (position ouverte, pas de retrait de collatéral)"
                        )
                        target = floor
                slot.budget_pct = target
            slot.weekly_pnl = 0.0
            slot.weekly_wins = 0
            slot.weekly_trades = 0
            slot.weekly_gross_win = 0.0
            slot.weekly_gross_loss = 0.0

        logger.info(
            "[Allocator] Nouveaux budgets : "
            + ", ".join(
                f"{k}={'OFF' if not v.enabled else f'{v.budget_pct:.0%}'}"
                for k, v in self._slots.items()
            )
        )

        # Persister les budgets recalculés si un callback est enregistré
        if self._persist_callback is not None:
            budgets = {k: round(v.budget_pct, 4) for k, v in self._slots.items() if v.enabled}
            try:
                self._persist_callback(budgets)
            except Exception as e:
                logger.warning(f"[Allocator] Persistance budgets (rebalance) KO : {e}")
        # Persiste les stats hebdo remises à zéro + le nouveau planning de rebalance.
        self._persist_weekly_stats()

    # ── Toggle slot ────────────────────────────────────────────────────────
    def set_slot_enabled(self, slot_key: str, enabled: bool) -> bool:
        """
        Active ou désactive un slot. Recalcule les budgets après toggle.
        Le budget est géré indépendamment via _apply_mode().
        """
        slot = self._slots.get(slot_key)
        if slot is None:
            return False
        slot.enabled = enabled
        if enabled:
            self._disabled_slots.discard(slot_key)
        else:
            self._disabled_slots.add(slot_key)
        self._apply_mode()
        logger.info(f"[Allocator] Slot {slot_key} → {'activé' if enabled else 'désactivé'}")
        return True

    # ── Mode ───────────────────────────────────────────────────────────────
    def set_mode(self, mode: str) -> bool:
        """Change le mode d'allocation ('equal', 'manual', 'performance')."""
        if mode not in _VALID_MODES:
            return False
        self._mode = mode
        self._apply_mode()
        logger.info(f"[Allocator] Mode changé → {mode}")
        return True

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def disabled_slots(self) -> list:
        return sorted(self._disabled_slots)

    # ── Allocation continue pilotée par le score (Phase 2 — SHADOW) ──────────
    def compute_shadow_allocation(self, scores: Dict[str, float],
                                  correlations: Dict[str, float] = None) -> dict:
        """Calcule l'allocation **cible** pilotée par le score, sans l'appliquer.

        « Shadow » : on affiche ce que l'allocateur *aurait* fait (doc §5, Phase 2).
        Pipeline : score → poids ; malus de corrélation ; réserve ; minimums
        exchange ; variation bornée ±``max_budget_step`` ; plancher de bots actifs.

        ``scores``       : ``{slot_key: score}`` (budget-indépendant).
        ``correlations`` : ``{slot_key: malus∈[0,1]}`` optionnel (0 = aucun malus).
        Retourne ``{targets, current, reserve_pct, dropped, notes, delta}``.
        """
        correlations = correlations or {}
        active = [s for s in self._slots.values() if s.enabled]
        notes: List[str] = []
        if not active:
            return {"targets": {}, "current": {}, "reserve_pct": self._reserve_pct,
                    "dropped": [], "notes": ["aucun slot actif"], "delta": {}}

        # 1) Score → poids (négatifs/absents = 0) + malus de corrélation.
        weights: Dict[str, float] = {}
        for s in active:
            w = max(float(scores.get(s.slot_key, 0.0)), 0.0)
            w *= (1.0 - max(0.0, min(float(correlations.get(s.slot_key, 0.0)), 1.0)))
            weights[s.slot_key] = w
        if sum(weights.values()) <= 0:
            # Aucun score positif → repli sur l'égalité.
            notes.append("aucun score positif → répartition égale")
            for k in weights:
                weights[k] = 1.0

        # 2) Normalise sur la part investie (1 - réserve).
        investable = max(0.0, 1.0 - self._reserve_pct)
        total = sum(weights.values())
        targets = {k: v / total * investable for k, v in weights.items()}

        # 3) Minimums exchange : sous le notionnel minimal → budget 0.
        dropped = []
        min_pct = (self._min_notional_usdc / self.capital) if self.capital > 0 else 0.0
        for k in list(targets):
            if 0 < targets[k] < min_pct:
                dropped.append(k)
                targets[k] = 0.0
        # Renormalise les survivants sur la part investie.
        surv_total = sum(v for v in targets.values() if v > 0)
        if surv_total > 0:
            for k in targets:
                if targets[k] > 0:
                    targets[k] = targets[k] / surv_total * investable

        # 4) Variation bornée ±max_budget_step depuis le budget courant.
        for k in targets:
            cur = self._slots[k].budget_pct
            lo, hi = max(0.0, cur - self._max_budget_step), cur + self._max_budget_step
            targets[k] = min(max(targets[k], lo), hi)
            targets[k] = min(targets[k], self._max_slot_pct)

        # 5) Plancher de bots actifs : garantir ≥ active_floor budgets > 0.
        positive = [k for k, v in targets.items() if v > 0]
        if len(positive) < self._active_floor:
            ranked = sorted(active, key=lambda s: scores.get(s.slot_key, 0.0), reverse=True)
            for s in ranked:
                if targets.get(s.slot_key, 0.0) <= 0:
                    targets[s.slot_key] = max(min_pct, 0.01)
                    notes.append(f"plancher actif : {s.slot_key} maintenu")
                if sum(1 for v in targets.values() if v > 0) >= self._active_floor:
                    break

        current = {s.slot_key: round(s.budget_pct, 4) for s in active}
        targets = {k: round(v, 4) for k, v in targets.items()}
        delta = {k: round(targets[k] - current.get(k, 0.0), 4) for k in targets}
        return {
            "targets": targets,
            "current": current,
            "reserve_pct": round(self._reserve_pct, 4),
            "dropped": dropped,
            "notes": notes,
            "delta": delta,
        }

    @property
    def continuous_allocation(self) -> bool:
        return self._continuous_allocation

    def apply_continuous_allocation(self, scores: Dict[str, float],
                                    correlations: Dict[str, float] = None) -> dict:
        """**Applique** l'allocation continue pilotée par le score (graduation
        shadow → actif). Identique à ``compute_shadow_allocation`` mais écrit les
        budgets, en respectant le garde-fou de collatéral (pas de retrait sous le
        notionnel d'une position ouverte) et le cap par slot. Persiste si callback.
        """
        res = self.compute_shadow_allocation(scores, correlations)
        targets = res["targets"]
        for key, tgt in targets.items():
            slot = self._slots.get(key)
            if not slot or not slot.enabled:
                continue
            if slot.used_notional > 0 and self.capital > 0:
                tgt = max(tgt, slot.used_notional / self.capital)  # garde-fou collatéral
            slot.budget_pct = round(min(tgt, self._max_slot_pct), 4)
        if self._persist_callback is not None:
            budgets = {k: round(v.budget_pct, 4) for k, v in self._slots.items() if v.enabled}
            try:
                self._persist_callback(budgets)
            except Exception as e:
                logger.warning(f"[Allocator] Persistance budgets (continu) KO : {e}")
        res["applied"] = True
        logger.info("[Allocator] Allocation continue appliquée : "
                    + ", ".join(f"{k}={v.budget_pct:.0%}" for k, v in self._slots.items() if v.enabled))
        return res

    # ── Persistance des stats hebdo (reprise après redémarrage) ─────────────
    def _persist_weekly_stats(self) -> None:
        """Sauvegarde les stats hebdo par slot + le prochain rebalance (no-op si
        non branché à une base). Sans ça, après un crash le rééquilibrage par
        profit-factor repart de zéro et ignore la semaine écoulée."""
        if not self._session_factory:
            return
        try:
            from app.core.database import save_allocator_state, session_scope
            blob = {
                "rebalance_next": self._rebalance_next,
                "slots": {
                    k: {
                        "weekly_pnl":        s.weekly_pnl,
                        "weekly_wins":       s.weekly_wins,
                        "weekly_trades":     s.weekly_trades,
                        "weekly_gross_win":  s.weekly_gross_win,
                        "weekly_gross_loss": s.weekly_gross_loss,
                    }
                    for k, s in self._slots.items()
                },
            }
            with session_scope(self._session_factory) as sess:
                save_allocator_state(sess, blob)
        except Exception as e:
            logger.debug(f"[Allocator] persistance stats hebdo KO : {e}")

    def _restore_weekly_stats(self) -> None:
        """Restaure les stats hebdo + le prochain rebalance depuis la base."""
        if not self._session_factory:
            return
        try:
            from app.core.database import load_allocator_state, session_scope
            with session_scope(self._session_factory) as sess:
                blob = load_allocator_state(sess)
        except Exception as e:
            logger.debug(f"[Allocator] reprise stats hebdo KO : {e}")
            return
        if not blob:
            return
        rb = blob.get("rebalance_next")
        if rb:
            try:
                self._rebalance_next = float(rb)
            except (TypeError, ValueError):
                pass
        restored = 0
        for k, sd in (blob.get("slots") or {}).items():
            s = self._slots.get(k)
            if not s:
                continue
            s.weekly_pnl        = float(sd.get("weekly_pnl", 0.0) or 0.0)
            s.weekly_wins       = int(sd.get("weekly_wins", 0) or 0)
            s.weekly_trades     = int(sd.get("weekly_trades", 0) or 0)
            s.weekly_gross_win  = float(sd.get("weekly_gross_win", 0.0) or 0.0)
            s.weekly_gross_loss = float(sd.get("weekly_gross_loss", 0.0) or 0.0)
            if s.weekly_trades > 0:
                restored += 1
        if restored:
            logger.info(f"[Allocator] Stats hebdo restaurées ({restored} slot(s) actifs).")

    # ── Statut pour l'API ──────────────────────────────────────────────────
    def get_status(self) -> List[dict]:
        return [
            {
                "slot_key":      s.slot_key,
                "strategy":      s.strategy,
                "tf":            s.tf,
                "enabled":       s.enabled,
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

    def get_config(self) -> dict:
        """Retourne la configuration courante de l'allocateur."""
        return {
            "mode":                   self._mode,
            "max_slot_pct":           round(self._max_slot_pct * 100, 1),
            "rebalance_interval":     self._rebalance_interval,
            "min_trades_for_rebalance": self._min_trades_for_rebalance,
            "max_symbol_exposure_pct": round(self._max_symbol_exposure_pct * 100, 1),
            "max_pyramiding":         self._max_pyramiding,
            "disabled_slots":         sorted(self._disabled_slots),
            "custom_budgets":         {k: round(v * 100, 1) for k, v in self._custom_budgets.items()},
        }

    def set_slot_budget(self, slot_key: str, budget_pct: float) -> bool:
        """
        Définit manuellement le budget d'un slot (en fraction du capital, ex: 0.25 = 25%).
        Renormalise ensuite les autres slots pour que la somme reste ≤ 100%.
        Retourne True si le slot a été trouvé et modifié.
        """
        if slot_key not in self._slots:
            return False
        budget_pct = max(0.01, min(self._max_slot_pct, round(float(budget_pct), 4)))
        self._slots[slot_key].budget_pct = budget_pct
        self._custom_budgets[slot_key] = budget_pct
        # Renormaliser : si la somme > 1.0, réduire proportionnellement les autres slots
        total = sum(s.budget_pct for s in self._slots.values() if s.enabled)
        if total > 1.0:
            factor = (1.0 - budget_pct) / max((total - budget_pct), 0.001)
            for key, slot in self._slots.items():
                if key != slot_key and slot.enabled:
                    slot.budget_pct = round(slot.budget_pct * factor, 4)
        logger.info(
            f"[Allocator] Budget {slot_key} → {budget_pct:.0%} (manuel) "
            + ", ".join(f"{k}={v.budget_pct:.0%}" for k, v in self._slots.items() if v.enabled)
        )
        return True

    def set_rebalance_interval(self, interval: str) -> None:
        """Met à jour l'intervalle de rééquilibrage ('daily', 'weekly' ou 'never')."""
        self._rebalance_interval = interval
        if interval != "never":
            self._rebalance_next = self._next_rebalance_ts()

    def set_max_slot_pct(self, pct: float) -> None:
        """Met à jour le pourcentage maximum par slot."""
        self._max_slot_pct = float(pct)

    # ── Utilitaires ────────────────────────────────────────────────────────
    def _next_rebalance_ts(self) -> float:
        """Next rebalance timestamp based on configured interval."""
        if self._rebalance_interval == "daily":
            return self._next_midnight_ts()
        return self._next_monday_ts()

    @staticmethod
    def _next_midnight_ts() -> float:
        """Prochain minuit UTC."""
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return tomorrow.timestamp()

    @staticmethod
    def _next_monday_ts() -> float:
        """Prochain lundi à 00:00 UTC."""
        now = datetime.now(timezone.utc)
        days_ahead = 7 - now.weekday()  # weekday: 0=lundi
        if days_ahead == 7:
            days_ahead = 0              # on est lundi → prochain lundi dans 7j
        delta_days = days_ahead if days_ahead > 0 else 7
        next_monday = (
            now.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=delta_days)
        )
        return next_monday.timestamp()
