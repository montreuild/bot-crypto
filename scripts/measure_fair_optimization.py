"""Comparaison ÉQUITABLE : chaque stratégie optimisée sur chaque timeframe.

La mesure précédente opposait `smc_ml_edge` réglée à la main en 1 h à
`smart_money` optimisée en 4 h. Chacune gagnait sur son terrain, ce qui ne
prouve rien : on comparait deux réglages, pas deux stratégies.

Ici les QUATRE cases reçoivent le même traitement — même découpe IS/OOS
(`app.core.is_oos.split_is_oos`, 65/35), même métrique de sélection
(`app.engine.opt_scoring.composite_score`), même budget de trials, même
recherche aléatoire sur le `param_space` déclaré par la stratégie.

Ce qui est rapporté :

- **score IS** : ce que la recherche a optimisé ;
- **score OOS** : ce que ça vaut sur des barres jamais vues par la recherche ;
- **ratio de surapprentissage** (`overfitting_ratio`) : l'écart entre les deux ;
- **`beats_baseline`** : le garde-fou du dépôt sur le nombre de trades, le PnL
  et le win-rate OOS — un score flatteur sur 4 trades ne compte pas.

Le critère de sélection est le score IS, JAMAIS le score OOS : choisir sur
l'OOS reviendrait à le transformer en second IS, et le chiffre annoncé ne
vaudrait plus rien.

Usage :  python scripts/measure_fair_optimization.py [--trials 40]
"""
import argparse
import json
import pathlib
import random
import sys
import time
from typing import Any, Dict, List

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.is_oos import split_is_oos  # noqa: E402
from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402
from app.engine.opt_scoring import (  # noqa: E402
    beats_baseline,
    composite_score,
    deflated_sharpe_ratio,
    overfitting_ratio,
)

BARRES = 12000
SYMBOLES = ("BTC_USDC", "ETH_USDC")
TIMEFRAMES = ("1h", "4h")


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
        "optimizer_results": {nom: opt_results} if opt_results else {},
    }


def fabrique(nom: str):
    if nom == "smc_ml_edge":
        from app.strategies.smc_ml_edge import Strategy
    else:
        from app.strategies.smart_money import Strategy
    return Strategy()


def backtest(nom: str, df: pl.DataFrame, symbole: str, tf: str, params: dict):
    eng = Engine()
    eng.register(fabrique(nom), silent=True)
    return Backtester(eng, cfg(nom, params, tf), ml_mode="inline").run(df, symbole, tf)


def resume(res) -> Dict[str, Any]:
    trades = list(getattr(res, "trades", []) or [])
    cap = float(getattr(res, "initial_capital", 1) or 1)

    def g(a):
        v = getattr(res, a, 0.0)
        return round(float(v), 3) if isinstance(v, (int, float)) else 0.0

    return {"trades": len(trades), "win": g("win_rate"),
            "pnl_pct": round(100.0 * float(getattr(res, "total_pnl", 0) or 0) / cap, 2),
            "pf": g("profit_factor"), "sharpe": g("sharpe"), "dd_pct": g("max_drawdown"),
            "bh_pct": g("buy_and_hold_pct")}


def base_params(nom: str, tf: str) -> Dict[str, Any]:
    """Paramètres NON cherchés : ceux qui définissent le modèle ou le cadre."""
    if nom == "smc_ml_edge":
        # `warmup` réduit en 4 h : 12 000 barres 4 h couvrent ~5,5 ans, mais un
        # warmup de 3 000 y mangerait le quart de la série.
        return {"warmup_bars": 2500 if tf == "4h" else 3000,
                "retrain_every": 2000, "train_window": 8000}
    import yaml
    f = pathlib.Path("strategies/smart_money.yaml")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {}
    return dict((d or {}).get("params") or {})


def espace(nom: str) -> Dict[str, List]:
    return dict(fabrique(nom).param_space or {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    lignes = []
    for nom in ("smc_ml_edge", "smart_money"):
        space = espace(nom)
        cles = sorted(space)
        for symbole in SYMBOLES:
            for tf in TIMEFRAMES:
                chemin = pathlib.Path(f"data/ohlcv/{symbole}/{tf}.parquet")
                if not chemin.exists():
                    continue
                df = pl.read_parquet(chemin).tail(BARRES)
                socle = base_params(nom, tf)
                warmup = int(socle.get("warmup_bars", 260))
                df_is, _df_oos, split = split_is_oos(df, warmup=warmup)
                if df_is is None or split <= warmup:
                    print(f"!! {nom} {symbole} {tf} : découpe impossible", flush=True)
                    continue

                # La passe OOS DÉMARRE `warmup` barres AVANT la coupure. Sans
                # cela, une stratégie à long rodage n'a plus rien à trader : la
                # première version de ce script donnait 0 trade OOS partout pour
                # `smc_ml_edge` (warmup 3000 sur une fenêtre de 4200), ce qui
                # mesurait le protocole et non la stratégie. Le rodage se fait
                # ainsi sur des barres IS, et le premier trade possible tombe
                # exactement à `split` — `score()` renvoie « none » tant que
                # `warmup_bars` n'est pas atteint.
                df_oos = df[max(0, split - warmup):]
                bars_oos_tradables = len(df_oos) - warmup
                if bars_oos_tradables < 400:
                    print(f"!! {nom} {symbole} {tf} : seulement "
                          f"{bars_oos_tradables} barres OOS tradables", flush=True)
                    continue

                print(f"\n=== {nom} · {symbole} · {tf} — IS {len(df_is)} barres "
                      f"({len(df_is) - warmup} tradables) / OOS {len(df_oos)} "
                      f"({bars_oos_tradables} tradables), {args.trials} trials ===",
                      flush=True)

                # Tirages SANS remise : deux fois le même jeu gaspillerait un trial.
                vus, tirages = set(), []
                for _ in range(args.trials * 8):
                    if len(tirages) >= args.trials:
                        break
                    p = tuple(rng.choice(space[k]) for k in cles)
                    if p not in vus:
                        vus.add(p)
                        tirages.append(dict(zip(cles, p)))

                t0 = time.time()
                meilleur, meilleur_score, meilleur_is = None, -1e9, None
                for i, essai in enumerate(tirages, 1):
                    params = {**socle, **essai}
                    try:
                        r_is = backtest(nom, df_is, symbole.replace("_", "/"), tf, params)
                    except Exception as e:
                        print(f"   trial {i:>3} ECHEC {type(e).__name__}: {e}", flush=True)
                        continue
                    sc = composite_score(r_is)
                    if sc > meilleur_score:
                        meilleur_score, meilleur, meilleur_is = sc, essai, resume(r_is)
                        print(f"   trial {i:>3}/{len(tirages)} — nouveau meilleur IS "
                              f"{sc:.3f} ({meilleur_is['trades']} trades, "
                              f"pnl {meilleur_is['pnl_pct']}%) · {essai}", flush=True)

                if meilleur is None:
                    print("   aucun trial exploitable", flush=True)
                    continue

                params = {**socle, **meilleur}
                r_oos = backtest(nom, df_oos, symbole.replace("_", "/"), tf, params)
                oos = resume(r_oos)
                sc_oos = composite_score(r_oos)

                # Reference : les params PAR DEFAUT sur le meme OOS. Sans elle,
                # « le meilleur des 40 trials gagne » ne dit pas si l'optimisation
                # a servi a quelque chose.
                r_def = backtest(nom, df_oos, symbole.replace("_", "/"), tf, socle)
                defaut = resume(r_def)
                solide, raison = beats_baseline(
                    oos["trades"], oos["pnl_pct"], oos["win"], oos["sharpe"],
                    baseline={"pnl": defaut["pnl_pct"], "wr": defaut["win"],
                              "sharpe": defaut["sharpe"]},
                    n_trials=len(tirages))
                # Sharpe deflate : un Sharpe obtenu apres N essais vaut moins
                # qu'un Sharpe obtenu du premier coup (Lopez de Prado 2014).
                try:
                    dsr = round(float(deflated_sharpe_ratio(
                        oos["sharpe"], max(oos["trades"], 2), n_trials=len(tirages))), 3)
                except Exception:
                    dsr = None

                ligne = {"strategie": nom, "symbole": symbole, "tf": tf,
                         "params": meilleur, "score_is": round(meilleur_score, 3),
                         "score_oos": round(sc_oos, 3),
                         "surapprentissage": round(overfitting_ratio(meilleur_score, sc_oos), 3),
                         "is": meilleur_is, "oos": oos, "oos_defaut": defaut,
                         "solide_oos": bool(solide), "raison": raison,
                         "sharpe_deflate": dsr,
                         "secondes": round(time.time() - t0, 1)}
                lignes.append(ligne)
                print(f"   >>> IS {meilleur_score:.3f} | OOS {sc_oos:.3f} "
                      f"(surappr. {ligne['surapprentissage']}) | OOS: "
                      f"{oos['trades']} trades, pnl {oos['pnl_pct']}%, "
                      f"pf {oos['pf']}, sharpe {oos['sharpe']}, bh {oos['bh_pct']}% "
                      f"| defaut: pnl {defaut['pnl_pct']}% sharpe {defaut['sharpe']} "
                      f"| solide={solide} ({raison or 'ok'}) | DSR={dsr} "
                      f"({ligne['secondes']}s)", flush=True)

    pathlib.Path("scripts/_fair_optim.json").write_text(
        json.dumps(lignes, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print("COMPARAISON ÉQUITABLE — chaque case optimisée avec le même budget")
    print("=" * 96)
    print(f"{'symbole':<10}{'tf':<5}{'stratégie':<14}{'OOS trades':>11}"
          f"{'OOS pnl%':>10}{'OOS pf':>8}{'OOS sharpe':>11}{'b&h%':>9}{'solide':>8}")
    for x in sorted(lignes, key=lambda r: (r["symbole"], r["tf"], r["strategie"])):
        o = x["oos"]
        print(f"{x['symbole']:<10}{x['tf']:<5}{x['strategie']:<14}{o['trades']:>11}"
              f"{o['pnl_pct']:>10}{o['pf']:>8}{o['sharpe']:>11}{o['bh_pct']:>9}"
              f"{str(x['solide_oos']):>8}")
    print("\nresultats -> scripts/_fair_optim.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
