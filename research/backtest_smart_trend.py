"""Backtest local (parquet) de la stratégie `smart_trend_adx`, en répliquant les
hypothèses du backtest TradingView du PineScript source :

  - 100 % de l'équité par trade (percent_of_equity=100) ;
  - commission 0,05 % par côté (taker = maker = 0.0005) ;
  - pas de slippage/spread, pas de coût d'emprunt (spot) ;
  - SL 1.2×ATR / TP 2.5×ATR fixes (pas de trailing).

Les trades sont filtrés sur la fenêtre TradingView [2024-01-01, 2026-07-01].
On teste plusieurs timeframes (le TV n'ayant pas été précisé) et on affiche
nb de trades / gagnants / win-rate / profit factor / rendement composé.

Usage : python research/backtest_smart_trend.py
"""
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402
from app.strategies.smart_trend_adx import Strategy  # noqa: E402

WIN_START = "2024-01-01"
WIN_END   = "2026-07-01"
WARMUP_START = "2023-01-01"   # marge avant la fenêtre pour réchauffer EMA200/ADX
TIMEFRAMES = ["1h", "2h", "4h", "1d"]
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "ohlcv", "BTC_USDC")


def _cfg() -> dict:
    """Config répliquant les hypothèses TradingView (sizing 100 % équité, 0,05 %)."""
    return {
        "trading": {
            "capital": 1000.0,
            "risk_per_trade": 0.15,      # élevé → le cap notionnel (100 %) borne toujours
            "score_threshold": 0.5,
            "taker_fee": 0.0005,         # 0,05 % / côté (comme le Pine)
            "maker_fee": 0.0005,
            "borrow_rate_daily": 0.0,    # spot, pas d'emprunt
            "borrow_periods_per_day": 24,
            "timeframe": "1h",
        },
        "backtest": {
            "spread_pct": 0.0,           # pas de slippage (comme TradingView par défaut)
            "partial_fill_pct": 1.0,     # remplissage complet
            "max_notional_pct": 1.0,     # 100 % de l'équité par trade
        },
        "strategy_params": {},           # défauts = valeurs exactes du PineScript
        "strategies": {"enabled": ["smart_trend_adx"]},
    }


def _load(tf: str) -> pl.DataFrame | None:
    path = os.path.join(DATA_DIR, f"{tf}.parquet")
    if not os.path.exists(path):
        return None
    df = pl.read_parquet(path)
    return df.filter(pl.col("time") >= pl.datetime(2023, 1, 1)).sort("time")


def _in_window(entry_time: str) -> bool:
    d = str(entry_time)[:10]
    return WIN_START <= d < WIN_END


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
    # Rendement composé : chaque trade engage 100 % de l'équité → facteur (1 + pnl/notional).
    eq = 1.0
    for t in closed:
        notional = t.get("notional", 0) or 0
        if notional > 0:
            eq *= (1 + t["pnl"] / notional)
    ret_pct = (eq - 1) * 100
    return {
        "n": n,
        "wins": len(wins),
        "win_rate": len(wins) / n * 100,
        "pf": pf,
        "ret_pct": ret_pct,
        "longs": sum(1 for t in closed if t["side"] == "long"),
        "shorts": sum(1 for t in closed if t["side"] == "short"),
        "tp": sum(1 for t in closed if t.get("exit_reason") == "take_profit"),
        "sl": sum(1 for t in closed if "stop" in str(t.get("exit_reason", ""))),
        "avg_win_pct":  (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss_pct": (sum(t["pnl_pct"] for t in closed if t["pnl"] <= 0)
                         / max(n - len(wins), 1)),
    }


def main():
    print(f"Backtest smart_trend_adx — BTC/USDC — fenêtre {WIN_START} → {WIN_END}")
    print("Réf. TradingView : +19 %, 63 trades, 16 gagnants, PF 1.451\n")
    header = (f"{'TF':>4} | {'trades':>6} | {'gagnants':>8} | {'win%':>6} | "
             f"{'PF':>6} | {'ret%':>8} | {'L/S':>7} | {'TP/SL':>7}")
    print(header)
    print("-" * len(header))
    rows = []
    for tf in TIMEFRAMES:
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
            rows.append((tf, m))
            continue
        pf_s = "inf" if m["pf"] == float("inf") else f"{m['pf']:.3f}"
        print(f"{tf:>4} | {m['n']:>6} | {m['wins']:>8} | {m['win_rate']:>5.1f}% | "
              f"{pf_s:>6} | {m['ret_pct']:>+7.2f}% | {m['longs']}/{m['shorts']:>3} | "
              f"{m['tp']}/{m['sl']:>3}")
        rows.append((tf, m))
    print()
    for tf, m in rows:
        if m.get("n"):
            print(f"  {tf}: avg win {m['avg_win_pct']:+.2f}% · avg loss {m['avg_loss_pct']:+.2f}% "
                  f"· TP={m['tp']} SL={m['sl']}")


if __name__ == "__main__":
    main()
