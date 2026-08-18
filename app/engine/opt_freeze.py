"""Gel de paramètres à faible impact (X-03). Mixin de ``OptimizerSearchEngine``."""
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from app.core.stats_thresholds import MIN_SIGNIFICANT_TRADES

logger = logging.getLogger(__name__)


class OptimizerFreezeMixin:
    cfg: Dict[str, Any]
    strategy_name: str
    param_space: Dict[str, List]
    _param_space_backup: Optional[Dict[str, Any]]

    def _should_reduce_space(self, n_trials: int) -> bool:
        """True si ≥ 6 params et cardinalité > 200 × n_trials."""
        if len(self.param_space) < 6:
            return False
        card = math.prod(len(v) for v in self.param_space.values())
        return card > max(n_trials, 1) * 200

    def _min_trades(self) -> int:
        """``optimizer.min_trades`` ou ``MIN_SIGNIFICANT_TRADES``."""
        return int((self.cfg.get("optimizer") or {}).get(
            "min_trades", MIN_SIGNIFICANT_TRADES))

    def _impact_scores(self, results: List[dict],
                       param_keys: List[str]) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """Impact marginal de chaque paramètre à partir d'essais déjà joués :
        écart entre la moyenne du score final des essais groupés par valeur,
        la plus haute et la plus basse. NaN si le paramètre n'a pas été
        observé à au moins 2 valeurs distinctes (donnée insuffisante pour
        juger — DIFFÉRENT d'un impact mesuré et nul, cf. ``_freeze_params``).
        0.0 si mesuré et effectivement plat (essais tous dégénérés, par ex.)."""
        impacts: Dict[str, float] = {}
        best_value_by_param: Dict[str, Any] = {}
        for k in param_keys:
            by_value: Dict[Any, List[float]] = {}
            for r in results:
                v = r["params"].get(k)
                by_value.setdefault(v, []).append(r["final_score"])
            means = {v: statistics.mean(s) for v, s in by_value.items() if s}
            finite = {v: m for v, m in means.items() if math.isfinite(m)}
            impacts[k] = (max(finite.values()) - min(finite.values())
                         if len(finite) >= 2 else float("nan"))
            if finite:
                best_value_by_param[k] = max(finite.items(), key=lambda kv: kv[1])[0]
            elif means:
                best_value_by_param[k] = next(iter(means))
            else:
                opts = self.param_space[k]
                best_value_by_param[k] = opts[len(opts) // 2]
        return impacts, best_value_by_param

    def _enough_to_freeze(self, results: List[dict], k: str) -> bool:
        """O-05 : au moins 2 valeurs distinctes et ``_MIN_SCREEN_PER_PARAM``
        observations par valeur — sinon l'impact n'est pas estimable."""
        by_value: Dict[Any, int] = {}
        for r in results:
            v = r["params"].get(k)
            by_value[v] = by_value.get(v, 0) + 1
        if len(by_value) < 2:
            return False
        return min(by_value.values()) >= self._MIN_SCREEN_PER_PARAM

    def _impact_below_noise(self, results: List[dict], k: str, impact: float) -> bool:
        """O-05 : ne geler que si l'impact est inférieur au bruit intra-groupe."""
        by_value: Dict[Any, List[float]] = {}
        for r in results:
            by_value.setdefault(r["params"].get(k), []).append(r["final_score"])
        sq: List[float] = []
        for scores in by_value.values():
            if len(scores) < 2:
                continue
            m = statistics.mean(scores)
            sq.extend((s - m) ** 2 for s in scores)
        if not sq or not math.isfinite(impact):
            return False
        noise = math.sqrt(sum(sq) / len(sq))
        return impact < noise

    def _freeze_params(self, impacts: Dict[str, float], best_value_by_param: Dict[str, Any],
                       param_keys: List[str], *,
                       max_cardinality: Optional[float] = None,
                       min_impact_share: float = 0.10,
                       screen_results: Optional[List[dict]] = None) -> Tuple[dict, list]:
        """Décide quels paramètres geler à partir d'impacts déjà calculés.
        Toujours au moins 1 paramètre conservé (jamais un espace totalement
        gelé). Deux modes :
          - ``max_cardinality`` (grid) : gèle par impact CROISSANT jusqu'à
            repasser sous ce seuil — réduction OBLIGATOIRE (une grille de
            plusieurs milliards de combinaisons est infaisable), donc gèle
            même sur signal faible ou absent (NaN inclus, en dernier
            recours), sinon la cible ne serait jamais atteignable.
          - sinon (random/bayesian, réduction facultative) : gèle seulement
            les paramètres MESURÉS à faible impact (part sous
            ``min_impact_share``). Un impact NaN (paramètre observé à moins
            de 2 valeurs distinctes dans le dépistage — arrive vite avec un
            dépistage en budget court face à un espace à beaucoup de
            paramètres, ex. 8 essais pour 21 paramètres) n'est PAS une
            preuve de faible impact : ce paramètre n'est jamais gelé sur
            cette base. Idem si aucun paramètre n'a de signal exploitable
            (impacts tous nuls ou NaN, ex: essais tous dégénérés à -999,
            observé sur opus_omnibus_v12 avec une fenêtre de test trop
            courte) : ne gèle RIEN plutôt que de figer des paramètres sur du
            bruit ou une absence de données.
        """
        # NaN (donnée insuffisante) toujours après les impacts mesurés, pour
        # qu'ils ne soient gelés qu'en tout dernier recours (mode grid) ou
        # jamais (mode random/bayesian, cf. boucle ci-dessous).
        ranked = sorted(param_keys, key=lambda k: (not math.isfinite(impacts.get(k, 0.0)),
                                                    impacts.get(k, 0.0)
                                                    if math.isfinite(impacts.get(k, 0.0)) else 0.0))
        if max_cardinality is not None:
            frozen_keys: List[str] = []
            card = math.prod(len(self.param_space[k]) for k in param_keys)
            for k in ranked:
                if card <= max_cardinality or len(frozen_keys) >= len(param_keys) - 1:
                    break
                card = max(1, card // max(1, len(self.param_space[k])))
                frozen_keys.append(k)
        else:
            total = sum(v for v in impacts.values() if math.isfinite(v) and v > 0)
            frozen_keys = []
            if total > 0:
                for k in ranked:
                    if len(frozen_keys) >= len(param_keys) - 1:
                        break
                    v = impacts.get(k, 0.0)
                    if not math.isfinite(v):
                        continue  # pas assez de données -> jamais gelé sur cette base
                    if v / total > min_impact_share:
                        break
                    if screen_results:
                        if not self._enough_to_freeze(screen_results, k):
                            continue
                        if not self._impact_below_noise(screen_results, k, v):
                            continue
                    frozen_keys.append(k)
        frozen = {k: best_value_by_param[k] for k in frozen_keys}
        kept_keys = [k for k in param_keys if k not in frozen]
        return frozen, kept_keys

    # Sous ce ratio (essais de dépistage / nombre de paramètres), l'estimateur
    # marginal n'a plus de signal fiable à offrir en mode facultatif : avec
    # aussi peu d'essais que de paramètres (voire moins), CHAQUE paramètre
    # varie simultanément à presque chaque essai — l'« impact » mesuré d'un
    # paramètre est alors dominé par le bruit de tous les autres qui changent
    # en même temps, pas par son propre effet. Mesuré sur opus_omnibus_v9 (21
    # paramètres, 8 essais de dépistage) : geler 20/21 paramètres sur cette
    # base, à chaque run, gel ou pas gel du signal NaN (cf. commit précédent).
    # Le mode grid (max_cardinality) n'est PAS concerné : sa réduction est
    # obligatoire (une grille de plusieurs milliards de combinaisons est
    # infaisable), il n'a pas le choix de reculer.
    _MIN_SCREEN_PER_PARAM = 2

    def _freeze_from_results(self, screen_results: List[dict], param_keys: List[str], *,
                             max_cardinality: Optional[float] = None) -> Optional[dict]:
        """Calcule l'impact de chaque paramètre à partir d'essais DÉJÀ joués
        (en budget, même fenêtre que la recherche) et gèle les moins
        impactants — mute ``self.param_space`` EN PLACE (sauvegardé pour
        ``_restore_param_space``). Retourne ``None`` si rien n'a été gelé."""
        if not screen_results:
            return None
        max_mod = max((len(self.param_space.get(k) or [None]) for k in param_keys),
                      default=1)
        # O-05 : au moins 2 essais par paramètre ET 2 × max(modalités).
        need = self._MIN_SCREEN_PER_PARAM * max(len(param_keys), max_mod)
        if max_cardinality is None and len(screen_results) < need:
            return None
        impacts, best_value_by_param = self._impact_scores(screen_results, param_keys)
        frozen, kept_keys = self._freeze_params(
            impacts, best_value_by_param, param_keys,
            max_cardinality=max_cardinality, screen_results=screen_results)
        if not frozen:
            return None
        card_before = math.prod(len(self.param_space[k]) for k in param_keys)
        if getattr(self, "_param_space_backup", None) is None:
            self._param_space_backup = dict(self.param_space)
        for k, v in frozen.items():
            self.param_space[k] = [v]
        card_after = math.prod(len(self.param_space[k]) for k in param_keys)
        logger.info(
            f"[ParamSearchOptim] {self.strategy_name} : {len(frozen)}/{len(param_keys)} "
            f"paramètres gelés (dépistage {len(screen_results)} essais, dans le budget) — "
            f"cardinalité {card_before:,} -> {card_after:,}"
        )
        return {
            "frozen_params": frozen, "kept_params": kept_keys,
            "n_screen": len(screen_results),
            "cardinality_before": card_before, "cardinality_after": card_after,
        }

    def _restore_param_space(self) -> None:
        backup = getattr(self, "_param_space_backup", None)
        if backup is not None:
            self.param_space = backup
            self._param_space_backup = None
