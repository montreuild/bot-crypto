"""Backtest de volatility_squeeze (réutilise le Backtester du repo).

Usage : python research/backtest_squeeze.py [--tf 4h,1d] [--wf] [--split] [--params k=v ...]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl

from app.engine.backtest import Backtester, WalkForwardAnalyzer
from app.engine.engine import Engine
from app.strategies.volatility_squeeze import Strategy

UPLOAD = "/root/.claude/uploads/6ee1d036-9dde-49c8-ba23-22b93133ed2a"
FILES = {"1h": f"{UPLOAD}/7591e47a-1h.parquet",
         "4h": f"{UPLOAD}/2e2ebbb8-4h.parquet",
         "1d": f"{UPLOAD}/0212ac4e-1d.parquet"}


def cfg(sp):
    return {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "4h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.55, "borrow_rate_daily": 0.00072},
        "backtest": {"spread_pct": 0.0005, "partial_fill_pct": 0.95,
                     "max_notional_pct": 0.50, "trail_wide": 2.5, "grace_bars": 3,
                     "breakeven_r": 0.7, "lock_r": 1.5, "tight_r": 4.0,
                     "trail_tight": 1.0, "use_swing": True},
        "strategy_params": {"volatility_squeeze": sp}, "optimizer_results": {}}


def load(tf):
    return pl.read_parquet(FILES[tf]).sort("time")


def run(tf, sp):
    df = load(tf)
    eng = Engine()
    eng.register(Strategy(), silent=True)
    t0 = time.time()
    r = Backtester(eng, cfg(sp), use_pretrained_ml=False).run(df, "BTC/USDC", timeframe=tf).to_dict()
    dt = time.time() - t0
    setups = {}
    for t in r["trades"]:
        if str(t.get("status", "")).startswith("closed"):
            s = t.get("setup", "?")
            d = setups.setdefault(s, {"n": 0, "pnl": 0.0, "w": 0})
            d["n"] += 1
            d["pnl"] += t.get("pnl", 0.0)
            d["w"] += 1 if t.get("pnl", 0) > 0 else 0
    print(f"\n{'═'*70}\n volatility_squeeze · {tf} · {len(df)} bougies · {dt:.1f}s\n{'═'*70}")
    print(f"  Rendement {(r['final_equity']/r['initial_capital']-1)*100:+.1f}%  | "
          f"B&H {r['buy_and_hold_pct']:+.0f}%  alpha {r['alpha']:+.0f}")
    print(f"  Sharpe {r['sharpe']:.2f} · maxDD {r['max_drawdown']:.1f}% · PF {r['profit_factor']:.2f} "
          f"· win {r['win_rate']:.1f}% · trades {r['total_trades']} · fees {r['total_fees']:.0f}")
    for s, d in sorted(setups.items(), key=lambda x: -x[1]["pnl"]):
        print(f"     {s:<18} n={d['n']:<4} pnl={d['pnl']:+8.1f} win={d['w']/max(d['n'],1)*100:4.1f}%")
    return r


WINDOWS = [("BEAR 2022", "2022-01-01", "2022-12-31"),
           ("BULL 2023-24", "2023-01-01", "2024-03-31"),
           ("CHOP/BULL 24-26", "2024-04-01", "2026-06-30")]


def run_split(tf, sp):
    base = load(tf)
    print(f"\n  ── macro-régime · {tf} ──")
    print(f"     {'fenêtre':<18}{'trades':>7}{'PnL%':>8}{'B&H%':>9}{'alpha%':>8}{'DD%':>7}{'PF':>6}")
    for name, d0, d1 in WINDOWS:
        sub = base.filter((pl.col("time") >= pl.lit(d0).str.to_datetime()) &
                          (pl.col("time") <= pl.lit(d1).str.to_datetime()))
        if len(sub) < 300:
            continue
        eng = Engine()
        eng.register(Strategy(), silent=True)
        r = Backtester(eng, cfg(sp), use_pretrained_ml=False).run(sub, "BTC/USDC", timeframe=tf).to_dict()
        pnl = (r["final_equity"] / r["initial_capital"] - 1) * 100
        print(f"     {name:<18}{r['total_trades']:>7}{pnl:>+8.1f}{r['buy_and_hold_pct']:>+9.1f}"
              f"{pnl - r['buy_and_hold_pct']:>+8.1f}{r['max_drawdown']:>7.1f}{r['profit_factor']:>6.2f}")


def run_wf(tf, sp):
    eng = Engine()
    eng.register(Strategy(), silent=True)
    r = WalkForwardAnalyzer(eng, cfg(sp), 5).run(load(tf), "BTC/USDC")
    if "error" in r:
        print(f"\n  ── WF {tf} : {r['error']}")
    else:
        print(f"\n  ── WF {tf} : OOS pnl_moy={r['avg_oos_pnl']:+.1f} sharpe={r['avg_oos_sharpe']:.2f} "
              f"wr={r['avg_oos_wr']:.0f}% consistance={r['consistency']:.0f}%")


def parse(argv):
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
    tfs = sys.argv[sys.argv.index("--tf") + 1] if "--tf" in sys.argv else "4h,1d"
    sp = parse(sys.argv)
    for tf in tfs.split(","):
        run(tf, dict(sp))
        if "--wf" in sys.argv:
            run_wf(tf, dict(sp))
        if "--split" in sys.argv:
            run_split(tf, dict(sp))
