"""Découpage entraînement / validation des séries labellisées.

Les trois entraîneurs du dépôt (`ml/backend/trainer.py`, `ml/recipe_trainer.py`,
`strategies/ml_dynamic_threshold.py`) coupaient en 80/20 **contigu, sans
intervalle**. Or les labels regardent devant : `multi_horizon_labels(close,
[1, 3, 6, 12], …)` construit ``y[i]`` à partir de ``close[i+12]``. Les douze
dernières lignes d'entraînement portaient donc une information tirée des
premières lignes de validation.

L'ampleur est modeste — une douzaine de lignes sur plusieurs milliers — et elle
n'explique certainement pas à elle seule les AUC observées. Mais c'est le
défaut **par construction** que l'embargo supprime (López de Prado, *purging &
embargo*), et l'AUC de validation n'est pas une métrique décorative : c'est
elle qui arbitre la promotion des modèles (`app/ml/policy.py`,
`app/ml/overfitting_gate.py`). Une métrique de décision doit être mesurée
proprement.

Attendre une **légère baisse des AUC rapportées** après ce changement : c'est
la correction d'un biais, pas une régression.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, NamedTuple, Optional, Sequence

logger = logging.getLogger(__name__)

#: Plancher historique des deux entraîneurs — repris tel quel.
MIN_TRAIN = 100
MIN_VALID = 50
DEFAULT_FRACTION = 0.8


class SplitPlan(NamedTuple):
    """Découpage d'une série de ``n`` labels.

    - ``train`` : ``[0, train)`` — lignes d'entraînement, embargo déjà retiré ;
    - ``split`` : début de la validation, ``[split, n)`` ;
    - ``embargo`` : lignes neutralisées entre les deux (``split - train``).
    """

    train: int
    split: int
    n: int
    embargo: int


def label_embargo(label_horizons: Optional[Iterable[Any]]) -> int:
    """Nombre de lignes que le label de la barre ``i`` peut atteindre devant lui.

    C'est l'horizon MAXIMAL : une seule tête qui regarde 12 barres devant suffit
    à contaminer 12 lignes, quelles que soient les autres.
    """
    if not label_horizons:
        return 1
    try:
        horizons = [int(h) for h in label_horizons if int(h) >= 1]
    except (TypeError, ValueError):
        return 1
    return max(horizons) if horizons else 1


def chrono_split(n: int, label_horizons: Optional[Iterable[Any]] = None,
                 fraction: float = DEFAULT_FRACTION,
                 min_train: int = MIN_TRAIN,
                 min_valid: int = MIN_VALID) -> Optional[SplitPlan]:
    """Découpage chronologique avec embargo, ou ``None`` s'il est infaisable.

    ``split`` reproduit exactement la formule historique
    (``min(max(int(n × 0.8), 100), n − 50)``) : à embargo nul, le découpage est
    celui d'avant, indice pour indice. L'embargo ne mord QUE sur la fin de
    l'entraînement — la validation garde toutes ses lignes, donc l'AUC reste
    calculée sur le même échantillon et les deux mesures restent comparables.
    """
    n = int(n)
    if n <= 0:
        return None
    split = max(int(n * fraction), min_train)
    split = min(split, n - min_valid)
    embargo = label_embargo(label_horizons)
    train = split - embargo
    if train < min_train or n - split < min_valid:
        return None
    return SplitPlan(train=train, split=split, n=n, embargo=embargo)


def purged_time_series_splits(n: int, n_splits: int, embargo: int = 0
                              ) -> Sequence[tuple]:
    """Folds walk-forward (fenêtre d'entraînement croissante) avec embargo.

    Reprend la géométrie de ``ml_dynamic_threshold._time_series_split`` — folds
    de test de taille fixe ``n // (n_splits + 1)`` — en retirant ``embargo``
    lignes à la fin de chaque fenêtre d'entraînement. Retourne une liste de
    ``(train_idx, test_idx)`` ; les folds dont l'entraînement deviendrait vide
    sont écartés plutôt que rendus tronqués.
    """
    import numpy as np

    if n_splits < 2:
        return []
    test_size = n // (n_splits + 1)
    if test_size < 1:
        return []
    splits = []
    for i in range(n_splits):
        train_end = n - (n_splits - i) * test_size
        train_stop = train_end - max(0, int(embargo))
        if train_stop <= 0:
            continue
        train_idx = np.arange(0, train_stop)
        test_idx = np.arange(train_end, train_end + test_size)
        if len(test_idx) > 0:
            splits.append((train_idx, test_idx))
    return splits
