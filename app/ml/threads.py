"""Nombre de threads LightGBM — un seul point de décision.

`n_jobs=1` était codé en dur dans les deux entraîneurs
(`app/ml/backend/trainer.py`, `app/ml/recipe_trainer.py`). C'est **juste** dans
un worker d'optimisation — ils sont déjà N à se partager les cœurs, et
`_worker_init` force `OMP_NUM_THREADS=1` pour éviter les `std::bad_alloc` de
LightGBM sous contention. C'est **inutilement coûteux** pour l'entraînement
autonome (`train_runner`, `/api/ml/train`, `scripts/retrain_all_models.py`),
qui tourne seul : mesuré sur 12 000 barres × 437 features × 300 arbres,
5,52 s à 1 thread contre 1,80 s à 4 (×3,1).

**Le modèle produit ne dépend pas du nombre de threads.** La question méritait
d'être posée — un ordre de sommation différent dans la construction des
histogrammes pourrait déplacer une coupure — donc elle a été mesurée plutôt que
supposée : entre `n_jobs=1` et `n_jobs=4`, la sérialisation du booster diffère
d'exactement DEUX lignes sur 4 002, `[num_threads: 1]` contre
`[num_threads: 4]`, et les prédictions sont bit-à-bit identiques. Changer ce
réglage ne change donc pas les artefacts publiés (cf. tests/test_ml_threads.py).

Deux verrous ramènent la valeur à 1 :

- ``OMP_NUM_THREADS=1`` dans l'environnement — c'est le worker d'optimisation,
  posé par ``opt_workers._worker_init`` ;
- :func:`single_thread`, utilisé par l'optimiseur quand il évalue ses essais
  **dans le process courant** (``n_jobs<=1``, tests, repli après
  ``BrokenProcessPool``) : là, aucune variable d'environnement ne le signale.

``ML_LGB_THREADS`` force la valeur, quel que soit le contexte.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

#: Plafond volontairement bas : au-delà, LightGBM rend de moins en moins sur
#: les matrices de cette taille, et le reste de la machine a besoin de tourner.
_MAX_THREADS = 4

_local = threading.local()


@contextmanager
def single_thread():
    """Force ``lgb_threads()`` à 1 dans le thread courant.

    Thread-local et non global : l'optimiseur peut évaluer un essai in-process
    pendant qu'un autre thread entraîne un modèle pour l'UI, sans que l'un
    impose sa contrainte à l'autre.
    """
    precedent = getattr(_local, "force_single", False)
    _local.force_single = True
    try:
        yield
    finally:
        _local.force_single = precedent


def lgb_threads() -> int:
    """Threads à passer à LightGBM (``n_jobs`` / ``num_threads``)."""
    forced = os.environ.get("ML_LGB_THREADS")
    if forced:
        try:
            return max(1, int(forced))
        except ValueError:
            logger.warning("[ML] ML_LGB_THREADS=%r ininterprétable — ignoré", forced)
    if getattr(_local, "force_single", False):
        return 1
    if (os.environ.get("OMP_NUM_THREADS") or "").strip() == "1":
        return 1
    return max(1, min(_MAX_THREADS, (os.cpu_count() or 1) - 1))
