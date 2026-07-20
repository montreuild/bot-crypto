"""Optimisation IS/OOS de `smart_trend_adx` sur 1h / 2h / 4h / 1d (parquet BTC/USDC).

Pour chaque timeframe :
  - split chronologique 65 % IS / 35 % OOS (warmup 220 barres partagé) ;
  - recherche bayésienne (Optuna TPE) sur le param_space de la stratégie ;
  - score composite IS & OOS + ratio de surapprentissage (score OOS pénalisé si
    IS/OOS > 2.5) — exactement le moteur d'optimisation du projet.

Écrit les meilleurs params par TF dans strategies/smart_trend_adx.yaml
(section optimizer_results) et affiche un récapitulatif.

Usage : python research/optimize_smart_trend.py
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

from app.core.yaml_io import dump_yaml, load_yaml  # noqa: E402
from app.engine.optimizer import PARAM_SPACES, StrategyOptimizer  # noqa: E402

STRAT = "smart_trend_adx"
TIMEFRAMES = ["1h", "2h", "4h", "1d"]
OOS_RATIO = 0.35
WARMUP = 220
N_TRIALS = 40
# Plafond de barres par TF (fenêtre récente) — borne le temps de calcul tout en
# gardant ~3 ans d'historique par timeframe. 0 = tout l'historique.
MAX_BARS = {"1h": 26000, "2h": 16000, "4h": 16000, "1d": 0}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "ohlcv", "BTC_USDC")
YAML_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "strategies", f"{STRAT}.yaml")


def _base_cfg() -> dict:
    """Config réaliste (frais OKX, sizing par risque) — base d'évaluation IS/OOS."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_yaml(os.path.join(root, "config.yaml"), default={})
    cfg = dict(cfg)
    cfg.setdefault("trading", {})
    cfg["trading"]["capital"] = 1000.0
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
    df_is  = df[:split]
    df_oos = df[max(0, split - WARMUP):]
    return df_is, df_oos


def main():
    random.seed(42)
    np.random.seed(42)
    cfg = _base_cfg()
    space = PARAM_SPACES.get(STRAT, {})
    print(f"Optimisation IS/OOS — {STRAT} — BTC/USDC — {N_TRIALS} trials/TF "
          f"(bayésien) — split {int((1-OOS_RATIO)*100)}/{int(OOS_RATIO*100)}\n")
    print(f"Espace : { {k: v for k, v in space.items()} }\n")

    results = {}
    header = (f"{'TF':>4} | {'ISscr':>6} | {'OOSscr':>7} | {'overfit':>7} | "
             f"{'OOS trades':>10} | {'OOS win%':>8} | {'OOS PnL':>9} | {'OOS DD%':>7}")
    print(header)
    print("-" * len(header))

    for tf in TIMEFRAMES:
        df_is, df_oos = _split(tf)
        if df_is is None or len(df_is) < WARMUP + 50 or len(df_oos) < WARMUP + 50:
            print(f"{tf:>4} | données insuffisantes")
            continue
        opt = StrategyOptimizer(STRAT, cfg, df_is, df_oos, param_space=space,
                                symbol="BTC/USDC", timeframe=tf)
        res = opt.bayesian_search(n_trials=N_TRIALS, n_jobs=1)
        if res.get("error"):
            print(f"{tf:>4} | {res['error']}")
            continue
        results[tf] = res
        print(f"{tf:>4} | {res['best_is_score']:>6.3f} | {res['best_oos_score']:>7.3f} | "
              f"{res['overfit']:>7.2f} | {res['best_oos_trades']:>10} | "
              f"{res['best_oos_wr']:>7.1f}% | {res['best_oos_pnl']:>+8.2f} | "
              f"{res['best_oos_dd']:>6.2f}%")

    # ── Détail des meilleurs params + écriture YAML ──────────────────────────
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
        def _upd(data):
            data.setdefault("optimizer_results", {})
            data["optimizer_results"].update(opt_results)
        y = load_yaml(YAML_PATH, default={})
        _upd(y)
        dump_yaml(YAML_PATH, y)
        print(f"\n✅ optimizer_results écrit dans {os.path.relpath(YAML_PATH)}")


if __name__ == "__main__":
    main()
