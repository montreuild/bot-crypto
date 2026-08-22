"""AutoOptimizer — optimisation asynchrone multi-timeframe par jobs (strategy@tf@symbol)."""
import importlib
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional

import polars as pl

from app.core.is_oos import (
    HOLDOUT_FRACTION_DEFAULT,
    split_is_oos,
)
from app.engine import opt_gate
from app.engine.engine import BaseStrategyML
from app.engine.opt_baseline import (  # noqa: F401
    _cfg_avec_params,
    _run_baseline,
    _slices_for,
)

# Ré-exports : le registre de jobs est atteint via ce module par l'API et les tests.
from app.engine.opt_jobs import (  # noqa: F401
    _cancel_flags,
    _job_id,
    _job_semaphore,
    _jobs,
    _jobs_lock,
    _update_job,
    any_optimization_running,
    cancel_job,
    delete_job,
    get_all_jobs,
    get_job,
)
from app.engine.opt_memory import (  # noqa: F401
    _acquire_mem_slot,
    _estimate_job_bytes,
    _release_mem_slot,
    _snapshot_mem_budget,
)
from app.engine.opt_scoring import fmt_metric
from app.engine.optimizer_search import (
    PARAM_SPACES,
    RECOMMENDED_LIMIT,
    STRATEGY_TIMEFRAMES,
    StrategyOptimizer,
    apply_best_params,
    record_optimizer_audit,
)

logger = logging.getLogger(__name__)


def _is_ml_strategy(name: str) -> bool:
    """Retourne True si la stratégie hérite de BaseStrategyML (détection structurelle)."""
    try:
        mod = importlib.import_module(f"app.strategies.{name}")
        return issubclass(mod.Strategy, BaseStrategyML)
    except Exception:
        return False


def _save_ml_model_post_opt(strategy_name: str, best_params: dict,
                             df_full: pl.DataFrame, timeframe: str,
                             df_is: Optional[pl.DataFrame] = None,
                             train_mode: str = "full") -> None:
    """
    Après optimisation ML : entraîne un modèle final avec les meilleurs paramètres
    et le persiste en .pkl. Appelé dans un thread daemon — ne bloque pas le
    reporting du job.

    S4-03 / O-10 — ``train_mode="full"`` entraîne sur IS+OOS (le modèle
    livré a vu la fenêtre de sélection). Défaut désormais ``is_only``.
    donc il a "vu" les données OOS que l'optimiseur a utilisées pour choisir
    les meilleurs params. Le score OOS rapporté par l'optimiseur reste
    honnête (calculé AVANT ce ré-entraînement final, sur un modèle qui
    n'avait vu que l'IS pendant la recherche de params) — mais le modèle
    *déployé en production* a un edge futur potentiellement surestimé par
    rapport à ce score, puisqu'il a ensuite été affiné sur l'OOS. Ce choix
    est délibéré : maximiser la donnée disponible pour le modèle réellement
    tradé, au prix d'un léger optimisme. Alternative disponible via
    ``optimizer.ml_final_train_mode: "is_only"`` (config.yaml) : entraîne
    uniquement sur l'IS (``df_is``), cohérent avec le score OOS rapporté
    mais avec moins de données pour le modèle final.
    """
    import os
    try:
        if train_mode == "is_only" and df_is is not None:
            df_full = df_is
            logger.info(
                f"[AutoOpt] ML post-opt {strategy_name}/{timeframe} : entraînement "
                f"IS-only (optimizer.ml_final_train_mode=is_only) — cohérent avec "
                f"le score OOS rapporté, mais moins de données que le mode 'full'."
            )
        # df_full peut être brut (sans colonnes _pre_*) — l'engine précompute
        # à chaque run(), mais ici on entraîne directement.
        from app.core.indicators import precompute_df
        if "_pre_atr14" not in df_full.columns:
            df_full = precompute_df(df_full)

        mod   = importlib.import_module(f"app.strategies.{strategy_name}")
        strat = mod.Strategy()

        # Contrat unifié BaseStrategyML.fit(df, params) : chaque stratégie ML
        # route en interne vers sa propre méthode d'entraînement (_train, _fit…)
        # et détecte le timeframe depuis df. On ne suppose donc PAS l'existence
        # d'une méthode privée _fit (toutes ne l'ont pas — d'où l'erreur
        # « 'Strategy' object has no attribute '_fit' » pour v10_retrained/v11).
        strat.fit(df_full, params={strategy_name: best_params})

        trained: set[str] = set(getattr(strat, "_trained_tfs", set()) or ())
        if timeframe not in trained and not trained:
            logger.warning(f"[AutoOpt] ML post-opt : entraînement KO pour {strategy_name}/{timeframe}")
            return
        path = os.path.join(strat.model_dir, f"{strategy_name}_{timeframe}")
        os.makedirs(strat.model_dir, exist_ok=True)
        strat.save_model(path)
        logger.info(f"[AutoOpt] Modèle ML sauvegardé après optimisation → {path}")
    except Exception as e:
        logger.warning(f"[AutoOpt] Sauvegarde modèle ML post-opt KO ({strategy_name}/{timeframe}): {e}")

class AutoOptimizer:
    """
    Optimiseur multi-stratégies × multi-timeframes avec jobs asynchrones.

    Paramètres :
      cfg             : config.yaml chargé en dict
      n_trials        : nombre de trials par (strategy, tf)
      method          : "random" | "bayesian" | "grid"
      param_search_optim : dépistage + gel des paramètres à faible impact
                        avant la recherche (activé par défaut, orthogonal
                        à ``method`` — pas un mode en plus)
      config_path     : chemin vers config.yaml
      on_apply_callback : callback(strategy_name, params) après application
    """

    def __init__(self, cfg: dict, n_trials: int = 40,
                 method: str = "bayesian",
                 config_path: str = "config.yaml",
                 on_apply_callback=None,
                 notifier=None,
                 n_jobs: int = 1,
                 early_stop_patience: int = 0,
                 ml_tune_hp: bool = False,
                 param_search_optim: bool = True):
        self.cfg               = cfg
        self.n_trials          = n_trials
        self.method            = method
        self.config_path       = config_path
        self.on_apply_callback = on_apply_callback
        self._notifier         = notifier
        self.n_jobs            = n_jobs
        self.early_stop_patience = early_stop_patience
        # Param Search Optim (activé par défaut) : dépistage EN BUDGET (les
        # premiers essais de la recherche elle-même, même fenêtre, même pool)
        # puis gel des paramètres à faible impact — pas un 4e "method", une
        # option orthogonale appliquée par celui choisi ci-dessus (cf.
        # StrategyOptimizer._freeze_from_results / _optuna_apply_freeze).
        self.param_search_optim = param_search_optim
        # #6 : optimisation ML two-phase (grille externe sur les hyperparamètres
        # d'entraînement × recherche interne sur les seuils). Opt-in : coûteux
        # (coût × nombre de combos HP). Sans effet sur les stratégies non-ML ou
        # celles qui n'exposent pas d'hyperparamètres d'entraînement réglables.
        self.ml_tune_hp        = ml_tune_hp

    # ── Lancement asynchrone ──────────────────────────────────────────────
    def start_async(self, df_map: Dict[str, pl.DataFrame], symbol: str,
                    strategies: List[str] | None = None,
                    timeframes: List[str] | None = None,
                    auto_apply: bool = False) -> tuple[list[str], list[dict[str, Any]]]:
        """
        Lance l'optimisation en arrière-plan pour chaque (strategy, tf).

        df_map  : { "1h": df_1h, "5m": df_5m, ... } — données par TF
        symbol  : paire représentative (ex: "BTC/USDC")
        strategies : liste de stratégies à optimiser (None = toutes dans PARAM_SPACES)
        timeframes : liste de TFs à optimiser (None = TFs issus de cfg)
        """
        strats = strategies or list(PARAM_SPACES.keys())
        tfs    = timeframes or self.cfg["trading"].get(
            "timeframes", [self.cfg["trading"].get("timeframe", "1h")]
        )
        job_ids: list[str] = []
        skipped: list[dict[str, Any]] = []

        # Mesure la RAM dispo pour ce lot → budget d'admission mémoire des jobs.
        _snapshot_mem_budget()
        holdout_fraction = float((self.cfg.get("optimizer") or {}).get(
            "holdout_fraction", HOLDOUT_FRACTION_DEFAULT))

        for tf in tfs:
            df = df_map.get(tf)
            n_available = len(df) if df is not None else 0

            # Le découpage (IS / sélection / holdout) est décidé par stratégie
            # dans `_slices_for` : la faisabilité du holdout dépend du
            # `min_bars` de chacune, pas seulement de la longueur de la série.
            for name in strats:
                # Pas d'espace de paramètres (ou espace vide) → rien à optimiser.
                # On évite de lancer un job qui tournerait dans le vide (baseline +
                # trials qui ne font qu'échantillonner un dict vide).
                if not PARAM_SPACES.get(name):
                    skipped.append({"strategy": name, "timeframe": tf,
                                    "reason": "aucun paramètre à optimiser (espace de recherche vide)"})
                    continue

                # Vérifier si les données sont suffisantes pour cette stratégie
                try:
                    mod = importlib.import_module(f"app.strategies.{name}")
                    min_bars = mod.Strategy().min_bars_required()
                except Exception:
                    min_bars = 220  # fallback conservateur

                min_total = math.ceil(min_bars / 0.35)  # OOS (35%) doit avoir min_bars bougies
                if n_available < min_total:
                    reason = (
                        f"bougies insuffisantes — {n_available} disponibles, "
                        f"{min_total} requises pour '{name}' sur {tf} "
                        f"(indicateurs requièrent {min_bars} bougies min dans la plage OOS)"
                    )
                    logger.warning(f"[AutoOpt] TF={tf} ignoré pour '{name}' — {reason}")
                    skipped.append({"strategy": name, "timeframe": tf, "reason": reason})
                    continue

                # TF non recommandé → avertissement uniquement, pas de blocage
                recommended_tfs = STRATEGY_TIMEFRAMES.get(name, list(RECOMMENDED_LIMIT.keys()))
                is_recommended  = tf in recommended_tfs

                s_is, s_oos, s_split, df_recherche, df_holdout, df_gate = \
                    _slices_for(df, name, tf, min_bars, holdout_fraction)

                jid = _job_id(name, tf, symbol)
                cancel_event = threading.Event()
                with _jobs_lock:
                    _cancel_flags[jid] = cancel_event
                _update_job(jid,
                    status="running", strategy=name, timeframe=tf, symbol=symbol,
                    method=self.method, n_trials=self.n_trials,
                    progress=0, best_score=-999, trials=[],
                    result=None, error=None,
                    started_at=time.time(), finished_at=None,
                    baseline=_run_baseline(name, self.cfg, df_gate, symbol, timeframe=tf),
                    holdout_bars=(len(df_holdout) if df_holdout is not None else 0),
                    is_recommended=is_recommended,
                    recommended_tfs=recommended_tfs,
                )
                t = threading.Thread(
                    target=self._run_one_job,
                    args=(jid, name, tf, s_is, s_oos, symbol, auto_apply,
                          df_recherche, s_split, df_holdout),
                    daemon=True,
                )
                t.start()
                job_ids.append(jid)
                rec_str = ("" if is_recommended else
                          f" [TF non recommandé pour {name}, recommandé: {', '.join(recommended_tfs)}]")
                logger.info(f"[AutoOpt] Job lancé : {jid} ({self.method}, {self.n_trials} trials){rec_str}")

        return job_ids, skipped

    def _run_one_job(self, job_id: str, strategy_name: str, timeframe: str,
                     df_is: pl.DataFrame, df_oos: pl.DataFrame,
                     symbol: str, auto_apply: bool,
                     df_recherche: pl.DataFrame | None = None, split: int | None = None,
                     df_holdout: pl.DataFrame | None = None):
        """Un job d'optimisation, de l'attente du créneau au verdict.

        DETTE-04b — 355 lignes découpées en étapes nommées. Les deux portillons
        (CPU puis mémoire) et le `finally` qui les rend restent ici : ce sont
        les seules ressources partagées entre jobs.
        """
        cancel_event = _cancel_flags.get(job_id)
        if not self._attendre_creneau(job_id, cancel_event):
            return

        mem_need = _estimate_job_bytes(df_is, df_oos, _is_ml_strategy(strategy_name))
        mem_held = False
        try:
            mem_held = _acquire_mem_slot(mem_need, cancel_event)
            if cancel_event and cancel_event.is_set():
                logger.info(f"[AutoOpt] {job_id} annulé pendant l'attente mémoire")
                _update_job(job_id, status="cancelled", finished_at=time.time())
                return

            result, n_trials_eff = self._chercher(
                job_id, strategy_name, timeframe, df_is, df_oos, symbol,
                df_recherche, split, cancel_event)

            mesure = opt_gate.mesurer_oos(
                result, df_holdout,
                lambda df: self._mesurer_holdout(job_id, strategy_name, timeframe,
                                                 symbol, result, df))
            _update_job(job_id, gate_source=mesure.source)

            applied = self._decider(job_id, strategy_name, timeframe, symbol,
                                    auto_apply, result, mesure, df_holdout,
                                    df_recherche, n_trials_eff)

            # Stratégies ML : modèle final sur les meilleurs params. O-10 —
            # défaut `is_only`, le modèle livré est celui qui a été évalué.
            if result.get("best_params") and _is_ml_strategy(strategy_name) \
                    and df_recherche is not None:
                _save_ml_model_post_opt(
                    strategy_name, result["best_params"], df_recherche, timeframe,
                    df_is=df_is,
                    train_mode=self.cfg.get("optimizer", {}).get(
                        "ml_final_train_mode", "is_only"))

            self._finaliser(job_id, strategy_name, timeframe, result, applied)

        except InterruptedError:
            logger.info(f"[AutoOpt] {job_id} annulé par l'utilisateur")
            _update_job(job_id, status="cancelled", finished_at=time.time())
        except Exception as e:
            logger.error(f"[AutoOpt] {job_id} KO : {e}", exc_info=True)
            _update_job(job_id, status="error", error=str(e), finished_at=time.time())
        finally:
            if mem_held:
                _release_mem_slot(mem_need)
            _job_semaphore.release()

    def _attendre_creneau(self, job_id: str, cancel_event) -> bool:
        """Cap CPU global. `False` si l'utilisateur a annulé pendant l'attente.

        Le job reste `queued` et annulable tant que le sémaphore n'est pas pris.
        """
        _update_job(job_id, status="queued")
        while not _job_semaphore.acquire(timeout=1.0):
            if cancel_event and cancel_event.is_set():
                logger.info(f"[AutoOpt] {job_id} annulé pendant l'attente du créneau")
                _update_job(job_id, status="cancelled", finished_at=time.time())
                return False
        _update_job(job_id, status="running")
        return True

    def _progression(self, job_id: str, cancel_event):
        """Callback de progression — seul endroit d'où l'annulation interrompt
        la recherche, en levant depuis le thread qui l'exécute."""
        trials_log: List[dict] = []

        def on_progress(trial: int, total: int, best_score: float, latest: dict):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError(f"Job {job_id} annulé par l'utilisateur")
            trials_log.append({
                "trial":       trial,
                "oos_pnl":     latest.get("oos_pnl", 0),
                "oos_sharpe":  latest.get("oos_sharpe", 0),
                "final_score": latest.get("final_score", 0),
                "overfit":     latest.get("overfit", 0),
            })
            _update_job(job_id,
                progress=round(trial / total * 100),
                trials_done=trial,
                best_score=round(best_score, 4),
                trials=trials_log[-50:],
            )

        return on_progress

    def _chercher(self, job_id: str, strategy_name: str, timeframe: str,
                  df_is, df_oos, symbol: str, df_recherche, split,
                  cancel_event) -> tuple:
        """Lance la recherche et rend `(result, n_trials_effectifs)`."""
        from app.engine.opt_budget import effective_n_trials, format_budget

        opt = StrategyOptimizer(
            strategy_name=strategy_name, cfg=self.cfg,
            df_is=df_is, df_oos=df_oos, symbol=symbol,
            progress_callback=self._progression(job_id, cancel_event),
            df_full=df_recherche, split=split, timeframe=timeframe,
            cancel_event=cancel_event,
        )

        # Budget proportionné à l'espace de CETTE stratégie : un espace à 3
        # paramètres et un espace à 58 ne se couvrent pas avec le même budget.
        n_trials_eff, budget = effective_n_trials(
            opt.param_space, self.n_trials, self.cfg)
        if n_trials_eff != self.n_trials:
            logger.info(format_budget(strategy_name, budget))
            _update_job(job_id, n_trials=n_trials_eff, n_trials_budget=budget)

        # #6 : two-phase pour les stratégies ML exposant des hyperparamètres
        # d'entraînement réglables (et si activé). Sinon phase unique.
        ml_hp_space = {}
        if self.ml_tune_hp and _is_ml_strategy(strategy_name):
            from app.engine.optimizer_search import ml_hp_space_for
            ml_hp_space = ml_hp_space_for(strategy_name)

        if ml_hp_space:
            result = opt.optimize_two_phase(
                self.method, n_trials_eff, self.n_jobs, ml_hp_space,
                early_stop_patience=self.early_stop_patience,
                param_search_optim=self.param_search_optim)
        elif self.method == "bayesian":
            result = opt.bayesian_search(
                n_trials_eff, n_jobs=self.n_jobs,
                early_stop_patience=self.early_stop_patience,
                param_search_optim=self.param_search_optim)
        elif self.method == "grid":
            result = opt.grid_search(n_jobs=self.n_jobs,
                                     param_search_optim=self.param_search_optim)
        else:
            result = opt.random_search(
                n_trials_eff, n_jobs=self.n_jobs,
                early_stop_patience=self.early_stop_patience,
                param_search_optim=self.param_search_optim)
        return result, n_trials_eff

    def _mesurer_holdout(self, job_id: str, strategy_name: str, timeframe: str,
                         symbol: str, result: dict, df_holdout) -> dict:
        """Backtest du candidat sur la tranche jamais vue (#5)."""
        h = _run_baseline(
            strategy_name,
            _cfg_avec_params(self.cfg, strategy_name, result["best_params"]),
            df_holdout, symbol, timeframe=timeframe)
        if not h:
            logger.warning(f"[AutoOpt] {job_id} : backtest holdout KO — gate "
                           f"mesuré sur la tranche de sélection")
            return {}
        _update_job(job_id, holdout=h, gate_source="holdout")
        logger.info(
            f"[AutoOpt] {job_id} : holdout ({len(df_holdout)} barres) — "
            f"PnL={h.get('pnl', 0):+.2f} WR={h.get('wr', 0):.1f}% "
            f"Sharpe={fmt_metric(h.get('sharpe'))} sur {h.get('trades', 0)} trades "
            f"(sélection : PnL={result.get('best_oos_pnl', 0):+.2f})")
        return h

    def _decider(self, job_id: str, strategy_name: str, timeframe: str,
                 symbol: str, auto_apply: bool, result: dict,
                 mesure, df_holdout, df_recherche, n_trials_eff: int) -> bool:
        """Applique, ou trace pour l'audit. Jamais les deux.

        « Non appliqué = non utilisé » : écrire dans `optimizer_results` rendrait
        le paramétrage refusé actif via la précédence de
        `resolve_strategy_params`.
        """
        # Le baseline a été mesuré sur la MÊME tranche que le candidat
        # (cf. start_async) : c'est ce qui rend la comparaison honnête.
        baseline = (get_job(job_id) or {}).get("baseline") or {}

        gate_ok = opt_gate.gates_passes(
            job_id, self.cfg, auto_apply=auto_apply, result=result,
            df_holdout=df_holdout, m=mesure, baseline=baseline,
            n_trials_eff=n_trials_eff, strategy_name=strategy_name,
            timeframe=timeframe, symbol=symbol, df_recherche=df_recherche,
            noter_consistance=lambda c: _update_job(job_id, wf_consistency=c))

        ecart = (f"OOS PnL={mesure.pnl:+.2f} vs baseline="
                 f"{baseline.get('pnl', float('-inf')):+.2f}, "
                 f"WR={mesure.wr:.1f}% vs {baseline.get('wr', 0):.1f}%, "
                 f"Sharpe={fmt_metric(mesure.sharpe)} vs "
                 f"{fmt_metric(baseline.get('sharpe', 0))}")

        if gate_ok:
            # Config par symbole : `optimizer_results[tf][symbol]`, chaque paire
            # a la sienne et elles coexistent.
            applied = apply_best_params(
                strategy_name, result["best_params"], self.config_path,
                timeframe=timeframe, oos_score=result.get("best_oos_score", 0.0),
                symbol=symbol)
            if applied and self.on_apply_callback:
                try:
                    self.on_apply_callback(strategy_name, result["best_params"])
                except Exception as _cb_err:
                    logger.warning(f"[AutoOpt] callback KO: {_cb_err}")
            if not applied:
                logger.info(f"[AutoOpt] {job_id} : résultat non appliqué car pas "
                            f"meilleur que le baseline ({ecart})")
            return applied

        if not result.get("best_params"):
            return False

        # La trace passe AVANT le log : le job entier était perdu quand le
        # formatage d'une métrique non mesurable levait.
        record_optimizer_audit(
            strategy_name, timeframe, result["best_params"],
            result.get("best_oos_score", 0.0), self.config_path)
        if auto_apply:
            logger.info(f"[AutoOpt] {job_id} : application refusée (gate qualité "
                        f"ou walk-forward) — {ecart}")
        return False

    def _finaliser(self, job_id: str, strategy_name: str, timeframe: str,
                   result: dict, applied: bool) -> None:
        """Statut terminal, log de bilan, notification.

        Un échec global de la recherche (tous les workers tombés sur un OOM
        LightGBM, par exemple) marque `error` et non `done` : le board doit
        refléter l'état réel.
        """
        job_failed = bool(result.get("failed")) or (
            result.get("error") and not result.get("best_params"))
        _update_job(job_id,
            status="error" if job_failed else "done",
            progress=100,
            result=result,
            applied=applied,
            error=result.get("error") if job_failed else None,
            finished_at=time.time(),
        )
        elapsed = time.time() - (get_job(job_id) or {}).get("started_at", time.time())
        logger.info(
            f"[AutoOpt] {job_id} terminé en {elapsed:.0f}s "
            f"| OOS score={result.get('best_oos_score', 0):.4f} "
            f"| PnL={result.get('best_oos_pnl', 0):+.2f} "
            f"| Applied={applied}")
        if self._notifier:
            try:
                self._notifier.notify_optimization_done(
                    strategy=f"{strategy_name}@{timeframe}",
                    score_before=result.get("baseline_score", 0),
                    score_after=result.get("best_oos_score", 0),
                    applied=applied,
                )
            except Exception as _ne:
                logger.debug(f"[AutoOpt] notify KO : {_ne}")

    # ── Exécution séquentielle (une stratégie à la fois) ──────────────────
    def optimize_sequential(self, df_map: Dict[str, pl.DataFrame], symbol: str,
                            strategies: List[str] | None = None,
                            timeframes: List[str] | None = None,
                            auto_apply: bool = False,
                            on_job_done=None) -> List[str]:
        """Optimise (strategy × tf) **une à une**, dans le thread courant.

        Contrairement à ``start_async`` (qui lance N threads bornés par un
        sémaphore), cette variante exécute chaque job de façon strictement
        séquentielle — un seul job tourne à un instant donné. C'est l'API
        utilisée par le script ``optimize_runner.py`` : déterministe, douce pour
        la machine, et réutilisant toute la logique d'un job (baseline,
        sauvegarde YAML, auto-apply vs baseline, persistance du modèle ML).

        L'ordre est **par stratégie d'abord** (toutes ses TFs), puis stratégie
        suivante — pour traiter les stratégies « une à une ».

        ``on_job_done(job_id, job_dict)`` est appelé après chaque job (reporting).
        Retourne la liste des job_ids exécutés.
        """
        strats = strategies or list(PARAM_SPACES.keys())
        tfs    = timeframes or self.cfg["trading"].get(
            "timeframes", [self.cfg["trading"].get("timeframe", "1h")]
        )
        done_ids: List[str] = []

        # Cohérence avec start_async (sans effet réel ici : les jobs séquentiels
        # ne se chevauchent jamais, donc le portillon admet toujours d'emblée).
        _snapshot_mem_budget()

        for name in strats:
            if not PARAM_SPACES.get(name):
                logger.info(f"[AutoOpt] {name} ignoré : aucun paramètre à optimiser")
                continue
            try:
                mod = importlib.import_module(f"app.strategies.{name}")
                min_bars = mod.Strategy().min_bars_required()
            except Exception:
                min_bars = 220

            for tf in tfs:
                df = df_map.get(tf)
                n_available = len(df) if df is not None else 0
                min_total = math.ceil(min_bars / 0.35)
                if n_available < min_total:
                    reason = (f"{n_available} bougies < {min_total} requises "
                              f"(min_bars={min_bars} sur la tranche OOS)")
                    logger.warning(f"[AutoOpt] {name}@{tf} ignoré : {reason}")
                    # Remonté visiblement à l'appelant (le runner l'affiche au lieu
                    # de le noyer dans les logs fichier).
                    if on_job_done:
                        try:
                            on_job_done(_job_id(name, tf, symbol), {
                                "status": "skipped", "strategy": name,
                                "timeframe": tf, "symbol": symbol, "error": reason,
                            })
                        except Exception as _cb:
                            logger.debug(f"[AutoOpt] on_job_done(skip) KO : {_cb}")
                    continue

                try:
                    _mod = importlib.import_module(f"app.strategies.{name}")
                    _min_bars = _mod.Strategy().min_bars_required()
                except Exception:
                    _min_bars = 220
                df_is, df_oos, split, df_recherche, df_holdout, df_gate = _slices_for(
                    df, name, tf, _min_bars,
                    float((self.cfg.get("optimizer") or {}).get(
                        "holdout_fraction", HOLDOUT_FRACTION_DEFAULT)))

                recommended_tfs = STRATEGY_TIMEFRAMES.get(name, list(RECOMMENDED_LIMIT.keys()))
                jid = _job_id(name, tf, symbol)
                cancel_event = threading.Event()
                with _jobs_lock:
                    _cancel_flags[jid] = cancel_event
                _update_job(jid,
                    status="running", strategy=name, timeframe=tf, symbol=symbol,
                    method=self.method, n_trials=self.n_trials,
                    progress=0, best_score=-999, trials=[], result=None, error=None,
                    started_at=time.time(), finished_at=None,
                    baseline=_run_baseline(name, self.cfg, df_gate, symbol, timeframe=tf),
                    holdout_bars=(len(df_holdout) if df_holdout is not None else 0),
                    is_recommended=(tf in recommended_tfs),
                    recommended_tfs=recommended_tfs,
                )
                # Exécution synchrone du job (réutilise toute la logique async).
                self._run_one_job(jid, name, tf, df_is, df_oos, symbol,
                                  auto_apply, df_recherche, split, df_holdout)
                done_ids.append(jid)
                if on_job_done:
                    try:
                        on_job_done(jid, get_job(jid))
                    except Exception as _cb:
                        logger.debug(f"[AutoOpt] on_job_done KO : {_cb}")

        return done_ids

    # ── Exécution synchrone ───────────────────────────────────────────────
    def optimize_all(self, df_map: Dict[str, pl.DataFrame], symbol: str,
                     strategies: List[str] | None = None,
                     timeframes: List[str] | None = None) -> Dict[str, dict]:
        """Exécution synchrone bloquante. Préférer start_async() pour l'API."""
        strats = strategies or list(PARAM_SPACES.keys())
        tfs    = timeframes or self.cfg["trading"].get(
            "timeframes", [self.cfg["trading"].get("timeframe", "1h")]
        )
        results = {}

        for tf in tfs:
            df = df_map.get(tf)
            if df is None or len(df) < 300:
                continue
            from app.core.is_oos import default_purge_embargo
            _p, _e = default_purge_embargo(len(df))
            df_is, df_oos, split = split_is_oos(df, purge_bars=_p, embargo_bars=_e)

            for name in strats:
                if name not in PARAM_SPACES:
                    continue
                supported_tfs = STRATEGY_TIMEFRAMES.get(name, list(RECOMMENDED_LIMIT.keys()))
                if tf not in supported_tfs:
                    continue
                key = f"{name}@{tf}"
                try:
                    opt = StrategyOptimizer(name, self.cfg, df_is, df_oos,
                                            symbol=symbol, df_full=df, split=split,
                                            timeframe=tf)
                    if self.method == "bayesian":
                        results[key] = opt.bayesian_search(
                            self.n_trials, n_jobs=self.n_jobs,
                            param_search_optim=self.param_search_optim)
                    elif self.method == "grid":
                        results[key] = opt.grid_search(
                            n_jobs=self.n_jobs, param_search_optim=self.param_search_optim)
                    else:
                        results[key] = opt.random_search(
                            self.n_trials, n_jobs=self.n_jobs,
                            param_search_optim=self.param_search_optim)
                except Exception as e:
                    results[key] = {"error": str(e)}
        return results

