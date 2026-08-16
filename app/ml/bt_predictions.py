"""Prédiction par LOT sur la fenêtre de backtest.

LightGBM paie un coût fixe par appel bien supérieur au calcul lui-même :
prédire 12 000 fois une ligne coûte un ordre de grandeur de plus que prédire
une fois 12 000 lignes (mesuré : 0,62 s contre 0,056 s sur 437 features et
300 arbres, soit ×11). Sur `polars`, le `slice().select()` par barre s'ajoute
au même titre.

Le dépôt applique déjà cette idée à deux endroits — ``MLBackend._batch_at``
(famille Opus) et ``smc_ml_edge._precalcule_predictions``, qui documente son
propre gain : ~190 s → ~15 s sur un backtest de 12 000 barres. Ce module donne
la primitive commune aux stratégies qui prédisaient encore ligne à ligne.

**Ce n'est pas une fuite**, et la raison mérite d'être répétée ici plutôt que
supposée connue. Le modèle utilisé est celui du dernier réentraînement, donc
entraîné sur des barres ANTÉRIEURES ; les features sont causales ; et la barre
``i`` ne lit que ``arr[i]``. On déplace le moment du CALCUL, jamais
l'information disponible. Le tableau est réécrit à chaque réentraînement, donc
une barre n'est jamais servie par un modèle plus récent qu'elle.

L'égalité est exacte, pas approchée : ``booster.predict(X)[i]`` est
bit-à-bit identique à ``booster.predict(X[i:i+1])[0]`` — chaque ligne est une
somme sur les arbres, indépendante des autres lignes. Vérifié NaN et inf
compris (cf. tests/test_bt_predictions.py).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


def predict_full(booster: Any, X: Any,
                 calibrator: Any = None) -> Optional[np.ndarray]:
    """Prédit ``booster`` sur TOUTE la matrice ``X``, calibration comprise.

    Retourne ``None`` — jamais une exception — si quoi que ce soit empêche le
    calcul : l'appelant retombe alors sur sa prédiction ligne à ligne. Au pire
    on perd la vitesse, jamais la justesse.
    """
    if booster is None or X is None or len(X) == 0:
        return None
    try:
        out = np.asarray(booster.predict(X), dtype=float)
    except Exception as e:                          # pragma: no cover — défensif
        logger.debug("[bt_predictions] predict par lot KO : %s", e)
        return None
    if out.ndim != 1 or len(out) != len(X):
        return None
    if calibrator is not None:
        try:
            out = np.asarray(calibrator.predict(out), dtype=float)
        except Exception as e:                      # pragma: no cover — défensif
            logger.debug("[bt_predictions] calibration par lot KO : %s", e)
            return None
    return out


def value_at(arr: Optional[np.ndarray], idx: int) -> Optional[float]:
    """``arr[idx]`` si l'indice est servable, ``None`` sinon."""
    if arr is None or idx < 0 or idx >= len(arr):
        return None
    v = float(arr[idx])
    return None if not np.isfinite(v) else v
