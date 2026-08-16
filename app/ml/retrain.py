"""Cadence de réentraînement inline — y compris quand l'entraînement échoue.

Les stratégies ML « retrained » décident de se réentraîner ainsi :

    besoin = (tf non entraîné) OU (appels - dernier_succès >= retrain_every)

Ce test a un angle mort : **un entraînement qui ÉCHOUE ne met pas à jour
``dernier_succès``**, et tant qu'aucun modèle n'existe la première clause reste
vraie. La stratégie relance donc un entraînement complet à CHAQUE appel de
``score()``, sans aucun délai, jusqu'à ce que l'un réussisse.

Mesuré sur un backtest de 8 000 barres (`scoring_statistique_opus_v4`) :

    retrain_every =  800  →     7 entraînements réussis,   368 échecs ré-essayés
    retrain_every = 1600  →     3 entraînements réussis, 1 168 échecs ré-essayés

Le paramètre ne gouvernait donc pas la cadence : il gouvernait la cadence des
SUCCÈS, pendant que les échecs tournaient en boucle. C'est aussi ce qui rendait
`retrain_every` illisible à la mesure — augmenter sa valeur pouvait multiplier
le nombre d'entraînements au lieu de le réduire.

Le remède garde la réactivité au démarrage — il faut bien un premier modèle —
tout en bornant la boucle : après un échec, on attend ``retrain_every /
RETRY_BACKOFF_DIV`` appels avant de réessayer. Sur les valeurs du dépôt cela
fait 80 barres au lieu d'une seule, soit dix fois moins de tentatives, sans
retarder le premier succès de plus de 80 barres.

Sur une série où l'entraînement réussit — le cas normal en production —
ce module ne change rien : ``dernier_succès`` est mis à jour, et la cadence
est exactement celle d'avant.
"""
from __future__ import annotations

#: Après un échec, on réessaie au dixième de la cadence nominale.
RETRY_BACKOFF_DIV = 10


def retry_backoff(cadence: int) -> int:
    """Nombre d'appels à attendre après un entraînement qui a échoué."""
    return max(1, int(cadence) // RETRY_BACKOFF_DIV)


def due_for_training(*, is_trained: bool, calls: int, last_success: int,
                     last_attempt: int, cadence: int) -> bool:
    """Faut-il (ré)entraîner à cet appel ?

    ``last_attempt`` compte les tentatives, réussies ou non ; ``last_success``
    ne compte que les réussites. Les deux sont nécessaires : sans le premier on
    boucle sur les échecs, sans le second un succès ne remettrait pas le
    compteur de cadence à zéro.
    """
    cadence = max(1, int(cadence))
    if is_trained:
        return calls - int(last_success) >= cadence
    if not last_attempt:
        return True                     # jamais tenté : il faut un modèle
    return calls - int(last_attempt) >= retry_backoff(cadence)
