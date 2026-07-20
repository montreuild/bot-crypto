"""I/O YAML round-trip — préserve commentaires, ordre et style à la réécriture.

Les sauvegardes de fichiers de stratégies (`strategies/{nom}.yaml`) et de
`config.yaml` suivent le schéma **charger → muter en place → réécrire**. Avec
PyYAML, la réécriture *efface les commentaires*. Ce module utilise
``ruamel.yaml`` en mode round-trip : les commentaires (et l'ordre des clés) de
toutes les parties **non modifiées** sont conservés. Si ``ruamel`` n'est pas
installé, on retombe proprement sur PyYAML (comportement historique, sans
commentaires) — aucune régression fonctionnelle.

Usage :
    data = load_yaml(path)          # CommentedMap si ruamel dispo
    data.setdefault("optimizer_results", {})[tf] = {...}
    dump_yaml(path, data)           # commentaires des clés intactes préservés
"""
import logging
import os
import threading

import yaml as _pyyaml

logger = logging.getLogger(__name__)

try:
    from ruamel.yaml import YAML  # type: ignore
    _RUAMEL = True

    def _new_yaml() -> "YAML":
        y = YAML()                       # round-trip par défaut
        y.preserve_quotes = True
        y.indent(mapping=2, sequence=4, offset=2)
        y.width = 4096                   # évite les retours à la ligne intempestifs
        return y
except Exception:                        # pragma: no cover - dépend de l'install
    _RUAMEL = False


def ruamel_available() -> bool:
    """True si la préservation des commentaires est active (ruamel installé)."""
    return _RUAMEL


def load_yaml(path: str, default=None):
    """Charge un YAML en conservant les métadonnées de commentaires (ruamel).

    Retourne ``default`` (``{}`` par défaut) si le fichier est absent, vide ou
    illisible. L'objet renvoyé se manipule comme un ``dict`` (CommentedMap).
    """
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        if _RUAMEL:
            with open(path, "r", encoding="utf-8") as f:
                data = _new_yaml().load(f)
            return data if data is not None else default
        with open(path, "r", encoding="utf-8") as f:
            data = _pyyaml.safe_load(f)
        return data if data is not None else default
    except Exception as e:
        logger.warning(f"[yaml_io] lecture {path} KO : {e}")
        return default


def dump_yaml(path: str, data) -> None:
    """Écrit ``data`` en YAML.

    Si ``data`` provient de :func:`load_yaml` (ruamel), les commentaires des
    clés non modifiées sont préservés. Pour un ``dict`` simple (fichier neuf),
    la sortie est valide sans commentaire. Repli PyYAML si ruamel échoue.
    """
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    if _RUAMEL:
        try:
            with open(path, "w", encoding="utf-8") as f:
                _new_yaml().dump(data, f)
            return
        except Exception as e:
            logger.warning(f"[yaml_io] écriture ruamel {path} KO ({e}) — repli PyYAML")
    with open(path, "w", encoding="utf-8") as f:
        _pyyaml.dump(data, f, default_flow_style=False,
                     allow_unicode=True, sort_keys=False)


# ── Mise à jour verrouillée de config.yaml (V4-D : ARCH-04) ──────────────────
# Verrou UNIQUE pour toutes les écritures de config.yaml, quel que soit
# l'appelant (routes API, LiveTrader). Avant : le verrou vivait dans
# app.api.state et live_trader importait un helper privé d'un fichier de
# routes FastAPI (inversion live→api).
_config_yaml_lock = threading.Lock()


def update_config_yaml(updates_fn, path: str = "config.yaml") -> None:
    """Applique ``updates_fn(disk_cfg)`` et réécrit ``path`` (thread-safe).

    Round-trip (ruamel) : les commentaires du YAML sont préservés."""
    with _config_yaml_lock:
        disk_cfg = load_yaml(path, default={})
        updates_fn(disk_cfg)
        dump_yaml(path, disk_cfg)
