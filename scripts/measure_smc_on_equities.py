"""Le bloc SMC tient-il hors crypto ? Le test de generalite qui manquait.

Tout le dossier SMC reposait sur BTC et ETH, qui correlent a 0.835 sur les
rendements horaires : deux vues d'un meme processus, pas deux echantillons.
Six actions XPAR correlent a 0.253 entre elles et 0.07-0.30 avec la crypto --
ce sont de vrais echantillons independants, sur un marche a seances, gaps de
nuit, et microstructure entierement differente.

Si le gain SMC sur la tete `dir` se retrouve la, il est structurel. Sinon, il
etait propre a la crypto, et le dossier doit le dire.

Usage :  python scripts/measure_smc_on_equities.py
"""
import json
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ml.recipe_trainer import train, train_multi  # noqa: E402

RECETTE = "omnibus_smc"
ACTIONS = ["AIR.PA", "MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "BNP.PA"]
VARIANTES = [("v4 (reference)", {"smc": False, "regime": False}),
             ("v4+smc",         {"smc": True,  "regime": False}),
             ("v4+smc+regime",  {"smc": True,  "regime": True})]


def frames() -> dict:
    out = {}
    for s in ACTIONS:
        p = pathlib.Path(f"data/ohlcv/{s}/1h.parquet")
        if p.exists():
            out[s] = pl.read_parquet(p)
    return out


def main() -> int:
    fr = frames()
    if not fr:
        print("aucune donnee action -- lancer scripts/backfill_equities.py")
        return 1
    lignes = []

    print("=" * 76)
    print("SOLO PAR TITRE (~4576 barres chacun -- echantillon mince, assume)")
    print("=" * 76)
    for s, df in fr.items():
        print(f"\n--- {s} ({len(df)} barres) ---", flush=True)
        for nom, params in VARIANTES:
            t0 = time.time()
            try:
                out = train(RECETTE, df, "1h", params=dict(params))
            except Exception as e:
                print(f"  {nom:<16} ECHEC {type(e).__name__}: {e}", flush=True)
                continue
            if out is None:
                print(f"  {nom:<16} train() -> None", flush=True)
                continue
            m = out.train_meta or {}
            r = {"portee": "solo", "symbole": s, "variante": nom,
                 "auc_amp": m.get("auc_amp"), "auc_dir": m.get("auc_dir")}
            lignes.append(r)
            print(f"  {nom:<16} auc_amp={r['auc_amp']} auc_dir={r['auc_dir']} "
                  f"({time.time() - t0:.0f}s)", flush=True)

    print("\n" + "=" * 76)
    print(f"POOLE — {len(fr)} titres mis en commun")
    print("=" * 76)
    for nom, params in VARIANTES:
        t0 = time.time()
        try:
            out = train_multi(RECETTE, fr, "1h", params=dict(params))
        except Exception as e:
            print(f"  {nom:<16} ECHEC {type(e).__name__}: {e}", flush=True)
            continue
        if out is None:
            print(f"  {nom:<16} train_multi() -> None", flush=True)
            continue
        m = out.train_meta or {}
        r = {"portee": "poole", "symbole": "6 actions", "variante": nom,
             "auc_amp": m.get("auc_amp"), "auc_dir": m.get("auc_dir")}
        lignes.append(r)
        print(f"  {nom:<16} auc_amp={r['auc_amp']} auc_dir={r['auc_dir']} "
              f"({time.time() - t0:.0f}s)", flush=True)

    print("\n" + "=" * 76)
    print("DELTAS CONTRE v4 (positif = le bloc apporte)")
    print("=" * 76)
    ref = {(x["portee"], x["symbole"]): x for x in lignes
           if x["variante"] == "v4 (reference)"}
    print(f"{'portee':<8}{'symbole':<12}{'variante':<16}{'d auc_amp':>11}{'d auc_dir':>11}")
    for x in lignes:
        b = ref.get((x["portee"], x["symbole"]))
        if not b or x["variante"] == "v4 (reference)":
            continue
        if None in (x["auc_amp"], b["auc_amp"], x["auc_dir"], b["auc_dir"]):
            continue
        print(f"{x['portee']:<8}{x['symbole']:<12}{x['variante']:<16}"
              f"{x['auc_amp'] - b['auc_amp']:>+11.4f}{x['auc_dir'] - b['auc_dir']:>+11.4f}")

    pathlib.Path("scripts/_smc_equities.json").write_text(
        json.dumps(lignes, indent=2), encoding="utf-8")
    print("\nresultats -> scripts/_smc_equities.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
