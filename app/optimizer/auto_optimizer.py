"""
Optimisation automatique par stratégie — Random Search.
Tournée au démarrage ou via API, sauvegarde les meilleurs params en DB.
"""
import importlib
import logging
import random
import time
from copy import deepcopy
from typing import Dict, List, Any

import numpy as np
import pandas as pd

from app.engine.engine import Engine
from app.engine.backtest import Backtester

logger = logging.getLogger(__name__)


# ── Espaces de recherche par stratégie ─────────────────────────────────────
PARAM_SPACES: Dict[str, Dict[str, List]] = {
    "trend": {
        "ema_fast":       [8, 10, 13, 15, 20, 25],
        "ema_slow":       [30, 40, 50, 60, 80, 100],
        "rsi_threshold":  [35, 40, 45, 50, 55],
        "atr_mult":       [1.0, 1.2, 1.5, 1.8, 2.0, 2.5],
        "vol_filter":     [True, False],
    },
    "breakout": {
        "period":         [10, 12, 15, 20, 25, 30],
        "atr_mult":       [1.2, 1.5, 1.8, 2.0, 2.5, 3.0],
        "confirm_bars":   [1, 2, 3, 4],
        "trailing":       [True, False],
    },
    "supertrend_macd": {
        "st_period":      [7, 9, 10, 12, 14],
        "st_mult":        [2.0, 2.5, 3.0, 3.5, 4.0],
        "macd_fast":      [8, 10, 12, 14, 16],
        "macd_slow":      [21, 24, 26, 28, 30],
        "macd_signal":    [7, 9, 11],
        "vol_mult":       [1.0, 1.1, 1.3, 1.5, 2.0],
    },
    "ml_strategy": {
        "feature_window": [30, 50, 70, 100],
        "lookahead":      [3, 5, 8, 10],
        "threshold_up":   [0.002, 0.003, 0.005],
        "min_confidence": [0.55, 0.60, 0.65, 0.70],
        "n_estimators":   [100, 200, 300],
        "max_depth":      [4, 5, 6, 8],
        "min_samples_leaf": [5, 10, 15, 20],
    },
}


def _composite_score(res_dict: dict) -> float:
    """Score composite sur les métriques OOS."""
    n = res_dict.get("total_trades", 0)
    if n < 3:
        return -999.0
    pf = res_dict.get("profit_factor", 0)
    if isinstance(pf, str):
        pf = 5.0
    pf = min(float(pf), 5.0)
    wr = res_dict.get("win_rate", 0) / 100
    sh = res_dict.get("sharpe", 0)
    exp = res_dict.get("expectancy", 0)
    dd = abs(res_dict.get("max_drawdown", -100))
    score = (sh * 0.35 + wr * 0.25 + pf / 5 * 0.20
             + min(exp, 20) / 20 * 0.10 + max(0, 1 - dd / 50) * 0.10)
    return round(score, 6)


class AutoOptimizer:
    """
    Optimiseur automatique Random Search pour toutes les stratégies actives.
    Divise les données en 70% IS / 30% OOS.
    """

    def __init__(self, cfg: dict, n_trials: int = 30):
        self.cfg      = cfg
        self.n_trials = n_trials

    def optimize_all(self, df: pd.DataFrame, symbol: str,
                     strategies: List[str] = None) -> Dict[str, dict]:
        """Lance l'optimisation pour chaque stratégie active."""
        strats   = strategies or self.cfg["strategies"]["enabled"]
        results  = {}
        split    = int(len(df) * 0.70)
        df_is    = df.iloc[:split].copy()
        df_oos   = df.iloc[split:].copy()

        for name in strats:
            logger.info(f"[AutoOpt] Début Random Search : {name} ({self.n_trials} trials)")
            t0 = time.time()
            try:
                res = self._optimize_one(name, df_is, df_oos, symbol)
                results[name] = res
                elapsed = time.time() - t0
                logger.info(f"[AutoOpt] {name} terminé en {elapsed:.1f}s "
                            f"— OOS score={res.get('best_oos_score', 0):.4f} "
                            f"— params={res.get('best_params')}")
            except Exception as e:
                logger.error(f"[AutoOpt] {name} KO : {e}")
                results[name] = {"error": str(e)}
        return results

    def _optimize_one(self, strategy_name: str, df_is: pd.DataFrame,
                      df_oos: pd.DataFrame, symbol: str) -> dict:
        space = PARAM_SPACES.get(strategy_name, {})
        if not space:
            return {"error": f"Espace de paramètres non défini pour {strategy_name}"}

        best_score   = -999.0
        best_params  = {}
        best_is_res  = {}
        best_oos_res = {}
        all_results  = []
        rng          = random.Random(int(time.time()) % 10000)

        for trial in range(self.n_trials):
            # Tirage aléatoire dans l'espace
            params = {k: rng.choice(v) for k, v in space.items()}
            try:
                is_res, oos_res = self._run_trial(strategy_name, params, df_is, df_oos, symbol)
                oos_score = _composite_score(oos_res)
                is_score  = _composite_score(is_res)
                all_results.append({
                    "trial":     trial + 1,
                    "params":    params,
                    "is_score":  round(is_score,  4),
                    "oos_score": round(oos_score, 4),
                    "is_trades": is_res.get("total_trades", 0),
                    "oos_trades":oos_res.get("total_trades", 0),
                    "oos_wr":    round(oos_res.get("win_rate", 0), 1),
                    "oos_sharpe":round(oos_res.get("sharpe", 0), 3),
                    "oos_pnl":   round(oos_res.get("total_pnl", 0), 4),
                })
                if oos_score > best_score:
                    best_score   = oos_score
                    best_params  = params
                    best_is_res  = is_res
                    best_oos_res = oos_res
                    logger.debug(f"[AutoOpt] {strategy_name} trial {trial+1} "
                                 f"new best OOS={oos_score:.4f}")
            except Exception as e:
                logger.debug(f"[AutoOpt] {strategy_name} trial {trial+1} KO : {e}")

        # Top 5 résultats
        top5 = sorted(all_results, key=lambda r: -r["oos_score"])[:5]

        return {
            "strategy":        strategy_name,
            "n_trials":        self.n_trials,
            "best_params":     best_params,
            "best_oos_score":  round(best_score, 4),
            "best_is_pnl":     round(best_is_res.get("total_pnl", 0), 4),
            "best_oos_pnl":    round(best_oos_res.get("total_pnl", 0), 4),
            "best_oos_wr":     round(best_oos_res.get("win_rate", 0), 1),
            "best_oos_sharpe": round(best_oos_res.get("sharpe", 0), 3),
            "best_oos_trades": best_oos_res.get("total_trades", 0),
            "top5":            top5,
            "all_results":     all_results,
        }

    def _run_trial(self, name: str, params: dict,
                   df_is: pd.DataFrame, df_oos: pd.DataFrame,
                   symbol: str):
        cfg = deepcopy(self.cfg)
        cfg.setdefault("strategy_params", {})[name] = params

        # Charger la stratégie
        mod = importlib.import_module(f"app.strategies.{name}")
        eng = Engine()
        if name == "ml_strategy":
            strat = mod.Strategy(params)
        else:
            strat = mod.Strategy()
        eng.register(strat)

        bt_is  = Backtester(eng, cfg)
        bt_oos = Backtester(eng, cfg)
        r_is   = bt_is.run(df_is,  symbol).to_dict()
        r_oos  = bt_oos.run(df_oos, symbol).to_dict()
        return r_is, r_oos
