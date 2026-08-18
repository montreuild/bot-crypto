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

import yaml as _pyyaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

try:
    from ruamel.yaml import YAML
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


def config_files(path: str = "config.yaml") -> list:
    """Fichiers composant la configuration : ``path`` puis ses ``include:``.

    Le découpage (S11) est déclaratif : ``config.yaml`` liste les fichiers de
    ``config/`` qui portent chacun une responsabilité. Une config monolithique
    (sans ``include:``) reste parfaitement valide — la liste vaut alors
    ``[config.yaml]``.
    """
    root = load_yaml(path, default={})
    files = [path]
    base = os.path.dirname(os.path.abspath(path))
    for inc in (root.get("include") or []):
        p = inc if os.path.isabs(inc) else os.path.join(base, inc)
        if os.path.exists(p):
            files.append(p)
        else:
            logger.warning(f"[yaml_io] include introuvable, ignoré : {inc}")
    return files


def load_config_documents(path: str = "config.yaml") -> tuple:
    """Charge tous les fichiers de config et retourne ``(docs, merged, owner)``.

    - ``docs``   : ``{chemin: document}`` (CommentedMap si ruamel).
    - ``merged`` : vue fusionnée section par section. Les valeurs sont les
      **objets mêmes** des documents, pas des copies : muter
      ``merged["lifecycle"]["force_active"]`` mute le document propriétaire, ce
      qui préserve ses commentaires à la réécriture.
    - ``owner``  : ``{section: chemin}`` — quel fichier porte quelle section.

    Une section déclarée dans DEUX fichiers est une ambiguïté (laquelle gagne ?)
    et lève : c'est la même règle que pour les venues, appliquée au découpage.
    """
    docs = {}
    merged: dict = {}
    owner: dict = {}
    for f in config_files(path):
        d = load_yaml(f, default={})
        docs[f] = d
        for section, value in d.items():
            if section == "include":
                continue
            if section in owner:
                raise ValueError(
                    f"Section '{section}' déclarée à la fois dans "
                    f"{os.path.basename(owner[section])} et "
                    f"{os.path.basename(f)} : une section doit vivre dans un "
                    f"seul fichier, sinon l'ordre de chargement décide "
                    f"silencieusement laquelle s'applique."
                )
            owner[section] = f
            merged[section] = value
    return docs, merged, owner


def update_config_yaml(updates_fn, path: str = "config.yaml") -> None:
    """Applique ``updates_fn(cfg)`` et réécrit le ou les fichiers touchés.

    ``cfg`` est la vue fusionnée (cf. :func:`load_config_documents`) : un
    appelant écrit ``cfg["lifecycle"]["force_active"] = [...]`` sans savoir dans
    quel fichier la section vit. Seuls les fichiers effectivement modifiés sont
    réécrits ; une section nouvelle atterrit dans le fichier racine.

    Round-trip (ruamel) : les commentaires des parties non modifiées sont
    préservés.
    """
    import copy

    with _config_yaml_lock:
        docs, merged, owner = load_config_documents(path)
        before = copy.deepcopy(dict(merged))
        updates_fn(merged)

        dirty = set()
        for section, value in merged.items():
            if before.get(section) == value and section in owner:
                continue
            target = owner.get(section, path)
            # Réaffectation only si l'objet a changé d'identité : sinon la
            # mutation en place a déjà atteint le document propriétaire (et ses
            # commentaires sont intacts).
            if docs[target].get(section) is not value:
                docs[target][section] = value
            dirty.add(target)
        # Sections supprimées par updates_fn (ex. `manual_active` de D6).
        for section in before:
            if section not in merged and section in owner:
                docs[owner[section]].pop(section, None)
                dirty.add(owner[section])

        for f in sorted(dirty):
            dump_yaml(f, docs[f])
