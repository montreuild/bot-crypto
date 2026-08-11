"""La tete `tb` (triple-barriere) apporte-t-elle sur `dir` ?

Entraine `omnibus_smc_tb` (trois tetes) sur les memes jeux que l'ablation SMC.
La comparaison qui decide est auc_tb contre auc_dir DANS LE MEME ENTRAINEMENT :
memes features, meme fenetre, meme split. Seule la question posee change.

Un ecart favorable a `tb` signifie que la difficulte de `dir` ne venait pas
seulement du bruit, mais de la question elle-meme -- predire le signe d'un
rendement a horizon fixe est plus dur, et moins utile, que predire si une
position avec stop et cible aurait gagne.

Usage :  python scripts/measure_triple_barrier.py
"""
import json
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ml.recipe_trainer import train  # noqa: E402

RECETTE = "omnibus_smc_tb"
JEUX = [("BTC_USDC", "1h"), ("ETH_USDC", "1h"),
        ("BTC_USDC", "30m"), ("ETH_USDC", "30m")]


def main() -> int:
    lignes = []
    for symbole, tf in JEUX:
        parquet = pathlib.Path(f"data/ohlcv/{symbole}/{tf}.parquet")
        if not parquet.exists():
            continue
        df = pl.read_parquet(parquet)
        t0 = time.time()
        try:
            out = train(RECETTE, df, tf)
        except Exception as e:
            print(f"{symbole} {tf} ECHEC : {type(e).__name__}: {e}", flush=True)
            continue
        if out is None:
            print(f"{symbole} {tf} -> None", flush=True)
            continue
        m = out.train_meta or {}
        st = (m.get("label_stats") or {})
        ligne = {
            "symbole": symbole, "tf": tf,
            "auc_amp": m.get("auc_amp"), "auc_dir": m.get("auc_dir"),
            "auc_tb": m.get("auc_tb"),
            "tb_pct_cible": st.get("tb_pct_cible"),
            "tb_pct_stop": st.get("tb_pct_stop"),
            "tb_pct_expire": st.get("tb_pct_expire"),
            "boosters": sorted(out.boosters),
            "secondes": round(time.time() - t0, 1),
        }
        lignes.append(ligne)
        print(f"{symbole} {tf}: amp={ligne['auc_amp']} dir={ligne['auc_dir']} "
              f"tb={ligne['auc_tb']}  (cible {ligne['tb_pct_cible']}%)  "
              f"boosters={ligne['boosters']}  {ligne['secondes']}s", flush=True)

    pathlib.Path("scripts/_tb_results.json").write_text(
        json.dumps(lignes, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print(f"{'jeu':<16}{'auc_dir':>10}{'auc_tb':>10}{'ecart':>10}")
    print("=" * 62)
    for r in lignes:
        if r["auc_dir"] is None or r["auc_tb"] is None:
            continue
        jeu = f"{r['symbole'].split('_')[0]} {r['tf']}"
        print(f"{jeu:<16}{r['auc_dir']:>10.4f}{r['auc_tb']:>10.4f}"
              f"{r['auc_tb'] - r['auc_dir']:>+10.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
