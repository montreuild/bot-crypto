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
import random
import threading
import time
import os
import io
from copy import deepcopy
from typing import Dict, List, Any, Callable, Optional

import numpy as np
import polars as pl

from app.engine.engine import Engine
from app.engine.backtest import Backtester, BacktestResult
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
    "1d":  2000,   # ~2000 jours (Binance fournit ~2500 max depuis 2017)
}

GLOBAL_TRADING_PARAMS = {
    "score_threshold", "risk_per_trade", "capital", "timeframe", "timeframes",
    "paper_mode", "max_positions", "taker_fee", "maker_fee",
}


# ── StrategyOptimizer — classe principale ──
class StrategyOptimizer:
    def __init__(self, strategy_name: str, cfg: dict,
                 df_is: pl.DataFrame, df_oos: pl.DataFrame,
                 param_space: Dict = None,
                 progress_callback: Optional[Callable] = None,
                 symbol: str = "BTC/USDC",
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
                      early_stop_patience: int = 0) -> dict:
        if not self.param_space:
            return {"error": f"Aucun espace de params pour {self.strategy_name}"}

        best_score = -999
        no_improve = 0

        for i in range(n_trials):
            params = {k: random.choice(v) for k, v in self.param_space.items()}
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

        return self._best_result()

    def bayesian_search(self, n_trials: int = 40, n_jobs: int = 1,
                        early_stop_patience: int = 0) -> dict:
        if not self.param_space:
            return {"error": f"Aucun espace de params pour {self.strategy_name}"}

        n_explore = max(8, n_trials // 3)
        n_exploit = n_trials - n_explore

        # Phase exploration : random
        self._run_parallel(n_explore, n_trials, trial_offset=0,
                           sampler=lambda: {k: random.choice(v) for k, v in self.param_space.items()},
                           n_jobs=n_jobs)

        # Phase exploitation : gaussian autour du meilleur
        if self.results and n_exploit > 0:
            best = max(self.results, key=self._penalized_score)
            no_improve = 0
            best_score = self._penalized_score(best)

            for i in range(n_exploit):
                trial_idx = n_explore + i
                params = self._perturb(best["params"])
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

    def _run_parallel(self, n: int, n_total: int, trial_offset: int = 0,
                      sampler=None, n_jobs: int = 1):
        if sampler is None:
            sampler = lambda: {k: random.choice(v) for k, v in self.param_space.items()}

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
            import yaml as _yaml

            param_list = [sampler() for _ in range(n)]
            best_so_far = -999
            done_count  = 0

            # Sérialisation des DataFrames via IPC (efficace, évite copie mémoire)
            _buf_is = io.BytesIO(); self.df_is.write_ipc(_buf_is);  df_is_ipc  = _buf_is.getvalue()
            _buf_oos = io.BytesIO(); self.df_oos.write_ipc(_buf_oos); df_oos_ipc = _buf_oos.getvalue()
            cfg_yaml = _yaml.dump(self.cfg)

            worker_args = [
                (self.strategy_name, cfg_yaml, df_is_ipc, df_oos_ipc, self.symbol, p, self.timeframe)
                for p in param_list
            ]

            # spawn : évite les problèmes de fork avec les threads FastAPI
            # Plafonnement à cpu_count-1 pour laisser un cœur au process principal
            import os as _os
            _cpu = _os.cpu_count() or 1
            _safe_jobs = max(1, min(n_jobs, max(1, _cpu - 1)))
            # Cap mémoire : chaque worker duplique IS+OOS (IPC) et reconstruit les
            # features lourdes + LightGBM. Estimation prudente ~5× le payload IPC.
            _per_worker = int((len(df_is_ipc) + len(df_oos_ipc)) * 5) + 256 * 1024 * 1024
            _safe_jobs = _mem_aware_max_workers(_safe_jobs, _per_worker)
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

    def grid_search(self) -> dict:
        if not self.param_space:
            return {"error": "Pas d'espace de params"}
        keys = list(self.param_space.keys())
        vals = list(self.param_space.values())
        combos = list(itertools.product(*vals))
        n_total = len(combos)
        logger.info(f"[Optimizer] Grid search : {n_total} combinaisons")

        for i, combo in enumerate(combos):
            params = dict(zip(keys, combo))
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
        return self._best_result()

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
