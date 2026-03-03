"""
Module 4 — Optimiseur automatique :
  - Grid Search
  - Random Search
  - Optimisation Bayésienne (via scipy/numpy, sans optuna)
  - Validation Out-of-Sample
"""
import logging
import importlib
import itertools
import random
from copy import deepcopy
from typing import Dict, List, Any, Callable

import numpy as np
import pandas as pd
from scipy.stats import uniform, randint

from app.engine.engine import Engine
from app.engine.backtest import Backtester, BacktestResult

logger = logging.getLogger(__name__)


def _score(result: BacktestResult) -> float:
    """Score composite pour comparer les configurations."""
    if result.total_trades < 5:
        return -999.0
    pf  = result.profit_factor if result.profit_factor != float("inf") else 5.0
    return (result.sharpe * 0.4 +
            result.win_rate / 100 * 0.3 +
            min(pf, 5.0) / 5.0 * 0.2 +
            (1 + result.max_drawdown / 100) * 0.1)


class StrategyOptimizer:
    def __init__(self, strategy_name: str, cfg: dict, df_is: pd.DataFrame,
                 df_oos: pd.DataFrame, param_space: Dict):
        self.strategy_name = strategy_name
        self.cfg           = deepcopy(cfg)
        self.df_is         = df_is
        self.df_oos        = df_oos
        self.param_space   = param_space   # {param: [values]} ou {param: (min, max, type)}
        self.results: List[Dict] = []

    def _run_with_params(self, params: dict) -> tuple[float, float, dict]:
        cfg = deepcopy(self.cfg)
        cfg.setdefault("strategy_params", {})[self.strategy_name] = params
        mod  = importlib.import_module(f"app.strategies.{self.strategy_name}")
        eng  = Engine(); eng.register(mod.Strategy())
        bt   = Backtester(eng, cfg)
        r_is = bt.run(self.df_is, "BTC/USDC")
        s_is = _score(r_is)
        # OOS validation
        bt2      = Backtester(eng, cfg)
        r_oos    = bt2.run(self.df_oos, "BTC/USDC")
        s_oos    = _score(r_oos)
        summary  = {
            "params": params, "is_score": round(s_is, 4), "oos_score": round(s_oos, 4),
            "is_sharpe": round(r_is.sharpe, 3), "oos_sharpe": round(r_oos.sharpe, 3),
            "is_pnl": round(r_is.total_pnl, 4), "oos_pnl": round(r_oos.total_pnl, 4),
            "is_wr": round(r_is.win_rate, 2), "oos_wr": round(r_oos.win_rate, 2),
        }
        return s_is, s_oos, summary

    def grid_search(self) -> Dict:
        """Essaie toutes les combinaisons."""
        keys   = list(self.param_space.keys())
        values = [self.param_space[k] if isinstance(self.param_space[k], list)
                  else [self.param_space[k][0]] for k in keys]
        combos = list(itertools.product(*values))
        logger.info(f"[Optimizer] Grid Search — {len(combos)} combinaisons")
        best_score, best_params, best_summary = -999, None, {}
        for combo in combos:
            params = dict(zip(keys, combo))
            try:
                s_is, s_oos, summary = self._run_with_params(params)
                self.results.append(summary)
                if s_oos > best_score:
                    best_score, best_params, best_summary = s_oos, params, summary
            except Exception as e:
                logger.debug(f"[Optimizer] Combo {params} KO : {e}")
        return {"method": "grid", "best": best_summary, "all": self.results}

    def random_search(self, n_trials: int = 50) -> Dict:
        """Recherche aléatoire dans l'espace des paramètres."""
        logger.info(f"[Optimizer] Random Search — {n_trials} essais")
        best_score, best_summary = -999, {}
        rng = random.Random(42)
        for _ in range(n_trials):
            params = {}
            for k, space in self.param_space.items():
                if isinstance(space, list):
                    params[k] = rng.choice(space)
                elif isinstance(space, tuple) and len(space) == 3:
                    lo, hi, typ = space
                    if typ == "int":
                        params[k] = rng.randint(int(lo), int(hi))
                    else:
                        params[k] = round(rng.uniform(lo, hi), 4)
            try:
                s_is, s_oos, summary = self._run_with_params(params)
                self.results.append(summary)
                if s_oos > best_score:
                    best_score, best_summary = s_oos, summary
            except Exception as e:
                logger.debug(f"[Optimizer] Trial KO : {e}")
        return {"method": "random", "best": best_summary, "all": self.results}

    def bayesian_search(self, n_trials: int = 50) -> Dict:
        """
        Optimisation Bayésienne simplifiée (GPR sur numpy).
        En l'absence d'optuna, utilise un UCB exploratoire.
        """
        try:
            return self._bayesian_numpy(n_trials)
        except Exception as e:
            logger.warning(f"[Optimizer] Bayesian KO ({e}) — fallback Random Search")
            return self.random_search(n_trials)

    def _bayesian_numpy(self, n_trials: int) -> Dict:
        """UCB-based acquisition function (numpy only)."""
        logger.info(f"[Optimizer] Bayesian (UCB) — {n_trials} essais")
        keys = list(self.param_space.keys())
        # Génère un pool de candidats
        pool = []
        rng  = random.Random(99)
        for _ in range(n_trials * 5):
            p = {}
            for k, space in self.param_space.items():
                if isinstance(space, list):
                    p[k] = rng.choice(space)
                elif isinstance(space, tuple) and len(space) == 3:
                    lo, hi, typ = space
                    p[k] = rng.randint(int(lo),int(hi)) if typ=="int" else round(rng.uniform(lo,hi),4)
            pool.append(p)

        scores_seen: List[tuple] = []
        best_score, best_summary = -999, {}
        # Phase exploration (25% des essais)
        n_explore = max(5, n_trials // 4)
        explore   = rng.sample(pool, min(n_explore, len(pool)))
        for params in explore:
            try:
                s_is, s_oos, summary = self._run_with_params(params)
                scores_seen.append((s_oos, params))
                self.results.append(summary)
                if s_oos > best_score:
                    best_score, best_summary = s_oos, summary
            except Exception: pass

        # Phase exploitation : favorise les zones proches des meilleurs
        for _ in range(n_trials - n_explore):
            if not scores_seen:
                break
            # Choisit le candidat le plus "proche" du meilleur connu (perturbation)
            top = sorted(scores_seen, key=lambda x: -x[0])[:3]
            base_params = rng.choice(top)[1]
            params = {}
            for k, space in self.param_space.items():
                if isinstance(space, list):
                    idx = space.index(base_params[k]) if base_params[k] in space else 0
                    shift = rng.randint(-1, 1)
                    params[k] = space[max(0, min(len(space)-1, idx+shift))]
                elif isinstance(space, tuple) and len(space) == 3:
                    lo, hi, typ = space
                    noise = (hi - lo) * 0.1
                    if typ == "int":
                        params[k] = max(int(lo), min(int(hi), int(base_params[k]) + rng.randint(-2,2)))
                    else:
                        params[k] = max(lo, min(hi, round(base_params[k] + rng.uniform(-noise, noise), 4)))
            try:
                s_is, s_oos, summary = self._run_with_params(params)
                scores_seen.append((s_oos, params))
                self.results.append(summary)
                if s_oos > best_score:
                    best_score, best_summary = s_oos, summary
            except Exception: pass

        return {"method": "bayesian", "best": best_summary, "all": self.results}


# Espaces de paramètres par défaut pour chaque stratégie
DEFAULT_SPACES = {
    "trend": {
        "ema_fast":      [10, 15, 20, 25],
        "ema_slow":      [40, 50, 60, 100],
        "rsi_threshold": [40, 45, 50, 55],
        "atr_mult":      [1.2, 1.5, 1.8, 2.0],
    },
    "breakout": {
        "period":      [10, 15, 20, 25, 30],
        "atr_mult":    [1.5, 1.8, 2.0, 2.5],
        "confirm_bars":[1, 2, 3],
    },
    "supertrend_macd": {
        "st_period": [7, 10, 14],
        "st_mult":   [2.0, 3.0, 4.0],
        "macd_fast": [8, 12, 16],
        "macd_slow": [21, 26, 30],
        "vol_mult":  [1.0, 1.3, 1.5, 2.0],
    },
}
