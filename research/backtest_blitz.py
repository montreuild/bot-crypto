"""Backtest agressif de momentum_blitz : rendement ET risque (DD, Monte-Carlo ruine).

Usage : python research/backtest_blitz.py [--tf 1h,4h] [--deploy full1x|lev2x]
        [--wf] [--split] [--mc] [--params k=v ...]
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl
from app.engine.engine import Engine
from app.engine.backtest import Backtester, WalkForwardAnalyzer, MonteCarlo
from app.strategies.momentum_blitz import Strategy

UPLOAD = "/root/.claude/uploads/6ee1d036-9dde-49c8-ba23-22b93133ed2a"
FILES = {"15m": f"{UPLOAD}/e4172c1f-15m.parquet",
         "30m": f"{UPLOAD}/eac34547-30m.parquet",
         "1h":  f"{UPLOAD}/7591e47a-1h.parquet",
         "4h":  f"{UPLOAD}/2e2ebbb8-4h.parquet"}
# max_notional_pct : full1x → size_factor 2.0 atteint 100% notional (1x plein) ;
# lev2x → atteint 200% (2x levier, compte margin).
DEPLOY = {"full1x": 0.50, "lev2x": 1.0, "lev3x": 1.5}


def cfg(strat_params: dict, max_notional=0.50, risk=0.01) -> dict:
    return {
        "trading": {"capital": 1000, "risk_per_trade": risk, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.52, "borrow_rate_daily": 0.00072},
        "backtest": {"spread_pct": 0.0005, "latency_ms": 50, "partial_fill_pct": 0.95,
                     "atr_stop_mult": 2.0, "trail_wide": 3.0, "trail_normal": 2.0,
                     "trail_lock": 1.5, "trail_tight": 1.2, "grace_bars": 3,
                     "breakeven_r": 0.5, "lock_r": 1.5, "tight_r": 5.0,
                     "lock_ratio": 0.60, "use_swing": True,
                     "max_notional_pct": max_notional},
        "strategy_params": {"momentum_blitz": strat_params},
        "optimizer_results": {},
    }


def load(tf):
    return pl.read_parquet(FILES[tf]).sort("time")


def run_tf(tf, params, deploy="full1x", do_mc=False, label=""):
    df = load(tf)
    mn = DEPLOY[deploy]
    eng = Engine(); eng.register(Strategy(), silent=True)
    bt = Backtester(eng, cfg(params, max_notional=mn), use_pretrained_ml=False)
    t0 = time.time()
    res = bt.run(df, "BTC/USDC", timeframe=tf)
    r = res.to_dict()
    dt = time.time() - t0
    setups = {}
    for t in r["trades"]:
        if str(t.get("status", "")).startswith("closed"):
            s = t.get("setup", "?")
            d = setups.setdefault(s, {"n": 0, "pnl": 0.0, "wins": 0})
            d["n"] += 1; d["pnl"] += t.get("pnl", 0.0); d["wins"] += 1 if t.get("pnl", 0) > 0 else 0
    print(f"\n{'═'*74}\n {label or 'momentum_blitz'} · {tf} · deploy={deploy} "
          f"(maxNotional={mn:.0%}) · {len(df)} bougies · {dt:.1f}s\n{'═'*74}")
    eqmult = r['final_equity'] / r['initial_capital']
    print(f"  RENDEMENT : {(eqmult-1)*100:+.1f}%  (x{eqmult:.2f}, equity {r['initial_capital']:.0f}→{r['final_equity']:.0f})")
    print(f"  Buy&Hold  : {r['buy_and_hold_pct']:+.1f}%   ALPHA={r['alpha']:+.0f} USDC")
    print(f"  RISQUE    : max DD {r['max_drawdown']:.1f}%  | Sharpe {r['sharpe']:.2f}  | PF {r['profit_factor']:.2f}")
    print(f"  Trades    : {r['total_trades']}  win {r['win_rate']:.1f}%  | "
          f"avg win/loss {r['avg_win']:+.1f}/{r['avg_loss']:+.1f}  | fees {r['total_fees']:.0f}")
    for s, d in sorted(setups.items(), key=lambda x: -x[1]["pnl"]):
        wr = d["wins"] / d["n"] * 100 if d["n"] else 0
        print(f"     {s:<14} n={d['n']:<5} pnl={d['pnl']:+9.1f}  win={wr:4.1f}%")
    if do_mc:
        mc = MonteCarlo(n_runs=500).run(r["trades"], r["initial_capital"])
        if "error" not in mc:
            print(f"  MONTE-CARLO (500 shuffles) : proba profit={mc['prob_profit']:.0f}%  "
                  f"proba ruine(-10%)={mc['prob_ruin_10pct']:.0f}%  "
                  f"equity p5={mc['final_equity_p5']:.0f} / p95={mc['final_equity_p95']:.0f}  "
                  f"maxDD p95={mc['max_dd_p95']:.0f}%")
    return r


WINDOWS = [("BEAR 2022", "2022-01-01", "2022-12-31"),
           ("BULL 2023-24", "2023-01-01", "2024-03-31"),
           ("CHOP/BULL 24-26", "2024-04-01", "2026-06-30")]


def run_split(tf, params, deploy="full1x"):
    mn = DEPLOY[deploy]
    base = load(tf)
    print(f"\n  ── Robustesse macro-régime · {tf} · {deploy} ──")
    print(f"     {'fenêtre':<18}{'trades':>7}{'PnL%':>8}{'B&H%':>9}{'alpha%':>8}{'DD%':>7}{'PF':>6}")
    for name, d0, d1 in WINDOWS:
        sub = base.filter((pl.col("time") >= pl.lit(d0).str.to_datetime()) &
                          (pl.col("time") <= pl.lit(d1).str.to_datetime()))
        if len(sub) < 300:
            continue
        eng = Engine(); eng.register(Strategy(), silent=True)
        r = Backtester(eng, cfg(params, max_notional=mn), use_pretrained_ml=False
                       ).run(sub, "BTC/USDC", timeframe=tf).to_dict()
        pnl = (r["final_equity"] / r["initial_capital"] - 1) * 100
        print(f"     {name:<18}{r['total_trades']:>7}{pnl:>+8.1f}{r['buy_and_hold_pct']:>+9.1f}"
              f"{pnl-r['buy_and_hold_pct']:>+8.1f}{r['max_drawdown']:>7.1f}{r['profit_factor']:>6.2f}")


def run_wf(tf, params, deploy="full1x"):
    eng = Engine(); eng.register(Strategy(), silent=True)
    r = WalkForwardAnalyzer(eng, cfg(params, max_notional=DEPLOY[deploy]), n_folds=5).run(load(tf), "BTC/USDC")
    if "error" in r:
        print(f"\n  ── WF {tf} : {r['error']}")
    else:
        print(f"\n  ── WF {tf} ({deploy}) : OOS pnl_moy={r['avg_oos_pnl']:+.1f} "
              f"sharpe={r['avg_oos_sharpe']:.2f} consistance={r['consistency']:.0f}%")


def parse_params(argv):
    p = {}
    if "--params" in argv:
        for kv in argv[argv.index("--params") + 1:]:
            if kv.startswith("--"):
                break
            k, v = kv.split("=")
            try:
                p[k] = json.loads(v)
            except Exception:
                p[k] = v
    return p


if __name__ == "__main__":
    tfs = sys.argv[sys.argv.index("--tf") + 1] if "--tf" in sys.argv else "1h,4h"
    deploy = sys.argv[sys.argv.index("--deploy") + 1] if "--deploy" in sys.argv else "full1x"
    params = parse_params(sys.argv)
    for tf in tfs.split(","):
        run_tf(tf, dict(params), deploy=deploy, do_mc="--mc" in sys.argv)
        if "--wf" in sys.argv:
            run_wf(tf, dict(params), deploy)
        if "--split" in sys.argv:
            run_split(tf, dict(params), deploy)
