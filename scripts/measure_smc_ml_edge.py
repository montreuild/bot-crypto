"""Backtest de `smc_ml_edge` — le seul juge du travail SMC.

Tout ce qui precede parlait d'AUC, donc de qualite de CLASSEMENT. Une AUC dir
de 0.60 ne dit rien de la rentabilite nette de frais : il faut que les trades
selectionnes paient le spread, les frais et les stops. Ce script le mesure.

Variantes croisees pour attribuer chaque effet :

    filtre SMC on/off   -- le modele voit deja les colonnes SMC ; un filtre dur
                           par-dessus peut n'etre que du retrait de trades
    selectivite         -- top 5 % / 10 % / 20 % des barres

La reference est le buy & hold de la meme fenetre : une strategie longue en
marche haussier peut afficher un PnL positif sans avoir le moindre edge.

Usage :  python scripts/measure_smc_ml_edge.py
"""
import json
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402

NOM = "smc_ml_edge"
BARRES = 12000


def _cfg(params: dict) -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0,
                     "trail_wide": 2.5, "trail_normal": 2.0, "trail_lock": 1.5,
                     "trail_tight": 1.0, "grace_bars": 4, "breakeven_r": 1.2,
                     "lock_r": 2.5, "tight_r": 4.0, "lock_ratio": 0.6,
                     "use_swing": False, "max_notional_pct": 0.2},
        "strategies": {"enabled": [NOM]},
        "strategy_params": {NOM: params},
    }


def _run(df: pl.DataFrame, symbole: str, tf: str, params: dict) -> dict:
    from app.strategies.smc_ml_edge import Strategy
    strat = Strategy()
    eng = Engine()
    eng.register(strat, silent=True)
    bt = Backtester(eng, _cfg(params), ml_mode="inline")
    res = bt.run(df, symbole, tf)

    trades = list(getattr(res, "trades", []) or [])
    longs = sum(1 for t in trades if str(t.get("side")) == "long")
    shorts = sum(1 for t in trades if str(t.get("side")) == "short")

    def g(nom, defaut=None):
        v = getattr(res, nom, defaut)
        return round(float(v), 4) if isinstance(v, (int, float)) else v

    capital = float(getattr(res, "initial_capital", 0) or 0) or 1.0
    return {
        "n_trades": len(trades), "longs": longs, "shorts": shorts,
        "winrate": g("win_rate"),
        "pnl_pct": round(100.0 * float(getattr(res, "total_pnl", 0) or 0) / capital, 3),
        "buy_hold_pct": g("buy_and_hold_pct"),
        "alpha_vs_bh": g("alpha_vs_bh"),
        "sharpe": g("sharpe"), "sortino": g("sortino"), "calmar": g("calmar"),
        "max_dd_pct": g("max_drawdown"), "profit_factor": g("profit_factor"),
        "expectancy": g("expectancy"),
        "fees_pct": round(100.0 * float(getattr(res, "total_fees", 0) or 0) / capital, 3),
    }


def main() -> int:
    variantes = [
        ("top10 + filtre SMC", {"amp_top_q": 0.10, "dir_top_q": 0.20, "use_smc_filter": True}),
        ("top10 sans filtre",  {"amp_top_q": 0.10, "dir_top_q": 0.20, "use_smc_filter": False}),
        ("top20 sans filtre",  {"amp_top_q": 0.20, "dir_top_q": 0.35, "use_smc_filter": False}),
        ("top5 sans filtre",   {"amp_top_q": 0.05, "dir_top_q": 0.10, "use_smc_filter": False}),
    ]
    base = {"warmup_bars": 3000, "retrain_every": 2000, "train_window": 8000,
            "sl_atr_mult": 1.5, "tp_atr_mult": 2.5, "max_hold_bars": 12}

    lignes = []
    for symbole, tf in (("BTC_USDC", "1h"), ("ETH_USDC", "1h")):
        chemin = pathlib.Path(f"data/ohlcv/{symbole}/{tf}.parquet")
        if not chemin.exists():
            continue
        df = pl.read_parquet(chemin).tail(BARRES)
        print(f"\n=== {symbole} {tf} ({len(df)} barres) ===", flush=True)
        for nom, surcharge in variantes:
            t0 = time.time()
            try:
                r = _run(df, symbole.replace("_", "/"), tf, {**base, **surcharge})
            except Exception as e:
                print(f"  {nom:<22} ECHEC {type(e).__name__}: {e}", flush=True)
                continue
            r.update({"symbole": symbole, "tf": tf, "variante": nom,
                      "secondes": round(time.time() - t0, 1)})
            lignes.append(r)
            print(f"  {nom:<22} trades={r['n_trades']:<4} "
                  f"(L{r['longs']}/S{r['shorts']}) win={r['winrate']}% "
                  f"pnl={r['pnl_pct']}% bh={r['buy_hold_pct']}% "
                  f"alpha={r['alpha_vs_bh']} sharpe={r['sharpe']} "
                  f"dd={r['max_dd_pct']}% pf={r['profit_factor']} "
                  f"frais={r['fees_pct']}% ({r['secondes']}s)", flush=True)

    pathlib.Path("scripts/_smc_edge_results.json").write_text(
        json.dumps(lignes, indent=2, default=str), encoding="utf-8")
    print("\nresultats -> scripts/_smc_edge_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
