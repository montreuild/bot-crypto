"""Ablation du bloc SMC et du bloc régime, sur la recette `omnibus_smc`.

Quatre variantes obtenues par les bascules `features.params` — donc sans
modifier une ligne de code entre deux entraînements :

    v4            smc=False regime=False   (= `omnibus_full`, référence)
    v4+regime     smc=False regime=True
    v4+smc        smc=True  regime=False
    v4+smc+regime smc=True  regime=True

Ce qu'on regarde en priorité est `auc_dir`. `auc_amp` est déjà à ~0.72 et
mesure « un mouvement notable arrive » ; `auc_dir` plafonne à ~0.53 et mesure
« dans quel sens » — c'est la métrique qui décide si une stratégie peut avoir
un edge directionnel. Un gain sur `amp` sans gain sur `dir` ne changerait rien
au problème que ce catalogue cherche à traiter.

Usage :  python scripts/measure_smc_ablation.py
"""
import json
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ml.recipe_trainer import train  # noqa: E402

RECETTE = "omnibus_smc"
VARIANTES = [
    ("v4 (reference)", {"smc": False, "regime": False}),
    ("v4+regime", {"smc": False, "regime": True}),
    ("v4+smc", {"smc": True, "regime": False}),
    ("v4+smc+regime", {"smc": True, "regime": True}),
]
JEUX = [("BTC_USDC", "1h"), ("ETH_USDC", "1h"),
        ("BTC_USDC", "30m"), ("ETH_USDC", "30m")]


def main() -> int:
    resultats = []
    for symbole, tf in JEUX:
        parquet = pathlib.Path(f"data/ohlcv/{symbole}/{tf}.parquet")
        if not parquet.exists():
            print(f"!! {symbole} {tf} : parquet absent, ignore", flush=True)
            continue
        df = pl.read_parquet(parquet)
        print(f"\n=== {symbole} {tf} ({len(df)} barres) ===", flush=True)
        for nom, params in VARIANTES:
            t0 = time.time()
            try:
                out = train(RECETTE, df, tf, params=dict(params))
            except Exception as e:
                print(f"  {nom:<16} ECHEC : {type(e).__name__}: {e}", flush=True)
                continue
            dt = time.time() - t0
            if out is None:
                print(f"  {nom:<16} train() -> None ({dt:.0f}s)", flush=True)
                continue
            m = out.train_meta or {}
            ligne = {
                "symbole": symbole, "tf": tf, "variante": nom,
                "auc_amp": m.get("auc_amp"), "auc_dir": m.get("auc_dir"),
                "n_features": m.get("n_features"),
                "n_kept": m.get("n_kept_features"),
                "secondes": round(dt, 1),
            }
            resultats.append(ligne)
            print(f"  {nom:<16} auc_amp={ligne['auc_amp']!s:<8.8} "
                  f"auc_dir={ligne['auc_dir']!s:<8.8} "
                  f"features={ligne['n_features']}->{ligne['n_kept']} ({dt:.0f}s)",
                  flush=True)

    sortie = pathlib.Path("scripts/_smc_ablation_results.json")
    sortie.write_text(json.dumps(resultats, indent=2), encoding="utf-8")

    # Recapitulatif : delta de chaque variante contre la reference du meme jeu.
    print("\n" + "=" * 78)
    print("DELTAS CONTRE LA REFERENCE v4 (positif = le bloc apporte)")
    print("=" * 78)
    ref = {(r["symbole"], r["tf"]): r for r in resultats
           if r["variante"] == "v4 (reference)"}
    print(f"{'jeu':<16}{'variante':<18}{'d auc_amp':>12}{'d auc_dir':>12}")
    for r in resultats:
        base = ref.get((r["symbole"], r["tf"]))
        if not base or r["variante"] == "v4 (reference)":
            continue
        if None in (r["auc_amp"], base["auc_amp"], r["auc_dir"], base["auc_dir"]):
            continue
        jeu = f"{r['symbole'].split('_')[0]} {r['tf']}"
        print(f"{jeu:<16}{r['variante']:<18}"
              f"{r['auc_amp'] - base['auc_amp']:>+12.4f}"
              f"{r['auc_dir'] - base['auc_dir']:>+12.4f}")
    print(f"\nresultats bruts -> {sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
