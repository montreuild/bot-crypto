"""L3 — la mémoire de structure change-t-elle quelque chose ?

Deux questions, deux mesures :

1. **Entrer en `WARNING` est-il pire qu'entrer en `CONFIRMED` ?** C'est le
   postulat de §62 : un premier MSS contraire ne retourne pas le biais externe.
   S'il est faux, le mécanisme n'a pas à exister. Répondu par `by_structure_state`
   sur une passe SANS porte — l'état est journalisé même quand il ne filtre rien.

2. **La porte `structure_gate` améliore-t-elle le résultat OOS ?** Comparaison
   à signaux par ailleurs identiques.

Le résultat attendu n'est pas « la porte gagne ». Un état qui ne sépare rien est
un résultat exploitable : il dit de ne pas payer sa complexité.

Usage :
    python scripts/measure_structure_gate.py --data data/ohlcv
"""
import argparse
import json
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402


def yaml_params(nom: str) -> dict:
    import yaml

    f = pathlib.Path(f"strategies/{nom}.yaml")
    if not f.exists():
        return {}
    return dict((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("params") or {})


def cfg(params: dict, tf: str) -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": tf,
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0, "trail_wide": 2.5,
                     "trail_normal": 2.0, "trail_lock": 1.5, "trail_tight": 1.0,
                     "grace_bars": 4, "breakeven_r": 1.2, "lock_r": 2.5,
                     "tight_r": 4.0, "lock_ratio": 0.6, "use_swing": False,
                     "max_notional_pct": 0.2},
        "strategies": {"enabled": ["smart_money"]},
        "strategy_params": {"smart_money": params},
    }


def run(params: dict, df: pl.DataFrame, symbole: str, tf: str) -> dict:
    from app.strategies.smart_money import Strategy

    eng = Engine()
    eng.register(Strategy(), silent=True)
    return Backtester(eng, cfg(params, tf), ml_mode="inline").run(
        df, symbole.replace("_", "/"), tf).to_dict()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ohlcv")
    ap.add_argument("--barres", type=int, default=12000)
    ap.add_argument("--sortie", default="scripts/_structure_gate.json")
    args = ap.parse_args()

    base = yaml_params("smart_money")
    racine = pathlib.Path(args.data)
    lignes = []

    for symbole in ("BTC_USDC", "ETH_USDC"):
        for tf in ("1h", "4h"):
            chemin = racine / symbole / f"{tf}.parquet"
            if not chemin.exists():
                continue
            df = pl.read_parquet(chemin).tail(args.barres)
            coupe = int(len(df) * 0.65)
            oos = df.tail(len(df) - coupe + 300)

            # ⚠ `no_pullback` a été choisi APRÈS lecture du découpage par état
            # sur la fenêtre OOS : c'est une sélection sur le test. La fenêtre
            # IS n'a pas servi à former l'hypothèse — elle en est donc la
            # vérification indépendante, et c'est elle qui fait foi ici.
            i_s = df.head(coupe)
            modes = {m: run({**base, "structure_gate": m}, oos, symbole, tf)
                     for m in ("off", "direction", "no_pullback", "both")}
            modes_is = {m: run({**base, "structure_gate": m}, i_s, symbole, tf)
                        for m in ("off", "no_pullback")}
            sans = modes["off"]

            print(f"\n=== {symbole} {tf} — OOS {len(oos)} barres ===")
            print("  par état de structure (porte OFF — l'état est journalisé "
                  "même quand il ne filtre rien) :")
            print(f"    {'état':<28} {'n':>4} {'PnL':>10} {'win%':>6} {'PF':>7}")
            for etat, d in sorted(sans["by_structure_state"].items(),
                                  key=lambda kv: -kv[1]["total_trades"]):
                print(f"    {etat:<28} {d['total_trades']:>4} "
                      f"{d['total_pnl']:>10.2f} {d['win_rate']:>6} "
                      f"{d['profit_factor']:>7}")

            print(f"  {'mode de porte':<14} {'n':>4} {'PnL':>10} {'PF':>7} {'DD%':>8}")
            for m, d in modes.items():
                print(f"  {m:<14} {d['total_trades']:>4} "
                      f"{d['net_profit']:>10.2f} {d['profit_factor']:>7} "
                      f"{d['max_drawdown']:>8}")

            print(f"  vérification IS ({coupe} barres, non utilisées pour "
                  f"former l'hypothèse) :")
            for m, d in modes_is.items():
                print(f"    {m:<12} {d['total_trades']:>4} "
                      f"{d['net_profit']:>10.2f} {d['profit_factor']:>7} "
                      f"{d['max_drawdown']:>8}")

            lignes.append({
                "symbole": symbole, "tf": tf,
                "verif_is": {m: {"n": d["total_trades"], "pnl": d["net_profit"],
                                 "pf": d["profit_factor"], "dd": d["max_drawdown"]}
                             for m, d in modes_is.items()},
                "par_etat": {k: {"n": v["total_trades"], "pnl": v["total_pnl"],
                                 "win": v["win_rate"], "pf": v["profit_factor"]}
                             for k, v in sans["by_structure_state"].items()},
                "portes": {m: {"n": d["total_trades"], "pnl": d["net_profit"],
                               "pf": d["profit_factor"], "dd": d["max_drawdown"]}
                           for m, d in modes.items()},
            })

    pathlib.Path(args.sortie).write_text(
        json.dumps(lignes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nresultats -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
