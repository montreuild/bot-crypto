"""Suite de `docs/ABLATION_SMC_V3.md` §5, point 1 — les neuf stratégies dont
le filtre HTF était inerte en backtest.

L5 a corrigé `htf_trend(None)` : il renvoyait 0 faute de `df_htf`, que seul le
live fournit. Neuf stratégies ont donc été optimisées contre un filtre inerte,
puis mises en production avec le même filtre **actif**. Leurs
`optimizer_results` portent sur un comportement qui n'a jamais existé en
production.

Deux étapes, dans cet ordre :

1. **Mesurer l'impact** — la correction change-t-elle réellement quelque chose
   pour chacune ? Une stratégie dont le résultat ne bouge pas (filtre désactivé
   dans son YAML, ou jamais mordant) n'a rien à recalibrer, et le dire évite
   quatre heures d'optimisation inutile.
2. **Recalibrer** celles qui bougent, avec l'outillage du dépôt —
   `split_is_oos`, `OptimizerSearchEngine`, `beats_baseline` — sélection sur
   l'IS et jamais sur l'OOS.

L'étape 1 seule est déjà un résultat publiable : elle dit lesquels des chiffres
publiés sont faux, et de combien.

Usage :
    python scripts/recalibrate_htf_strategies.py --data data/ohlcv --etape 1
    python scripts/recalibrate_htf_strategies.py --data data/ohlcv --etape 2 --trials 40
"""
import argparse
import importlib
import json
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.is_oos import split_is_oos  # noqa: E402
from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402

# Les neuf stratégies qui consomment `htf_trend`. `tvr_trend` et `multi_tf_sr`
# lisent `df_htf` directement en plus de l'appel, elles sont traitées à part.
STRATEGIES = [
    "breakout", "breakout_filtreHor", "fear_momentum", "gemini_trend_follow",
    "multi_tf_sr", "pullback_trend", "supertrend_macd", "trend", "tvr_trend",
]

#: Modules où `htf_trend` est importé par NOM : patcher
#: `app.core.indicators_market.htf_trend` ne suffirait pas, la liaison locale
#: pointerait encore sur l'original.
MODULES_A_PATCHER = [f"app.strategies.{n}" for n in STRATEGIES] + [
    "app.core.indicators", "app.core.indicators_market",
]


class _FiltreInerte:
    """Rétablit le comportement d'avant L5, le temps d'une mesure.

    ⚠ La fonction de remplacement doit fermer sur l'ORIGINAL capturé avant le
    patch. Une première version la ré-important depuis
    `app.core.indicators_market` — lui-même patché — s'appelait elle-même :
    récursion infinie, avalée par le `try/except` de l'Engine, et donc zéro
    trade partout sans qu'aucune erreur ne remonte. La mesure aurait été
    silencieusement fausse.
    """

    def __enter__(self):
        from app.core.indicators_market import htf_trend as original

        def sans_repli(df_htf, ema_period: int = 50, *, df_ltf=None,
                       mult: int = 4) -> int:
            return original(df_htf, ema_period)   # `df_ltf` volontairement ignoré

        self._origine = {}
        for nom in MODULES_A_PATCHER:
            try:
                mod = importlib.import_module(nom)
            except Exception:
                continue
            if hasattr(mod, "htf_trend"):
                self._origine[nom] = mod.htf_trend
                mod.htf_trend = sans_repli
        return self

    def __exit__(self, *_):
        for nom, fn in self._origine.items():
            importlib.import_module(nom).htf_trend = fn


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


def instancier(nom: str):
    mod = importlib.import_module(f"app.strategies.{nom}")
    return mod.Strategy()


def run(nom: str, params: dict, df: pl.DataFrame, symbole: str, tf: str,
        opt: dict = None) -> dict:
    eng = Engine()
    eng.register(instancier(nom), silent=True)
    d = Backtester(eng, cfg(nom, params, tf, opt), ml_mode="inline").run(
        df, symbole.replace("_", "/"), tf).to_dict()
    return {"n": d["total_trades"], "pnl": round(d["net_profit"], 2),
            "pf": d["profit_factor"], "dd": d["max_drawdown"],
            "sharpe": d["sharpe"], "wr": d["win_rate"]}


def _verifier_le_patch(df: pl.DataFrame) -> None:
    """Le patch fait-il vraiment ce qu'il prétend ?

    Sans ce contrôle, une erreur dans le patch (récursion, module manqué) est
    avalée par le `try/except` de l'Engine : la mesure sortirait des chiffres
    plausibles et faux. On exige donc les deux comportements, explicitement.
    """
    from app.core.indicators import htf_trend as depuis_indicators

    apres = depuis_indicators(None, df_ltf=df)
    with _FiltreInerte():
        from app.core.indicators import htf_trend as patche
        avant = patche(None, df_ltf=df)
    if avant != 0:
        raise AssertionError(
            f"le patch ne neutralise pas le repli (attendu 0, obtenu {avant})")
    if apres == 0:
        raise AssertionError(
            "le repli ne produit aucune tendance sur la série de contrôle — "
            "la comparaison avant/après ne mesurerait rien")
    print(f"  contrôle du patch : avant={avant} après={apres} ✓")


def etape_1(racine: pathlib.Path, barres: int) -> list:
    """Combien la correction déplace-t-elle les chiffres publiés ?"""
    lignes = []
    for nom in STRATEGIES:
        params, opt = yaml_strategie(nom)
        print(f"\n=== {nom} ===")
        print(f"  {'symbole':<10} {'tf':>4} {'n avant':>8} {'n après':>8} "
              f"{'PnL avant':>11} {'PnL après':>11} {'Δ':>10}")
        for symbole in ("BTC_USDC", "ETH_USDC"):
            for tf in ("1h", "4h"):
                chemin = racine / symbole / f"{tf}.parquet"
                if not chemin.exists():
                    continue
                df = pl.read_parquet(chemin).tail(barres)
                try:
                    with _FiltreInerte():
                        avant = run(nom, params, df, symbole, tf, opt)
                    apres = run(nom, params, df, symbole, tf, opt)
                except Exception as e:
                    print(f"  {symbole:<10} {tf:>4}  ECHEC {type(e).__name__}: {e}")
                    continue
                delta = apres["pnl"] - avant["pnl"]
                identique = (avant == apres)
                lignes.append({"strategie": nom, "symbole": symbole, "tf": tf,
                               "avant": avant, "apres": apres,
                               "delta_pnl": round(delta, 2),
                               "identique": identique})
                marque = "  (inchangé)" if identique else ""
                print(f"  {symbole:<10} {tf:>4} {avant['n']:>8} {apres['n']:>8} "
                      f"{avant['pnl']:>11.2f} {apres['pnl']:>11.2f} "
                      f"{delta:>+10.2f}{marque}")
    return lignes


def etape_2(racine: pathlib.Path, barres: int, trials: int,
            impacts: list, n_jobs: int = 1) -> list:
    """Recalibre les couples (stratégie, symbole, tf) que l'étape 1 a vus bouger."""
    from app.engine.optimizer_search import OptimizerSearchEngine

    a_refaire = sorted({(x["strategie"], x["symbole"], x["tf"])
                        for x in impacts if not x["identique"]})
    print(f"\n{len(a_refaire)} couples à recalibrer "
          f"(sur {len(impacts)} mesurés)\n")
    sorties = []
    for nom, symbole, tf in a_refaire:
        chemin = racine / symbole / f"{tf}.parquet"
        df = pl.read_parquet(chemin).tail(barres)
        df_is, df_oos, split = split_is_oos(df)
        params, opt = yaml_strategie(nom)
        t0 = time.time()
        try:
            moteur = OptimizerSearchEngine(
                nom, cfg(nom, params, tf), df_is, df_oos,
                symbol=symbole.replace("_", "/"), timeframe=tf,
                df_full=df, split=split, ml_mode="inline")
            res = moteur.random_search(n_trials=trials, n_jobs=n_jobs)
        except Exception as e:
            print(f"  {nom:<22} {symbole} {tf}  ECHEC {type(e).__name__}: {e}")
            continue
        if res.get("error"):
            print(f"  {nom:<22} {symbole} {tf}  {res['error']}")
            continue
        sorties.append({"strategie": nom, "symbole": symbole, "tf": tf,
                        "best_params": res["best_params"],
                        "oos_pnl": res["best_oos_pnl"],
                        "oos_trades": res["best_oos_trades"],
                        "oos_sharpe": res["best_oos_sharpe"],
                        "oos_score": res["best_oos_score"],
                        "overfit": res["overfit"],
                        "secondes": round(time.time() - t0, 1)})
        print(f"  {nom:<22} {symbole} {tf}  OOS pnl={res['best_oos_pnl']:>9.2f} "
              f"trades={res['best_oos_trades']:>4} "
              f"sharpe={res['best_oos_sharpe']:>7.2f} "
              f"overfit={res['overfit']:>5} ({time.time() - t0:.0f}s)")
    return sorties


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ohlcv")
    ap.add_argument("--barres", type=int, default=12000)
    ap.add_argument("--etape", type=int, default=1, choices=(1, 2))
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--njobs", type=int, default=1)
    ap.add_argument("--sortie", default="scripts/_recalibrage_htf.json")
    args = ap.parse_args()

    racine = pathlib.Path(args.data)
    for symbole in ("BTC_USDC", "ETH_USDC"):
        ref = racine / symbole / "4h.parquet"
        if ref.exists():
            _verifier_le_patch(pl.read_parquet(ref).tail(2000))
            break
    fichier = pathlib.Path(args.sortie)
    donnees = json.loads(fichier.read_text(encoding="utf-8")) \
        if fichier.exists() else {}

    if args.etape == 1:
        donnees["impacts"] = etape_1(racine, args.barres)
        bouges = sum(1 for x in donnees["impacts"] if not x["identique"])
        print(f"\n══ {bouges} couples sur {len(donnees['impacts'])} changent "
              f"de résultat — ce sont eux dont les paramètres publiés sont faux ══")
    else:
        if not donnees.get("impacts"):
            print("Lancer d'abord --etape 1 : sans elle on recalibrerait tout, "
                  "y compris ce qui n'a pas bougé.")
            return 1
        donnees["recalibrage"] = etape_2(racine, args.barres, args.trials,
                                         donnees["impacts"], args.njobs)

    fichier.write_text(json.dumps(donnees, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    print(f"\nresultats -> {fichier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
