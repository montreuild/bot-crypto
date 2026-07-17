"""AutoOptimizer — optimisation asynchrone multi-timeframe par jobs (strategy@tf@symbol)."""
import importlib
import logging
import math
import threading
import time
from typing import Dict, List, Optional

import polars as pl

from app.engine.engine   import Engine, BaseStrategyML
from app.engine.backtest import Backtester
from app.core.is_oos import split_is_oos
from app.engine.optimizer import (
    StrategyOptimizer, PARAM_SPACES, STRATEGY_TIMEFRAMES, RECOMMENDED_LIMIT,
    apply_best_params, record_optimizer_audit, get_active_strategies_per_tf
)
from app.engine.opt_workers import available_memory_bytes

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

    S4-03 — Data leakage PAR DESIGN en mode ``train_mode="full"`` (défaut,
    comportement historique inchangé) : le modèle est entraîné sur IS+OOS,
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

        trained = getattr(strat, "_trained_tfs", set())
        if timeframe not in trained and not trained:
            logger.warning(f"[AutoOpt] ML post-opt : entraînement KO pour {strategy_name}/{timeframe}")
            return
        path = os.path.join(strat.model_dir, f"{strategy_name}_{timeframe}.pkl")
        os.makedirs(strat.model_dir, exist_ok=True)
        strat.save_model(path)
        logger.info(f"[AutoOpt] Modèle ML sauvegardé après optimisation → {path}")
    except Exception as e:
        logger.warning(f"[AutoOpt] Sauvegarde modèle ML post-opt KO ({strategy_name}/{timeframe}): {e}")

# ════════════════════════════════════════════════════════════════════════════
#  État global des jobs (thread-safe)
# ════════════════════════════════════════════════════════════════════════════
_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()
_cancel_flags: Dict[str, threading.Event] = {}

# Borne le nombre de jobs d'optimisation exécutés *simultanément*, toutes sources
# confondues (auto-optimisation planifiée + API). start_async peut créer des
# centaines de threads (n_stratégies × n_TF) ; sans cette borne ils saturent le
# CPU/la mémoire du serveur pendant le live. Les threads en excès attendent
# (bloqués sur le sémaphore) au lieu de tourner tous en même temps.
def _max_concurrent_opt_jobs() -> int:
    import os
    cpu = os.cpu_count() or 2
    return max(1, cpu - 1)

_job_semaphore = threading.BoundedSemaphore(_max_concurrent_opt_jobs())


# ════════════════════════════════════════════════════════════════════════════
#  Portillon mémoire (admission control anti-OOM inter-jobs)
# ════════════════════════════════════════════════════════════════════════════
# Le sémaphore ci-dessus borne le nombre de jobs concurrents par le CPU (cpu-1),
# mais PAS par la mémoire. Or avec n_jobs=1 (défaut de l'UI), chaque job évalue
# ses trials IN-PROCESS : cpu-1 backtests ML walk-forward — qui réentraînent
# LightGBM en boucle sur de larges matrices de features — tournent alors
# simultanément dans le MÊME process. Sur de gros jeux de données le pic mémoire
# cumulé épuise la RAM → std::bad_alloc LightGBM / OOM → mort silencieuse du
# process (aucune traceback). Le cap ``mem_aware_max_workers`` existant ne couvre
# QUE le ProcessPool d'un job (chemin n_jobs>1), jamais cette concurrence
# inter-jobs.
#
# Ce portillon borne la mémoire CUMULÉE des jobs actifs : un job n'entre en
# exécution que si son empreinte estimée tient dans le budget restant (70 % de la
# RAM dispo, snapshotée par lot). Règle anti-blocage : un job seul (aucun autre
# n'occupe de mémoire) est toujours admis, même si son estimation dépasse le
# budget — au pire les jobs lourds se sérialisent un par un.
_MEM_BUDGET_FRACTION = 0.70
_mem_cond            = threading.Condition()
_mem_committed       = 0                 # octets réservés par les jobs en cours
_mem_budget: Optional[int] = None        # snapshot du budget (None = non calculé)


def _snapshot_mem_budget() -> None:
    """(Re)mesure la RAM dispo et fixe le budget d'admission pour le lot courant.
    Appelé au démarrage d'un lot (start_async / optimize_sequential)."""
    global _mem_budget
    avail = available_memory_bytes()
    with _mem_cond:
        _mem_budget = int(avail * _MEM_BUDGET_FRACTION) if avail else None


def _mem_budget_bytes() -> Optional[int]:
    """Budget d'admission courant (snapshot paresseux au 1er appel si besoin).
    Retourne None si la RAM dispo est inconnue (→ portillon désactivé)."""
    if _mem_budget is None:
        _snapshot_mem_budget()
    return _mem_budget


def _estimate_job_bytes(df_is, df_oos, is_ml: bool) -> int:
    """Estimation prudente du pic mémoire d'un job d'optimisation.

    Échelonnée sur la TAILLE réelle des données (la variable qui provoque l'OOM) :
    nb de barres × nb de colonnes de features × 8 o × nb de copies vivant
    simultanément (features float64 + copie scalée + Dataset LightGBM biné +
    temporaires numpy), plus un plancher fixe (interpréteur, modèles, caches)."""
    rows = (len(df_is) if df_is is not None else 0) + (len(df_oos) if df_oos is not None else 0)
    if is_ml:
        feat_cols, copies, floor = 480, 6, 350 * 1024 * 1024
    else:
        feat_cols, copies, floor = 64, 3, 150 * 1024 * 1024
    return rows * feat_cols * 8 * copies + floor


def _acquire_mem_slot(need: int, cancel_event: Optional[threading.Event] = None) -> bool:
    """Réserve ``need`` octets dès qu'ils tiennent dans le budget restant.

    Retourne True si la réservation a été faite (→ à libérer ensuite via
    ``_release_mem_slot``), False si le portillon est désactivé (RAM inconnue) ou
    si annulé pendant l'attente."""
    global _mem_committed
    budget = _mem_budget_bytes()
    if not budget:
        return False
    with _mem_cond:
        while True:
            # committed == 0 : ce job est seul → toujours admis (anti-blocage).
            if _mem_committed == 0 or _mem_committed + need <= budget:
                _mem_committed += need
                return True
            if cancel_event is not None and cancel_event.is_set():
                return False
            _mem_cond.wait(timeout=1.0)


def _release_mem_slot(need: int) -> None:
    global _mem_committed
    with _mem_cond:
        _mem_committed = max(0, _mem_committed - need)
        _mem_cond.notify_all()


def _job_id(strategy: str, timeframe: str, symbol: str) -> str:
    return f"{strategy}@{timeframe}@{symbol}"


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return dict(_jobs.get(job_id, {}))


def get_all_jobs() -> dict:
    with _jobs_lock:
        return {k: dict(v) for k, v in _jobs.items()}


def any_optimization_running() -> bool:
    """True si au moins un job d'optimisation est en cours ou en file.

    Sert aux tâches de fond (forward-test, cycle de vie) à se mettre en attente
    pendant une optimisation lourde, pour ne pas saturer mémoire/CPU.
    """
    with _jobs_lock:
        return any(j.get("status") in ("running", "queued") for j in _jobs.values())


def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id not in _jobs:
            _jobs[job_id] = {}
        _jobs[job_id].update(kwargs)


def cancel_job(job_id: str) -> bool:
    """Signal a running job to stop. Returns True if the job was running."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") != "running":
            return False
    event = _cancel_flags.get(job_id)
    if event:
        event.set()
    return True


def delete_job(job_id: str) -> bool:
    """Remove a job from the registry (only if not running). Returns True if deleted."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        if job.get("status") == "running":
            return False
        del _jobs[job_id]
        _cancel_flags.pop(job_id, None)
        return True


# ════════════════════════════════════════════════════════════════════════════
#  Baseline (snapshot avant optimisation)
# ════════════════════════════════════════════════════════════════════════════
def _run_baseline(strategy_name: str, cfg: dict,
                  df_oos: pl.DataFrame, symbol: str,
                  timeframe: str = None) -> dict:
    try:
        mod = importlib.import_module(f"app.strategies.{strategy_name}")
        eng = Engine()
        eng.register(mod.Strategy())
        bt  = Backtester(eng, cfg)
        # timeframe transmis pour que resolve_strategy_params superpose
        # optimizer_results[tf] : le baseline reflète ainsi le paramétrage
        # RÉELLEMENT actif (params: + optimizer_results), comme le live/comparatif,
        # et non le seul bloc params: par défaut.
        res = bt.run(df_oos, symbol, timeframe=timeframe).to_dict()
        return {
            "trades": res.get("total_trades", 0),
            "pnl":    round(res.get("total_pnl", 0), 4),
            "sharpe": round(res.get("sharpe", 0), 3),
            "wr":     round(res.get("win_rate", 0), 1),
            "dd":     round(res.get("max_drawdown", 0), 2),
            "alpha":  round(res["alpha"], 4) if res.get("alpha") is not None else None,
        }
    except Exception as e:
        logger.debug(f"[AutoOpt] baseline {strategy_name} KO : {e}")
        return {}


# ════════════════════════════════════════════════════════════════════════════
#  AutoOptimizer
# ════════════════════════════════════════════════════════════════════════════
class AutoOptimizer:
    """
    Optimiseur multi-stratégies × multi-timeframes avec jobs asynchrones.

    Paramètres :
      cfg             : config.yaml chargé en dict
      n_trials        : nombre de trials par (strategy, tf)
      method          : "random" | "bayesian" | "grid"
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
                 ml_tune_hp: bool = False):
        self.cfg               = cfg
        self.n_trials          = n_trials
        self.method            = method
        self.config_path       = config_path
        self.on_apply_callback = on_apply_callback
        self._notifier         = notifier
        self.n_jobs            = n_jobs
        self.early_stop_patience = early_stop_patience
        # #6 : optimisation ML two-phase (grille externe sur les hyperparamètres
        # d'entraînement × recherche interne sur les seuils). Opt-in : coûteux
        # (coût × nombre de combos HP). Sans effet sur les stratégies non-ML ou
        # celles qui n'exposent pas d'hyperparamètres d'entraînement réglables.
        self.ml_tune_hp        = ml_tune_hp

    # ── Lancement asynchrone ──────────────────────────────────────────────
    def start_async(self, df_map: Dict[str, pl.DataFrame], symbol: str,
                    strategies: List[str] = None,
                    timeframes: List[str] = None,
                    auto_apply: bool = False) -> List[str]:
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
        job_ids  = []
        skipped  = []   # [(strategy, tf, reason)]

        # Mesure la RAM dispo pour ce lot → budget d'admission mémoire des jobs.
        _snapshot_mem_budget()

        for tf in tfs:
            df = df_map.get(tf)
            n_available = len(df) if df is not None else 0

            # Convention unique de split IS/OOS (BT-08) — cf. app/core/is_oos.py
            df_is, df_oos, split = split_is_oos(df)

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
                    baseline=_run_baseline(name, self.cfg, df_oos, symbol, timeframe=tf),
                    is_recommended=is_recommended,
                    recommended_tfs=recommended_tfs,
                )
                t = threading.Thread(
                    target=self._run_one_job,
                    args=(jid, name, tf, df_is, df_oos, symbol, auto_apply, df, split),
                    daemon=True,
                )
                t.start()
                job_ids.append(jid)
                rec_str = "" if is_recommended else f" [TF non recommandé pour {name}, recommandé: {', '.join(recommended_tfs)}]"
                logger.info(f"[AutoOpt] Job lancé : {jid} ({self.method}, {self.n_trials} trials){rec_str}")

        return job_ids, skipped

    def _run_one_job(self, job_id: str, strategy_name: str, timeframe: str,
                     df_is: pl.DataFrame, df_oos: pl.DataFrame,
                     symbol: str, auto_apply: bool,
                     df_full: pl.DataFrame = None, split: int = None):
        trials_log = []

        cancel_event = _cancel_flags.get(job_id)

        # Attente bornée d'un créneau d'exécution (cap CPU global). Tant que le
        # sémaphore n'est pas acquis, le job reste "queued" et reste annulable.
        _update_job(job_id, status="queued")
        while not _job_semaphore.acquire(timeout=1.0):
            if cancel_event and cancel_event.is_set():
                logger.info(f"[AutoOpt] {job_id} annulé pendant l'attente du créneau")
                _update_job(job_id, status="cancelled", finished_at=time.time())
                return
        _update_job(job_id, status="running")

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

        # Portillon mémoire (anti-OOM inter-jobs) : on ne lance les trials que
        # lorsque l'empreinte estimée de CE job tient dans le budget restant.
        # Le créneau CPU est déjà détenu ; on le libère via le ``finally``.
        mem_need = _estimate_job_bytes(df_is, df_oos, _is_ml_strategy(strategy_name))
        mem_held = False
        try:
            mem_held = _acquire_mem_slot(mem_need, cancel_event)
            if cancel_event and cancel_event.is_set():
                logger.info(f"[AutoOpt] {job_id} annulé pendant l'attente mémoire")
                _update_job(job_id, status="cancelled", finished_at=time.time())
                return

            opt = StrategyOptimizer(
                strategy_name=strategy_name,
                cfg=self.cfg,
                df_is=df_is,
                df_oos=df_oos,
                symbol=symbol,
                progress_callback=on_progress,
                df_full=df_full,
                split=split,
                timeframe=timeframe,
                cancel_event=cancel_event,
            )

            # #6 : two-phase pour les stratégies ML exposant des hyperparamètres
            # d'entraînement réglables (et si activé). Sinon phase unique.
            ml_hp_space = {}
            if self.ml_tune_hp and _is_ml_strategy(strategy_name):
                from app.engine.optimizer import ml_hp_space_for
                ml_hp_space = ml_hp_space_for(strategy_name)

            if ml_hp_space:
                result = opt.optimize_two_phase(
                    self.method, self.n_trials, self.n_jobs, ml_hp_space,
                    early_stop_patience=self.early_stop_patience)
            elif self.method == "bayesian":
                result = opt.bayesian_search(self.n_trials, n_jobs=self.n_jobs,
                                             early_stop_patience=self.early_stop_patience)
            elif self.method == "grid":
                result = opt.grid_search()
            else:
                result = opt.random_search(self.n_trials, n_jobs=self.n_jobs,
                                           early_stop_patience=self.early_stop_patience)

            applied = False
            oos_trades   = result.get("best_oos_trades", 0)
            best_oos_pnl = result.get("best_oos_pnl", 0)
            best_oos_score = result.get("best_oos_score", 0.0)

            # Récupérer le baseline pour comparer avant d'appliquer
            _baseline      = get_job(job_id).get("baseline", {})
            baseline_pnl   = _baseline.get("pnl", float("-inf"))
            baseline_wr    = _baseline.get("wr", 0)
            baseline_sharpe = _baseline.get("sharpe", 0)
            best_oos_wr    = result.get("best_oos_wr", 0)
            best_oos_sharpe = result.get("best_oos_sharpe", 0)

            # Garde-fou UNIQUE d'application (BT-04/BT-06) : fonction pure
            # partagée avec la route /api/optimize/apply — échantillon OOS
            # ≥ MIN_SIGNIFICANT_TRADES (10, cf. app/core/stats_thresholds.py,
            # remplace l'ancien seuil 3 du TODO), PnL OOS positif ET meilleur
            # que le baseline, plus une amélioration de qualité (WR ou Sharpe).
            # TODO(Phase 0) restant : seuil de **Deflated Sharpe** (Bailey &
            # López de Prado) au gate de naissance (biais multiple-testing).
            from app.engine.opt_scoring import beats_baseline as _bb

            def _beats_baseline() -> bool:
                ok, reason = _bb(oos_trades, best_oos_pnl, best_oos_wr,
                                 best_oos_sharpe, _baseline)
                if not ok:
                    logger.info(f"[AutoOpt] {job_id} : gate d'apply refusé — {reason}")
                return ok

            def _wf_consistent() -> bool:
                """Gate walk-forward (BT-07) : les best_params FIGÉS (aucune
                re-optimisation par fold) doivent rester positifs sur une
                majorité de fenêtres OOS glissantes avant l'auto-apply — un
                unique split IS/OOS ne suffit pas. Neutre (True) si le gate
                est désactivé (optimizer.wf_gate: false), si les données
                complètes manquent, ou si le walk-forward est indisponible
                (historique trop court) : on ne durcit pas à l'aveugle."""
                opt_cfg = (self.cfg.get("optimizer") or {})
                if not bool(opt_cfg.get("wf_gate", True)) or df_full is None:
                    return True
                min_cons = float(opt_cfg.get("wf_min_consistency", 60.0))
                try:
                    from copy import deepcopy
                    from app.engine.backtest import WalkForwardAnalyzer
                    cfg2 = {k: v for k, v in self.cfg.items()}
                    sp = deepcopy(self.cfg.get("strategy_params") or {})
                    frozen = dict(sp.get(strategy_name, {}))
                    frozen.update(result["best_params"])
                    sp[strategy_name] = frozen
                    cfg2["strategy_params"] = sp
                    cfg2["optimizer_results"] = {}   # params figés, pas d'overlay
                    mod = importlib.import_module(f"app.strategies.{strategy_name}")
                    eng = Engine()
                    eng.register(mod.Strategy(), silent=True)
                    wf = WalkForwardAnalyzer(eng, cfg2,
                                             n_folds=int(opt_cfg.get("wf_folds", 5)))
                    res_wf = wf.run(df_full, symbol)
                    if "error" in res_wf:
                        logger.info(f"[AutoOpt] {job_id} : walk-forward indisponible "
                                    f"({res_wf['error']}) — gate neutre")
                        return True
                    cons = float(res_wf.get("consistency", 0.0))
                    _update_job(job_id, wf_consistency=cons)
                    if cons < min_cons:
                        logger.info(f"[AutoOpt] {job_id} : gate walk-forward refusé — "
                                    f"consistency {cons:.0f}% < {min_cons:.0f}%")
                        return False
                    return True
                except Exception as e:
                    logger.warning(f"[AutoOpt] {job_id} : walk-forward KO ({e}) — gate neutre")
                    return True

            gate_ok = bool(auto_apply and result.get("best_params")
                           and _beats_baseline() and _wf_consistent())

            if gate_ok:
                best_params = result["best_params"]
                # Config par symbole : on écrit sous optimizer_results[tf][symbol]
                # (chaque paire a sa propre config, elles coexistent).
                applied = apply_best_params(
                    strategy_name, best_params, self.config_path,
                    timeframe=timeframe, oos_score=best_oos_score, symbol=symbol
                )
                if applied and self.on_apply_callback:
                    try:
                        self.on_apply_callback(strategy_name, best_params)
                    except Exception as _cb_err:
                        logger.warning(f"[AutoOpt] callback KO: {_cb_err}")
                if not applied:
                    logger.info(
                        f"[AutoOpt] {job_id} : résultat non appliqué car pas meilleur que le baseline "
                        f"(OOS PnL={best_oos_pnl:+.2f} vs baseline={baseline_pnl:+.2f}, "
                        f"WR={best_oos_wr:.1f}% vs {baseline_wr:.1f}%, "
                        f"Sharpe={best_oos_sharpe:.2f} vs {baseline_sharpe:.2f})"
                    )
            elif auto_apply and result.get("best_params"):
                logger.info(
                    f"[AutoOpt] {job_id} : application refusée (gate qualité ou walk-forward) — "
                    f"OOS PnL={best_oos_pnl:+.2f} vs baseline={baseline_pnl:+.2f}, "
                    f"WR={best_oos_wr:.1f}% vs {baseline_wr:.1f}%, "
                    f"Sharpe={best_oos_sharpe:.2f} vs {baseline_sharpe:.2f}"
                )
                # Non appliqué = non utilisé : on trace pour l'audit sans écrire
                # dans optimizer_results (sinon le paramétrage refusé deviendrait
                # actif via la précédence de resolve_strategy_params).
                record_optimizer_audit(
                    strategy_name, timeframe,
                    result["best_params"],
                    best_oos_score,
                    self.config_path
                )
            elif result.get("best_params"):
                # Sans auto_apply : on ne fait que tracer le résultat pour l'audit.
                # L'application reste explicite (bouton « Appliquer » de l'UI →
                # apply_best_params), conformément à « non appliqué = non utilisé ».
                record_optimizer_audit(
                    strategy_name, timeframe,
                    result["best_params"],
                    result.get("best_oos_score", 0.0),
                    self.config_path
                )

            # Pour les stratégies ML : entraîner un modèle final avec les meilleurs params
            # sur l'ensemble complet des données (par défaut) et le persister sur
            # disque. S4-03 : optimizer.ml_final_train_mode contrôle full (IS+OOS,
            # défaut, comportement inchangé) vs is_only (cohérent avec le score
            # OOS rapporté, cf. docstring de _save_ml_model_post_opt).
            if result.get("best_params") and _is_ml_strategy(strategy_name) and df_full is not None:
                _ml_train_mode = self.cfg.get("optimizer", {}).get("ml_final_train_mode", "full")
                _save_ml_model_post_opt(strategy_name, result["best_params"], df_full, timeframe,
                                        df_is=df_is, train_mode=_ml_train_mode)

            # Si l'optimiseur signale un échec global (ex: tous les workers
            # tombés à cause d'un OOM LightGBM), on marque le job "error"
            # plutôt que "done" pour que le board reflète l'état réel.
            job_failed = bool(result.get("failed")) or (
                result.get("error") and not result.get("best_params")
            )
            _update_job(job_id,
                status="error" if job_failed else "done",
                progress=100,
                result=result,
                applied=applied,
                error=result.get("error") if job_failed else None,
                finished_at=time.time(),
            )
            elapsed = time.time() - get_job(job_id).get("started_at", time.time())
            logger.info(
                f"[AutoOpt] {job_id} terminé en {elapsed:.0f}s "
                f"| OOS score={result.get('best_oos_score', 0):.4f} "
                f"| PnL={result.get('best_oos_pnl', 0):+.2f} "
                f"| Applied={applied}"
            )
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

    # ── Exécution séquentielle (une stratégie à la fois) ──────────────────
    def optimize_sequential(self, df_map: Dict[str, pl.DataFrame], symbol: str,
                            strategies: List[str] = None,
                            timeframes: List[str] = None,
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

                df_is, df_oos, split = split_is_oos(df)   # convention unique (BT-08)

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
                    baseline=_run_baseline(name, self.cfg, df_oos, symbol, timeframe=tf),
                    is_recommended=(tf in recommended_tfs),
                    recommended_tfs=recommended_tfs,
                )
                # Exécution synchrone du job (réutilise toute la logique async).
                self._run_one_job(jid, name, tf, df_is, df_oos, symbol,
                                  auto_apply, df, split)
                done_ids.append(jid)
                if on_job_done:
                    try:
                        on_job_done(jid, get_job(jid))
                    except Exception as _cb:
                        logger.debug(f"[AutoOpt] on_job_done KO : {_cb}")

        return done_ids

    # ── Exécution synchrone ───────────────────────────────────────────────
    def optimize_all(self, df_map: Dict[str, pl.DataFrame], symbol: str,
                     strategies: List[str] = None,
                     timeframes: List[str] = None) -> Dict[str, dict]:
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
            df_is, df_oos, split = split_is_oos(df)   # convention unique (BT-08)

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
                        results[key] = opt.bayesian_search(self.n_trials, n_jobs=self.n_jobs)
                    elif self.method == "grid":
                        results[key] = opt.grid_search()
                    else:
                        results[key] = opt.random_search(self.n_trials, n_jobs=self.n_jobs)
                except Exception as e:
                    results[key] = {"error": str(e)}
        return results

