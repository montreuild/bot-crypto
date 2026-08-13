"""Les `optimizer_results` publiés valent-ils mieux que les défauts du YAML ?

Question posée après le correctif HTF de L5 : ces paramétrages ont été
sélectionnés contre un filtre inerte, faut-il les supprimer ?

**Supprimer n'est pas neutre.** Sans `optimizer_results`, le bot retombe sur le
bloc `params:` du YAML — des valeurs qui n'ont pas davantage été validées contre
le comportement réel. Le choix n'est donc pas « garder du faux » contre « revenir
au propre », mais entre deux jeux non validés. Ce script mesure lequel des deux
se comporte le mieux **une fois le correctif appliqué**.

Deux configurations, tout le reste égal :

    `optimisé`  params + optimizer_results (ce qui tourne aujourd'hui)
    `défauts`   params seuls (ce qu'on obtiendrait en supprimant)

Sur toutes les combinaisons (stratégie, symbole, timeframe) pour lesquelles un
`optimizer_results` existe — y compris les timeframes que la campagne
précédente n'avait jamais mesurés (5m, 15m, 30m, 1d).

Usage :
    python scripts/measure_optimizer_results_value.py --data data/ohlcv
"""
import argparse
import importlib
import json
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.is_oos import split_is_oos  # noqa: E402
from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402

STRATEGIES = [
    "breakout", "breakout_filtreHor", "fear_momentum", "gemini_trend_follow",
    "multi_tf_sr", "pullback_trend", "supertrend_macd", "trend",
]


def yaml_strategie(nom: str) -> tuple:
    import yaml

    f = pathlib.Path(f"strategies/{nom}.yaml")
    if not f.exists():
        return {}, {}
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return dict(d.get("params") or {}), dict(d.get("optimizer_results") or {})


def cfg(nom: str, params: dict, tf: str, opt: dict = None) -> dict:
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
        "optimizer_results": {nom: opt} if opt else {},
    }


def run(nom: str, params: dict, df: pl.DataFrame, symbole: str, tf: str,
        opt: dict = None) -> dict:
    mod = importlib.import_module(f"app.strategies.{nom}")
    eng = Engine()
    eng.register(mod.Strategy(), silent=True)
    d = Backtester(eng, cfg(nom, params, tf, opt), ml_mode="inline").run(
        df, symbole.replace("_", "/"), tf).to_dict()
    return {"n": d["total_trades"], "pnl": round(d["net_profit"], 2),
            "pf": d["profit_factor"], "sharpe": d["sharpe"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ohlcv")
    ap.add_argument("--symboles", default="BTC_USDC,ETH_USDC")
    ap.add_argument("--max-barres", dest="max_barres", type=int, default=60000)
    ap.add_argument("--sortie", default="scripts/_optimizer_results_value.json")
    args = ap.parse_args()

    racine = pathlib.Path(args.data)
    lignes = []
    print(f"  {'stratégie':<20} {'sym':>4} {'tf':>4} "
          f"{'n opt':>6} {'PnL opt':>9} | {'n déf':>6} {'PnL déf':>9} | verdict")
    for nom in STRATEGIES:
        params, opt = yaml_strategie(nom)
        for tf in opt:
            for symbole in args.symboles.split(","):
                chemin = racine / symbole / f"{tf}.parquet"
                if not chemin.exists():
                    continue
                df = pl.read_parquet(chemin)
                if len(df) < 1500:
                    continue
                # Plafond sur les bas timeframes : BTC 5m compte ~500 000
                # barres, dont l'essentiel est du hors-régime ancien. 60 000
                # reste cinq fois la fenêtre des campagnes précédentes, et
                # laisse 1 h / 4 h / 1 j en historique complet.
                if len(df) > args.max_barres:
                    df = df.tail(args.max_barres)
                # Lecture sur l'OOS seulement : l'IS a servi à produire les
                # `optimizer_results`, les y comparer serait leur donner
                # l'avantage du terrain.
                _, df_oos, _ = split_is_oos(df)
                try:
                    o = run(nom, params, df_oos, symbole, tf, {tf: opt[tf]})
                    d = run(nom, params, df_oos, symbole, tf, None)
                except Exception as e:
                    print(f"  {nom:<20} {symbole[:3]:>4} {tf:>4}  "
                          f"ECHEC {type(e).__name__}: {e}")
                    continue
                if o == d:
                    verdict = "identique"
                elif o["pnl"] > d["pnl"]:
                    verdict = "optimisé gagne"
                else:
                    verdict = "DÉFAUTS GAGNENT"
                lignes.append({"strategie": nom, "symbole": symbole, "tf": tf,
                               "optimise": o, "defauts": d, "verdict": verdict})
                print(f"  {nom:<20} {symbole[:3]:>4} {tf:>4} "
                      f"{o['n']:>6} {o['pnl']:>9.1f} | "
                      f"{d['n']:>6} {d['pnl']:>9.1f} | {verdict}")

    n = len(lignes)
    gagne = sum(1 for x in lignes if x["verdict"] == "optimisé gagne")
    perd = sum(1 for x in lignes if x["verdict"] == "DÉFAUTS GAGNENT")
    idem = n - gagne - perd
    print(f"\n══ sur {n} cas : optimisé gagne {gagne}, défauts gagnent {perd}, "
          f"identiques {idem} ══")
    if perd > gagne:
        print("  → les `optimizer_results` sont en moyenne PIRES que les "
              "défauts, une fois le correctif appliqué.")
    pathlib.Path(args.sortie).write_text(
        json.dumps(lignes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"resultats -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
