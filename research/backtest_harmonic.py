"""Backtest de la stratégie harmonic_regime sur BTC 1h/4h/1d via le Backtester du repo.

Usage : python research/backtest_harmonic.py [--tf 1h,4h,1d] [--limit N] [--params k=v ...]
Réutilise frais/spread/borrow réalistes (config.yaml) + benchmark Buy&Hold/alpha.
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl
from app.engine.engine import Engine
from app.engine.backtest import Backtester, WalkForwardAnalyzer
from app.strategies.harmonic_regime import Strategy

UPLOAD = "/root/.claude/uploads/6ee1d036-9dde-49c8-ba23-22b93133ed2a"
FILES = {"1h": f"{UPLOAD}/7591e47a-1h.parquet",
         "4h": f"{UPLOAD}/2e2ebbb8-4h.parquet",
         "1d": f"{UPLOAD}/0212ac4e-1d.parquet"}


def cfg(strat_params: dict) -> dict:
    return {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.55, "borrow_rate_daily": 0.00072},
        "backtest": {"spread_pct": 0.0005, "latency_ms": 50, "partial_fill_pct": 0.95,
                     "atr_stop_mult": 2.0, "trail_wide": 2.5, "trail_normal": 2.0,
                     "trail_lock": 1.5, "trail_tight": 1.0, "grace_bars": 4,
                     "breakeven_r": 1.2, "lock_r": 2.5, "tight_r": 4.0,
                     "lock_ratio": 0.60, "use_swing": True, "max_notional_pct": 0.50},
        "strategy_params": {"harmonic_regime": strat_params},
        "optimizer_results": {},
    }


def load(tf: str, limit: int = None) -> pl.DataFrame:
    df = pl.read_parquet(FILES[tf]).sort("time")
    if limit:
        df = df.tail(limit)
    return df


def run_tf(tf: str, params: dict, limit: int = None, label: str = "") -> dict:
    df = load(tf, limit)
    eng = Engine()
    eng.register(Strategy(), silent=True)
    bt = Backtester(eng, cfg(params), use_pretrained_ml=False)
    t0 = time.time()
    res = bt.run(df, "BTC/USDC", timeframe=tf).to_dict()
    dt = time.time() - t0
    # Répartition par setup
    setups = {}
    for t in res["trades"]:
        if str(t.get("status", "")).startswith("closed"):
            s = t.get("setup", "?")
            d = setups.setdefault(s, {"n": 0, "pnl": 0.0, "wins": 0})
            d["n"] += 1
            d["pnl"] += t.get("pnl", 0.0)
            d["wins"] += 1 if t.get("pnl", 0) > 0 else 0
    res["_setups"] = setups
    res["_dt"] = dt
    res["_tf"] = tf
    res["_bars"] = len(df)
    _print(res, label)
    return res


def _print(r: dict, label: str = ""):
    tf = r["_tf"]
    print(f"\n{'═'*72}")
    print(f" {label or 'harmonic_regime'} · {tf} · {r['_bars']} bougies "
          f"· {r['_dt']:.1f}s")
    print(f"{'═'*72}")
    print(f"  PnL net        : {r['total_pnl']:+.2f} USDC  "
          f"(equity {r['initial_capital']:.0f} → {r['final_equity']:.0f}, "
          f"{(r['final_equity']/r['initial_capital']-1)*100:+.1f}%)")
    print(f"  Buy & Hold     : {r['buy_and_hold_pnl']:+.2f} USDC "
          f"({r['buy_and_hold_pct']:+.1f}%)   ALPHA = {r['alpha']:+.2f} USDC")
    print(f"  Trades         : {r['total_trades']}  | win {r['win_rate']:.1f}%  "
          f"| PF {r['profit_factor']:.2f}  | expectancy {r['expectancy']:+.3f}")
    print(f"  Sharpe         : {r['sharpe']:.2f}   | max DD {r['max_drawdown']:.1f}%  "
          f"| fees {r['total_fees']:.1f}")
    print(f"  avg win/loss   : {r['avg_win']:+.2f} / {r['avg_loss']:+.2f}  "
          f"| MAE {r.get('avg_mae',0):.2f}% MFE {r.get('avg_mfe',0):.2f}%")
    if r["_setups"]:
        print(f"  par setup :")
        for s, d in sorted(r["_setups"].items(), key=lambda x: -x[1]["pnl"]):
            wr = d["wins"] / d["n"] * 100 if d["n"] else 0
            print(f"     {s:<14} n={d['n']:<5} pnl={d['pnl']:+9.2f}  win={wr:4.1f}%")
    dg = r.get("diagnostics") or {}
    print(f"  diag: signaux acceptés={dg.get('signal_accepted','?')} "
          f"rej_notional={dg.get('rejected_notional','?')} "
          f"%temps en position={100*dg.get('bars_in_position',0)/max(dg.get('bars_total',1),1):.0f}%")


def run_wf(tf: str, params: dict, folds: int = 5):
    df = load(tf)
    eng = Engine()
    eng.register(Strategy(), silent=True)
    wf = WalkForwardAnalyzer(eng, cfg(params), n_folds=folds)
    r = wf.run(df, "BTC/USDC")
    print(f"\n  ── Walk-Forward {tf} ({folds} folds) ──")
    if "error" in r:
        print(f"     {r['error']}")
        return
    print(f"     OOS: pnl_moy={r['avg_oos_pnl']:+.2f}  sharpe_moy={r['avg_oos_sharpe']:.2f}  "
          f"wr_moy={r['avg_oos_wr']:.1f}%  consistance={r['consistency']:.0f}% des folds >0")


# Fenêtres macro pour tester bull vs bear (cœur de la demande)
WINDOWS = [
    ("BEAR 2022",       "2022-01-01", "2022-12-31"),
    ("BULL 2023-24",    "2023-01-01", "2024-03-31"),
    ("CHOP/BULL 24-26", "2024-04-01", "2026-06-30"),
    ("BEAR 2018-19",    "2018-12-15", "2019-12-31"),
]


def run_split(tf: str, params: dict):
    import datetime as _dt
    print(f"\n  ── Robustesse par macro-régime · {tf} ──")
    print(f"     {'fenêtre':<18} {'trades':>6} {'PnL%':>7} {'B&H%':>8} {'alpha%':>7} "
          f"{'PF':>5} {'DD%':>6} {'Sharpe':>7}")
    base = load(tf)
    for name, d0, d1 in WINDOWS:
        sub = base.filter((pl.col("time") >= pl.lit(d0).str.to_datetime()) &
                          (pl.col("time") <= pl.lit(d1).str.to_datetime()))
        if len(sub) < 300:
            continue
        eng = Engine(); eng.register(Strategy(), silent=True)
        bt = Backtester(eng, cfg(params), use_pretrained_ml=False)
        r = bt.run(sub, "BTC/USDC", timeframe=tf).to_dict()
        pnl_pct = (r["final_equity"] / r["initial_capital"] - 1) * 100
        alpha_pct = pnl_pct - r["buy_and_hold_pct"]
        print(f"     {name:<18} {r['total_trades']:>6} {pnl_pct:>+7.1f} "
              f"{r['buy_and_hold_pct']:>+8.1f} {alpha_pct:>+7.1f} "
              f"{r['profit_factor']:>5.2f} {r['max_drawdown']:>6.1f} {r['sharpe']:>7.2f}")


def parse_params(argv) -> dict:
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
    tfs = "1h,4h,1d"
    limit = None
    if "--tf" in sys.argv:
        tfs = sys.argv[sys.argv.index("--tf") + 1]
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    params = parse_params(sys.argv)
    for tf in tfs.split(","):
        run_tf(tf, dict(params), limit=limit)
        if "--wf" in sys.argv:
            run_wf(tf, dict(params))
        if "--split" in sys.argv:
            run_split(tf, dict(params))
