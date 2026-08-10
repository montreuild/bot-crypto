"""P2-1 : Utilitaires d'optimisation extraits de ``optimizer_search.py``.

Ce module est une **façade** qui ré-exporte les fonctions utilitaires
d'``optimizer_search.py`` pour permettre aux appelants d'importer depuis
un module plus léger, sans tirer tout le moteur de recherche (1292 lignes).

À terme, ces fonctions devraient vivre dans leur propre module et
``optimizer_search.py`` ne contenir que la classe ``StrategyOptimizer``
et ses méthodes de recherche (random/bayesian/grid/two-phase).
"""
from app.engine.optimizer_search import (
    FIXED_PARAMS,
    ML_HP_SPACE,
    # Constantes
    PARAM_SPACES,
    RECOMMENDED_LIMIT,
    STRATEGY_TIMEFRAMES,
    # Persistance
    apply_best_params,
    auto_fetch_limit,
    get_active_strategies_per_tf,
    ml_hp_space_for,
    record_optimizer_audit,
    # Fonctions utilitaires
    required_total_bars,
)

__all__ = [
    'PARAM_SPACES', 'STRATEGY_TIMEFRAMES', 'RECOMMENDED_LIMIT',
    'ML_HP_SPACE', 'FIXED_PARAMS',
    'required_total_bars', 'auto_fetch_limit', 'ml_hp_space_for',
    'apply_best_params', 'record_optimizer_audit', 'get_active_strategies_per_tf',
]
