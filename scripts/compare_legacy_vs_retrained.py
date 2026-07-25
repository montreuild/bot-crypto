#!/usr/bin/env python3
"""Le pack V4 legacy gagne-t-il encore contre un ré-entraînement ?

Comparaison LÉGITIME : les deux artefacts sont scorés sur le MÊME holdout,
avec la MÊME convention de labels (celle déclarée par la recette V4 :
label_horizons=[1], amp_top_pct=0.30). Le candidat est entraîné strictement
avant le holdout — aucune fuite.

C'est exactement ce que fait ``policy.decide_gate`` en production ; ce script
l'exécute hors gate pour trancher une question d'architecture : le pack figé
de mai 2026 mérite-t-il encore le code qui le maintient en vie ?

Usage ::

    PYTHONPATH=. python scripts/compare_legacy_vs_retrained.py

Résultat de référence (2026-07-25, BTC/USDC, holdout = 1500 dernières barres) ::

    TF    LEGACY auc_amp   RETRAIN auc_amp   legacy dir   retrain dir
    15m            0.598             0.638        0.467         0.529
    30m            0.656             0.674        0.524         0.519
    1h             0.600             0.663        0.482         0.530

Le ré-entraînement gagne 3/3. Cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §1.5.
"""
import os
import shutil
import sys
import tempfile

import app.ml.model_registry as registry
import app.ml.policy as policy
import app.strategies.opus_stat_retrained_v4 as retr
from app.core.candle_store import get_store

HOLDOUT = 1500
TFS = ("15m", "30m", "1h")

print(f"{'TF':>4} {'n_train':>8} {'holdout':>8} {'LEGACY auc_amp':>15} "
      f"{'RETRAIN auc_amp':>16} {'legacy dir':>11} {'retrain dir':>12}  verdict")
print("-" * 104)

# La convention de labels vient de la RECETTE (gate_spec), pas d'un défaut :
# scorer le pack V4 contre des labels multi-horizon qu'il n'a jamais appris
# fausserait la comparaison dans son sens défavorable.
gc_ = policy.GateConfig.from_params(policy.recipe_gate_defaults(retr.Strategy()))
print(f"# gate_spec de la recette : label_horizons={gc_.label_horizons} "
      f"amp_top_pct={gc_.amp_top_pct} metric={gc_.metric}\n", file=sys.stderr)

rows = []
for tf in TFS:
    df = get_store().load_cached("BTC/USDC", tf).sort("time")
    n = len(df)
    holdout_df = df.tail(HOLDOUT + 210)
    train_df = df.slice(0, n - HOLDOUT)

    # ── Sortant : le pack legacy, tel qu'il est résolu en production
    art = registry.resolve("BTC/USDC", tf, "opus_stat_pretrained_v4", pin="legacy")
    leg = policy.score_holdout(art.path_prefix, holdout_df,
                               strategy="opus_stat_pretrained_v4", gate_cfg=gc_)

    # ── Candidat : même recette, ré-entraînée sur tout l'historique dispo
    strat = retr.Strategy()
    p = {"warmup_bars": 750, "retrain_every": 10 ** 9}
    strat.fit(train_df, params={"opus_stat_retrained_v4": p})
    tmp = tempfile.mkdtemp(prefix="cmp_")
    try:
        prefix = os.path.join(tmp, f"opus_stat_retrained_v4_{tf}")
        strat.save_model(prefix)
        new = policy.score_holdout(prefix, holdout_df, strategy=strat, gate_cfg=gc_, params=p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    la, na = leg.get("auc_amp"), new.get("auc_amp")
    ld, nd = leg.get("auc_dir"), new.get("auc_dir")
    verdict = "?" if la is None or na is None else (
        "RETRAIN gagne" if na > la else "LEGACY tient")
    fmt = lambda v: f"{v:.3f}" if isinstance(v, float) else "n/a"  # noqa: E731
    print(f"{tf:>4} {len(train_df):>8} {len(holdout_df):>8} {fmt(la):>15} "
          f"{fmt(na):>16} {fmt(ld):>11} {fmt(nd):>12}  {verdict}")
    rows.append((tf, la, na))

print("-" * 104)
wins = sum(1 for _, la, na in rows if la is not None and na is not None and na > la)
print(f"Le ré-entraînement bat le legacy sur {wins}/{len(rows)} timeframes.")
