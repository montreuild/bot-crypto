"""Recherche bayésienne (Optuna TPE ou heuristique). Mixin de ``OptimizerSearchEngine``."""
import logging
import math
import random
from typing import Any, Dict, List, Optional

from app.engine.opt_workers import _eval_worker, _worker_init
from app.live.protocols import OptimizerHost

logger = logging.getLogger(__name__)


class OptimizerBayesianMixin(OptimizerHost):
    def bayesian_search(self, n_trials: int = 40, n_jobs: int = 1,
                        early_stop_patience: int = 0,
                        param_search_optim: bool = True) -> dict:
        """Recherche bayésienne. Utilise Optuna (TPE, recherche informée par un
        modèle de substitution) si la librairie est installée ; sinon retombe sur
        l'heuristique historique (exploration aléatoire + raffinement local)."""
        if not self.param_space:
            return {"error": f"Aucun espace de params pour {self.strategy_name}"}
        try:
            import optuna  # noqa: F401
        except Exception:
            logger.info("[Bayesian] Optuna absent — repli sur random+perturbation. "
                        "Installez optuna pour une vraie recherche TPE.")
            try:
                return self._bayesian_search_legacy(n_trials, n_jobs, early_stop_patience,
                                                    param_search_optim=param_search_optim)
            finally:
                self._restore_param_space()
        return self._bayesian_search_optuna(n_trials, n_jobs, early_stop_patience,
                                            param_search_optim=param_search_optim)

    # ── Optuna (TPE) : vraie recherche informée ───────────────────────────────
    def _params_from_trial(self, trial) -> dict:
        """Construit un jeu de params depuis un trial Optuna. Chaque paramètre est
        encodé par l'INDICE de son option (catégoriel hashable) — robuste quel que
        soit le type des valeurs (float/int/bool/list)."""
        p = {}
        for k, opts in self.param_space.items():
            idx = trial.suggest_categorical(k, list(range(len(opts))))
            p[k] = opts[idx]
        return self._with_hp(p)

    def _bayesian_search_optuna(self, n_trials: int, n_jobs: int,
                                early_stop_patience: int,
                                param_search_optim: bool = True) -> dict:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        n_startup = max(8, n_trials // 3)
        # O-06 : seed configurable. None (défaut) = chaque campagne explore
        # un chemin différent. Les tests passent optimizer.seed: 0.
        _seed = (self.cfg.get("optimizer") or {}).get("seed")
        sampler = optuna.samplers.TPESampler(n_startup_trials=n_startup, seed=_seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)

        param_keys = list(self.param_space.keys())
        freeze_at = (n_startup if (param_search_optim and self._should_reduce_space(n_trials))
                    else None)

        safe_jobs = self._safe_worker_count(n_jobs)
        if safe_jobs <= 1:
            reduction = self._optuna_sequential(study, n_trials, early_stop_patience,
                                                freeze_at=freeze_at, param_keys=param_keys)
        else:
            reduction = self._optuna_parallel(study, n_trials, safe_jobs, early_stop_patience,
                                              freeze_at=freeze_at, param_keys=param_keys)
        result = self._best_result()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    def _optuna_param_importances(self, study,
                                  param_keys: List[str]) -> Optional[Dict[str, float]]:
        """Importances de paramètres estimées par Optuna, basées ANOVA.

        Essaie les évaluateurs dans l'ordre de préférence :
          1. fANOVA — le plus robuste face aux paramètres corrélés, mais requiert
             scikit-learn (RandomForestRegressor interne). Depuis la suppression
             de sklearn (phase 6), il n'est donc plus disponible en production ;
             conservé en tête pour les environnements de dev qui l'ont encore.
          2. PedANOVA — variante sklearn-free (pure Optuna), disponible même sans
             sklearn. C'est le chemin effectif du repo post-phase 6.

        Retourne ``None`` si aucun évaluateur ne rend un signal fini de somme
        strictement positive — l'appelant retombe alors sur l'estimateur
        marginal ``_impact_scores`` (défense en profondeur)."""
        import warnings

        import optuna
        from optuna.importance import get_param_importances

        evaluator_factories: list[tuple[str, Any]] = []
        try:
            from optuna.importance import FanovaImportanceEvaluator
            evaluator_factories.append(("fANOVA", lambda: FanovaImportanceEvaluator(seed=0)))
        except Exception:
            pass
        try:
            from optuna.importance import PedAnovaImportanceEvaluator
            evaluator_factories.append(("PedANOVA", lambda: PedAnovaImportanceEvaluator()))
        except Exception:
            pass

        for name, make_evaluator in evaluator_factories:
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore", category=optuna.exceptions.ExperimentalWarning)
                    raw = get_param_importances(study, evaluator=make_evaluator())
                impacts = {k: float(raw.get(k, 0.0)) for k in param_keys}
                if (all(math.isfinite(v) for v in impacts.values())
                        and sum(impacts.values()) > 0):
                    return impacts
            except Exception as _e:
                logger.info("[Bayesian/TPE] importance %s indisponible (%s) — "
                            "essai de l'évaluateur suivant.", name, _e)
        return None

    def _optuna_apply_freeze(self, study, screen_results: List[dict],
                             param_keys: List[str] | None) -> Optional[dict]:
        """Gèle le SAMPLER Optuna (``PartialFixedSampler``) sur les paramètres
        à faible impact, calculé à partir des essais de démarrage du TPE déjà
        joués (en budget). Ne mute JAMAIS ``self.param_space`` : le sampler
        fige la valeur lui-même, contrairement à random/grid/legacy — pas de
        backup/restore nécessaire pour ce chemin. Utilise l'estimateur
        d'importance fANOVA d'Optuna quand disponible (plus robuste que
        l'écart marginal simple face à des paramètres corrélés), avec repli
        sur l'estimateur marginal partagé (``_impact_scores``) si fANOVA
        échoue ou ne rend aucun signal exploitable."""
        keys = param_keys or []
        max_mod = max((len(self.param_space.get(k) or [None]) for k in keys),
                      default=1)
        if len(screen_results) < self._MIN_SCREEN_PER_PARAM * max(len(keys), max_mod):
            return None
        import optuna
        own_impacts, best_value_by_param = self._impact_scores(screen_results, keys)
        optuna_impacts = self._optuna_param_importances(study, keys)
        impacts = optuna_impacts if optuna_impacts is not None else own_impacts

        frozen, kept_keys = self._freeze_params(
            impacts, best_value_by_param, keys,
            screen_results=screen_results)
        if not frozen:
            return None
        fixed_indices: Dict[str, int] = {}
        for k, v in frozen.items():
            try:
                fixed_indices[k] = self.param_space[k].index(v)
            except ValueError:
                continue  # valeur introuvable (ne devrait pas arriver) -> ne fige pas ce param
        if not fixed_indices:
            return None

        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
            study.sampler = optuna.samplers.PartialFixedSampler(fixed_indices, study.sampler)

        card_before = math.prod(len(self.param_space[k]) for k in keys)
        card_after  = math.prod(1 if k in fixed_indices else len(self.param_space[k])
                                for k in keys)
        logger.info(
            f"[ParamSearchOptim] {self.strategy_name} (bayésien/TPE) : "
            f"{len(fixed_indices)}/{len(keys)} paramètres gelés "
            f"(dépistage {len(screen_results)} essais, dans le budget, sampler figé) — "
            f"cardinalité {card_before:,} -> {card_after:,}"
        )
        return {
            "frozen_params": {k: frozen[k] for k in fixed_indices},
            "kept_params": kept_keys,
            "n_screen": len(screen_results),
            "cardinality_before": card_before, "cardinality_after": card_after,
        }

    def _tell_progress(self, done: int, n_trials: int, best_score: float,
                       r: Optional[dict]) -> None:
        """Un essai de plus, abouti ou non — la barre suit le budget consommé."""
        if not self.progress_callback:
            return
        self.progress_callback(done, n_trials, best_score, {} if r is None else {
            "oos_pnl":     r["oos_pnl"],
            "oos_sharpe":  r["oos_sharpe"],
            "final_score": r.get("final_score", -999.0),
            "overfit":     r.get("overfit", 1.0),
        })

    def _optuna_sequential(self, study, n_trials: int, early_stop_patience: int,
                           freeze_at: Optional[int] = None,
                           param_keys: Optional[List[str]] = None) -> Optional[dict]:
        """Ask/tell séquentiel in-process — garde le cache d'entraînement chaud
        (même process) d'un trial à l'autre."""
        best_score = -999.0
        no_improve = 0
        reduction = None
        own_results: List[dict] = []
        for i in range(n_trials):
            trial  = study.ask()
            params = self._params_from_trial(trial)
            r      = self._eval(params)
            score  = self._penalized_score(r)
            r["final_score"] = score
            self.results.append(r)
            own_results.append(r)
            study.tell(trial, score if math.isfinite(score) else -999.0)

            if score > best_score:
                best_score = score
                no_improve = 0
            else:
                no_improve += 1

            self._tell_progress(i + 1, n_trials, best_score, r)

            if freeze_at is not None and reduction is None and len(own_results) >= freeze_at:
                reduction = self._optuna_apply_freeze(study, own_results, param_keys)

            if self._should_early_stop(no_improve, early_stop_patience, i + 1, n_trials):
                logger.info(f"[Bayesian/TPE] Early stop à trial {i+1}/{n_trials}")
                break
        return reduction

    def _optuna_parallel(self, study, n_trials: int, safe_jobs: int,
                         early_stop_patience: int,
                         freeze_at: Optional[int] = None,
                         param_keys: Optional[List[str]] = None) -> Optional[dict]:
        """Ask/tell par lots sur un ProcessPool **persistant** (un seul pool pour
        toute la recherche → workers et cache de features/entraînement réutilisés
        entre les lots). Repli séquentiel si le pool casse (OOM worker)."""
        import concurrent.futures
        try:
            from concurrent.futures.process import BrokenProcessPool
        except ImportError:
            BrokenProcessPool = Exception  # type: ignore
        import multiprocessing as _mp

        cfg_yaml, df_is_ipc, df_oos_ipc, init_args = self._serialize_pool_inputs()
        ctx = _mp.get_context("spawn")
        done = 0
        best_score = -999.0
        no_improve = 0
        _worker_timeout = 300
        reduction = None
        own_results: List[dict] = []
        # `done` compte les essais RENDUS à Optuna, échecs compris : c'est lui
        # qui borne la boucle. La progression rapportait `len(self.results)`,
        # qui n'agrège que les succès — un lot de trials en échec faisait donc
        # geler la barre avant la fin (« 200/400 ») alors que la recherche
        # allait bien à son terme. Deux quantités, un seul libellé.
        echecs = 0

        try:
            with concurrent.futures.ProcessPoolExecutor(
                    max_workers=safe_jobs, mp_context=ctx,
                    initializer=_worker_init, initargs=init_args) as exe:
                while done < n_trials:
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        raise InterruptedError("annulé")
                    k = min(safe_jobs, n_trials - done)
                    trials = [study.ask() for _ in range(k)]
                    params = [self._params_from_trial(t) for t in trials]
                    args   = [self._worker_args(p, cfg_yaml, df_is_ipc, df_oos_ipc)
                              for p in params]
                    futs   = {exe.submit(_eval_worker, a): i for i, a in enumerate(args)}
                    for fut in concurrent.futures.as_completed(futs):
                        i = futs[fut]
                        try:
                            r = fut.result(timeout=_worker_timeout)
                        except Exception as _e:
                            logger.warning(f"[Bayesian/TPE] worker KO : {_e}")
                            study.tell(trials[i], -999.0)
                            done += 1
                            echecs += 1
                            self._tell_progress(done, n_trials, best_score, None)
                            continue
                        if "error" in r:
                            logger.warning("[Bayesian/TPE] worker erreur : %s", r["error"])
                            study.tell(trials[i], -999.0)
                            done += 1
                            echecs += 1
                            self._tell_progress(done, n_trials, best_score, None)
                            continue
                        score = self._penalized_score(r)
                        r["final_score"] = score
                        self.results.append(r)
                        own_results.append(r)
                        study.tell(trials[i], score if math.isfinite(score) else -999.0)
                        if score > best_score:
                            best_score = score
                            no_improve = 0
                        else:
                            no_improve += 1
                        done += 1
                        self._tell_progress(done, n_trials, best_score, r)
                    if freeze_at is not None and reduction is None and len(own_results) >= freeze_at:
                        reduction = self._optuna_apply_freeze(study, own_results, param_keys)
                    if self._should_early_stop(no_improve, early_stop_patience, done, n_trials):
                        logger.info(f"[Bayesian/TPE] Early stop à {done}/{n_trials}")
                        break
                if echecs:
                    logger.warning(
                        "[Bayesian/TPE] %d/%d essai(s) en échec — le score retenu "
                        "ne repose que sur %d évaluation(s)",
                        echecs, done, done - echecs)
        except BrokenProcessPool as _bp:
            logger.error("[Bayesian/TPE] pool brisé (OOM worker ?) — repli séquentiel "
                         "pour les trials restants : %s", _bp)
            remaining_freeze_at = None
            if freeze_at is not None and reduction is None:
                remaining_freeze_at = freeze_at - len(own_results)
                if remaining_freeze_at <= 0:
                    remaining_freeze_at = None
            seq_reduction = self._optuna_sequential(
                study, n_trials - len(self.results), early_stop_patience,
                freeze_at=remaining_freeze_at, param_keys=param_keys)
            reduction = reduction or seq_reduction
        return reduction

    def _bayesian_search_legacy(self, n_trials: int = 40, n_jobs: int = 1,
                                early_stop_patience: int = 0,
                                param_search_optim: bool = True) -> dict:
        """Heuristique historique : exploration aléatoire (1/3, EN BUDGET) puis
        raffinement local (perturbation ±1 cran autour du meilleur). Repli quand
        Optuna est absent (ex. environnement de production sans la dépendance).
        La phase d'exploration sert aussi de dépistage Param Search Optim :
        aucun essai ni pool de process supplémentaire."""
        n_explore = max(8, n_trials // 3)
        n_exploit = n_trials - n_explore
        param_keys = list(self.param_space.keys())
        do_reduce = param_search_optim and self._should_reduce_space(n_trials)

        base = len(self.results)  # essais de CET appel seulement (réutilisation d'instance)
        with self._open_pool(n_jobs) as pool:
            self._run_parallel(n_explore, n_trials, trial_offset=0,
                               sampler=lambda: self._with_hp(
                                   {k: random.choice(v) for k, v in self.param_space.items()}),
                               pool=pool)

        reduction = None
        if do_reduce and len(self.results) > base:
            reduction = self._freeze_from_results(self.results[base:], param_keys)

        # Phase exploitation : gaussian autour du meilleur (séquentielle — le
        # pool ci-dessus est déjà refermé, pas de 2e pool ouvert ici).
        if self.results and n_exploit > 0:
            best = max(self.results, key=self._penalized_score)
            no_improve = 0
            best_score = self._penalized_score(best)

            for i in range(n_exploit):
                trial_idx = n_explore + i
                params = self._with_hp(self._perturb(best["params"]))
                r = self._eval(params)
                score = self._penalized_score(r)
                r["final_score"] = score
                self.results.append(r)

                if score > best_score:
                    best_score = score
                    best = r
                    no_improve = 0
                else:
                    no_improve += 1

                if self.progress_callback:
                    self.progress_callback(trial_idx + 1, n_trials, best_score, {
                        "oos_pnl":     r["oos_pnl"],
                        "oos_sharpe":  r["oos_sharpe"],
                        "final_score": score,
                        "overfit":     r.get("overfit", 1.0),
                    })
                if self._should_early_stop(no_improve, early_stop_patience, i + 1, n_exploit):
                    logger.info(f"[Bayesian] Early stop exploit trial {i+1}/{n_exploit}")
                    break

        result = self._best_result()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    # ── Helpers ProcessPool (partagés random/bayesian) ───────────────────────
