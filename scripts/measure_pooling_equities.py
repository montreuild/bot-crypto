#!/usr/bin/env python3
"""ML-17 — valider le pooling multi-symboles sur des données ACTIONS RÉELLES.

Le pooling (``recipe_trainer.train_multi``) n'avait été mesuré que sur séries
SYNTHÉTIQUES : les hôtes Yahoo étaient refusés par la politique d'egress de
l'environnement où il a été écrit. Or une série synthétique valide la
mécanique (pas de fuite temporelle, pas de concaténation d'OHLCV) et ne dit
rien de ce qui compte : est-ce que ça produit un modèle utile sur de vraies
actions ? Ce script répond depuis le cache Parquet local — aucun appel réseau,
donc reproductible.

Trois mesures, dans l'ordre où elles tranchent :

1. **Couverture.** Combien de titres de l'univers peuvent produire un modèle
   SEULS ? C'est l'argument d'existence du pooling. S'il s'avérait que la
   plupart le peuvent, le pooling serait un raffinement, pas une nécessité.

2. **Solo vs poolé, sur le MÊME holdout.** La seule comparaison légitime.
   Comparer un modèle solo sur son holdout à un modèle poolé sur la moyenne
   de douze holdouts comparerait deux protocoles, pas deux modèles. La
   comparaison n'est possible que sur les titres assez longs pour le solo —
   c'est justement leur rareté qui est mesurée en (1).

3. **Dispersion par symbole.** Une moyenne de 0,64 peut recouvrir douze
   titres à 0,64 ou six à 0,75 et six à 0,53. Le gate arbitre sur la moyenne ;
   savoir laquelle des deux situations on a change ce qu'on fait ensuite.

Usage :
    python scripts/measure_pooling_equities.py
    python scripts/measure_pooling_equities.py --tf 1d --recipe omnibus_v4_multi
    python scripts/measure_pooling_equities.py --max-symbols 20
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# La console Windows est en cp1252 : les flèches et les barres de l'histogramme
# la font lever UnicodeEncodeError au milieu d'une mesure de dix minutes.
# ``errors="replace"`` plutôt que de renoncer aux caractères : un rapport
# légèrement dégradé vaut mieux qu'un script qui meurt après le calcul.
try:                                                        # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def _equities(tf: str):
    """(symbole, nb_barres) des actions du cache pour ce TF, plus longs d'abord.

    L'univers est déduit du CACHE et non d'une liste en dur : ce qui est
    mesurable est ce qui est là. Le suffixe ``.PA`` identifie Euronext Paris —
    convention du dépôt (cf. ``scripts/backfill_equities.py``).
    """
    from app.core.candle_store import get_store
    rows = [s for s in get_store().all_stats()
            if s.get("tf") == tf and str(s.get("symbol", "")).endswith(".PA")]
    return sorted(((r["symbol"], int(r["bars"])) for r in rows), key=lambda x: -x[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recipe", default="omnibus_v4_multi")
    ap.add_argument("--tf", default="1d")
    ap.add_argument("--max-symbols", type=int, default=16,
                    help="borne le pool (coût d'entraînement), plus longs d'abord")
    args = ap.parse_args()

    import app.ml.policy as policy
    from app.ml import recipe_trainer
    from app.ml.recipe import load_recipe
    from app.ml.train_runner import load_offline_ohlcv, train_multi_and_publish

    r = load_recipe(args.recipe)
    p = {**r.train_params(), **r.hp_for_tf(args.tf)}
    gc_ = policy.GateConfig.from_params({**r.gate_spec(), **r.gate_params(), **p})
    need_solo = gc_.holdout_bars + gc_.min_window_bars
    need_pool = gc_.holdout_bars + 250

    universe = _equities(args.tf)
    if not universe:
        print(f"Aucune action en cache pour tf={args.tf}. "
              f"Lancez d'abord scripts/backfill_equities.py.")
        return 1

    # ── 1. Couverture ────────────────────────────────────────────────────────
    solo_ok = [s for s, n in universe if n >= need_solo]
    pool_ok = [s for s, n in universe if n >= need_pool]
    print("=" * 72)
    print(f"1. COUVERTURE — univers actions en cache, tf={args.tf}, "
          f"recette={args.recipe}")
    print("=" * 72)
    print(f"  Titres en cache                     : {len(universe)}")
    print(f"  Entraînables SEULS (>= {need_solo} barres) : {len(solo_ok)}"
          f"  {'← ' + ', '.join(solo_ok) if solo_ok else ''}")
    print(f"  Éligibles au POOL  (>= {need_pool} barres) : {len(pool_ok)}")
    print(f"  Exclus même du pool                 : {len(universe) - len(pool_ok)}")
    print(f"\n  → {len(universe) - len(solo_ok)}/{len(universe)} titres ne peuvent "
          f"produire aucun modèle sans pooling.")

    pooled_syms = pool_ok[:args.max_symbols]
    if not pooled_syms:
        print("\n  Aucun titre éligible au pool — mesure impossible.")
        return 1

    # ── 2/3. Poolé ───────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"2. MODÈLE POOLÉ sur {len(pooled_syms)} titres")
    print("=" * 72)
    pooled = train_multi_and_publish(args.recipe, pooled_syms, args.tf, publish=False)
    if not str(pooled.get("decision", "")).startswith("dry_run"):
        print(f"  ÉCHEC : {pooled.get('reason')}")
        return 1

    cand = pooled.get("candidate") or {}
    tm = pooled.get("train_meta") or {}
    print(f"  Décision              : {pooled['decision']} — {pooled.get('reason')}")
    print(f"  Lignes train / valid  : {tm.get('n_train')} / {tm.get('n_valid')}")
    print(f"  Features (gardées)    : {tm.get('n_features')} ({tm.get('n_kept_features')})")
    print(f"  Coupure temporelle    : "
          f"{(tm.get('label_stats') or {}).get('split_at')}")
    print(f"  AUC en VALIDATION     : amp={tm.get('auc_amp')} dir={tm.get('auc_dir')}")
    print(f"  AUC sur HOLDOUT (moy.): amp={cand.get('auc_amp')} dir={cand.get('auc_dir')}"
          f"  [{cand.get('n_scored_symbols')} titres]")

    per = cand.get("per_symbol") or {}
    aucs = sorted(((s, m.get("auc_amp")) for s, m in per.items()
                   if isinstance(m, dict) and m.get("auc_amp") is not None),
                  key=lambda x: -(x[1] or 0))
    if aucs:
        print("\n  3. DISPERSION par titre (auc_amp sur holdout) :")
        for s, a in aucs:
            bar = "█" * int(max(0.0, (a - 0.45)) * 100)
            print(f"     {s:<10} {a:.3f}  {bar}")
        vals = [a for _, a in aucs]
        print(f"     {'—' * 30}")
        print(f"     min={min(vals):.3f}  médiane={sorted(vals)[len(vals)//2]:.3f}  "
              f"max={max(vals):.3f}  au-dessus du plancher {gc_.auc_floor} : "
              f"{sum(1 for v in vals if v >= gc_.auc_floor)}/{len(vals)}")

    # ── Solo vs poolé, même holdout ──────────────────────────────────────────
    print("\n" + "=" * 72)
    print("4. SOLO vs POOLÉ — même titre, même holdout")
    print("=" * 72)
    if not solo_ok:
        print("  Aucun titre n'a l'historique requis pour un modèle solo :")
        print("  la comparaison est IMPOSSIBLE sur cet univers, et c'est le")
        print("  résultat le plus parlant du script — sur actions, le pooling")
        print("  n'a pas d'alternative à laquelle être comparé.")
        return 0

    for symbol in solo_ok:
        df = load_offline_ohlcv(symbol, args.tf)
        n = len(df)
        holdout = df.tail(gc_.holdout_bars + 210)
        train_df = df.slice(0, n - gc_.holdout_bars)
        solo = recipe_trainer.train(args.recipe, train_df, args.tf, params=p)
        if solo is None:
            print(f"  {symbol} : entraînement solo KO")
            continue
        tmp = tempfile.mkdtemp(prefix="ml17_")
        try:
            prefix = os.path.join(tmp, f"{args.recipe}_{args.tf}")
            solo.save(prefix)
            m_solo = policy.score_holdout(prefix, holdout, strategy=args.recipe,
                                          gate_cfg=gc_, params=p)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        m_pool = per.get(symbol) or {}
        a_solo, a_pool = m_solo.get("auc_amp"), m_pool.get("auc_amp")
        print(f"  {symbol} ({n} barres) — holdout commun de {gc_.holdout_bars} barres")
        print(f"     solo  : auc_amp={a_solo}")
        print(f"     poolé : auc_amp={a_pool}")
        if a_solo is not None and a_pool is not None:
            delta = a_pool - a_solo
            verdict = ("le pooling AIDE" if delta > 0.01 else
                       "le pooling COÛTE" if delta < -0.01 else "équivalent")
            print(f"     écart : {delta:+.3f} → {verdict}")
    print("\n  Lecture : un seul titre permet cette comparaison. Un écart mesuré")
    print("  sur n=1 n'est pas un verdict — c'est un garde-fou contre une")
    print("  dégradation grossière, pas une preuve de gain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
