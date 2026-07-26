"""Univers d'instruments statiques (G2 — actions).

Le scan dynamique par volume est un concept crypto : on interroge l'exchange,
il répond la liste des paires cotées. Sur actions, l'univers est un **choix**
(un indice, une watchlist) et pas une découverte — le plan directeur tranche
donc pour un fichier statique versionné, ``data/universe/<nom>.yaml``.

Ce module ne connaît ni le SBF 120 ni Euronext : il lit un fichier de la forme

    name: SBF 120
    venue: euronext-paris          # venue à assigner (optionnel)
    asset_class: equity
    quote_currency: EUR
    as_of: 2026-07-26
    members:
      - symbol: AIR.PA
        name: Airbus
      - MC.PA                      # forme courte acceptée

et retourne des symboles. Ajouter le Nasdaq ou une watchlist personnelle =
déposer un fichier, sans toucher au code.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Dict, List, Optional

import yaml

from app.core.config import DATA_ROOT

logger = logging.getLogger(__name__)

UNIVERSE_DIR = os.path.join(DATA_ROOT, "universe")

_cache: Dict[str, dict] = {}
_cache_lock = threading.Lock()


def universe_path(name: str) -> str:
    """Chemin du fichier d'univers — accepte ``sbf120`` ou ``sbf120.yaml``."""
    safe = os.path.basename(str(name).strip())
    if not safe.endswith((".yaml", ".yml")):
        safe += ".yaml"
    return os.path.join(UNIVERSE_DIR, safe)


def load_universe(name: str, refresh: bool = False) -> dict:
    """Charge un univers. Retourne ``{}`` si le fichier est absent ou illisible.

    Le résultat est mémoïsé : la boucle live appelle ``get_symbols`` à chaque
    cycle et l'univers, lui, ne bouge qu'entre deux revues d'indice.
    """
    key = str(name).strip()
    if not key:
        return {}
    if not refresh:
        with _cache_lock:
            cached = _cache.get(key)
        if cached is not None:
            return cached

    path = universe_path(key)
    data: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if isinstance(raw, dict):
            data = raw
        else:
            logger.warning(f"[Universe] {path} — racine YAML non-dict, ignoré.")
    except FileNotFoundError:
        logger.warning(
            f"[Universe] Univers '{key}' introuvable ({path}) — liste vide. "
            f"Créez le fichier ou retirez la référence dans scanner.universe."
        )
    except yaml.YAMLError as e:
        logger.error(f"[Universe] {path} illisible : {e}")

    with _cache_lock:
        _cache[key] = data
    return data


def universe_members(name: str, refresh: bool = False) -> List[dict]:
    """Membres normalisés : ``[{"symbol": ..., "name": ...}, …]``.

    Les entrées en forme courte (chaîne nue) et longue (mapping) coexistent —
    un fichier peut être enrichi progressivement sans être réécrit.
    """
    data = load_universe(name, refresh=refresh)
    out: List[dict] = []
    seen: set = set()
    for entry in (data.get("members") or []):
        if isinstance(entry, str):
            symbol, label = entry.strip(), ""
        elif isinstance(entry, dict):
            symbol = str(entry.get("symbol") or "").strip()
            label = str(entry.get("name") or "")
        else:
            logger.warning(f"[Universe] {name} — entrée ignorée : {entry!r}")
            continue
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        item = {"symbol": symbol, "name": label}
        if isinstance(entry, dict):
            for extra in ("sector", "lot_size", "provider_symbol"):
                if entry.get(extra) is not None:
                    item[extra] = entry[extra]
        out.append(item)
    return out


def universe_symbols(name: str, refresh: bool = False) -> List[str]:
    """Symboles d'un univers, dans l'ordre du fichier."""
    return [m["symbol"] for m in universe_members(name, refresh=refresh)]


def universe_venue(name: str) -> Optional[str]:
    """Venue à assigner aux membres (clé ``venue`` du fichier), ou None."""
    venue = load_universe(name).get("venue")
    return str(venue) if venue else None


def resolve_universes(names) -> List[str]:
    """Concatène plusieurs univers en préservant l'ordre, sans doublon.

    ``names`` accepte une chaîne (``"sbf120"``) ou une liste — c'est la forme
    que prend ``scanner.universe`` dans ``config.yaml``.
    """
    if not names:
        return []
    if isinstance(names, str):
        names = [names]
    out: List[str] = []
    seen: set = set()
    for uni in names:
        for symbol in universe_symbols(uni):
            if symbol not in seen:
                seen.add(symbol)
                out.append(symbol)
    return out


def clear_cache() -> None:
    """Vide le cache mémoire (tests, édition à chaud d'un fichier d'univers)."""
    with _cache_lock:
        _cache.clear()
