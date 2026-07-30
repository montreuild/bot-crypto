"""Helpers YAML partagés par les routers de configuration (ARCH-013).

Avant le split, ``_save_yaml`` et ``_save_strategy_yaml`` vivaient dans
``app/api/routes/config.py`` et étaient importés — via ``from app.api.routes.config
import _save_yaml`` — par des modules *non-route* (``trades.py``, ``portfolio.py``).
Le rapport d'audit ARCH-013 pointait cette inversion : un module live dépendait
d'un helper privé d'un fichier de routes FastAPI.

Ce module dédié casse ce couplage : les routers et les modules live importent
désormais tous depuis ici, sans qu'aucun ne dépende d'un router spécifique.
"""
import logging

from app.api import state

logger = logging.getLogger(__name__)


def _save_yaml(updates_fn):
    """Applique updates_fn(disk_cfg) et réécrit config.yaml (thread-safe).

    V4-D : délègue au verrou UNIQUE de app.core.yaml_io (partagé avec le
    LiveTrader) — le verrou api ne protégeait pas des écritures live."""
    from app.core.yaml_io import update_config_yaml
    update_config_yaml(updates_fn)


def _save_strategy_yaml(strategy_name: str, updates_fn):
    """
    Applique updates_fn(data) et réécrit strategies/{strategy_name}.yaml (thread-safe).
    Crée le fichier s'il n'existe pas. Préserve les commentaires (round-trip).
    """
    from app.core.yaml_io import dump_yaml
    from app.engine.optimizer_search import _load_strategy_file, _strategy_file_path
    strat_path = _strategy_file_path(strategy_name)
    with state._config_write_lock:
        data = _load_strategy_file(strat_path)
        updates_fn(data)
        dump_yaml(strat_path, data)
