"""Pooling BTC+ETH et valeur reelle de la tete `tb`.

Deux questions, un seul dispositif :

1. Entrainer sur BTC ET ETH ensemble donne-t-il de meilleures AUC que deux
   modeles solo ? Les deux marches correlent a 0.835 sur les rendements
   horaires : le pooling ajoute donc surtout du VOLUME sur un processus
   proche, pas de la diversite. C'est exactement le regime ou il peut aider.
2. La tete `tb` (triple-barriere) apporte-t-elle, une fois qu'on la mesure
   sur un holdout franchement disjoint plutot que sur le split de validation ?

Protocole : holdout = les 4000 dernieres barres de chaque symbole, jamais vues.
Tout le reste sert a l'entrainement. Solo et poole sont scores sur LE MEME
holdout, sans quoi la comparaison ne voudrait rien dire.

Usage :  python scripts/measure_pooling_and_tb.py
"""
import json
import pathlib
import sys
import time

import numpy as np
import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ml import features_catalog as fc  # noqa: E402
from app.ml import labelling  # noqa: E402
from app.ml.recipe import load_recipe  # noqa: E402
from app.ml.recipe_trainer import train, train_multi  # noqa: E402
from app.ml.scoring import rank_auc  # noqa: E402

HOLDOUT = 4000
SYMBOLES = ("BTC_USDC", "ETH_USDC")


def evalue(modele, df_test, recette) -> dict:
    r = load_recipe(recette)
    fs = fc.build(r.features_catalog, df_test, r.features_params)
    lab = labelling.build(r.label_scheme, fs.frame, r.train_params())
    if fs is None or lab is None:
        return {}
    noms = list(modele.feature_names)
    X = fs.frame.select(noms).to_numpy().astype(np.float32)
    for j, c in enumerate(noms):
        m = ~np.isfinite(X[:, j])
        if m.any():
            X[m, j] = float(modele.medians.get(c, 0.0))
    out = {}
    for tete, booster in modele.boosters.items():
        y = lab.y.get(tete)
        if y is None or len(np.unique(y)) < 2:
            continue
        a = rank_auc(np.asarray(y), np.asarray(booster.predict(X))[:lab.n])
        if a is not None:
            out[f"auc_{tete}"] = round(float(a), 4)
    return out


def main() -> int:
    lignes = []
    for recette in ("omnibus_smc", "omnibus_smc_tb"):
        print(f"\n{'=' * 74}\nRECETTE {recette}\n{'=' * 74}", flush=True)
        entrain, test = {}, {}
        for s in SYMBOLES:
            df = pl.read_parquet(f"data/ohlcv/{s}/1h.parquet").tail(20000)
            entrain[s], test[s] = df.head(len(df) - HOLDOUT), df.tail(HOLDOUT)

        solos = {}
        for s in SYMBOLES:
            t0 = time.time()
            solos[s] = train(recette, entrain[s], "1h")
            print(f"  solo {s} entraine ({time.time() - t0:.0f}s)", flush=True)

        t0 = time.time()
        poole = train_multi(recette, entrain, "1h")
        print(f"  poole BTC+ETH entraine ({time.time() - t0:.0f}s)", flush=True)
        if poole is None:
            print("  !! train_multi a renvoye None", flush=True)
            continue

        tetes = sorted(set(poole.boosters))
        entete = "".join(f"{'auc_' + t:>10}" for t in tetes)
        print(f"\n  {'modele':<16}{'holdout':<12}{entete}")
        for s in SYMBOLES:
            for nom, modele in (("solo", solos.get(s)), ("poole BTC+ETH", poole)):
                if modele is None:
                    continue
                o = evalue(modele, test[s], recette)
                o.update(recette=recette, modele=nom, holdout=s)
                lignes.append(o)
                vals = "".join(f"{o.get('auc_' + t, float('nan')):>10.4f}" for t in tetes)
                print(f"  {nom:<16}{s:<12}{vals}", flush=True)

    pathlib.Path("scripts/_pooling_tb.json").write_text(
        json.dumps(lignes, indent=2), encoding="utf-8")
    print("\nresultats -> scripts/_pooling_tb.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
