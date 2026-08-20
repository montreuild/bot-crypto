"""Budget d'essais d'une optimisation — proportionné à l'espace de recherche.

`optimizer.n_trials: 40` était un littéral de config, identique pour
`signal_consensus` (3 paramètres, 27 combinaisons — 40 essais dépassent la
grille entière) et pour `smart_money` (58 paramètres, 4,5×10¹⁹ combinaisons).
`scripts/audit_param_space.py` le montrait déjà : 15 stratégies sur 41 sous le
seuil de couverture.

La règle appliquée ici, dans l'ordre :

1. partir de ``optimizer.n_trials`` (le budget demandé par l'opérateur) ;
2. ne pas descendre sous ``trials_per_param × nombre de paramètres`` — une
   dimension de plus, c'est un axe de plus à explorer ;
3. plafonner à ``optimizer.max_trials`` — au-delà, le coût cesse d'être
   raisonnable et c'est le gel des paramètres inertes
   (``_should_reduce_space``) qui prend le relais, pas le budget brut ;
4. ne jamais dépasser la CARDINALITÉ de l'espace : tirer 40 fois dans
   27 combinaisons, c'est payer treize essais pour des doublons.

La règle 4 fait baisser le budget, les règles 2-3 le font monter. Le coût
global du parc augmente — c'est assumé : les corrections de performance de
cette même branche (htf_trend, bb_squeeze, cache d'entraînement) ont libéré
bien plus que ce que ce budget consomme.

``optimizer.trials_per_param: 0`` désactive tout et rend ``n_trials`` tel quel.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_TRIALS_PER_PARAM = 15
DEFAULT_MAX_TRIALS = 400


def cardinality(param_space: Optional[Dict[str, Any]]) -> int:
    """Produit du nombre de choix par paramètre (grille discrète)."""
    if not param_space:
        return 0
    try:
        return math.prod(len(choices) for choices in param_space.values())
    except TypeError:                       # un paramètre non énumérable
        return 0


def effective_n_trials(param_space: Optional[Dict[str, Any]],
                       base_n_trials: int,
                       cfg: Optional[dict] = None) -> tuple:
    """``(n_trials, detail)`` pour cet espace de recherche.

    ``detail`` est un dict destiné aux logs et à l'UI : il dit POURQUOI le
    budget vaut ce qu'il vaut, sinon un opérateur qui a demandé 40 essais et en
    voit tourner 330 n'a aucun moyen de le comprendre.
    """
    opt = ((cfg or {}).get("optimizer") or {})
    per_param = int(opt.get("trials_per_param", DEFAULT_TRIALS_PER_PARAM))
    plafond = int(opt.get("max_trials", DEFAULT_MAX_TRIALS))
    base = max(1, int(base_n_trials))

    n_params = len(param_space or {})
    card = cardinality(param_space)
    detail: dict = {"base": base, "n_params": n_params, "cardinality": card,
                    "trials_per_param": per_param, "max_trials": plafond}

    if per_param <= 0 or n_params == 0:
        detail["raison"] = "mise à l'échelle désactivée"
        return base, detail

    n = max(base, per_param * n_params)
    raison = "proportionné au nombre de paramètres" if n > base else "budget demandé"
    if n > plafond:
        n, raison = plafond, "plafonné (max_trials)"
    if 0 < card < n:
        n, raison = card, "borné par la cardinalité de l'espace"

    detail["raison"] = raison
    detail["n_trials"] = n
    return n, detail


def format_budget(strategy_name: str, detail: dict) -> str:
    """Ligne de log lisible — le budget effectif et sa justification."""
    return (f"[Optimizer] {strategy_name} : {detail.get('n_trials', detail['base'])} "
            f"essais ({detail['raison']}) — {detail['n_params']} paramètre(s), "
            f"cardinalité {detail['cardinality']:,}, demandé {detail['base']}"
            .replace(",", " "))
