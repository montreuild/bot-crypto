"""Suite de `docs/ABLATION_SMC_V3.md` §5, point 3 — des fréquences mesurées
plutôt que des poids postulés.

L4 a rejeté `target_mode: expected_value` parce que les probabilités venaient
d'un barème inventé (§78 : « HTF external +30, Previous Week +25… »). §3.5 du
plan l'avait annoncé — « poser une probabilité à la main puis maximiser dessus,
c'est optimiser une croyance ». C'est la seule piste de L4 qui n'a pas été
invalidée, parce qu'elle n'avait pas encore été essayée avec des chiffres.

Protocole en walk-forward strict :

    fenêtre IS  →  on COMPTE la fréquence d'atteinte par classe de liquidité
    fenêtre OOS →  on APPLIQUE ces fréquences, sans jamais les ré-estimer

Trois configurations comparées sur l'OOS, à signaux par ailleurs identiques :

    `nearest`          la cible la plus proche (l'existant)
    `postulé`          valeur attendue avec les poids de §77 (rejeté en L4)
    `mesuré`           valeur attendue avec les fréquences estimées sur l'IS

Si `mesuré` ne bat pas `nearest`, la piste est close et §78/§79 avec elle.

Usage :
    python scripts/measure_target_probabilities.py --data data/ohlcv
"""
import argparse
import json
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.is_oos import split_is_oos  # noqa: E402
from app.core.smc.quality import POIDS_CLASSE, frequences_atteinte  # noqa: E402
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
    ap.add_argument("--sortie", default="scripts/_target_probabilities.json")
    args = ap.parse_args()

    base = dict(yaml_params("smart_money"))
    base["use_calendar_liquidity"] = "targets"   # sinon toutes les classes se
    racine = pathlib.Path(args.data)             # confondent en « pool »
    lignes = []

    for symbole in ("BTC_USDC", "ETH_USDC"):
        for tf in ("1h", "4h"):
            chemin = racine / symbole / f"{tf}.parquet"
            if not chemin.exists():
                continue
            df = pl.read_parquet(chemin).tail(args.barres)
            df_is, df_oos, _ = split_is_oos(df)

            # ── Estimation sur l'IS, et JAMAIS sur l'OOS ────────────────────
            r_is = run({**base, "target_mode": "nearest"}, df_is, symbole, tf)
            proba = frequences_atteinte(r_is["trades"])

            print(f"\n=== {symbole} {tf} ===")
            print("  fréquences d'atteinte mesurées sur l'IS "
                  f"({r_is['total_trades']} trades) :")
            for cl in POIDS_CLASSE:
                mesuree = proba.get(cl)
                postule = POIDS_CLASSE[cl]
                marque = "" if mesuree == postule else "  ← mesuré"
                print(f"    {cl:<14} postulé {postule:.2f}   "
                      f"retenu {mesuree:.2f}{marque}")

            variantes = {
                "nearest":  {"target_mode": "nearest"},
                "postulé":  {"target_mode": "expected_value"},
                "mesuré":   {"target_mode": "expected_value",
                             "target_proba": proba},
            }
            res = {}
            print(f"  {'variante':<10} {'n':>4} {'PnL OOS':>10} {'PF':>7} {'DD%':>8}")
            for nom, surcharge in variantes.items():
                d = run({**base, **surcharge}, df_oos, symbole, tf)
                res[nom] = {"n": d["total_trades"], "pnl": d["net_profit"],
                            "pf": d["profit_factor"], "dd": d["max_drawdown"]}
                print(f"  {nom:<10} {d['total_trades']:>4} "
                      f"{d['net_profit']:>10.2f} {d['profit_factor']:>7} "
                      f"{d['max_drawdown']:>8}")

            gagne = res["mesuré"]["pnl"] > res["nearest"]["pnl"]
            print(f"  → mesuré {'BAT' if gagne else 'ne bat pas'} nearest")
            lignes.append({"symbole": symbole, "tf": tf,
                           "proba_mesurees": proba,
                           "n_trades_is": r_is["total_trades"],
                           "oos": res, "mesure_gagne": gagne})

    gagnants = sum(1 for x in lignes if x["mesure_gagne"])
    print(f"\n══ {gagnants} cas sur {len(lignes)} où les fréquences mesurées "
          f"battent la cible la plus proche ══")
    pathlib.Path(args.sortie).write_text(
        json.dumps(lignes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"resultats -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
