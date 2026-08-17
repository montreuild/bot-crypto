"""Point 6 — la conversion signal → trade, sur l'HISTORIQUE COMPLET.

Les mesures precedentes portaient sur 12 000 barres et concluaient qu'aucune
strategie ne passait la validation OOS. Deux choses ont change depuis :
l'historique complet est utilise (15 800 barres en 4 h, 51 900 en 1 h) et les
MODES DE SORTIE sont selectionnables, donc l'effet de la gestion de position
devient attribuable.

Deux protocoles, choisis par le COUT reel mesure :

  A. `smc_ml_edge` — 176 s par backtest sur 51 909 barres (26 reentrainements).
     Une recherche de 40 essais couterait des heures par case. On fait donc une
     ABLATION A PARAMETRES FIXES : seul le mode de sortie varie, ce qui isole
     exactement la question posee (« la sortie explique-t-elle la conversion ? »).

  B. `smart_money` — 6 s par backtest. Le budget permet une vraie RECHERCHE par
     mode, via `OptimizerSearchEngine` du depot (decoupe IS/OOS canonique,
     selection sur l'IS, report sur l'OOS, ratio de surapprentissage).

Reutiliser l'outillage plutot que le reecrire est deliberé : la version maison
precedente avait reintroduit un probleme deja resolu (fenetre OOS sans barres
de rodage, d'ou 0 trade partout).

Usage :  python scripts/measure_exit_conversion.py [--trials 40] [--jobs 4]
"""
import argparse
import json
import pathlib
import sys
import time
from typing import Any, Dict, Tuple

import polars as pl
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.execution import EXIT_MODES  # noqa: E402
from app.core.is_oos import split_is_oos  # noqa: E402
from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402

SYMBOLES = ("BTC_USDC", "ETH_USDC")


def yaml_strategie(nom: str) -> Tuple[dict, dict]:
    f = pathlib.Path(f"strategies/{nom}.yaml")
    d = yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {}
    d = d or {}
    return dict(d.get("params") or {}), dict(d.get("optimizer_results") or {})


def cfg(nom: str, params: dict, tf: str, opt: dict = None,
        mode: str = "as_declared") -> dict:
    return {
        "trading": {"capital": 10000, "risk_per_trade": 0.01, "timeframe": tf,
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0, "trail_wide": 2.5,
                     "trail_normal": 2.0, "trail_lock": 1.5, "trail_tight": 1.0,
                     "grace_bars": 4, "breakeven_r": 1.2, "lock_r": 2.5,
                     "tight_r": 4.0, "lock_ratio": 0.6, "use_swing": False,
                     "max_notional_pct": 0.2, "exit_mode": mode},
        "strategies": {"enabled": [nom]},
        "strategy_params": {nom: params},
        "optimizer_results": {nom: opt} if opt else {},
    }


def fabrique(nom: str):
    if nom == "smc_ml_edge":
        from app.strategies.smc_ml_edge import Strategy
    else:
        from app.strategies.smart_money import Strategy
    return Strategy()


def resume(res) -> Dict[str, Any]:
    trades = list(getattr(res, "trades", []) or [])
    cap = float(getattr(res, "initial_capital", 1) or 1)

    def g(a):
        v = getattr(res, a, 0.0)
        return round(float(v), 3) if isinstance(v, (int, float)) else 0.0

    jambes = sum(len(t.get("exits") or []) for t in trades)
    return {"trades": len(trades), "jambes_partielles": jambes,
            "win": g("win_rate"),
            "pnl_pct": round(100.0 * float(getattr(res, "total_pnl", 0) or 0) / cap, 2),
            "pf": g("profit_factor"), "sharpe": g("sharpe"),
            "dd_pct": g("max_drawdown"), "bh_pct": g("buy_and_hold_pct"),
            "frais_pct": round(100.0 * float(getattr(res, "total_fees", 0) or 0) / cap, 2)}


# ─────────────────────────────────────────────────────────────────────────────
#  A. Ablation des modes à paramètres fixes (stratégie coûteuse)
# ─────────────────────────────────────────────────────────────────────────────
def ablation_modes(nom: str, tf: str, socle: dict) -> list:
    params_yaml, opt = yaml_strategie(nom)
    lignes = []
    print(f"\n{'=' * 78}\nA. ABLATION DES MODES — {nom} · {tf} · historique complet\n{'=' * 78}",
          flush=True)
    for symbole in SYMBOLES:
        chemin = pathlib.Path(f"data/ohlcv/{symbole}/{tf}.parquet")
        if not chemin.exists():
            continue
        df = pl.read_parquet(chemin)
        print(f"\n--- {symbole} ({len(df)} barres) ---", flush=True)
        for mode in EXIT_MODES:
            t0 = time.time()
            eng = Engine()
            eng.register(fabrique(nom), silent=True)
            p = {**params_yaml, **socle}
            try:
                r = Backtester(eng, cfg(nom, p, tf, opt, mode),
                               ml_mode="inline").run(df, symbole.replace("_", "/"), tf)
            except Exception as e:
                print(f"  {mode:<22} ECHEC {type(e).__name__}: {e}", flush=True)
                continue
            d = resume(r)
            d.update(strategie=nom, symbole=symbole, tf=tf, mode=mode,
                     secondes=round(time.time() - t0, 1))
            lignes.append(d)
            print(f"  {mode:<22} trades={d['trades']:<4} jambes={d['jambes_partielles']:<4} "
                  f"win={d['win']}% pnl={d['pnl_pct']}% pf={d['pf']} "
                  f"sharpe={d['sharpe']} dd={d['dd_pct']}% bh={d['bh_pct']}% "
                  f"({d['secondes']}s)", flush=True)
    return lignes


# ─────────────────────────────────────────────────────────────────────────────
#  B. Recherche par mode via l'optimiseur du dépôt (stratégie bon marché)
# ─────────────────────────────────────────────────────────────────────────────
def recherche_par_mode(nom: str, tf: str, trials: int, jobs: int) -> list:
    from app.engine.optimizer_search import OptimizerSearchEngine

    params_yaml, opt = yaml_strategie(nom)
    lignes = []
    print(f"\n{'=' * 78}\nB. RECHERCHE PAR MODE — {nom} · {tf} · {trials} essais\n{'=' * 78}",
          flush=True)
    for symbole in SYMBOLES:
        chemin = pathlib.Path(f"data/ohlcv/{symbole}/{tf}.parquet")
        if not chemin.exists():
            continue
        df = pl.read_parquet(chemin)
        df_is, df_oos, split = split_is_oos(df)
        print(f"\n--- {symbole} ({len(df)} barres : IS {len(df_is)} / OOS {len(df_oos)}) ---",
              flush=True)
        for mode in EXIT_MODES:
            t0 = time.time()
            try:
                moteur = OptimizerSearchEngine(
                    nom, cfg(nom, params_yaml, tf, opt, mode), df_is, df_oos,
                    symbol=symbole.replace("_", "/"), timeframe=tf,
                    df_full=df, split=split, ml_mode="inline")
                res = moteur.random_search(n_trials=trials, n_jobs=jobs)
            except Exception as e:
                print(f"  {mode:<22} ECHEC {type(e).__name__}: {e}", flush=True)
                continue
            if res.get("error"):
                print(f"  {mode:<22} {res['error']}", flush=True)
                continue
            d = {"strategie": nom, "symbole": symbole, "tf": tf, "mode": mode,
                 "oos_pnl": res.get("best_oos_pnl"),
                 "oos_trades": res.get("best_oos_trades"),
                 "oos_sharpe": res.get("best_oos_sharpe"),
                 "oos_score": res.get("best_oos_score"),
                 "overfit": res.get("overfit"),
                 "best_params": res.get("best_params"),
                 "secondes": round(time.time() - t0, 1)}
            lignes.append(d)
            print(f"  {mode:<22} OOS pnl={d['oos_pnl']:>9} trades={d['oos_trades']:>4} "
                  f"sharpe={d['oos_sharpe']:>7} score={d['oos_score']:>7} "
                  f"surappr={d['overfit']} ({d['secondes']}s)", flush=True)
    return lignes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--skip-ml", action="store_true",
                    help="saute l'ablation ML (176 s par backtest)")
    args = ap.parse_args()

    tout = []
    if not args.skip_ml:
        tout += ablation_modes("smc_ml_edge", "1h",
                               {"warmup_bars": 3000, "retrain_every": 2000,
                                "train_window": 8000, "amp_top_q": 0.10,
                                "dir_top_q": 0.20, "use_smc_filter": True})
    tout += ablation_modes("smart_money", "4h", {})
    tout += recherche_par_mode("smart_money", "4h", args.trials, args.jobs)

    pathlib.Path("scripts/_exit_conversion.json").write_text(
        json.dumps(tout, indent=2, default=str), encoding="utf-8")
    print("\nresultats -> scripts/_exit_conversion.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
