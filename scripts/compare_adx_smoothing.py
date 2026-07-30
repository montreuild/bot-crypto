#!/usr/bin/env python3
"""ADX/ATR : le lissage trop réactif est-il un défaut… ou un avantage ?

``app/core/indicators_precompute`` lisse ATR/ADX/DI en ``ewm_mean(span=14)``
(α = 2/15) là où la définition de Wilder veut α = 1/14 — un ``span=14``
équivaut à un Wilder de période 7,5 (cf. §8ter). Conclure « c'est un bug,
corrigeons » serait aller trop vite : **les seuils des stratégies concernées
ont été optimisés contre cette échelle**, et un indicateur plus réactif n'est
pas mécaniquement moins bon en trading. Il se peut très bien que la
sur-réactivité soit précisément ce qui fait marcher ces stratégies.

**Pourquoi comparer à paramètres constants serait malhonnête.** Un
``adx_min: 20`` réglé contre un ADX qui vaut 35 en moyenne ne veut pas dire la
même chose face à un ADX qui vaut 28 : le même seuil devient beaucoup plus
sélectif. Rejouer les paramètres actuels sous Wilder ne mesurerait pas Wilder,
mais le désaccordage des seuils. Chaque variante est donc **réoptimisée
séparément**, avec le même budget d'essais et la même graine.

**Trois fenêtres, pas deux.** L'optimiseur choisit son meilleur essai sur le
score OOS pénalisé — l'OOS lui sert donc de *sélection*, et comparer les
variantes dessus les flatterait toutes les deux. On découpe donc :

===========  ==================================================================
``IS``       apprentissage des paramètres (backtest interne à chaque essai)
``OOS``      sélection du meilleur essai — vu par l'optimiseur
``VAL``      **jamais vue par l'optimiseur** — c'est là que les deux variantes
             sont comparées, avec les paramètres que chacune a retenus
===========  ==================================================================

C'est la seule comparaison qui réponde à la vraie question : « si j'avais
développé ce bot sous l'autre convention, aurais-je obtenu mieux ? »

Usage ::

    PYTHONPATH=. python scripts/compare_adx_smoothing.py [--trials 40] \\
        [--jobs 4] [--only pullback_trend]

Cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter.
"""
from __future__ import annotations

import argparse
import sys
from copy import deepcopy

import app.core.indicators_precompute as ip
from app.core.candle_store import get_store
from app.core.config import load_config
from app.engine.backtest import Backtester
from app.engine.engine import Engine
from app.engine.opt_scoring import composite_score
from app.engine.optimizer_search import OptimizerSearchEngine

# Stratégies ACTIVES (config.yaml: manual_active) qui lisent `_pre_adx14` —
# ce sont elles dont les bons scores de backtest pourraient tenir à la
# sur-réactivité. Les autres consommateurs de `_pre_adx14` ne sont pas actifs
# et ne pèsent donc pas dans la décision.
TARGETS = [
    ("pullback_trend",             "1h"),
    ("scoring_statistique_opus",   "1h"),
    ("scoring_statistique_opus_v2", "4h"),
    ("multi_tf_sr",                "1d"),
]

SYMBOL = "BTC/USDC"
# Découpage 60 / 20 / 20 sur l'historique disponible.
FRAC_IS, FRAC_OOS = 0.60, 0.20

#: En dessous de ce nombre de trades sur VAL, l'écart entre variantes n'est
#: pas interprétable : un « score » calculé sur 2 trades mesure deux tirages,
#: pas une stratégie. Le cas se présente réellement (multi_tf_sr en 1d fait
#: 2 trades sur 521 barres), et c'est précisément là que l'écart brut est le
#: plus spectaculaire — d'où ce garde-fou dans le script plutôt que dans le
#: commentaire de celui qui le lit.
MIN_VAL_TRADES = 20


def split_three(df):
    n = len(df)
    a = int(n * FRAC_IS)
    b = int(n * (FRAC_IS + FRAC_OOS))
    return df.slice(0, a), df.slice(a, b - a), df.slice(b, n - b)


def run_val(strategy_name: str, cfg: dict, params: dict, df_val, tf: str) -> dict:
    """Backtest de VAL avec des paramètres figés — aucune recherche ici."""
    import importlib
    mod = importlib.import_module(f"app.strategies.{strategy_name}")
    eng = Engine()
    eng.register(mod.Strategy(), silent=True)
    c = deepcopy(cfg)
    c.setdefault("strategy_params", {})[strategy_name] = params
    c.get("optimizer_results", {}).pop(strategy_name, None)
    res = Backtester(eng, c).run(df_val, SYMBOL, timeframe=tf)
    return {
        "score":  composite_score(res),
        "pnl":    res.total_pnl,
        "sharpe": res.sharpe,
        "trades": res.total_trades,
        "wr":     res.win_rate,
        "dd":     res.max_drawdown,
    }


def optimize_under(wilder: bool, strategy_name: str, cfg: dict, df_is, df_oos,
                   tf: str, trials: int, jobs: int) -> dict:
    """Réoptimise ``strategy_name`` sous une convention de lissage donnée."""
    ip.set_wilder_atr_adx(wilder)
    c = deepcopy(cfg)
    # Le drapeau doit voyager par cfg : les workers sont SPAWNÉS et n'héritent
    # d'aucun global du parent (cf. opt_workers._worker_init).
    c.setdefault("indicators", {})["wilder_atr_adx"] = wilder
    opt = OptimizerSearchEngine(strategy_name, c, df_is, df_oos,
                                symbol=SYMBOL, timeframe=tf)
    if not opt.param_space:
        return {"error": "espace de params vide"}
    return opt.random_search(trials, n_jobs=jobs, param_search_optim=False)


def fmt(v, w=9, p=2):
    return f"{v:>{w}.{p}f}" if isinstance(v, (int, float)) else f"{'n/m':>{w}}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    cfg = load_config()
    targets = [t for t in TARGETS if args.only in (None, t[0])]

    print(f"# {args.trials} essais/variante, sélection sur OOS, comparaison sur VAL "
          f"(jamais vue par l'optimiseur)\n", file=sys.stderr)
    rows = []
    for name, tf in targets:
        df = get_store().load_cached(SYMBOL, tf).sort("time")
        df_is, df_oos, df_val = split_three(df)
        print(f"\n{'=' * 78}\n{name} / {tf} — IS={len(df_is)} OOS={len(df_oos)} "
              f"VAL={len(df_val)}\n{'=' * 78}")

        out = {}
        for wilder in (False, True):
            label = "Wilder α=1/14" if wilder else "span=14 (actuel)"
            best = optimize_under(wilder, name, cfg, df_is, df_oos, tf,
                                  args.trials, args.jobs)
            if best.get("error") or best.get("failed"):
                print(f"  {label:<18} OPTIMISATION KO : "
                      f"{best.get('error', 'aucun essai')}")
                out[wilder] = None
                continue
            params = best.get("best_params") or {}
            # VAL est évaluée SOUS LA MÊME convention que l'optimisation qui a
            # produit ces paramètres — sinon on mesurerait un désaccordage.
            ip.set_wilder_atr_adx(wilder)
            c = deepcopy(cfg)
            c.setdefault("indicators", {})["wilder_atr_adx"] = wilder
            val = run_val(name, c, params, df_val, tf)
            out[wilder] = {"oos_score": best.get("best_oos_score"), "val": val,
                           "params": params}
            print(f"  {label:<18} OOS(sélection)={fmt(best.get('best_oos_score'), 7, 3)}"
                  f"   VAL score={fmt(val['score'], 7, 3)} "
                  f"pnl={fmt(val['pnl'])} sharpe={fmt(val['sharpe'], 6)} "
                  f"trades={val['trades']:>4} wr={fmt(val['wr'], 5, 1)} "
                  f"dd={fmt(val['dd'], 6)}")

        a, b = out.get(False), out.get(True)
        if a and b:
            d = b["val"]["score"] - a["val"]["score"]
            few = min(a["val"]["trades"], b["val"]["trades"]) < MIN_VAL_TRADES
            verdict = ("Wilder MEILLEUR" if d > 0 else
                       "span=14 MEILLEUR" if d < 0 else "égalité")
            if few:
                verdict = (f"NON INTERPRÉTABLE — {a['val']['trades']} vs "
                           f"{b['val']['trades']} trades (< {MIN_VAL_TRADES})")
            print(f"  → écart VAL (Wilder − span14) : {d:+.3f}  → {verdict}")
            rows.append((name, tf, a["val"], b["val"], d, few))

    print("\n" + "=" * 78)
    print("SYNTHÈSE — score VAL (fenêtre jamais vue par l'optimiseur)")
    print("=" * 78)
    print(f"{'stratégie':<28} {'TF':>4} {'span=14':>9} {'Wilder':>9} {'écart':>9} "
          f"{'trades':>13}")
    for name, tf, va, vb, d, few in rows:
        flag = " ⚠" if few else ""
        print(f"{name:<28} {tf:>4} {fmt(va['score'], 9, 3)} {fmt(vb['score'], 9, 3)} "
              f"{d:>+9.3f} {va['trades']:>6}/{vb['trades']:<6}{flag}")
    usable = [r for r in rows if not r[5]]
    if usable:
        wins = sum(r[4] > 0 for r in usable)
        worst = max(abs(r[4]) for r in usable)
        print(f"\nSur les {len(usable)} cibles INTERPRÉTABLES (≥ {MIN_VAL_TRADES} "
              f"trades) : Wilder l'emporte sur {wins}.")
        print(f"Écart absolu maximum : {worst:.3f}.")
        if len(rows) > len(usable):
            skipped = ", ".join(f"{r[0]}/{r[1]}" for r in rows if r[5])
            print(f"Écartées faute de trades : {skipped} — c'est là que l'écart brut")
            print("est le plus grand, et c'est exactement pourquoi il ne compte pas.")
        print("\nLecture : un écart faible et de signe variable = la convention de")
        print("lissage n'est pas ce qui fait vivre ces stratégies, et l'aligner")
        print("sur Wilder est alors une correction sans coût. Un avantage net et")
        print("répété pour span=14 dirait l'inverse : la sur-réactivité EST le")
        print("signal, et il faudrait alors la nommer correctement plutôt que la")
        print("supprimer (ex. ADX(7) assumé).")
    else:
        print("\nAucune cible interprétable : trop peu de trades sur VAL partout.")
    ip.set_wilder_atr_adx(False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
