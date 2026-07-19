"""Optimiseur de stratégies — auto-découverte des espaces de paramètres via le registre.

Découpage V13 (recherche / scoring / persistance) :
  - ``optimizer.py``       : constantes + ``StrategyOptimizer`` (grid/random/bayesian)
  - ``opt_scoring.py``     : score composite IS/OOS, ratio de surapprentissage
  - ``opt_persistence.py`` : YAML stratégies, changelog, stratégies actives par TF
  - ``opt_workers.py``     : workers ProcessPoolExecutor (état partagé, cap mémoire)

Ce module ré-exporte les noms historiques : les imports existants
(``from app.engine.optimizer import apply_best_params, PARAM_SPACES, …``)
restent valides.
"""
import logging
import importlib
import itertools
import math
import random
import statistics
import threading
import os
import io
from contextlib import contextmanager
from copy import deepcopy
from typing import Dict, List, Any, Callable, Optional

import numpy as np
import polars as pl

from app.engine.engine import Engine
from app.engine.backtest import Backtester
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.engine.registry import (
    get_strategy_timeframes,
    get_param_spaces,
    get_fixed_params,
)

# ── Sous-modules (ré-exports compatibilité — noms historiques inclus) ────────
from app.engine.opt_scoring import (              # noqa: F401
    composite_score, overfitting_ratio,
    _composite_score, _overfitting_ratio,
)
from app.engine.opt_persistence import (          # noqa: F401
    save_optimizer_results, record_optimizer_audit, apply_best_params,
    get_active_strategies_per_tf,
    _config_write_lock, _changelog_lock, _append_changelog,
    _resolve_config_path, _strategy_file_path, _load_strategy_file,
    _write_strategy_file,
)
from app.engine.opt_workers import (              # noqa: F401
    _W, _worker_init, _eval_worker, _install_features_cache,
    available_memory_bytes as _available_memory_bytes,
    mem_aware_max_workers as _mem_aware_max_workers,
)

logger = logging.getLogger(__name__)


# Métadonnées auto-découvertes via app.strategies.registry
STRATEGY_TIMEFRAMES: Dict[str, List[str]] = get_strategy_timeframes()
PARAM_SPACES:        Dict[str, Dict[str, List]] = get_param_spaces()
FIXED_PARAMS:        Dict[str, Dict[str, Any]]  = get_fixed_params()
# Alias historique (cli.py optimise via DEFAULT_SPACES)
DEFAULT_SPACES:      Dict[str, Dict[str, List]] = PARAM_SPACES

# Barres recommandées par timeframe
RECOMMENDED_LIMIT: Dict[str, int] = {
    "1m":  2000,   # ~1.4 jours
    "5m":  4000,   # ~14 jours
    "15m": 2000,   # ~21 jours
    "30m": 1500,   # ~31 jours
    "1h":  1500,   # ~62 jours
    "4h":   800,   # ~133 jours
    "1d":  2000,   # ~2000 jours (limité par l'historique OHLCV de l'exchange)
}

GLOBAL_TRADING_PARAMS = {
    "score_threshold", "risk_per_trade", "capital", "timeframe", "timeframes",
    "paper_mode", "max_positions", "taker_fee", "maker_fee",
}

# Fraction de la fenêtre réservée à l'OOS dans le découpage des jobs (cf.
# auto_optimizer : split ≈ 65 % IS / 35 % OOS). Sert à dimensionner le nombre
# minimal de bougies à charger pour qu'une stratégie ne soit PAS ignorée.
from app.core.is_oos import OOS_FRACTION_DEFAULT as _OOS_FRACTION  # BT-08 : constante partagée

# Conversion TF -> minutes (pour exprimer la fenêtre OOS en temps).
from app.core.timeframes import TF_MINUTES as _TF_MINUTES  # V4-A : source unique

# Fenêtre de TRADING visée dans la tranche OOS, AU-DELÀ du warmup, pour qu'elle
# génère assez de trades (~ lifecycle.eval_min_trades). Sans elle, l'OOS ne
# réservait que le warmup -> 0 bougie tradable après warmup -> 0 trade -> score
# OOS dégénéré : c'est ce qui faisait sortir -999 les opus_omnibus_v* (gros
# warmup ML + filtre horaire). Exprimée en jours, convertie en bougies par TF,
# puis bornée pour ne pas exploser le runtime sur les bas TF ni rester trop
# courte sur les hauts TF.
_OOS_TRADE_DAYS = 200
_OOS_TRADE_BARS_FLOOR = 1500
_OOS_TRADE_BARS_CAP = 7000


def _oos_trade_window_bars(timeframe: str = None) -> int:
    """Bougies de fenêtre de trading visées dans l'OOS pour un TF donné."""
    minutes = _TF_MINUTES.get(timeframe or "1h", 60)
    bars_per_day = 1440.0 / minutes
    return int(min(_OOS_TRADE_BARS_CAP,
                   max(_OOS_TRADE_BARS_FLOOR, round(bars_per_day * _OOS_TRADE_DAYS))))


def required_total_bars(strategy_name: str, timeframe: str = None,
                        params: dict = None) -> int:
    """Bougies TOTALES à charger pour qu'une stratégie soit évaluable en OOS.

    La tranche OOS (~35 %) doit contenir le warmup de la stratégie
    (``min_bars_required``) PLUS une fenêtre de trading suffisante pour générer
    assez de trades. L'ancienne formule ne réservait que le warmup
    (``ceil(min_bars / 0.35)``) : l'OOS = juste le warmup -> 0 trade -> score
    OOS dégénéré (-999) pour les stratégies ML à gros warmup. Fallback
    conservateur si l'import échoue.
    """
    try:
        mod = importlib.import_module(f"app.strategies.{strategy_name}")
        min_bars = int(mod.Strategy().min_bars_required(params))
    except Exception:
        min_bars = 220
    oos_needed = min_bars + _oos_trade_window_bars(timeframe)
    return math.ceil(oos_needed / _OOS_FRACTION)


def auto_fetch_limit(timeframe: str, strategies: List[str],
                     headroom: float = 1.15) -> int:
    """Nombre de bougies à charger pour un TF, dérivé des besoins des stratégies.

    Prend le max entre la base recommandée et le plus gros besoin parmi les
    stratégies (warmup + fenêtre de trading OOS, cf. ``required_total_bars``),
    avec une marge (``headroom``). La fenêtre de trading OOS évite que l'OOS des
    stratégies ML à gros warmup ne contienne aucun trade (score -999).
    """
    base = RECOMMENDED_LIMIT.get(timeframe, 1000)
    needed = max((required_total_bars(s, timeframe) for s in strategies), default=0)
    return int(max(base, math.ceil(needed * headroom)))


# ── Hyperparamètres d'ENTRAÎNEMENT ML explorables (#6, two-phase) ────────────
# Petite grille externe : chaque combinaison segmente le cache d'entraînement
# (clé = hyperparamètres d'entraînement), donc le coût croît ~linéairement avec
# le nombre de combos. On reste volontairement frugal (4 combos par défaut).
# Intersection avec les ``fixed_params`` d'une stratégie : seules les clés
# qu'elle déclare effectivement figées comme hyperparamètres d'entraînement
# sont explorées (les autres seraient ignorées par ``_train`` → combos gâchés).
ML_HP_SPACE: Dict[str, List] = {
    "learning_rate": [0.03, 0.05],
    "n_estimators":  [300, 500],
}


def ml_hp_space_for(strategy_name: str) -> Dict[str, List]:
    """Sous-espace d'hyperparamètres d'entraînement réellement réglables pour
    une stratégie (intersection de ``ML_HP_SPACE`` avec ses ``fixed_params``).
    Vide si la stratégie n'expose aucun de ces hyperparamètres → phase unique."""
    fixed = FIXED_PARAMS.get(strategy_name, {})
    return {k: v for k, v in ML_HP_SPACE.items() if k in fixed}


# ── StrategyOptimizer — classe principale ──
class StrategyOptimizer:
    def __init__(self, strategy_name: str, cfg: dict,
                 df_is: pl.DataFrame, df_oos: pl.DataFrame,
                 param_space: Dict = None,
                 progress_callback: Optional[Callable] = None,
                 symbol: str = DEFAULT_CONFIG_SYMBOL,
                 df_full: pl.DataFrame = None,
                 split: int = None,
                 timeframe: str = None,
                 cancel_event: Optional[threading.Event] = None):
        self.strategy_name     = strategy_name
        self.cfg               = deepcopy(cfg)
        self.df_is             = df_is
        self.df_oos            = df_oos
        self.param_space       = param_space or PARAM_SPACES.get(strategy_name, {})
        self.progress_callback = progress_callback
        self.symbol            = symbol
        self.timeframe         = timeframe
        self._cancel_event     = cancel_event
        self.results: List[Dict] = []
        self.df_full = df_full if df_full is not None else pl.concat([df_is, df_oos])
        self.split   = split   if split   is not None else len(df_is)
        # Hyperparamètres d'entraînement ML figés pour la passe courante (#6,
        # two-phase) — fusionnés dans chaque jeu de params échantillonné via
        # ``_with_hp``. None = phase unique (comportement historique inchangé).
        self._fixed_ml_hp: Optional[Dict] = None

    def _with_hp(self, params: dict) -> dict:
        """Fusionne les hyperparamètres d'entraînement ML figés (``_fixed_ml_hp``)
        dans un jeu de params échantillonné. Injecté au niveau du sampler pour
        que les HP atteignent à la fois ``_eval`` (in-process) et les workers
        (``_eval_worker`` reçoit ``params`` tel quel), et soient persistés dans
        ``best_params``. No-op quand ``_fixed_ml_hp`` est None."""
        if not self._fixed_ml_hp:
            return params
        return {**params, **self._fixed_ml_hp}

    def _load_strategy(self):
        mod = importlib.import_module(f"app.strategies.{self.strategy_name}")
        eng = Engine()
        eng.register(mod.Strategy(), silent=True)  # silence: appelé à chaque trial
        return eng

    def _eval(self, params: dict) -> dict:
        eng = self._load_strategy()
        cfg = deepcopy(self.cfg)
        cfg.setdefault("strategy_params", {})[self.strategy_name] = params
        # Empêche resolve_strategy_params() d'écraser les params échantillonnés :
        # l'entrée optimizer_results sauvegardée dans le YAML stratégie a une
        # priorité supérieure et avalerait silencieusement les params du trial.
        if self.strategy_name in cfg.get("optimizer_results", {}):
            del cfg["optimizer_results"][self.strategy_name]
        # use_pretrained_ml=False : l'optimiseur évalue le comportement réel de la ML
        # avec réentraînement inline (walk-forward), sans charger de modèle pré-existant.
        bt  = Backtester(eng, cfg, cancel_event=self._cancel_event, use_pretrained_ml=False)

        res_is  = bt.run(self.df_is,  self.symbol, timeframe=self.timeframe)
        res_oos = bt.run(self.df_oos, self.symbol, timeframe=self.timeframe)

        is_score  = _composite_score(res_is)
        oos_score = _composite_score(res_oos)
        overfit   = _overfitting_ratio(is_score, oos_score)

        return {
            "params":      params,
            "is_score":    is_score,
            "oos_score":   oos_score,
            "overfit":     overfit,
            "is_pnl":      res_is.total_pnl,
            "oos_pnl":     res_oos.total_pnl,
            "is_sharpe":   res_is.sharpe,
            "oos_sharpe":  res_oos.sharpe,
            "is_trades":   res_is.total_trades,
            "oos_trades":  res_oos.total_trades,
            "is_wr":       res_is.win_rate,
            "oos_wr":      res_oos.win_rate,
            "oos_dd":      res_oos.max_drawdown,
            "oos_alpha":   getattr(res_oos, "alpha", None),
        }

    def _penalized_score(self, r: dict) -> float:
        """Score final pénalisé si surapprentissage détecté."""
        oos = r["oos_score"]
        ovf = r.get("overfit", 1.0)
        if np.isnan(ovf):
            return oos
        if ovf > 2.5:
            return oos * (2.5 / ovf)
        return oos

    def random_search(self, n_trials: int = 40, n_jobs: int = 1,
                      early_stop_patience: int = 0,
                      param_search_optim: bool = True) -> dict:
        if not self.param_space:
            return {"error": f"Aucun espace de params pour {self.strategy_name}"}

        reduction = self._maybe_reduce_space(param_search_optim, n_trials, n_jobs)
        try:
            if n_jobs and n_jobs > 1:
                # ``n_jobs`` était accepté mais jamais utilisé (boucle toujours
                # séquentielle) — réutilise le ProcessPoolExecutor déjà construit
                # pour _bayesian_search_legacy/_optuna_parallel (même sampler
                # uniforme par défaut, cap mémoire anti-OOM, repli séquentiel si
                # le pool casse). ``early_stop_patience`` n'est pas respecté dans
                # ce mode : tous les trials sont soumis d'un coup, même
                # comportement que la phase d'exploration bayésienne.
                self._run_parallel(n_trials, n_trials, trial_offset=0, n_jobs=n_jobs)
                result = self._best_result()
            else:
                best_score = -999
                no_improve = 0

                for i in range(n_trials):
                    params = self._with_hp({k: random.choice(v) for k, v in self.param_space.items()})
                    r = self._eval(params)
                    score = self._penalized_score(r)
                    r["final_score"] = score
                    self.results.append(r)

                    if score > best_score:
                        best_score = score
                        no_improve = 0
                    else:
                        no_improve += 1

                    if self.progress_callback:
                        self.progress_callback(i + 1, n_trials, best_score, {
                            "oos_pnl":     r["oos_pnl"],
                            "oos_sharpe":  r["oos_sharpe"],
                            "final_score": score,
                            "overfit":     r.get("overfit", 1.0),
                        })
                    if early_stop_patience > 0 and no_improve >= early_stop_patience:
                        logger.info(f"[Optimizer] Early stop à trial {i+1}/{n_trials}")
                        break

                result = self._best_result()
        finally:
            self._restore_param_space()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    # ── Param Search Optim : dépistage + gel des paramètres à faible impact ──
    # Option (activée par défaut) appliquée EN AMONT de random_search/
    # bayesian_search/grid_search — ce n'est pas un 4e mode de recherche.
    def _should_reduce_space(self, n_trials: int) -> bool:
        """Ne réduit que quand ça peut vraiment aider : espace à au moins 6
        paramètres ET couverture (n_trials / cardinalité) très faible — même
        seuil d'esprit que scripts/audit_param_space.py. Sur un petit espace
        déjà bien couvert, le dépistage ne ferait que gaspiller du budget."""
        if len(self.param_space) < 6:
            return False
        card = math.prod(len(v) for v in self.param_space.values())
        return card > max(n_trials, 1) * 200

    def _maybe_reduce_space(self, enabled: bool, n_trials: int, n_jobs: int) -> Optional[dict]:
        if not enabled or not self._should_reduce_space(n_trials):
            return None
        return self.reduce_param_space(n_jobs=n_jobs)

    def reduce_param_space(self, n_jobs: int = 1, freeze_fraction: float = 0.3,
                           small_window_frac: float = 0.35,
                           n_screen: Optional[int] = None) -> dict:
        """Dépistage (essais sur fenêtre RÉDUITE, ``small_window_frac`` de
        ``df_is``/``df_oos``) puis gel des paramètres à faible impact.

        Pour chaque paramètre, l'« impact » est l'écart entre la moyenne du
        score des essais groupés par valeur la plus haute et la plus basse.
        Les ``freeze_fraction`` paramètres les moins impactants sont gelés à
        leur valeur la plus performante observée — ``self.param_space`` est
        muté EN PLACE (clé gelée → liste à 1 valeur) et reste réduit jusqu'à
        ``_restore_param_space()``.

        Ne lance AUCUNE recherche elle-même : conçue pour être appelée juste
        avant ``random_search``/``bayesian_search``/``grid_search``, qui
        héritent alors d'un espace réduit sans aucune modification de leur
        propre logique (un paramètre à 1 seule option ne coûte plus ni essai
        ni dimension). Retourne un diagnostic (jamais un résultat de trial).
        """
        param_keys = list(self.param_space.keys())
        if n_screen is None:
            n_screen = max(12, 2 * len(param_keys))

        small_is  = self.df_is.tail(max(int(len(self.df_is) * small_window_frac), 200))
        small_oos = self.df_oos.tail(max(int(len(self.df_oos) * small_window_frac), 100))
        screen_results = self._eval_batch_isolated(n_screen, None, n_jobs, small_is, small_oos)
        frozen, kept_keys = self._compute_freeze(screen_results, param_keys, freeze_fraction)

        card_before = math.prod(len(v) for v in self.param_space.values())
        self._param_space_backup = dict(self.param_space)
        for k, v in frozen.items():
            self.param_space[k] = [v]
        card_after = math.prod(len(v) for v in self.param_space.values())

        logger.info(
            f"[ParamSearchOptim] {self.strategy_name} : {len(frozen)}/{len(param_keys)} "
            f"paramètres gelés après dépistage ({len(screen_results)} essais) — "
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

    def _compute_freeze(self, screen_results: List[dict], param_keys: List[str],
                        freeze_fraction: float):
        """Impact d'un paramètre = écart entre la moyenne du score final des
        essais groupés par valeur la plus haute et la plus basse (dépistage).
        Gèle les ``freeze_fraction`` paramètres les moins impactants à leur
        valeur la plus performante en moyenne. Toujours au moins 1 paramètre
        conservé (jamais un espace de recherche totalement gelé)."""
        impacts: Dict[str, float] = {}
        best_value_by_param: Dict[str, Any] = {}
        for k in param_keys:
            by_value: Dict[Any, List[float]] = {}
            for r in screen_results:
                v = r["params"].get(k)
                by_value.setdefault(v, []).append(r["final_score"])
            means = {v: statistics.mean(s) for v, s in by_value.items() if s}
            impacts[k] = (max(means.values()) - min(means.values())) if len(means) >= 2 else 0.0
            if means:
                best_value_by_param[k] = max(means.items(), key=lambda kv: kv[1])[0]
            else:
                opts = self.param_space[k]
                best_value_by_param[k] = opts[len(opts) // 2]

        n_freeze = max(0, min(len(param_keys) - 1, round(len(param_keys) * freeze_fraction)))
        ranked = sorted(param_keys, key=lambda k: impacts[k])  # impact croissant
        frozen_keys = ranked[:n_freeze]
        frozen = {k: best_value_by_param[k] for k in frozen_keys}
        kept_keys = [k for k in param_keys if k not in frozen]
        return frozen, kept_keys

    @contextmanager
    def _temp_data_window(self, df_is: pl.DataFrame, df_oos: pl.DataFrame):
        """Substitue temporairement ``self.df_is``/``df_oos`` — permet de
        réutiliser ``_run_parallel`` (séquentiel ET ProcessPoolExecutor)
        inchangé sur une fenêtre de données différente de celle passée au
        constructeur, sans dupliquer sa logique de sérialisation/cap mémoire."""
        orig_is, orig_oos = self.df_is, self.df_oos
        self.df_is, self.df_oos = df_is, df_oos
        try:
            yield
        finally:
            self.df_is, self.df_oos = orig_is, orig_oos

    def _eval_batch_isolated(self, n: int, sampler: Optional[Callable[[], dict]],
                             n_jobs: int, df_is: pl.DataFrame, df_oos: pl.DataFrame,
                             progress: bool = False, n_total: Optional[int] = None) -> List[dict]:
        """Évalue ``n`` essais (via ``_run_parallel``, séquentiel ou
        ProcessPoolExecutor selon ``n_jobs``) sur une fenêtre de données
        donnée, SANS polluer ``self.results`` — les phases de dépistage/étage
        réduit d'un ``param_search_optim`` ne sont pas comparables aux essais
        sur fenêtre complète (scores calculés sur moins de barres) et ne
        doivent donc jamais entrer en compétition dans ``_best_result()``.
        """
        saved_results = self.results
        saved_callback = self.progress_callback
        self.results = []
        if not progress:
            self.progress_callback = None
        try:
            with self._temp_data_window(df_is, df_oos):
                self._run_parallel(n, n_total or n, trial_offset=0,
                                   sampler=sampler, n_jobs=n_jobs)
            batch = self.results
        finally:
            self.results = saved_results
            self.progress_callback = saved_callback
        return batch

    def bayesian_search(self, n_trials: int = 40, n_jobs: int = 1,
                        early_stop_patience: int = 0,
                        param_search_optim: bool = True) -> dict:
        """Recherche bayésienne. Utilise Optuna (TPE, recherche informée par un
        modèle de substitution) si la librairie est installée ; sinon retombe sur
        l'heuristique historique (exploration aléatoire + raffinement local)."""
        if not self.param_space:
            return {"error": f"Aucun espace de params pour {self.strategy_name}"}
        reduction = self._maybe_reduce_space(param_search_optim, n_trials, n_jobs)
        try:
            try:
                import optuna  # noqa: F401
            except Exception:
                logger.info("[Bayesian] Optuna absent — repli sur random+perturbation. "
                            "Installez optuna pour une vraie recherche TPE.")
                result = self._bayesian_search_legacy(n_trials, n_jobs, early_stop_patience)
            else:
                result = self._bayesian_search_optuna(n_trials, n_jobs, early_stop_patience)
        finally:
            self._restore_param_space()
        if reduction:
            result["param_search_optim"] = reduction
        return result

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
                                early_stop_patience: int) -> dict:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        sampler = optuna.samplers.TPESampler(
            n_startup_trials=max(8, n_trials // 3), seed=0,
        )
        study = optuna.create_study(direction="maximize", sampler=sampler)

        safe_jobs = self._safe_worker_count(n_jobs)
        if safe_jobs <= 1:
            self._optuna_sequential(study, n_trials, early_stop_patience)
        else:
            self._optuna_parallel(study, n_trials, safe_jobs, early_stop_patience)
        return self._best_result()

    def _optuna_sequential(self, study, n_trials: int, early_stop_patience: int) -> None:
        """Ask/tell séquentiel in-process — garde le cache d'entraînement chaud
        (même process) d'un trial à l'autre."""
        best_score = -999.0
        no_improve = 0
        for i in range(n_trials):
            trial  = study.ask()
            params = self._params_from_trial(trial)
            r      = self._eval(params)
            score  = self._penalized_score(r)
            r["final_score"] = score
            self.results.append(r)
            study.tell(trial, score if math.isfinite(score) else -999.0)

            if score > best_score:
                best_score = score
                no_improve = 0
            else:
                no_improve += 1

            if self.progress_callback:
                self.progress_callback(i + 1, n_trials, best_score, {
                    "oos_pnl":     r["oos_pnl"],
                    "oos_sharpe":  r["oos_sharpe"],
                    "final_score": score,
                    "overfit":     r.get("overfit", 1.0),
                })
            if early_stop_patience > 0 and no_improve >= early_stop_patience:
                logger.info(f"[Bayesian/TPE] Early stop à trial {i+1}/{n_trials}")
                break

    def _optuna_parallel(self, study, n_trials: int, safe_jobs: int,
                         early_stop_patience: int) -> None:
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
                            continue
                        if "error" in r:
                            logger.warning("[Bayesian/TPE] worker erreur : %s", r["error"])
                            study.tell(trials[i], -999.0)
                            continue
                        score = self._penalized_score(r)
                        r["final_score"] = score
                        self.results.append(r)
                        study.tell(trials[i], score if math.isfinite(score) else -999.0)
                        if score > best_score:
                            best_score = score
                            no_improve = 0
                        else:
                            no_improve += 1
                        if self.progress_callback:
                            self.progress_callback(len(self.results), n_trials, best_score, {
                                "oos_pnl":     r["oos_pnl"],
                                "oos_sharpe":  r["oos_sharpe"],
                                "final_score": score,
                                "overfit":     r.get("overfit", 1.0),
                            })
                    done += k
                    if early_stop_patience > 0 and no_improve >= early_stop_patience:
                        logger.info(f"[Bayesian/TPE] Early stop à {done}/{n_trials}")
                        break
        except BrokenProcessPool as _bp:
            logger.error("[Bayesian/TPE] pool brisé (OOM worker ?) — repli séquentiel "
                         "pour les trials restants : %s", _bp)
            self._optuna_sequential(study, n_trials - len(self.results),
                                    early_stop_patience)

    def _bayesian_search_legacy(self, n_trials: int = 40, n_jobs: int = 1,
                                early_stop_patience: int = 0) -> dict:
        """Heuristique historique : exploration aléatoire (1/3) puis raffinement
        local (perturbation ±1 cran autour du meilleur). Repli quand Optuna est
        absent (ex. environnement de production sans la dépendance)."""
        n_explore = max(8, n_trials // 3)
        n_exploit = n_trials - n_explore

        # Phase exploration : random
        self._run_parallel(n_explore, n_trials, trial_offset=0,
                           sampler=lambda: self._with_hp(
                               {k: random.choice(v) for k, v in self.param_space.items()}),
                           n_jobs=n_jobs)

        # Phase exploitation : gaussian autour du meilleur
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
                if early_stop_patience > 0 and no_improve >= early_stop_patience:
                    logger.info(f"[Bayesian] Early stop exploit trial {i+1}/{n_exploit}")
                    break

        return self._best_result()

    # ── Helpers ProcessPool (partagés random/bayesian) ───────────────────────
    def _serialize_pool_inputs(self):
        """Sérialise (une fois) cfg + DataFrames IS/OOS pour les workers spawn.
        Retourne ``(cfg_yaml, df_is_ipc, df_oos_ipc, init_args)``."""
        import yaml as _yaml
        _buf_is = io.BytesIO();  self.df_is.write_ipc(_buf_is);   df_is_ipc  = _buf_is.getvalue()
        _buf_oos = io.BytesIO(); self.df_oos.write_ipc(_buf_oos); df_oos_ipc = _buf_oos.getvalue()
        cfg_yaml = _yaml.dump(self.cfg)
        init_args = (self.strategy_name, cfg_yaml,
                     df_is_ipc, df_oos_ipc, self.symbol, self.timeframe)
        return cfg_yaml, df_is_ipc, df_oos_ipc, init_args

    def _worker_args(self, params: dict, cfg_yaml: str,
                     df_is_ipc: bytes, df_oos_ipc: bytes) -> tuple:
        return (self.strategy_name, cfg_yaml, df_is_ipc, df_oos_ipc,
                self.symbol, params, self.timeframe)

    def _safe_worker_count(self, n_jobs: int) -> int:
        """Plafonne le nombre de workers : cpu-1 puis cap mémoire anti-OOM."""
        if n_jobs <= 1:
            return 1
        _cpu = os.cpu_count() or 1
        safe = max(1, min(n_jobs, max(1, _cpu - 1)))
        # Estimation prudente ~5× le payload IPC + 256 Mo (features + LightGBM).
        try:
            _buf_is = io.BytesIO();  self.df_is.write_ipc(_buf_is)
            _buf_oos = io.BytesIO(); self.df_oos.write_ipc(_buf_oos)
            per_worker = int((_buf_is.tell() + _buf_oos.tell()) * 5) + 256 * 1024 * 1024
            safe = _mem_aware_max_workers(safe, per_worker)
        except Exception:
            pass
        return safe

    def _run_parallel(self, n: int, n_total: int, trial_offset: int = 0,
                      sampler=None, n_jobs: int = 1):
        if sampler is None:
            sampler = lambda: self._with_hp({k: random.choice(v) for k, v in self.param_space.items()})

        if n_jobs <= 1:
            best_so_far = -999
            for i in range(n):
                r = self._eval(sampler())
                score = self._penalized_score(r)
                r["final_score"] = score
                self.results.append(r)
                if score > best_so_far:
                    best_so_far = score
                if self.progress_callback:
                    self.progress_callback(trial_offset + i + 1, n_total, best_so_far, {
                        "oos_pnl":     r["oos_pnl"],
                        "oos_sharpe":  r["oos_sharpe"],
                        "final_score": score,
                        "overfit":     r.get("overfit", 1.0),
                    })
        else:
            # ProcessPoolExecutor pour parallélisme CPU réel (contourne le GIL)
            import multiprocessing as _mp
            import concurrent.futures

            param_list = [sampler() for _ in range(n)]
            best_so_far = -999
            done_count  = 0

            # Sérialisation des DataFrames via IPC (efficace, évite copie mémoire)
            cfg_yaml, df_is_ipc, df_oos_ipc, _init_args = self._serialize_pool_inputs()

            worker_args = [
                self._worker_args(p, cfg_yaml, df_is_ipc, df_oos_ipc)
                for p in param_list
            ]

            # spawn : évite les problèmes de fork avec les threads FastAPI ;
            # plafonnement cpu-1 puis cap mémoire anti-OOM.
            _safe_jobs = self._safe_worker_count(n_jobs)
            _worker_timeout = 300  # 5 min max par évaluation
            ctx = _mp.get_context("spawn")
            try:
                from concurrent.futures.process import BrokenProcessPool
            except ImportError:  # py<3.3 fallback (jamais atteint)
                BrokenProcessPool = Exception  # type: ignore

            # initializer/initargs : chaque worker pré-calcule les features
            # (lourdes, ~462 colonnes × 20k barres) une seule fois et les
            # réutilise pour tous ses trials → gain typique ×5-10 sur les
            # stratégies à features lourdes (opus_omnibus_v8/v10_retrained…).
            _init_args = (self.strategy_name, cfg_yaml,
                          df_is_ipc, df_oos_ipc, self.symbol, self.timeframe)

            remaining_params: List[dict] = []
            pool_broken = False
            try:
                with concurrent.futures.ProcessPoolExecutor(
                        max_workers=_safe_jobs, mp_context=ctx,
                        initializer=_worker_init, initargs=_init_args) as exe:
                    futures_map = {exe.submit(_eval_worker, a): i for i, a in enumerate(worker_args)}
                    for fut in concurrent.futures.as_completed(futures_map):
                        done_count += 1
                        try:
                            r = fut.result(timeout=_worker_timeout)
                        except concurrent.futures.TimeoutError:
                            logger.warning("[Optimizer] worker timeout (>%ds), ignoré", _worker_timeout)
                            continue
                        except BrokenProcessPool as _bp:
                            # Un worker a été tué (OOM LightGBM, segfault…). Le pool
                            # entier est compromis : on bascule sur du séquentiel
                            # pour les trials restants au lieu de tout perdre.
                            logger.error(
                                "[Optimizer] BrokenProcessPool (worker tué, ex: OOM) — "
                                "bascule en séquentiel pour les trials restants : %s", _bp,
                            )
                            pool_broken = True
                            for f, idx in futures_map.items():
                                if not f.done():
                                    remaining_params.append(param_list[idx])
                                    f.cancel()
                            break
                        except Exception as _e:
                            logger.warning(f"[Optimizer] worker KO : {_e}")
                            continue
                        if "error" in r:
                            logger.warning("[Optimizer] worker erreur : %s", r["error"])
                            continue
                        score = self._penalized_score(r)
                        r["final_score"] = score
                        self.results.append(r)
                        if score > best_so_far:
                            best_so_far = score
                        if self.progress_callback:
                            self.progress_callback(trial_offset + done_count, n_total, best_so_far, {
                                "oos_pnl":     r["oos_pnl"],
                                "oos_sharpe":  r["oos_sharpe"],
                                "final_score": score,
                                "overfit":     r.get("overfit", 1.0),
                            })
            except BrokenProcessPool as _bp:
                # Le pool est mort à l'__exit__ (ex: shutdown KO après crash).
                # On absorbe l'erreur ; les trials déjà collectés restent valides.
                logger.error("[Optimizer] pool brisé à la fermeture, ignoré : %s", _bp)
                pool_broken = True

            # Fallback séquentiel pour les trials non traités après pool brisé.
            if pool_broken and remaining_params:
                logger.info(
                    "[Optimizer] reprise en séquentiel de %d trial(s) restant(s)",
                    len(remaining_params),
                )
                for p in remaining_params:
                    try:
                        r = self._eval(p)
                    except Exception as _se:
                        logger.warning(f"[Optimizer] trial séquentiel KO : {_se}")
                        continue
                    done_count += 1
                    score = self._penalized_score(r)
                    r["final_score"] = score
                    self.results.append(r)
                    if score > best_so_far:
                        best_so_far = score
                    if self.progress_callback:
                        try:
                            self.progress_callback(trial_offset + done_count, n_total, best_so_far, {
                                "oos_pnl":     r["oos_pnl"],
                                "oos_sharpe":  r["oos_sharpe"],
                                "final_score": score,
                                "overfit":     r.get("overfit", 1.0),
                            })
                        except InterruptedError:
                            raise

    def _perturb(self, params: dict) -> dict:
        """Perturbation légère d'un jeu de params pour l'exploitation."""
        new_params = deepcopy(params)
        keys = list(self.param_space.keys())
        if not keys:
            return new_params
        n_perturb = max(1, len(keys) // 3)
        for k in random.sample(keys, min(n_perturb, len(keys))):
            options = self.param_space[k]
            if len(options) > 1:
                curr_idx = options.index(params[k]) if params[k] in options else 0
                offsets  = [-1, 0, 1]
                new_idx  = curr_idx + random.choice(offsets)
                new_idx  = max(0, min(len(options) - 1, new_idx))
                new_params[k] = options[new_idx]
        return new_params

    # Grid search est exhaustif — au-delà de ce nombre de combinaisons, une
    # grille complète devient impraticable ; c'est là que la réduction
    # d'espace (gel des paramètres à faible impact) rend le plus service.
    _GRID_REDUCE_THRESHOLD = 5000

    def grid_search(self, n_jobs: int = 1, param_search_optim: bool = True) -> dict:
        if not self.param_space:
            return {"error": "Pas d'espace de params"}

        reduction = None
        if (param_search_optim and len(self.param_space) >= 6
                and math.prod(len(v) for v in self.param_space.values())
                > self._GRID_REDUCE_THRESHOLD):
            reduction = self.reduce_param_space(n_jobs=n_jobs)

        try:
            keys = list(self.param_space.keys())
            vals = list(self.param_space.values())
            combos = list(itertools.product(*vals))
            n_total = len(combos)
            logger.info(f"[Optimizer] Grid search : {n_total} combinaisons")

            for i, combo in enumerate(combos):
                params = self._with_hp(dict(zip(keys, combo)))
                r = self._eval(params)
                score = self._penalized_score(r)
                r["final_score"] = score
                self.results.append(r)
                if self.progress_callback:
                    best_now = max(self._penalized_score(x) for x in self.results)
                    self.progress_callback(i + 1, n_total, best_now, {
                        "oos_pnl":    r["oos_pnl"],
                        "oos_sharpe": r["oos_sharpe"],
                        "final_score": score,
                        "overfit":    r.get("overfit", 1.0),
                    })
            result = self._best_result()
        finally:
            self._restore_param_space()
        if reduction:
            result["param_search_optim"] = reduction
        return result

    def _best_result(self) -> dict:
        if not self.results:
            return {
                "error": ("Aucun trial complété (workers tous KO — ex: OOM LightGBM). "
                          "Réduisez --jobs / n_jobs, ou diminuez le nombre de bougies."),
                "failed": True,
                "completed_trials": 0,
            }
        best = max(self.results, key=self._penalized_score)
        # Top 5 par score final
        sorted_results = sorted(self.results, key=self._penalized_score, reverse=True)
        top5 = [
            {
                "is_score":    round(r["is_score"], 4),
                "oos_score":   round(r["oos_score"], 4),
                "final_score": round(self._penalized_score(r), 4),
                "oos_pnl":     round(r["oos_pnl"], 2),
                "oos_wr":      round(r.get("oos_wr", 0.0), 1),
                "oos_dd":      round(r.get("oos_dd", 0.0), 2),
                "overfit":     round(r.get("overfit", 1.0), 2),
            }
            for r in sorted_results[:5]
        ]
        return {
            "strategy":       self.strategy_name,
            "timeframe":      self.timeframe,
            "symbol":         self.symbol,
            "best_params":    best["params"],
            "best_is_score":  best["is_score"],
            "best_oos_score": self._penalized_score(best),
            "best_is_pnl":    best["is_pnl"],
            "best_oos_pnl":   best["oos_pnl"],
            "best_is_sharpe": best["is_sharpe"],
            "best_oos_sharpe":best["oos_sharpe"],
            "best_is_trades": best["is_trades"],
            "best_oos_trades":best["oos_trades"],
            "best_oos_wr":    round(best.get("oos_wr", 0.0), 1),
            "best_is_wr":     round(best.get("is_wr", 0.0), 1),
            "best_oos_dd":    round(best.get("oos_dd", 0.0), 2),
            "best_oos_alpha": round(best["oos_alpha"], 4) if best.get("oos_alpha") is not None else None,
            "overfit":        best.get("overfit", 1.0),
            "n_trials":       len(self.results),
            "top5":           top5,
        }

    # ── Dispatch & two-phase ML (#6) ──────────────────────────────────────────
    def _dispatch(self, method: str, n_trials: int, n_jobs: int,
                  early_stop_patience: int = 0,
                  param_search_optim: bool = True) -> dict:
        """Lance une recherche selon la méthode demandée (phase unique).

        ``param_search_optim`` (gel des paramètres à faible impact avant la
        recherche, cf. ``reduce_param_space``) n'est PAS un 4e ``method`` —
        c'est une option orthogonale appliquée par ``random_search``/
        ``bayesian_search``/``grid_search`` eux-mêmes, quel que soit celui
        choisi ici."""
        if method == "grid":
            return self.grid_search(n_jobs=n_jobs, param_search_optim=param_search_optim)
        if method == "bayesian":
            return self.bayesian_search(n_trials, n_jobs=n_jobs,
                                        early_stop_patience=early_stop_patience,
                                        param_search_optim=param_search_optim)
        return self.random_search(n_trials, n_jobs=n_jobs,
                                  early_stop_patience=early_stop_patience,
                                  param_search_optim=param_search_optim)

    def optimize_two_phase(self, method: str, n_trials: int, n_jobs: int,
                           ml_hp_space: Dict[str, List],
                           early_stop_patience: int = 0) -> dict:
        """Optimisation ML en deux phases (#6).

        Phase externe : petite **grille** sur les hyperparamètres d'entraînement
        (``ml_hp_space``). Phase interne : recherche ``method`` sur les seuils de
        décision (``param_space``), avec les HP figés pour la combinaison
        courante. Les HP figés sont injectés dans chaque jeu de params
        (``_with_hp``) → le cache d'entraînement reste chaud *au sein* d'une
        combinaison (clé de cache constante), et chaque combinaison repaie ses
        propres réentraînements (coût ~linéaire avec le nombre de combos).

        Sans HP réglables (``ml_hp_space`` vide), retombe sur la phase unique.
        Retourne le meilleur résultat (best_oos_score) parmi les combinaisons ;
        ``best_params`` y inclut les HP retenus (donc persistés et réutilisés au
        ré-entraînement du modèle final).
        """
        keys = list(ml_hp_space.keys())
        if not keys:
            return self._dispatch(method, n_trials, n_jobs, early_stop_patience)

        combos = list(itertools.product(*[ml_hp_space[k] for k in keys]))
        logger.info("[Optimizer] Two-phase ML %s : %d combo(s) HP × recherche %s",
                    self.strategy_name, len(combos), method)

        candidates: List[dict] = []
        for combo in combos:
            hp = dict(zip(keys, combo))
            self._fixed_ml_hp = hp
            self.results = []  # isole les trials de cette combinaison
            try:
                res = self._dispatch(method, n_trials, n_jobs, early_stop_patience)
            except InterruptedError:
                self._fixed_ml_hp = None
                raise
            if res.get("best_params"):
                res = dict(res)
                res["ml_hp"] = hp
                candidates.append(res)
            logger.info("[Optimizer]   HP=%s → OOS=%.4f", hp,
                        res.get("best_oos_score", float("nan")))
        self._fixed_ml_hp = None

        if not candidates:
            return {"error": "two-phase : aucun trial exploitable", "failed": True}
        best = max(candidates, key=lambda r: r.get("best_oos_score", -999))
        best["n_hp_combos"] = len(combos)
        return best
