"""Backtest local (parquet) d'une stratégie portée depuis un PineScript, en
répliquant les hypothèses TradingView :

  - 100 % de l'équité par trade ;
  - commission 0,05 % par côté (taker = maker = 0.0005) ;
  - pas de slippage/spread, pas de coût d'emprunt (spot).

Trades filtrés sur la fenêtre TradingView [2024-01-01, 2026-07-01], sur plusieurs
timeframes. Affiche trades / gagnants / win-rate / profit factor / rendement composé.

Usage : python research/backtest_pine.py <strategy_name> [tf1,tf2,...]
        (défaut : liquidity_sweep_vol, TFs 1h,2h,4h,1d)
"""
import importlib
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.engine import Engine            # noqa: E402
from app.engine.backtest import Backtester      # noqa: E402

WIN_START, WIN_END = "2024-01-01", "2026-07-01"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "ohlcv", "BTC_USDC")


def _cfg() -> dict:
    return {
        "trading": {
            "capital": 1000.0, "risk_per_trade": 0.15, "score_threshold": 0.5,
            "taker_fee": 0.0005, "maker_fee": 0.0005,
            "borrow_rate_daily": 0.0, "borrow_periods_per_day": 24, "timeframe": "1h",
        },
        "backtest": {"spread_pct": 0.0, "partial_fill_pct": 1.0, "max_notional_pct": 1.0},
        "strategy_params": {},
    }


def _load(tf: str):
    path = os.path.join(DATA_DIR, f"{tf}.parquet")
    if not os.path.exists(path):
        return None
    return pl.read_parquet(path).filter(pl.col("time") >= pl.datetime(2023, 1, 1)).sort("time")


def _in_window(t: str) -> bool:
    return WIN_START <= str(t)[:10] < WIN_END


def _metrics(trades: list) -> dict:
    closed = [t for t in trades if str(t.get("status", "")).startswith("closed")
              and _in_window(t.get("entry_time", ""))]
    closed.sort(key=lambda t: str(t.get("entry_time", "")))
    n = len(closed)
    if n == 0:
        return {"n": 0}
    wins = [t for t in closed if t["pnl"] > 0]
    gross_win  = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in closed if t["pnl"] <= 0))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    eq = 1.0
    for t in closed:
        notional = t.get("notional", 0) or 0
        if notional > 0:
            eq *= (1 + t["pnl"] / notional)
    return {
        "n": n, "wins": len(wins), "win_rate": len(wins) / n * 100, "pf": pf,
        "ret_pct": (eq - 1) * 100,
        "longs": sum(1 for t in closed if t["side"] == "long"),
        "shorts": sum(1 for t in closed if t["side"] == "short"),
        "tp": sum(1 for t in closed if t.get("exit_reason") == "take_profit"),
        "sl": sum(1 for t in closed if "stop" in str(t.get("exit_reason", ""))),
    }


def main():
    strat_name = sys.argv[1] if len(sys.argv) > 1 else "liquidity_sweep_vol"
    tfs = (sys.argv[2].split(",") if len(sys.argv) > 2 else ["1h", "2h", "4h", "1d"])
    Strategy = importlib.import_module(f"app.strategies.{strat_name}").Strategy

    print(f"Backtest {strat_name} — BTC/USDC — fenêtre {WIN_START} → {WIN_END}\n")
    header = f"{'TF':>4} | {'trades':>6} | {'gagnants':>8} | {'win%':>6} | {'PF':>6} | {'ret%':>8} | {'L/S':>7} | {'TP/SL':>7}"
    print(header)
    print("-" * len(header))
    for tf in tfs:
        df = _load(tf)
        if df is None or len(df) < 300:
            print(f"{tf:>4} | données insuffisantes")
            continue
        eng = Engine()
        eng.register(Strategy(), silent=True)
        res = Backtester(eng, _cfg()).run(df, "BTC/USDC", timeframe=tf)
        m = _metrics(res.to_dict().get("trades", []))
        if m["n"] == 0:
            print(f"{tf:>4} | 0 trade sur la fenêtre")
            continue
        pf_s = "inf" if m["pf"] == float("inf") else f"{m['pf']:.3f}"
        print(f"{tf:>4} | {m['n']:>6} | {m['wins']:>8} | {m['win_rate']:>5.1f}% | "
              f"{pf_s:>6} | {m['ret_pct']:>+7.2f}% | {m['longs']}/{m['shorts']:>3} | "
              f"{m['tp']}/{m['sl']:>3}")


if __name__ == "__main__":
    main()
