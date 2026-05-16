"""Optimiseur de stratégies — auto-découverte des espaces de paramètres via le registre."""
import logging
import importlib
import itertools
import random
import threading
import time
import yaml
import os
import io
from copy import deepcopy
from datetime import datetime
from typing import Dict, List, Any, Callable, Optional

# Lock global pour protéger les écritures concurrentes dans les fichiers YAML
_config_write_lock = threading.Lock()

import numpy as np
import polars as pl

from app.engine.engine import Engine
from app.engine.backtest import Backtester, BacktestResult
from app.engine.registry import (
    get_strategy_timeframes,
    get_param_spaces,
    get_fixed_params,
)

logger = logging.getLogger(__name__)


# Métadonnées auto-découvertes via app.strategies.registry
STRATEGY_TIMEFRAMES: Dict[str, List[str]] = get_strategy_timeframes()
PARAM_SPACES:        Dict[str, Dict[str, List]] = get_param_spaces()
FIXED_PARAMS:        Dict[str, Dict[str, Any]]  = get_fixed_params()

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


# ── Score composite ──
def _composite_score(res: dict, min_trades: int = 2) -> float:
    n = res.get("total_trades", 0) if isinstance(res, dict) else res.total_trades
    if n < min_trades:
        return -999.0

    if isinstance(res, dict):
        sharpe = res.get("sharpe", 0)
        wr     = res.get("win_rate", 0) / 100
        pf     = res.get("profit_factor", 0)
        pnl    = res.get("total_pnl", 0)
        dd     = abs(res.get("max_drawdown", -100))
        exp    = res.get("expectancy", 0)
        alpha  = res.get("alpha", 0)
    else:
        sharpe = res.sharpe
        wr     = res.win_rate / 100
        pf     = res.profit_factor
        pnl    = res.total_pnl
        dd     = abs(res.max_drawdown)
        exp    = res.expectancy
        alpha  = getattr(res, "alpha", 0)

    if isinstance(pf, str):
        pf = 6.0
    pf = min(float(pf), 6.0)

    trade_factor = min(n / 10, 1.0)
    dd_factor    = max(0, 1.0 - dd / 30)
    pnl_sign     = 1.0 if pnl > 0 else 0.3
    alpha_norm   = max(min(alpha / 50.0, 1.0), -1.0)
    alpha_bonus  = alpha_norm * 0.10

    score = (
        sharpe          * 0.28 +
        wr              * 0.18 +
        (pf / 6)        * 0.18 +
        min(exp, 30) / 30 * 0.08 +
        dd_factor       * 0.10 +
        trade_factor    * 0.08 +
        alpha_bonus     * 1.00
    ) * pnl_sign

    return round(score, 6)


def _overfitting_ratio(is_score: float, oos_score: float) -> float:
    if oos_score <= -990 or is_score <= -990:
        return float('nan')
    if is_score <= 0:
        return 0.0
    return round(min(is_score / max(oos_score, 0.01), 10.0), 2)


# ── Worker standalone pour ProcessPoolExecutor (picklable — niveau module) ──
def _eval_worker(args: tuple) -> dict:
    """
    Évalue un jeu de paramètres dans un processus séparé.
    Utilise polars IPC pour déséraliser les DataFrames efficacement.
    """
    strategy_name, cfg_yaml, df_is_ipc, df_oos_ipc, symbol, params, timeframe = args
    try:
        import yaml as _yaml
        import importlib as _imp
        import polars as _pl
        from copy import deepcopy as _dp
        from app.engine.engine import Engine as _Engine
        from app.engine.backtest import Backtester as _Backtester

        _cfg = _yaml.safe_load(cfg_yaml)
        _df_is  = _pl.read_ipc(io.BytesIO(df_is_ipc))
        _df_oos = _pl.read_ipc(io.BytesIO(df_oos_ipc))

        _mod = _imp.import_module(f"app.strategies.{strategy_name}")
        _eng = _Engine()
        _eng.register(_mod.Strategy(), silent=True)
        _cfg_copy = _dp(_cfg)
        _cfg_copy.setdefault("strategy_params", {})[strategy_name] = params
        # Empêche resolve_strategy_params() d'écraser les params échantillonnés
        if strategy_name in _cfg_copy.get("optimizer_results", {}):
            del _cfg_copy["optimizer_results"][strategy_name]
        _bt = _Backtester(_eng, _cfg_copy, use_pretrained_ml=False)

        _res_is  = _bt.run(_df_is,  symbol, timeframe=timeframe)
        _res_oos = _bt.run(_df_oos, symbol, timeframe=timeframe)

        _is_score  = _composite_score(_res_is)
        _oos_score = _composite_score(_res_oos)
        _overfit   = _overfitting_ratio(_is_score, _oos_score)

        return {
            "params":     params,
            "is_score":   _is_score,
            "oos_score":  _oos_score,
            "overfit":    _overfit,
            "is_pnl":     _res_is.total_pnl,
            "oos_pnl":    _res_oos.total_pnl,
            "is_sharpe":  _res_is.sharpe,
            "oos_sharpe": _res_oos.sharpe,
            "is_trades":  _res_is.total_trades,
            "oos_trades": _res_oos.total_trades,
            "is_wr":      _res_is.win_rate,
            "oos_wr":     _res_oos.win_rate,
            "oos_dd":     _res_oos.max_drawdown,
            "oos_alpha":  getattr(_res_oos, "alpha", None),
        }
    except Exception as _exc:
        # Retourner une erreur sérialisable plutôt que de laisser le process crasher,
        # ce qui évite les BrokenProcessPool et les processus orphelins.
        return {"error": str(_exc), "params": params}


# ── Persistance des résultats d'optimisation ──────────────────────────────────

def _resolve_config_path(config_path: str) -> str:
    """Résout le chemin du fichier config principal en chemin absolu."""
    if os.path.isabs(config_path):
        return config_path
    cwd_path = os.path.abspath(config_path)
    if os.path.exists(cwd_path):
        return cwd_path
    return cwd_path


def _strategy_file_path(strategy_name: str, config_path: str = "config.yaml") -> str:
    """Retourne le chemin absolu de strategies/{strategy_name}.yaml."""
    config_dir = os.path.dirname(os.path.abspath(_resolve_config_path(config_path)))
    return os.path.join(config_dir, "strategies", f"{strategy_name}.yaml")


def _load_strategy_file(strat_path: str) -> dict:
    """Charge un fichier stratégie YAML ; retourne {} si absent ou vide."""
    if not os.path.exists(strat_path):
        return {}
    try:
        with open(strat_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[Optimizer] Lecture {strat_path} KO : {e}")
        return {}


def _write_strategy_file(strat_path: str, data: dict) -> None:
    """Écrit un fichier stratégie YAML avec un commentaire d'en-tête."""
    os.makedirs(os.path.dirname(strat_path), exist_ok=True)
    with open(strat_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def save_optimizer_results(strategy_name: str, timeframe: str,
                           params: dict, oos_score: float,
                           config_path: str = "config.yaml") -> bool:
    """
    Persiste le résultat d'optimisation dans strategies/{strategy_name}.yaml.
    Préserve les autres timeframes existants. Thread-safe via _config_write_lock.
    """
    strat_path = _strategy_file_path(strategy_name, config_path)
    try:
        with _config_write_lock:
            data = _load_strategy_file(strat_path)
            data.setdefault("optimizer_results", {})[timeframe] = {
                "run_date":  datetime.utcnow().strftime("%Y-%m-%d"),
                "oos_score": round(float(oos_score), 6),
                "params":    deepcopy(params),
            }
            _write_strategy_file(strat_path, data)

        try:
            _append_changelog(
                _resolve_config_path(config_path),
                strategy_name, timeframe, params, oos_score, {}
            )
        except Exception as _cle:
            logger.debug(f"[Optimizer] changelog KO (non bloquant) : {_cle}")

        logger.info(
            f"[Optimizer] Résultat sauvegardé → strategies/{strategy_name}.yaml "
            f"[{timeframe}] score OOS={oos_score:.4f}"
        )
        return True
    except Exception as e:
        logger.error(f"[Optimizer] save_optimizer_results KO : {e}")
        return False


def apply_best_params(strategy_name: str, params: dict,
                      config_path: str = "config.yaml",
                      timeframe: str = None,
                      oos_score: float = 0.0) -> bool:
    """
    Met à jour params et optimizer_results dans strategies/{strategy_name}.yaml.
    Préserve tous les autres timeframes/paramètres. Thread-safe via _config_write_lock.
    """
    strat_path = _strategy_file_path(strategy_name, config_path)
    old_params_snapshot = {}
    try:
        with _config_write_lock:
            data = _load_strategy_file(strat_path)

            # Snapshot pour le changelog
            old_params_snapshot = deepcopy(data.get("params", {}))

            # Mettre à jour les params par défaut (hors params globaux)
            existing = data.setdefault("params", {})
            for k, v in params.items():
                if k not in GLOBAL_TRADING_PARAMS:
                    existing[k] = v

            # Mettre à jour optimizer_results si TF fourni
            if timeframe:
                data.setdefault("optimizer_results", {})[timeframe] = {
                    "run_date":  datetime.utcnow().strftime("%Y-%m-%d"),
                    "oos_score": round(float(oos_score), 6),
                    "params":    deepcopy(params),
                }

            _write_strategy_file(strat_path, data)

        try:
            _append_changelog(
                _resolve_config_path(config_path),
                strategy_name, timeframe, params, oos_score, old_params_snapshot
            )
        except Exception as _cle:
            logger.debug(f"[Optimizer] changelog KO (non bloquant) : {_cle}")

        logger.info(
            f"[Optimizer] Params appliqués → strategies/{strategy_name}.yaml"
            + (f" [{timeframe}]" if timeframe else "")
        )
        return True
    except Exception as e:
        logger.error(f"[Optimizer] apply_best_params KO : {e}")
        return False


_changelog_lock = threading.Lock()

def _append_changelog(config_path: str, strategy: str, timeframe: str,
                      new_params: dict, oos_score: float, old_params: dict) -> None:
    """Ajoute une entrée dans optimizer_changelog.json (max 200 entrées). Thread-safe."""
    import json
    # Utiliser le répertoire du fichier config (résolu en absolu)
    abs_config = config_path if os.path.isabs(config_path) else os.path.abspath(config_path)
    changelog_path = os.path.join(os.path.dirname(abs_config), "optimizer_changelog.json")
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source":    "optimizer",
        "strategy":  strategy,
        "timeframe": timeframe or "",
        "oos_score": round(float(oos_score), 4),
        "params":    deepcopy(new_params),
        "changed":   {},
    }
    for k, v in new_params.items():
        old_v = old_params.get(k) if old_params else None
        if old_v != v:
            entry["changed"][k] = {"before": old_v, "after": v}
    with _changelog_lock:
        try:
            with open(changelog_path, "r", encoding="utf-8") as f:
                log = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            log = []
        log.append(entry)
        log = log[-200:]  # garder les 200 derniers
        with open(changelog_path, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)


def get_active_strategies_per_tf(cfg: dict) -> Dict[str, List[dict]]:
    """
    Retourne les stratégies actives par TF depuis optimizer_results.
    Format : { "1h": [{"name": "trend", "params": {...}, "score": 0.82}, ...], ... }

    Fallback : si aucun résultat pour un TF, utilise strategies.enabled avec strategy_params.
    """
    timeframes   = cfg["trading"].get("timeframes", [cfg["trading"].get("timeframe", "1h")])
    top_n        = cfg["trading"].get("top_strategies_per_tf", 2)
    opt_results  = cfg.get("optimizer_results") or {}
    strat_params = cfg.get("strategy_params", {})
    result       = {}

    for tf in timeframes:
        candidates = []
        for strat_name, tf_map in opt_results.items():
            if not isinstance(tf_map, dict):
                continue
            if tf in tf_map:
                entry = tf_map[tf]
                if isinstance(entry, dict):
                    score  = entry.get("oos_score") if entry.get("oos_score") is not None else -999
                    params = entry.get("params", strat_params.get(strat_name, {}))
                    candidates.append({
                        "name":   strat_name,
                        "params": {strat_name: params},
                        "score":  score,
                        "tf":     tf,
                    })

        # Tri par score OOS décroissant, top N
        # MIN_VIABLE_SCORE: exclure les stratégies nettement perdantes en OOS
        # (-0.05 = légèrement négatif acceptable, -0.15 = clairement mauvais)
        MIN_VIABLE_SCORE = -0.05
        candidates.sort(key=lambda x: x["score"], reverse=True)
        viable   = [c for c in candidates if c["score"] >= MIN_VIABLE_SCORE]
        rejected = [c for c in candidates if c["score"] < MIN_VIABLE_SCORE and c["score"] > -999]
        active   = viable[:top_n]

        if rejected:
            for r in rejected:
                logger.warning(
                    f"[Optimizer] {r['name']}@{tf} exclu du live : "
                    f"score OOS {r['score']:.4f} < seuil {MIN_VIABLE_SCORE} — "
                    f"relancez une optimisation pour améliorer."
                )

        if active:
            result[tf] = active
        elif not candidates:
            # Fallback uniquement si aucun résultat d'optimisation n'existe pour ce TF
            # (stratégies jamais optimisées) — pas de fallback si tous les scores sont négatifs
            enabled = cfg["strategies"].get("enabled", [])
            result[tf] = [
                {"name": n, "params": {n: strat_params.get(n, {})}, "score": 0.0, "tf": tf}
                for n in enabled
            ]
            logger.info(f"[Optimizer] {tf} : aucun résultat OOS — "
                        f"fallback sur stratégies activées : {enabled}")
        else:
            # Tous les candidats ont un score OOS sous le seuil → aucune stratégie active sur ce TF
            scores_str = ', '.join(f"{c['name']}={c['score']:.3f}" for c in candidates[:5])
            logger.warning(
                f"[Optimizer] {tf} : tous les scores OOS < {MIN_VIABLE_SCORE} "
                f"({scores_str}) — aucune stratégie active sur ce TF. "
                f"Relancez l'optimiseur pour améliorer les scores."
            )
            result[tf] = []

    return result


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
        # Empêche resolve_strategy_params() d'écraser les params échantillonnés
        # avec des optimizer_results sauvegardés (ex : warmup_bars=2000 d'un ancien run).
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
            _worker_timeout = 300  # 5 min max par évaluation
            ctx = _mp.get_context("spawn")
            with concurrent.futures.ProcessPoolExecutor(
                    max_workers=_safe_jobs, mp_context=ctx) as exe:
                futures_map = {exe.submit(_eval_worker, a): i for i, a in enumerate(worker_args)}
                for fut in concurrent.futures.as_completed(futures_map):
                    done_count += 1
                    try:
                        r = fut.result(timeout=_worker_timeout)
                    except concurrent.futures.TimeoutError:
                        logger.warning("[Optimizer] worker timeout (>%ds), ignoré", _worker_timeout)
                        continue
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
            return {"error": "Aucun trial complété"}
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
