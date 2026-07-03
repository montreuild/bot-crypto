"""Optimisation IS/OOS générique d'une stratégie portée d'un PineScript, sur
plusieurs timeframes (parquet BTC/USDC).

Pour chaque timeframe :
  - split chronologique 65 % IS / 35 % OOS (warmup 220 barres partagé) ;
  - recherche bayésienne (Optuna TPE) sur le param_space de la stratégie ;
  - score composite IS & OOS + ratio de surapprentissage (OOS pénalisé si IS/OOS
    > 2.5) — le moteur d'optimisation du projet.

Écrit les meilleurs params par TF dans strategies/<strategy>.yaml (optimizer_results).

Usage : python research/optimize_pine.py <strategy_name> [tf1,tf2,...]
        (défaut : liquidity_sweep_vol, TFs 30m,1h,2h,4h,1d)
"""
import logging
import os
import random
import sys
from datetime import date

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.getLogger().setLevel(logging.WARNING)
for _n in ("app", "optuna"):
    logging.getLogger(_n).setLevel(logging.WARNING)

from app.core.yaml_io import load_yaml, dump_yaml          # noqa: E402
from app.engine.optimizer import StrategyOptimizer, PARAM_SPACES  # noqa: E402

OOS_RATIO = 0.35
WARMUP = 220
N_TRIALS = 40
# Plafond de barres par TF (fenêtre récente) — borne le temps de calcul.
# 0 = tout l'historique.
MAX_BARS = {"30m": 26000, "1h": 24000, "2h": 15000, "4h": 0, "1d": 0}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "ohlcv", "BTC_USDC")


def _base_cfg() -> dict:
    cfg = dict(load_yaml(os.path.join(ROOT, "config.yaml"), default={}))
    cfg.setdefault("trading", {})["capital"] = 1000.0
    cfg.setdefault("backtest", {})
    return cfg


def _split(tf: str):
    path = os.path.join(DATA_DIR, f"{tf}.parquet")
    if not os.path.exists(path):
        return None, None
    df = pl.read_parquet(path).sort("time")
    cap = MAX_BARS.get(tf, 0)
    if cap and len(df) > cap:
        df = df.tail(cap)
    n = len(df)
    split = int(n * (1 - OOS_RATIO))
    return df[:split], df[max(0, split - WARMUP):]


def main():
    strat = sys.argv[1] if len(sys.argv) > 1 else "liquidity_sweep_vol"
    tfs = (sys.argv[2].split(",") if len(sys.argv) > 2
           else ["30m", "1h", "2h", "4h", "1d"])
    yaml_path = os.path.join(ROOT, "strategies", f"{strat}.yaml")
    space = PARAM_SPACES.get(strat, {})
    if not space:
        print(f"Aucun param_space pour {strat}")
        return

    random.seed(42)
    np.random.seed(42)
    cfg = _base_cfg()
    print(f"Optimisation IS/OOS — {strat} — BTC/USDC — {N_TRIALS} trials/TF "
          f"(bayésien) — split {int((1-OOS_RATIO)*100)}/{int(OOS_RATIO*100)}")
    print(f"Espace : {space}\n")
    header = (f"{'TF':>4} | {'ISscr':>6} | {'OOSscr':>7} | {'overfit':>7} | "
             f"{'OOS tr':>6} | {'OOS win%':>8} | {'OOS PnL':>9} | {'OOS DD%':>7}")
    print(header)
    print("-" * len(header))

    results = {}
    for tf in tfs:
        df_is, df_oos = _split(tf)
        if df_is is None or len(df_is) < WARMUP + 50 or len(df_oos) < WARMUP + 50:
            print(f"{tf:>4} | données insuffisantes")
            continue
        opt = StrategyOptimizer(strat, cfg, df_is, df_oos, param_space=space,
                                symbol="BTC/USDC", timeframe=tf)
        res = opt.bayesian_search(n_trials=N_TRIALS, n_jobs=1)
        if res.get("error"):
            print(f"{tf:>4} | {res['error']}")
            continue
        results[tf] = res
        print(f"{tf:>4} | {res['best_is_score']:>6.3f} | {res['best_oos_score']:>7.3f} | "
              f"{res['overfit']:>7.2f} | {res['best_oos_trades']:>6} | "
              f"{res['best_oos_wr']:>7.1f}% | {res['best_oos_pnl']:>+8.2f} | "
              f"{res['best_oos_dd']:>6.2f}%")

    print("\nMeilleurs paramètres par TF :")
    opt_results = {}
    for tf, res in results.items():
        bp = res["best_params"]
        print(f"  {tf:>4}: {bp}")
        opt_results[tf] = {
            "run_date": date.today().isoformat(),
            "oos_score": round(float(res["best_oos_score"]), 6),
            "params": {k: bp[k] for k in sorted(bp)},
        }

    if opt_results:
        y = load_yaml(yaml_path, default={})
        y.setdefault("optimizer_results", {}).update(opt_results)
        dump_yaml(yaml_path, y)
        print(f"\n✅ optimizer_results écrit dans {os.path.relpath(yaml_path)}")


if __name__ == "__main__":
    main()
