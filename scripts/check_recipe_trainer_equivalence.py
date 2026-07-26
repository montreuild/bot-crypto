#!/usr/bin/env python3
"""``recipe_trainer`` reproduit-il le ``_train`` autonome d'une stratégie ?

Même question que ``check_train_impl_equivalence.py`` posait pour MLBackend,
et même protocole : entraîner les deux chemins sur la MÊME fenêtre, comparer
les prédictions sur un holdout commun. La conception exige que toute
divergence soit **énumérée avant** la bascule, pas constatée après.

Résultats de référence (BTC/USDC 1h synthétique, 3 400 barres d'entraînement).

**Famille omnibus — équivalence EXACTE.** ``omnibus_v4_multi`` passe par
``v4_polars@1`` + ``amp_dir_quantile``, et ``recipe_trainer`` reprend les
réglages de ``app.ml.backend.trainer`` à l'identique (``scale_pos_weight``,
early stopping 20, ``max_bin=63``, aucun ``seed`` explicite) ::

    omnibus_v4_multi   amp  écart max 0.00000000  corr 1.000000
                       dir  écart max 0.00000000  corr 1.000000

Basculer une recette omnibus ne change donc PAS le modèle produit. C'est la
mesure qui rend la bascule sûre, au sens du protocole de non-régression.
Verrouillée par ``tests/test_recipe_trainer.py::TestMLBackendEquivalence``.

**Famille stat48 — divergence, et elle est explicable** ::

    stat48_v5   amp  écart max 0.579  corr -0.075
                dir  écart max 0.433  corr  0.012

Deux causes, toutes deux du côté de la stratégie :

1. elle code en dur ``num_boost_round=300`` alors que la recette déclare
   ``n_estimators: 500`` — autrement dit la recette ne décrivait pas ce que la
   stratégie faisait, et le chemin recette la rend enfin vraie ;
2. son early stopping s'arrête sur un jeu de validation construit à partir de
   features anonymes, là où le chemin recette les nomme.

Conséquence opérationnelle : basculer ``stat48_v4``/``stat48_v5`` **change le
modèle produit**. C'est une décision, pas un détail d'implémentation — d'où
l'absence de bascule automatique. Ce que la bascule apporte en échange est
mesurable : l'artefact porte enfin ses noms de features, donc le gate sait le
scorer (``unsupported_format`` disparaît) et la recette devient promouvable,
ce qu'elle n'a jamais été.

Usage ::

    PYTHONPATH=. python scripts/check_recipe_trainer_equivalence.py stat48_v5
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Recette → stratégie qui porte le ``_train`` autonome de référence.
_REFERENCE_STRATEGY = {
    "stat48_v4": "scoring_statistique_opus_v4",
    "stat48_v5": "scoring_statistique_opus_v5",
}


def _synthetic(n: int, seed: int = 9) -> pl.DataFrame:
    """Série synthétique — le script doit tourner sans cache ni réseau."""
    from app.core.indicators import precompute_df
    rng = np.random.RandomState(seed)
    times = [dt.datetime(2023, 1, 1) + dt.timedelta(hours=i) for i in range(n)]
    close = 100 * np.cumprod(1 + rng.normal(0.0002, 0.012, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    return precompute_df(pl.DataFrame({
        "time": times, "open": open_, "close": close,
        "high": np.maximum(open_, close) * 1.004,
        "low": np.minimum(open_, close) * 0.996,
        "volume": rng.uniform(100, 1000, n),
    }))


def main() -> int:
    recipe = sys.argv[1] if len(sys.argv) > 1 else "stat48_v5"
    strat_name = _REFERENCE_STRATEGY.get(recipe)
    if strat_name is None:
        print(f"Aucune stratégie de référence pour {recipe!r} — "
              f"connues : {sorted(_REFERENCE_STRATEGY)}")
        return 2

    import importlib

    import app.ml.features_catalog as fc
    from app.ml import recipe_trainer as rt
    from app.ml.backend.persistence import load_lgb_with_scaler
    from app.ml.recipe import load_recipe

    df = _synthetic(4600)
    train_df, holdout = df.slice(0, 3400), df.tail(1200)
    r = load_recipe(recipe)

    print(f"\nRecette {recipe} — hp déclarés : {r.hp}")

    tmp = tempfile.mkdtemp(prefix="equiv_")
    strat = importlib.import_module(f"app.strategies.{strat_name}").Strategy()
    strat.fit(train_df, params={strat.name: {}})
    old_prefix = os.path.join(tmp, f"{recipe}_1h")
    strat.save_model(old_prefix)
    old = load_lgb_with_scaler(old_prefix)
    if old is None:
        print("La stratégie de référence n'a produit aucun artefact — abandon.")
        return 1

    new = rt.train(recipe, train_df, "1h")
    if new is None:
        print("recipe_trainer n'a produit aucun modèle — abandon.")
        return 1

    X = fc.build(r.features_catalog, holdout, r.features_params).matrix().astype(np.float64)
    print(f"\n{'tête':6} {'écart max':>10} {'corrélation':>12} "
          f"{'moy. ancien':>12} {'moy. nouveau':>13}")
    diverged = False
    for head, key in (("amp", "amp_model"), ("dir", "dir_model")):
        if head not in new.boosters or old.get(key) is None:
            continue
        p_old = old[key].predict(X)
        p_new = new.boosters[head].predict(X)
        gap = float(np.max(np.abs(p_old - p_new)))
        corr = float(np.corrcoef(p_old, p_new)[0, 1])
        diverged |= gap > 1e-6
        print(f"{head:6} {gap:10.6f} {corr:12.4f} {p_old.mean():12.3f} {p_new.mean():13.3f}")

    print("\n" + ("Les deux chemins DIVERGENT — cf. les trois causes énumérées "
                  "dans la docstring de ce script. Basculer cette recette change "
                  "le modèle produit : c'est une décision d'exploitation."
                  if diverged else
                  "Les deux chemins sont équivalents (écart < 1e-6) : la bascule "
                  "ne change pas le modèle produit."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
