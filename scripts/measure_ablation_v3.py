"""L8 (§102) / L10 — harnais d'ablation unique, avec la règle de L3 intégrée.

Le dépôt ablait déjà setup par setup, à la main, un module à la fois. Ce script
généralise : chaque mécanisme introduit par L1–L6, plus les modules laissés en
veille (§110), est activé seul par-dessus la configuration du YAML, et mesuré
sur les DEUX fenêtres.

**La règle vient de L3 et n'est pas négociable.** `no_pullback` balayait 4 cas
sur 4 en OOS et ne répliquait pas en IS ; `expected_value` a échoué de la même
façon en L4. Un mécanisme n'est donc déclaré **VALIDÉ** que s'il améliore le PnL
sur les deux fenêtres, dans une majorité de cas. Tout le reste est « non
concluant » — ce qui, pour un mécanisme optionnel, veut dire : reste désactivé.

Usage :
    python scripts/measure_ablation_v3.py --data data/ohlcv
"""
import argparse
import json
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.engine.backtest import Backtester  # noqa: E402
from app.engine.engine import Engine  # noqa: E402

# Un mécanisme = un nom, une surcharge de paramètres. Rien d'autre ne change.
MECANISMES = {
    # ── L1 — géométrie de sortie ─────────────────────────────────────────────
    "L1 sorties partielles":   {"use_partial_exits": True, "trail_mode": "dynamic"},
    "L1 trailing structurel":  {"use_partial_exits": True, "trail_mode": "structure"},
    "L1 trailing ATR seul":    {"use_trailing": True},
    # ── L3 — mémoire de structure ────────────────────────────────────────────
    "L3 porte direction":      {"structure_gate": "direction"},
    "L3 porte no_pullback":    {"structure_gate": "no_pullback"},
    # ── L4 — ciblage et qualité ──────────────────────────────────────────────
    "L4 cible valeur att.":    {"target_mode": "expected_value",
                                "use_calendar_liquidity": "targets"},
    "L4 plafond stop 4 ATR":   {"max_stop_atr": 4.0},
    # ── L6 — tiers ───────────────────────────────────────────────────────────
    "L6 porte tier D":         {"tier_gate": True},
    "L6 sizing par tier":      {"tier_sizing": True},
    # ── L10 — modules en veille (§110), repassés au harnais ──────────────────
    "L10 SMT filtre":          {"smt_filter": True},
    "L10 Silver Bullet":       {"sb_bonus": True},
    "L10 AMD":                 {"amd_bonus": True},
    "L10 Breaker retest":      {"use_breakers": True},
    "L10 BPR reversal":        {"use_bpr": True},
    "L10 Rejection blocks":    {"use_rejection_blocks": True},
    "L10 Sweeps calendaires":  {"use_calendar_liquidity": "sweeps"},
}


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
    d = Backtester(eng, cfg(params, tf), ml_mode="inline").run(
        df, symbole.replace("_", "/"), tf).to_dict()
    return {"n": d["total_trades"], "pnl": d["net_profit"],
            "pf": d["profit_factor"], "dd": d["max_drawdown"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ohlcv")
    # Historique COMPLET par défaut. La campagne d'origine tronquait à 12 000
    # barres : docs/SUITE_ABLATION_V3.md §2 a montré que ce plafond, et non la
    # métrique de sélection, expliquait une bonne part des optima dégénérés.
    ap.add_argument("--barres", type=int, default=60000)
    ap.add_argument("--cas", default="BTC_USDC:1h,BTC_USDC:4h,BTC_USDC:1d,"
                                     "ETH_USDC:1h,ETH_USDC:4h,ETH_USDC:1d",
                    help="liste symbole:tf séparée par des virgules")
    ap.add_argument("--sortie", default="scripts/_ablation_v3.json")
    args = ap.parse_args()

    base = yaml_params("smart_money")
    racine = pathlib.Path(args.data)
    cas, resultats = [], {m: [] for m in MECANISMES}

    for _spec in args.cas.split(","):
        symbole, tf = _spec.strip().split(":")
        chemin = racine / symbole / f"{tf}.parquet"
        if not chemin.exists():
            print(f"  (absent : {chemin})")
            continue
        df = pl.read_parquet(chemin).tail(args.barres)
        if len(df) < 800:
            print(f"  (trop court : {symbole} {tf}, {len(df)} barres)")
            continue
        if True:
            coupe = int(len(df) * 0.65)
            fenetres = {"IS": df.head(coupe), "OOS": df.tail(len(df) - coupe + 300)}
            ref = {f: run(base, d, symbole, tf) for f, d in fenetres.items()}
            cas.append({"symbole": symbole, "tf": tf, "reference": ref})
            print(f"\n=== {symbole} {tf} — référence : "
                  f"IS {ref['IS']['pnl']:+.1f} / OOS {ref['OOS']['pnl']:+.1f} ===")
            print(f"  {'mécanisme':<24} {'ΔIS':>9} {'ΔOOS':>9} {'n OOS':>6}  verdict")
            for nom, surcharge in MECANISMES.items():
                try:
                    r = {f: run({**base, **surcharge}, d, symbole, tf)
                         for f, d in fenetres.items()}
                except Exception as e:
                    print(f"  {nom:<24} ECHEC {type(e).__name__}: {e}")
                    continue
                d_is = r["IS"]["pnl"] - ref["IS"]["pnl"]
                d_oos = r["OOS"]["pnl"] - ref["OOS"]["pnl"]
                deux = d_is > 0 and d_oos > 0
                resultats[nom].append({"symbole": symbole, "tf": tf,
                                       "d_is": round(d_is, 2),
                                       "d_oos": round(d_oos, 2),
                                       "deux_fenetres": deux,
                                       "n_oos": r["OOS"]["n"]})
                print(f"  {nom:<24} {d_is:>+9.1f} {d_oos:>+9.1f} "
                      f"{r['OOS']['n']:>6}  {'✓ les deux' if deux else '–'}")

    print("\n\n══ Synthèse — un mécanisme n'est VALIDÉ que s'il gagne sur les "
          "DEUX fenêtres, dans une majorité de cas ══")
    print(f"  {'mécanisme':<24} {'cas 2/2':>8} {'cas':>5}  verdict")
    synthese = {}
    for nom, lignes in resultats.items():
        if not lignes:
            continue
        gagnants = sum(1 for x in lignes if x["deux_fenetres"])
        valide = gagnants > len(lignes) / 2
        synthese[nom] = {"cas": len(lignes), "gagnants_2_fenetres": gagnants,
                         "valide": valide, "detail": lignes}
        print(f"  {nom:<24} {gagnants:>8} {len(lignes):>5}  "
              f"{'VALIDÉ' if valide else 'non concluant → reste off'}")

    pathlib.Path(args.sortie).write_text(
        json.dumps({"cas": cas, "synthese": synthese}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    print(f"\nresultats -> {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
