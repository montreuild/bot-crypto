"""Registre des stratégies — découverte automatique des métadonnées d'optimisation."""
import importlib
import pkgutil
import logging
from typing import Dict, List, Any

import app.strategies as _strategies_pkg

logger = logging.getLogger(__name__)


def _build_registry() -> tuple:
    """Parcourt app/strategies/ et collecte les métadonnées de chaque Strategy."""
    timeframes_map: Dict[str, List[str]]        = {}
    param_spaces_map: Dict[str, Dict[str, List]] = {}
    fixed_params_map: Dict[str, Dict[str, Any]]  = {}

    for _, module_name, _ in pkgutil.iter_modules(_strategies_pkg.__path__):
        try:
            mod = importlib.import_module(f"app.strategies.{module_name}")
        except Exception as exc:
            logger.debug(
                f"[Registry] Impossible d'importer app.strategies.{module_name}: {exc}"
            )
            continue

        cls = getattr(mod, "Strategy", None)
        if cls is None:
            continue

        # Inclure uniquement les stratégies qui déclarent un espace de paramètres
        ps = getattr(cls, "param_space", None)
        if not ps:
            continue

        strat_name = getattr(cls, "name", module_name)
        tf = getattr(cls, "timeframes", None)
        fp = getattr(cls, "fixed_params", {})

        param_spaces_map[strat_name] = ps
        fixed_params_map[strat_name] = fp
        if tf:
            timeframes_map[strat_name] = tf

    logger.debug(
        f"[Registry] {len(param_spaces_map)} stratégies découvertes : "
        f"{sorted(param_spaces_map)}"
    )
    return timeframes_map, param_spaces_map, fixed_params_map


# ─── Singletons construits une seule fois à l'import du module ─────────────
_STRATEGY_TIMEFRAMES, _PARAM_SPACES, _FIXED_PARAMS = _build_registry()


def get_strategy_timeframes() -> Dict[str, List[str]]:
    """Retourne le mapping stratégie → timeframes recommandés."""
    return _STRATEGY_TIMEFRAMES


def get_param_spaces() -> Dict[str, Dict[str, List]]:
    """Retourne le mapping stratégie → espace de paramètres optimisables."""
    return _PARAM_SPACES


def get_fixed_params() -> Dict[str, Dict[str, Any]]:
    """Retourne le mapping stratégie → paramètres fixes."""
    return _FIXED_PARAMS
