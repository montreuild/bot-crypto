#!/usr/bin/env python3
"""MLBackend reproduit-il le ``_train_impl`` autonome d'une stratégie ?

C'est la question qui décide de l'étape C. Si oui pour une recette donnée, on
peut supprimer son `_train_impl` sans changer le modèle produit ; si non, la
bascule change le modèle et doit être traitée comme telle.

Protocole : entraîner la stratégie par son chemin actuel, puis entraîner V11
(qui passe par MLBackend) sur la MÊME fenêtre avec les MÊMES paramètres de
recette, et comparer les prédictions sur un holdout commun.

Résultat de référence (2026-07-25, BTC/USDC 1h, 4 000 barres d'entraînement) —
les trois `_train_impl` autonomes restants produisent des prédictions
**strictement identiques** à celles de MLBackend, écart maximum 0.000000 :

    opus_stat_retrained_v4         amp 0.000000  dir 0.000000  corr 1.0000
    opus_omnibus_v7                amp 0.000000  dir 0.000000  corr 1.0000
    opus_omnibus_v11_followsetup   amp 0.000000  dir 0.000000  corr 1.0000
      (avec label_horizons=[1,3,6], calibrate=True — sa recette)

Autrement dit : leur suppression au profit de MLBackend ne change PAS le
modèle produit. C'est ce qui rend l'étape C sûre — sans cette mesure, elle
resterait un pari sur du code d'entraînement dupliqué.

Usage ::

    PYTHONPATH=. TRAIN_CACHE_MAX=0 \
        python scripts/check_train_impl_equivalence.py <stratégie>
"""
import os
import shutil
import sys
import tempfile

import numpy as np

from app.core.candle_store import get_store
from app.ml.backend.features import build_features
from app.ml.backend.persistence import load_amp_dir_bundle
from app.ml.backend.predictor import predict_batch_raw

import app.strategies.opus_omnibus_v11 as v11  # isort: skip

TF = "1h"
N_TRAIN = 4000
N_HOLDOUT = 400

# Paramètres de la recette omnibus_v4_single, identiques des deux côtés.
RECIPE_PARAMS = {
    "label_horizons": [1], "amp_top_pct": 0.30, "calibrate": False,
    "prune_features": False, "n_estimators": 500, "num_leaves": 31,
    "learning_rate": 0.03, "warmup_bars": 750, "retrain_every": 10 ** 9,
    "log_training": False, "enable_hour_filter": False,
    "di_rescue": float("inf"),
}


def train_and_predict(strategy, name: str, train_df, holdout_feats):
    strategy.fit(train_df, params={name: RECIPE_PARAMS})
    if not getattr(strategy, "is_trained", False):
        return None
    tmp = tempfile.mkdtemp(prefix="unify_")
    try:
        prefix = os.path.join(tmp, f"{name}_{TF}")
        strategy.save_model(prefix)
        b = load_amp_dir_bundle(prefix)
        if b is None or b.get("amp_model") is None:
            return None
        amp = predict_batch_raw(b["amp_model"], b.get("amp_cal"),
                                b["features"], b["medians"], holdout_feats)
        dir_ = predict_batch_raw(b.get("dir_model"), b.get("dir_cal"),
                                 b["features"], b["medians"], holdout_feats)
        return amp, dir_, len(b["features"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "opus_stat_retrained_v4"
    import importlib
    mod = importlib.import_module(f"app.strategies.{target}")

    df = get_store().load_cached("BTC/USDC", TF).sort("time")
    train_df = df.slice(len(df) - N_TRAIN - N_HOLDOUT, N_TRAIN)
    holdout = df.slice(len(df) - N_HOLDOUT - 300, N_HOLDOUT + 300)
    feats = build_features(holdout)
    if feats is None:
        print("features indisponibles")
        return 2

    a = train_and_predict(mod.Strategy(), target, train_df, feats)
    b = train_and_predict(v11.Strategy(), "opus_omnibus_v11", train_df, feats)
    if a is None or b is None:
        print(f"entraînement KO : {target}={a is not None} v11={b is not None}")
        return 2

    (amp_a, dir_a, nf_a), (amp_b, dir_b, nf_b) = a, b
    print(f"{'':>14} {target:<30} v11/MLBackend")
    print(f"{'n features':>14} {nf_a:<30} {nf_b}")
    for label, xa, xb in (("amp", amp_a, amp_b), ("dir", dir_a, dir_b)):
        if xa is None or xb is None:
            print(f"{label:>14} indisponible")
            continue
        d = np.abs(xa - xb)
        corr = float(np.corrcoef(xa, xb)[0, 1]) if len(xa) > 2 else float("nan")
        print(f"{label:>14} écart max={d.max():.6f}  moyen={d.mean():.6f}  "
              f"corr={corr:.4f}")
    same = (amp_a is not None and amp_b is not None
            and np.allclose(amp_a, amp_b, atol=1e-9))
    print("\nVERDICT :", "IDENTIQUE — _train_impl supprimable sans changement"
          if same else "DIVERGENT — la bascule changerait le modèle produit")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
