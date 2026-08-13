"""L4 — la hiérarchie de liquidité correspond-elle à quelque chose ?

Deux questions :

1. **`by_target_class`** — une cible « plus haut de la semaine » est-elle
   vraiment plus souvent atteinte qu'un swing local ? C'est le postulat de §77,
   et rien ne l'avait vérifié.
2. **`target_mode: expected_value`** — choisir la cible de meilleure valeur
   attendue plutôt que la plus proche améliore-t-il le résultat ?

Protocole imposé par L3 : toute règle est lue sur les DEUX fenêtres. Une règle
qui ne gagne que là où elle a été choisie ne vaut rien
(cf. docs/MOTEUR_STRUCTURE_SEQUENTIEL.md §3).

Usage :
    python scripts/measure_target_quality.py --data data/ohlcv
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


VARIANTES = {
    "actuel":            {},
    "expected_value":    {"target_mode": "expected_value",
                          "use_calendar_liquidity": "targets"},
    "stop_max_4atr":     {"max_stop_atr": 4.0},
    "les_deux":          {"target_mode": "expected_value",
                          "use_calendar_liquidity": "targets",
                          "max_stop_atr": 4.0},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ohlcv")
    ap.add_argument("--barres", type=int, default=12000)
    ap.add_argument("--sortie", default="scripts/_target_quality.json")
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
            fenetres = {"IS": df.head(coupe), "OOS": df.tail(len(df) - coupe + 300)}
            print(f"\n=== {symbole} {tf} ===")

            # §77 — la hiérarchie tient-elle ? Lecture sur la variante qui
            # produit des cibles calendaires, sinon toutes les classes se
            # confondent en « pool ».
            ref = run({**base, "use_calendar_liquidity": "targets"},
                      fenetres["OOS"], symbole, tf)
            if ref["by_target_class"]:
                print(f"  {'classe visée':<16} {'n':>4} {'PnL':>10} {'win%':>6} {'PF':>7}")
                for cl, d in sorted(ref["by_target_class"].items(),
                                    key=lambda kv: -kv[1]["total_trades"]):
                    print(f"  {cl:<16} {d['total_trades']:>4} "
                          f"{d['total_pnl']:>10.2f} {d['win_rate']:>6} "
                          f"{d['profit_factor']:>7}")

            res_v = {}
            print(f"  {'variante':<16} {'fenêtre':>7} {'n':>4} {'PnL':>10} "
                  f"{'PF':>7} {'DD%':>8}")
            for nom, surcharge in VARIANTES.items():
                for fen, sous_df in fenetres.items():
                    d = run({**base, **surcharge}, sous_df, symbole, tf)
                    res_v[f"{nom}/{fen}"] = {
                        "n": d["total_trades"], "pnl": d["net_profit"],
                        "pf": d["profit_factor"], "dd": d["max_drawdown"]}
                    print(f"  {nom:<16} {fen:>7} {d['total_trades']:>4} "
                          f"{d['net_profit']:>10.2f} {d['profit_factor']:>7} "
                          f"{d['max_drawdown']:>8}")

            lignes.append({
                "symbole": symbole, "tf": tf,
                "par_classe": {k: {"n": v["total_trades"], "pnl": v["total_pnl"],
                                   "win": v["win_rate"], "pf": v["profit_factor"]}
                               for k, v in ref["by_target_class"].items()},
                "variantes": res_v,
            })

    pathlib.Path(args.sortie).write_text(
        json.dumps(lignes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nresultats -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
