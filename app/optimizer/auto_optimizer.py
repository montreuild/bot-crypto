"""
AutoOptimizer V8 — Multi-Timeframe.

Nouveautés V8 :
  - Job_id format : "strategy@tf@symbol" (ex: "trend@1h@BTC/USDC")
  - Optimise chaque (strategy, tf) sur BTC/USDC comme paire représentative
  - Persiste dans optimizer_results via save_optimizer_results()
  - Reload dynamique des stratégies actives dans le LiveTrader
"""
import importlib
import logging
import math
import threading
import time
from typing import Dict, List, Optional

import polars as pl

from app.engine.engine   import Engine
from app.engine.backtest import Backtester
from app.optimizer.optimizer import (
    StrategyOptimizer, PARAM_SPACES, STRATEGY_TIMEFRAMES, RECOMMENDED_LIMIT,
    apply_best_params, save_optimizer_results, get_active_strategies_per_tf
)

logger = logging.getLogger(__name__)

ML_STRATEGIES = {"ml_strategy", "ml_dynamic_threshold"}

# ════════════════════════════════════════════════════════════════════════════
#  État global des jobs (thread-safe)
# ════════════════════════════════════════════════════════════════════════════
_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _job_id(strategy: str, timeframe: str, symbol: str) -> str:
    return f"{strategy}@{timeframe}@{symbol}"


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return dict(_jobs.get(job_id, {}))


def get_all_jobs() -> dict:
    with _jobs_lock:
        return {k: dict(v) for k, v in _jobs.items()}


def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id not in _jobs:
            _jobs[job_id] = {}
        _jobs[job_id].update(kwargs)


# ════════════════════════════════════════════════════════════════════════════
#  Baseline (snapshot avant optimisation)
# ════════════════════════════════════════════════════════════════════════════
def _run_baseline(strategy_name: str, cfg: dict,
                  df_oos: pl.DataFrame, symbol: str) -> dict:
    try:
        mod = importlib.import_module(f"app.strategies.{strategy_name}")
        eng = Engine()
        eng.register(mod.Strategy())
        bt  = Backtester(eng, cfg)
        res = bt.run(df_oos, symbol).to_dict()
        return {
            "trades": res.get("total_trades", 0),
            "pnl":    round(res.get("total_pnl", 0), 4),
            "sharpe": round(res.get("sharpe", 0), 3),
            "wr":     round(res.get("win_rate", 0), 1),
            "dd":     round(res.get("max_drawdown", 0), 2),
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
                 early_stop_patience: int = 0):
        self.cfg               = cfg
        self.n_trials          = n_trials
        self.method            = method
        self.config_path       = config_path
        self.on_apply_callback = on_apply_callback
        self._notifier         = notifier
        self.n_jobs            = n_jobs
        self.early_stop_patience = early_stop_patience

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

        for tf in tfs:
            df = df_map.get(tf)
            n_available = len(df) if df is not None else 0

            WARMUP = 210
            split  = max(WARMUP + 100, int(n_available * 0.65)) if n_available > 0 else 0
            df_is  = df[:split]  if df is not None else None
            df_oos = df[split:]  if df is not None else None

            for name in strats:
                if name in ML_STRATEGIES:
                    skipped.append({"strategy": name, "timeframe": tf, "reason": "stratégie ML (non optimisable ici)"})
                    continue
                if name not in PARAM_SPACES:
                    skipped.append({"strategy": name, "timeframe": tf, "reason": "aucun espace de paramètres"})
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
                _update_job(jid,
                    status="running", strategy=name, timeframe=tf, symbol=symbol,
                    method=self.method, n_trials=self.n_trials,
                    progress=0, best_score=-999, trials=[],
                    result=None, error=None,
                    started_at=time.time(), finished_at=None,
                    baseline=_run_baseline(name, self.cfg, df_oos, symbol),
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

        def on_progress(trial: int, total: int, best_score: float, latest: dict):
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

        try:
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
            )

            if self.method == "bayesian":
                result = opt.bayesian_search(self.n_trials, n_jobs=self.n_jobs,
                                             early_stop_patience=self.early_stop_patience)
            elif self.method == "grid":
                result = opt.grid_search()
            else:
                result = opt.random_search(self.n_trials, n_jobs=self.n_jobs,
                                           early_stop_patience=self.early_stop_patience)

            applied = False
            oos_trades = result.get("best_oos_trades", 0)
            if (auto_apply
                    and result.get("best_params")
                    and result.get("best_oos_pnl", 0) > 0
                    and oos_trades >= 3):   # min 3 trades OOS pour être statistiquement crédible
                best_params = result["best_params"]
                oos_score   = result.get("best_oos_score", 0.0)
                applied = apply_best_params(
                    strategy_name, best_params, self.config_path,
                    timeframe=timeframe, oos_score=oos_score
                )
                if applied and self.on_apply_callback:
                    try:
                        self.on_apply_callback(strategy_name, best_params)
                    except Exception as _cb_err:
                        logger.warning(f"[AutoOpt] callback KO: {_cb_err}")
            elif result.get("best_params"):
                # Sauvegarder le résultat même sans auto_apply
                save_optimizer_results(
                    strategy_name, timeframe,
                    result["best_params"],
                    result.get("best_oos_score", 0.0),
                    self.config_path
                )

            _update_job(job_id,
                status="done", progress=100,
                result=result, applied=applied,
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

        except Exception as e:
            logger.error(f"[AutoOpt] {job_id} KO : {e}", exc_info=True)
            _update_job(job_id, status="error", error=str(e), finished_at=time.time())

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
            WARMUP = 210
            split  = max(WARMUP + 100, int(len(df) * 0.65))
            df_is  = df[:split]
            df_oos = df[split:]

            for name in strats:
                if name in ML_STRATEGIES or name not in PARAM_SPACES:
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
