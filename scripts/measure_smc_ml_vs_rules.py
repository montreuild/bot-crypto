"""SMC piloté par des RÈGLES contre SMC piloté par un MODÈLE.

Le dossier SMC a montré que la structure de marché est ce qui fait le plus
progresser la tête direction. Question naturelle : une stratégie non-ML qui
exploite ces mêmes structures fait-elle aussi bien, ou mieux ?

Le dépôt contient déjà une telle stratégie — `smart_money`, 1 616 lignes
réparties en 5 setups (balayage-retournement, sweep calendaire, retest d'order
block, retest de breaker, retournement BPR), avec confluence volume / FVG
inversé / chandeliers et divergence SMT. Elle n'avait jamais été mesurée contre
`smc_ml_edge`.

DEUX PRÉCAUTIONS DE LOYAUTÉ, apprises d'une première version fausse :

1. **Charger les paramètres du YAML**, pas les défauts de code. `smart_money`
   a été optimisée par timeframe et par symbole ; la juger sur des valeurs que
   personne ne fait tourner ne mesurerait rien.
2. **Comparer sur SON timeframe.** Son YAML documente que seul le 4 h est
   tradable (oos_score +0.37) et que le 1 h y est négatif. La tester en 1 h
   seulement reviendrait à confirmer ce qu'elle annonce déjà.

Usage :  python scripts/measure_smc_ml_vs_rules.py
"""
import json
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402

BARRES = 12000


def yaml_strategie(nom: str) -> tuple:
    """``(params, optimizer_results)`` du YAML de la stratégie."""
    import yaml

    f = pathlib.Path(f"strategies/{nom}.yaml")
    if not f.exists():
        return {}, {}
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return dict(d.get("params") or {}), dict(d.get("optimizer_results") or {})


def cfg(nom: str, params: dict, tf: str, opt_results: dict = None) -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": tf,
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0, "trail_wide": 2.5,
                     "trail_normal": 2.0, "trail_lock": 1.5, "trail_tight": 1.0,
                     "grace_bars": 4, "breakeven_r": 1.2, "lock_r": 2.5,
                     "tight_r": 4.0, "lock_ratio": 0.6, "use_swing": False,
                     "max_notional_pct": 0.2},
        "strategies": {"enabled": [nom]},
        "strategy_params": {nom: params},
        # Superposé par `resolve_strategy_params(cfg, tf, symbole)` — le chemin
        # emprunté à l'identique par le backtest et par le live.
        "optimizer_results": {nom: opt_results} if opt_results else {},
    }


def run(nom: str, fabrique, df: pl.DataFrame, symbole: str, params: dict,
        tf: str, opt_results: dict = None) -> dict:
    eng = Engine()
    eng.register(fabrique(), silent=True)
    res = Backtester(eng, cfg(nom, params, tf, opt_results),
                     ml_mode="inline").run(df, symbole, tf)
    trades = list(getattr(res, "trades", []) or [])
    cap = float(getattr(res, "initial_capital", 1) or 1)

    def g(attr, defaut=0.0):
        v = getattr(res, attr, defaut)
        return round(float(v), 3) if isinstance(v, (int, float)) else defaut

    return {
        "n_trades": len(trades),
        "longs": sum(1 for t in trades if str(t.get("side")) == "long"),
        "shorts": sum(1 for t in trades if str(t.get("side")) == "short"),
        "win": g("win_rate"),
        "pnl_pct": round(100.0 * float(getattr(res, "total_pnl", 0) or 0) / cap, 2),
        "bh_pct": g("buy_and_hold_pct"),
        "sharpe": g("sharpe"), "dd_pct": g("max_drawdown"),
        "pf": g("profit_factor"),
        "fees_pct": round(100.0 * float(getattr(res, "total_fees", 0) or 0) / cap, 2),
    }


def main() -> int:
    def ml():
        from app.strategies.smc_ml_edge import Strategy
        return Strategy()

    def regles():
        from app.strategies.smart_money import Strategy
        return Strategy()

    p_ml = {"warmup_bars": 3000, "retrain_every": 2000, "train_window": 8000,
            "amp_top_q": 0.10, "dir_top_q": 0.20, "use_smc_filter": True,
            "sl_atr_mult": 2.5, "tp_atr_mult": 2.5, "max_hold_bars": 12}
    p_sm, opt_sm = yaml_strategie("smart_money")

    concurrents = [
        ("smc_ml_edge", ml, p_ml, None),
        ("smart_money", regles, p_sm, opt_sm),
    ]

    lignes = []
    for symbole in ("BTC_USDC", "ETH_USDC"):
        for tf in ("1h", "4h"):
            chemin = pathlib.Path(f"data/ohlcv/{symbole}/{tf}.parquet")
            if not chemin.exists():
                continue
            df = pl.read_parquet(chemin).tail(BARRES)
            print(f"\n=== {symbole} {tf} ({len(df)} barres) ===", flush=True)
            for nom, fabrique, params, opt in concurrents:
                t0 = time.time()
                try:
                    r = run(nom, fabrique, df, symbole.replace("_", "/"),
                            params, tf, opt)
                except Exception as e:
                    print(f"  {nom:<14} ECHEC {type(e).__name__}: {e}", flush=True)
                    continue
                r.update(symbole=symbole, tf=tf, strategie=nom,
                         secondes=round(time.time() - t0, 1))
                lignes.append(r)
                print(f"  {nom:<14} trades={r['n_trades']:<4} "
                      f"(L{r['longs']}/S{r['shorts']}) win={r['win']}% "
                      f"pnl={r['pnl_pct']}% bh={r['bh_pct']}% "
                      f"sharpe={r['sharpe']} dd={r['dd_pct']}% pf={r['pf']} "
                      f"frais={r['fees_pct']}% ({r['secondes']}s)", flush=True)

    pathlib.Path("scripts/_ml_vs_rules.json").write_text(
        json.dumps(lignes, indent=2), encoding="utf-8")
    print("\nresultats -> scripts/_ml_vs_rules.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
