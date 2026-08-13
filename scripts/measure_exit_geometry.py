"""L0 — pourquoi les positions sortent-elles, et à quel moment ?

`docs/STRATEGY_SMC_ML_EDGE.md` §4 pose le diagnostic sans le chiffrer : sur ETH
top5, 40.8 % de trades gagnants pour un profit factor de 0.49 signifie que les
gains moyens sont bien plus petits que les pertes moyennes — alors que la
géométrie demandée (cible 2.5 ATR, stop 1.5 ATR) prévoit l'inverse. Donc les
positions sortent avant leur cible. Restait à savoir par où.

Ce script répond par trois tableaux :

1. **distribution des `exit_reason`** — le trade meurt-il sur stop, sur trailing,
   sur TP ou sur expiration ?
2. **MFE / MAE par `exit_reason`**, en multiples de R — un trade stoppé qui
   affichait +1.8R de MFE dit que le stop n'était pas le problème, la sortie si.
3. **MFE atteint vs TP demandé** — quelle fraction des trades a touché sa cible,
   et de combien les autres s'en sont approchés.

Le R est calculé sur le risque PLANIFIÉ (`entry` → `planned_stop`), pas sur le
risque réalisé : c'est le seul dénominateur commun entre un trade stoppé et un
trade qui a couru.

Usage :
    python scripts/measure_exit_geometry.py [--data data/ohlcv] [--barres 12000]
"""
import argparse
import json
import pathlib
import statistics
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402


def yaml_strategie(nom: str) -> tuple:
    """``(params, optimizer_results)`` du YAML — jamais les défauts de code :
    juger une stratégie sur des valeurs que personne ne fait tourner ne mesure
    rien (leçon de scripts/measure_smc_ml_vs_rules.py)."""
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
        "optimizer_results": {nom: opt_results} if opt_results else {},
    }


def en_R(trade: dict) -> dict:
    """MFE / MAE / TP du trade en multiples du risque planifié."""
    entree = float(trade.get("entry") or 0)
    stop = trade.get("planned_stop")
    if not entree or stop is None:
        return {}
    risque = abs(entree - float(stop))
    if risque <= 0:
        return {}
    sens = 1 if trade.get("side") == "long" else -1
    tp = trade.get("planned_tp")
    out = {
        # mfe/mae sont en % du prix d'entrée, signés du bon côté par le moteur.
        "mfe_R": abs(float(trade.get("mfe") or 0)) / 100.0 * entree / risque,
        "mae_R": abs(float(trade.get("mae") or 0)) / 100.0 * entree / risque,
        "pnl_R": float(trade.get("gross_pnl") or 0) / (risque * float(
            trade.get("size") or 1)),
    }
    if tp is not None:
        out["tp_R"] = abs(float(tp) - entree) / risque * sens * sens
    return out


def mediane(xs: list) -> float:
    return round(statistics.median(xs), 2) if xs else 0.0


def analyse(res, etiquette: str) -> dict:
    trades = [t for t in (getattr(res, "trades", []) or [])
              if str(t.get("status", "")).startswith("closed")]
    if not trades:
        return {"etiquette": etiquette, "n": 0}

    par_raison: dict = {}
    for t in trades:
        r = en_R(t)
        d = par_raison.setdefault(str(t.get("exit_reason") or "inconnu"),
                                  {"n": 0, "mfe": [], "mae": [], "pnl": [],
                                   "gagnants": 0})
        d["n"] += 1
        if t.get("pnl", 0) > 0:
            d["gagnants"] += 1
        if r:
            d["mfe"].append(r["mfe_R"])
            d["mae"].append(r["mae_R"])
            d["pnl"].append(r["pnl_R"])

    # Cible atteinte : le MFE a-t-il dépassé le TP demandé ?
    avec_tp = [en_R(t) for t in trades]
    avec_tp = [r for r in avec_tp if r and "tp_R" in r and r["tp_R"] > 0]
    touches = sum(1 for r in avec_tp if r["mfe_R"] >= r["tp_R"])

    print(f"\n=== {etiquette} — {len(trades)} trades ===")
    print(f"  {'sortie':<18} {'n':>4} {'%':>6} {'win%':>6} "
          f"{'MFE_R méd':>10} {'MAE_R méd':>10} {'PnL_R méd':>10}")
    for raison, d in sorted(par_raison.items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {raison:<18} {d['n']:>4} "
              f"{100 * d['n'] / len(trades):>5.1f}% "
              f"{100 * d['gagnants'] / d['n']:>5.1f}% "
              f"{mediane(d['mfe']):>10} {mediane(d['mae']):>10} "
              f"{mediane(d['pnl']):>10}")

    if avec_tp:
        mfe_med = mediane([r["mfe_R"] for r in avec_tp])
        tp_med = mediane([r["tp_R"] for r in avec_tp])
        print(f"  cible touchée : {touches}/{len(avec_tp)} "
              f"({100 * touches / len(avec_tp):.1f}%) — "
              f"MFE médian {mfe_med}R vs TP demandé {tp_med}R")

    cap = float(getattr(res, "initial_capital", 1) or 1)
    return {
        "etiquette": etiquette,
        "n": len(trades),
        "pnl_pct": round(100.0 * float(getattr(res, "total_pnl", 0) or 0) / cap, 2),
        "net_pct": round(100.0 * float(getattr(res, "net_profit", 0) or 0) / cap, 2),
        "pf": round(float(getattr(res, "profit_factor", 0) or 0), 3),
        "frais_pct": round(100.0 * float(getattr(res, "total_fees", 0) or 0) / cap, 2),
        "slippage_pct": round(
            100.0 * float(getattr(res, "total_slippage_cost", 0) or 0) / cap, 3),
        "cible_touchee_pct": round(100 * touches / len(avec_tp), 1) if avec_tp else None,
        "par_raison": {
            k: {"n": v["n"], "part_pct": round(100 * v["n"] / len(trades), 1),
                "win_pct": round(100 * v["gagnants"] / v["n"], 1),
                "mfe_R_median": mediane(v["mfe"]), "mae_R_median": mediane(v["mae"]),
                "pnl_R_median": mediane(v["pnl"])}
            for k, v in par_raison.items()
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ohlcv")
    ap.add_argument("--barres", type=int, default=12000)
    ap.add_argument("--sortie", default="scripts/_exit_geometry.json")
    args = ap.parse_args()

    def regles():
        from app.strategies.smart_money import Strategy
        return Strategy()

    p_sm, opt_sm = yaml_strategie("smart_money")
    racine = pathlib.Path(args.data)
    lignes = []

    for symbole in ("BTC_USDC", "ETH_USDC"):
        for tf in ("1h", "4h"):
            chemin = racine / symbole / f"{tf}.parquet"
            if not chemin.exists():
                print(f"  (absent : {chemin})")
                continue
            df = pl.read_parquet(chemin).tail(args.barres)
            eng = Engine()
            eng.register(regles(), silent=True)
            t0 = time.time()
            res = Backtester(eng, cfg("smart_money", p_sm, tf, opt_sm),
                             ml_mode="inline").run(
                df, symbole.replace("_", "/"), tf)
            r = analyse(res, f"smart_money {symbole} {tf}")
            r.update(symbole=symbole, tf=tf, secondes=round(time.time() - t0, 1))
            lignes.append(r)

    pathlib.Path(args.sortie).write_text(
        json.dumps(lignes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nresultats -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
