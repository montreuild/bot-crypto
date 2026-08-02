"""Cycle de vie des bots — machine à états (Phase 2).

Les états **Candidat / Essai / Actif / Retiré** ne sont qu'une *lecture humaine*
de la trajectoire du budget et de la réalisation live d'un bot (cf. doc §2). Ce
module dérive l'état de chaque bot à partir de signaux **budget-indépendants** :

- ``budget_pct``        : trajectoire du budget (un budget effondré = retrait) ;
- ``live_trades``       : nombre de trades réels (assez de preuve live ?) ;
- ``verdict``           : le live confirme-t-il la simulation ? (oos_tracker) ;
- ``live_avg_return_pct``: rendement réel moyen par trade ;
- ``score``             : score budget-indépendant du forward-test.

Pour éviter le *flush général* et la *tempête de re-optimisations* (doc §3), les
transitions sont **lissées** : plancher de bots actifs + quota de rétrogradations
par jour. Les bots retirés alimentent une **file de re-optimisation** (exposée,
non exécutée ici — la décision reste réversible). Les transitions sont persistées
en base (``slot_lifecycle_events``).
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.bot_identity import parse_slot_key
from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES

logger = logging.getLogger(__name__)


def _lookup_legacy(mapping, slot_key: str) -> Optional[str]:
    """Résout ``slot_key`` (``strategy::tf::symbol``) dans ``mapping`` (dict ou
    set de clés persistées dans config.yaml), avec repli sur la clé héritée à
    2 parties ``strategy::tf`` si la clé exacte à 3 parties est absente.

    Cf. OPS-01 : avant la refonte per-symbole, ``lifecycle.manual_active`` ne
    portait pas la dimension symbole. Une clé 2 parties sans match exact
    s'applique donc à TOUS les slots de préfixe ``strategy::tf::``. La clé
    exacte à 3 parties, si présente, a toujours priorité sur la clé héritée.

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


class LifecycleState:
    CANDIDAT = "candidat"   # créé/optimisé, pas (ou très peu) de trades live
    ESSAI    = "essai"      # trade en live, preuve encore mince
    ACTIF    = "actif"      # assez de trades + live conforme à la simulation
    RETIRE   = "retire"     # budget effondré ou live contredit la sim → re-opt

    ALL = (CANDIDAT, ESSAI, ACTIF, RETIRE)


# Rang pour distinguer promotions (rang ↑) et rétrogradations (rang ↓).
_RANK = {LifecycleState.RETIRE: 0, LifecycleState.CANDIDAT: 1,
         LifecycleState.ESSAI: 2, LifecycleState.ACTIF: 3}


class SlotLifecycleManager:
    """Dérive et lisse les états du cycle de vie, persiste les transitions."""

    def __init__(self, cfg: dict, session_factory=None):
        lc = (cfg or {}).get("lifecycle", {}) or {}
        self._plancher_budget = float(lc.get("plancher_budget_pct", 0.02))
        self._trial_min       = int(lc.get("trial_min_trades", 3))
        self._active_min      = int(lc.get("active_min_trades", 10))
        self._eval_min        = int(lc.get("eval_min_trades", 10))
        # Promotion par edge (cf. docs/CONCEPTION_PROMOTION_PAR_EDGE.md)
        self._edge_min_trades = int(lc.get("edge_min_trades", 20))
        self._max_worst_trade = float(lc.get("max_worst_trade_pct", 50.0))
        # BT-06 : la promotion ACTIF est une décision engageante → seuil de
        # significativité partagé (10) et non plus 2 (indiscernable du bruit).
        self._fidelity_min_fills = int(lc.get("fidelity_min_fills",
                                              MIN_SIGNIFICANT_TRADES))
        # Forçage manuel : bots forcés ACTIF (droit de veto utilisateur).
        #
        # D6 (plan directeur) — la clé s'appelle `lifecycle.force_active` et le
        # défaut est la LISTE VIDE : la machinerie candidat/essai/actif/retiré
        # décide seule. `manual_active` reste lue en rétro-compat (configs
        # existantes) mais est dépréciée : son nom laissait croire à un réglage
        # de routine alors que c'est un court-circuit du cycle de vie.
        #
        # Ce que le forçage court-circuite réellement, au-delà de la promotion :
        # un slot forcé n'est JAMAIS retiré, même quand son budget s'effondre ou
        # que le live contredit la simulation en perdant — et il n'entre donc
        # jamais dans la file de ré-optimisation.
        forced = lc.get("force_active")
        legacy_key_used = False
        if forced is None:
            forced = lc.get("manual_active")
            legacy_key_used = forced is not None
        self._force_active = set(forced or [])
        if legacy_key_used and self._force_active:
            logger.warning(
                "[Lifecycle] `lifecycle.manual_active` est DÉPRÉCIÉE (D6) — "
                "renommez-la `lifecycle.force_active`. Lue telle quelle pour "
                "cette exécution."
            )
        if self._force_active:
            logger.warning(
                f"[Lifecycle] {len(self._force_active)} slot(s) forcés ACTIF via "
                f"lifecycle.force_active — pour ces slots le lifecycle "
                f"automatique est COURT-CIRCUITÉ : ni promotion par edge, ni "
                f"retrait sur budget effondré ou live perdant, ni "
                f"ré-optimisation. Override assumé (tests/debug) : en "
                f"production, laissez `force_active: []`."
            )
        # Compatibilité OPS-01 : clés héritées à 2 parties (sans symbole),
        # appliquées par préfixe à tous les slots concernés (cf. _lookup_legacy).
        legacy_forced = _legacy_keys(self._force_active)
        if legacy_forced:
            logger.info(
                "[Lifecycle] Clés force_active héritées 2-parties détectées "
                f"(appliquées par préfixe à tous les symboles) : {legacy_forced}"
            )
        # S4-06 : cohérence lifecycle ↔ budgets. Si `force_active` liste
        # des slots qui ne sont PAS dans `slot_budgets`, c'est une
        # incohérence de config — on logge un warning pour que l'utilisateur
        # corrige (sinon le slot est actif sans budget explicite → budget
        # égal par défaut, ce qui peut surprendre).
        alloc_cfg = (cfg or {}).get("capital_allocator", {}) or {}
        self._custom_budgets: dict = dict(alloc_cfg.get("slot_budgets") or {})
        if self._force_active and self._custom_budgets:
            budget_keys = set(self._custom_budgets.keys())
            manual_keys = set(self._force_active)
            missing_in_budgets = manual_keys - budget_keys
            if missing_in_budgets:
                logger.warning(
                    f"[Lifecycle] {len(missing_in_budgets)} slot(s) dans "
                    f"force_active SANS budget explicite dans "
                    f"capital_allocator.slot_budgets : {sorted(missing_in_budgets)}. "
                    f"Ils seront actifs avec un budget par défaut (égal), ce qui "
                    f"peut surprendre. Ajoutez-les à `slot_budgets` ou retirez-"
                    f"les de `force_active`."
                )
            extra_in_budgets = budget_keys - manual_keys
            if extra_in_budgets:
                logger.info(
                    f"[Lifecycle] {len(extra_in_budgets)} slot(s) ont un "
                    f"budget explicite MAIS NE SONT PAS dans force_active : "
                    f"{sorted(extra_in_budgets)}. S'ils sont inactifs, le "
                    f"budget est inutilisé — vérifiez la cohérence."
                )
        # Lissage anti-flush
        self._min_active      = int(lc.get("min_active_bots", 2))
        self._max_demotions_per_day = int(lc.get("max_demotions_per_day", 2))

        self._session_factory = session_factory
        self._states: Dict[str, str] = {}   # cache de l'état courant
        self._loaded = False
        self._demotions_today = 0
        self._day_key = self._today()
        self._reopt_queue: List[str] = []

    # ── Promotion par edge ───────────────────────────────────────────────────
    def _edge_significant(self, d: dict) -> bool:
        """L'edge est-elle significative sur le backtest ?

        Borne basse du cône d'expectancy > 0, plancher de trades backtest
        (anti-dégénérescence du bootstrap) et garde-fou de queue (pire trade
        simulé borné). Cf. docs/CONCEPTION_PROMOTION_PAR_EDGE.md §2.1.
        """
        ci_low = d.get("edge_ci_low")
        n_sim  = int(d.get("edge_n", 0) or 0)
        worst  = d.get("worst_trade_pct")
        if ci_low is None or ci_low <= 0 or n_sim < self._edge_min_trades:
            return False
        if worst is not None and worst < -self._max_worst_trade:
            return False
        return True

    # ── Dérivation de l'état proposé ─────────────────────────────────────────
    def _propose(self, d: dict) -> str:
        # Forçage manuel : l'utilisateur impose l'activation (droit de veto).
        # `manual_active` accepté en entrée pour rétro-compat des appelants.
        if d.get("force_active") or d.get("manual_active"):
            return LifecycleState.ACTIF

        budget = float(d.get("budget_pct", 0.0) or 0.0)
        n      = int(d.get("live_trades", 0) or 0)
        # ``live_in_band`` est le signal de fidélité (True/False/None) ; on
        # accepte ``verdict`` pour rétro-compat.
        in_band = d.get("live_in_band", d.get("verdict"))
        ret    = d.get("live_avg_return_pct")

        # Budget effondré sous le plancher (avec au moins un peu de vécu) → retrait.
        if n >= 1 and budget < self._plancher_budget:
            return LifecycleState.RETIRE
        # Le réel contredit la simulation et perd, sur un échantillon suffisant → retrait.
        if (in_band is False and ret is not None and ret < 0
                and n >= self._eval_min):
            return LifecycleState.RETIRE

        # Edge non prouvée sur le backtest → reste Candidat (même s'il a tradé).
        if not self._edge_significant(d):
            return LifecycleState.CANDIDAT

        # Edge prouvée → la fidélité live confirme l'activation.
        if n >= self._fidelity_min_fills and in_band is True:
            return LifecycleState.ACTIF
        return LifecycleState.ESSAI

    # ── Forçage manuel ────────────────────────────────────────────────────────
    def set_force_active(self, slot_key: str, enabled: bool) -> None:
        """Force (ou libère) l'activation manuelle d'un bot. La transition est
        appliquée au prochain ``evaluate`` ; la persistance config est gérée par
        l'appelant (route API)."""
        if enabled:
            self._force_active.add(slot_key)
        else:
            self._force_active.discard(slot_key)
            # Cf. OPS-01 : si le forçage venait d'une clé héritée 2-parties,
            # la retirer aussi — sinon la désactivation resterait sans effet
            # (le repli par préfixe re-forcerait le slot au prochain evaluate).
            legacy = _lookup_legacy(self._force_active, slot_key)
            if legacy is not None:
                self._force_active.discard(legacy)

    #: Alias déprécié (D6) — conservé pour les appelants historiques.
    set_manual_active = set_force_active

    @property
    def _manual_active(self) -> set:
        """Alias déprécié (D6) de ``_force_active`` — lecture seule."""
        return self._force_active

    # ── Évaluation lissée ────────────────────────────────────────────────────
    def evaluate(self, slots_data: Dict[str, dict]) -> dict:
        """Met à jour les états de tous les slots, en lissant les rétrogradations.

        ``slots_data`` : ``{slot_key: {budget_pct, live_trades, verdict,
        live_avg_return_pct, score}}``. Retourne un snapshot
        ``{states, transitions, reopt_queue, counts}``.
        """
        self._ensure_loaded()
        self._roll_day()

        # Compte des bots non-retirés AVANT changements (pour le plancher).
        def _active_count(states: Dict[str, str]) -> int:
            return sum(1 for s in states.values() if s != LifecycleState.RETIRE)

        proposed = {
            k: self._propose({**d, "force_active": (_lookup_legacy(self._force_active, k) is not None)
                              or bool(d.get("force_active"))
                              or bool(d.get("manual_active"))})
            for k, d in slots_data.items()
        }
        transitions = []

        # 1) Promotions / nouveaux slots : appliquées immédiatement.
        for key, prop in proposed.items():
            cur = self._states.get(key)
            if cur is None:
                self._states[key] = prop
                transitions.append((key, None, prop, "création"))
            elif _RANK[prop] > _RANK[cur]:
                self._states[key] = prop
                transitions.append((key, cur, prop, "promotion"))

        # 2) Rétrogradations : soumises au quota/jour ET au plancher de bots actifs.
        demotions = [
            (key, self._states.get(key), prop)
            for key, prop in proposed.items()
            if self._states.get(key) is not None
            and _RANK[prop] < _RANK[self._states[key]]
        ]
        # Les pires d'abord (plus grosse chute de rang).
        demotions.sort(key=lambda t: _RANK[t[2]] - _RANK[t[1]])
        for key, cur, prop in demotions:
            if self._demotions_today >= self._max_demotions_per_day:
                logger.info(f"[Lifecycle] {key} : rétrogradation différée "
                            f"(quota {self._max_demotions_per_day}/j atteint)")
                continue
            # Plancher : ne pas tomber sous min_active bots non-retirés.
            if (prop == LifecycleState.RETIRE
                    and _active_count(self._states) <= self._min_active):
                logger.info(f"[Lifecycle] {key} : retrait différé (plancher "
                            f"de {self._min_active} bots actifs)")
                continue
            self._states[key] = prop
            self._demotions_today += 1
            transitions.append((key, cur, prop, "rétrogradation"))
            if prop == LifecycleState.RETIRE and key not in self._reopt_queue:
                self._reopt_queue.append(key)

        # Persiste les transitions
        for key, frm, to, reason in transitions:
            d = slots_data.get(key, {})
            self._persist(key, frm, to, reason, d.get("score"), d.get("budget_pct"))
            logger.info(f"[Lifecycle] {key} : {frm or '∅'} → {to} ({reason})")

        counts = {st: sum(1 for v in self._states.values() if v == st)
                  for st in LifecycleState.ALL}
        return {
            "states": dict(self._states),
            "transitions": [
                {"slot_key": k, "from": f, "to": t, "reason": r}
                for k, f, t, r in transitions
            ],
            "reopt_queue": list(self._reopt_queue),
            "counts": counts,
            "demotions_today": self._demotions_today,
        }

    def states(self) -> Dict[str, str]:
        self._ensure_loaded()
        return dict(self._states)

    def pop_reopt_queue(self) -> List[str]:
        """Vide et retourne la file de re-optimisation (consommée par l'orchestrateur)."""
        q, self._reopt_queue = list(self._reopt_queue), []
        return q

    # ── Persistance ──────────────────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._session_factory:
            return
        try:
            from app.core.database import get_current_lifecycle_states, session_scope
            with session_scope(self._session_factory) as sess:
                self._states = get_current_lifecycle_states(sess)
            logger.info(f"[Lifecycle] {len(self._states)} état(s) repris depuis la base.")
        except Exception as e:
            logger.debug(f"[Lifecycle] reprise états KO : {e}")

    def _persist(self, slot_key, from_state, to_state, reason, score, budget_pct) -> None:
        if not self._session_factory:
            return
        try:
            from app.core.database import record_lifecycle_event, session_scope
            with session_scope(self._session_factory) as sess:
                record_lifecycle_event(sess, slot_key, from_state, to_state,
                                       reason, score, budget_pct)
        except Exception as e:
            logger.debug(f"[Lifecycle] persistance transition KO : {e}")

    # ── Utilitaires ──────────────────────────────────────────────────────────
    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day_key:
            self._day_key = today
            self._demotions_today = 0

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
