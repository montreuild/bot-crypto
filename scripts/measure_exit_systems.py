"""L1 (§101) — quel système de sortie, mesuré plutôt que supposé.

Quatre systèmes, mêmes signaux, mêmes frais, même découpe :

1. `tp_fixe`      — tout-ou-rien sur la poche de liquidité (l'existant)
2. `trailing_atr` — pas de TP, trailing ATR multi-phases sur toute la position
3. `partiel_atr`  — TP1 à 1 R (25 %) + TP2 poche (25 %) + runner en trailing ATR
4. `partiel_struct` — idem, runner derrière le dernier pivot confirmé (§30)

Seule la GESTION DE POSITION change d'un cas à l'autre : la détection, le score
et le ciblage sont identiques. C'est la seule façon d'attribuer l'écart à la
sortie et non au signal.

⚠ Sélection sur l'IS, lecture sur l'OOS. Sans découpe, on lirait de
l'in-sample : `docs/STRATEGY_SMC_ML_EDGE.md` §3 quinquies a montré ce que ça
coûte (+42 % qui devient −2 % sous validation honnête).

Usage :
    python scripts/measure_exit_systems.py --data data/ohlcv
"""
import argparse
import json
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402
from app.engine.opt_scoring import beats_baseline, composite_score  # noqa: E402

SYSTEMES = {
    "tp_fixe":        {"use_trailing": False, "use_partial_exits": False},
    "trailing_atr":   {"use_trailing": True,  "use_partial_exits": False},
    "partiel_atr":    {"use_trailing": False, "use_partial_exits": True,
                       "trail_mode": "dynamic"},
    "partiel_struct": {"use_trailing": False, "use_partial_exits": True,
                       "trail_mode": "structure"},
}


def yaml_strategie(nom: str) -> tuple:
    import yaml

    f = pathlib.Path(f"strategies/{nom}.yaml")
    if not f.exists():
        return {}, {}
    d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return dict(d.get("params") or {}), dict(d.get("optimizer_results") or {})


def cfg(nom: str, params: dict, tf: str) -> dict:
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
    }


def run(params: dict, df: pl.DataFrame, symbole: str, tf: str) -> dict:
    from app.strategies.smart_money import Strategy

    eng = Engine()
    eng.register(Strategy(), silent=True)
    res = Backtester(eng, cfg("smart_money", params, tf), ml_mode="inline").run(
        df, symbole.replace("_", "/"), tf)
    d = res.to_dict()
    trades = res.trades or []
    jambes = sum(len(t.get("exits") or []) for t in trades)
    return {
        "n": len(trades), "jambes": jambes,
        "pnl_pct": round(100.0 * d["net_profit"] / d["initial_capital"], 2),
        "pf": d["profit_factor"], "sharpe": d["sharpe"], "dd": d["max_drawdown"],
        "win": d["win_rate"], "score": round(composite_score(d), 3),
        "_res": d,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ohlcv")
    ap.add_argument("--barres", type=int, default=12000)
    ap.add_argument("--sortie", default="scripts/_exit_systems.json")
    args = ap.parse_args()

    base, _ = yaml_strategie("smart_money")
    racine = pathlib.Path(args.data)
    lignes = []
    reference: dict = {}   # (symbole, tf) -> métriques OOS du système actuel

    for symbole in ("BTC_USDC", "ETH_USDC"):
        for tf in ("1h", "4h"):
            chemin = racine / symbole / f"{tf}.parquet"
            if not chemin.exists():
                continue
            df = pl.read_parquet(chemin).tail(args.barres)
            # Découpe 65/35 : mêmes proportions que `split_is_oos` du dépôt.
            coupe = int(len(df) * 0.65)
            print(f"\n=== {symbole} {tf} — IS {coupe} / OOS {len(df) - coupe} ===")
            print(f"  {'système':<16} {'n':>4} {'jambes':>7} {'PnL%':>8} "
                  f"{'PF':>6} {'Sharpe':>7} {'DD%':>7} {'win%':>6} {'gate':>6}")
            for nom, surcharge in SYSTEMES.items():
                params = dict(base)
                params.update(surcharge)
                t0 = time.time()
                try:
                    r_is = run(params, df.head(coupe), symbole, tf)
                    # La passe OOS démarre `warmup` barres avant la coupure pour
                    # que le rodage se consomme sur des barres IS (défaut du
                    # protocole corrigé dans STRATEGY_SMC_ML_EDGE §3 quinquies).
                    r_oos = run(params, df.tail(len(df) - coupe + 300), symbole, tf)
                except Exception as e:
                    print(f"  {nom:<16} ECHEC {type(e).__name__}: {e}")
                    continue
                d = r_oos["_res"]
                # Référence = le système actuel (tp_fixe) sur la MÊME fenêtre :
                # la question n'est pas « ce système gagne-t-il ? » mais
                # « fait-il mieux que ce qui tourne aujourd'hui ? ».
                ref = reference.get((symbole, tf))
                gate, motif = beats_baseline(
                    r_oos["n"], d["net_profit"], d["win_rate"] / 100.0,
                    d["sharpe"], ref or {},
                ) if (r_oos["n"] and ref is not None) else (False, "référence absente")
                if nom == "tp_fixe":
                    reference[(symbole, tf)] = {
                        "pnl": d["net_profit"], "win_rate": d["win_rate"] / 100.0,
                        "sharpe": d["sharpe"], "trades": r_oos["n"],
                    }
                print(f"  {nom:<16} {r_oos['n']:>4} {r_oos['jambes']:>7} "
                      f"{r_oos['pnl_pct']:>8} {r_oos['pf']:>6} "
                      f"{r_oos['sharpe']:>7} {r_oos['dd']:>7} {r_oos['win']:>6} "
                      f"{'OK' if gate else 'refusé':>6}")
                for r in (r_is, r_oos):
                    r.pop("_res", None)
                lignes.append({"symbole": symbole, "tf": tf, "systeme": nom,
                               "is": r_is, "oos": r_oos, "gate_oos": bool(gate),
                               "secondes": round(time.time() - t0, 1)})

    pathlib.Path(args.sortie).write_text(
        json.dumps(lignes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nresultats -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
