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

logger = logging.getLogger(__name__)


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
        # Lissage anti-flush
        self._min_active      = int(lc.get("min_active_bots", 2))
        self._max_demotions_per_day = int(lc.get("max_demotions_per_day", 2))

        self._session_factory = session_factory
        self._states: Dict[str, str] = {}   # cache de l'état courant
        self._loaded = False
        self._demotions_today = 0
        self._day_key = self._today()
        self._reopt_queue: List[str] = []

    # ── Dérivation de l'état proposé ─────────────────────────────────────────
    def _propose(self, d: dict) -> str:
        budget = float(d.get("budget_pct", 0.0) or 0.0)
        n      = int(d.get("live_trades", 0) or 0)
        verdict = d.get("verdict")            # True / False / None
        ret    = d.get("live_avg_return_pct")
        score  = float(d.get("score", 0.0) or 0.0)

        # Budget effondré sous le plancher (avec au moins un peu de vécu) → retrait.
        if n >= 1 and budget < self._plancher_budget:
            return LifecycleState.RETIRE
        # Live contredit la simulation et perd, sur un échantillon suffisant → retrait.
        if (verdict is False and ret is not None and ret < 0
                and n >= self._eval_min):
            return LifecycleState.RETIRE
        if n == 0:
            return LifecycleState.CANDIDAT
        if n < self._trial_min:
            return LifecycleState.ESSAI
        if (n >= self._active_min and score > 0
                and verdict in (True, None)):
            return LifecycleState.ACTIF
        return LifecycleState.ESSAI

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

        proposed = {k: self._propose(d) for k, d in slots_data.items()}
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
