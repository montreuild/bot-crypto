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
import re
import threading
from typing import Any, Dict, List, Optional

import yaml  # type: ignore[import-untyped,unused-ignore]

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


#: Index inverse symbole → venue, mémoïsé par tuple de noms d'univers.
#: ``resolve_venue`` est appelé par symbole ET par cycle de scan : reconstruire
#: la table à chaque appel coûterait une boucle sur tout l'indice.
_venue_index: Dict[tuple, Dict[str, str]] = {}


def symbol_venue_index(names) -> Dict[str, str]:
    """Table ``{symbole: venue}`` déduite de la clé ``venue:`` des univers.

    Le premier univers qui déclare un symbole gagne — même règle d'ordre que
    ``resolve_universes``, pour qu'un instrument listé deux fois ne change pas
    de venue selon le chemin de lecture.
    """
    if not names:
        return {}
    if isinstance(names, str):
        names = [names]
    key = tuple(str(n) for n in names)
    with _cache_lock:
        cached = _venue_index.get(key)
    if cached is not None:
        return cached

    index: Dict[str, str] = {}
    for uni in key:
        venue = universe_venue(uni)
        if not venue:
            continue
        for symbol in universe_symbols(uni):
            index.setdefault(symbol, venue)
    with _cache_lock:
        _venue_index[key] = index
    return index


# ─────────────────────────────────────────────────────────────────────────────
#  Édition — insertion TEXTUELLE, pas round-trip YAML
# ─────────────────────────────────────────────────────────────────────────────
#
# Pourquoi pas `yaml_io.dump_yaml` comme partout ailleurs : mesuré sur
# `sbf120.yaml`, un aller-retour ruamel préserve les commentaires d'en-tête mais
# RÉÉCRIT les 122 lignes de `members:` — il perd l'alignement en colonnes tenu à
# la main et le commentaire de section « ── Reste du SBF 120 ── » posé au milieu
# de la liste. Ajouter un titre produisait donc un diff de 122 lignes dont une
# seule était voulue, sur un fichier que l'on relit et révise trimestriellement.
#
# L'insertion textuelle ne touche que la ligne ajoutée. Elle suppose la forme
# « une entrée par ligne » — celle du dépôt ; toute autre forme fait échouer
# proprement plutôt que de réécrire à l'aveugle.
_MEMBER_LINE = re.compile(r"^(\s*)-\s*(\{.*\}|\S.*)$")


def _members_block(lines: List[str]) -> tuple:
    """``(index_première, index_dernière, indentation)`` du bloc ``members:``.

    Lève ``ValueError`` si le bloc est absent ou n'est pas une liste d'entrées
    ligne à ligne : mieux vaut refuser que deviner.
    """
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"^members:\s*$", ln.rstrip("\n"))), None)
    if start is None:
        raise ValueError("clé 'members:' introuvable ou non vide sur sa ligne")
    first = last = None
    indent = "  "
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _MEMBER_LINE.match(lines[i].rstrip("\n"))
        if m:
            if first is None:
                first, indent = i, m.group(1)
            last = i
            continue
        if not lines[i].startswith((" ", "\t")):
            break            # nouvelle clé de premier niveau : fin du bloc
    if first is None or last is None:
        raise ValueError("bloc 'members:' vide ou de forme non reconnue")
    return first, last, indent


def add_member(name: str, symbol: str, label: str = "",
               **extra: Any) -> int:
    """Ajoute une entrée au fichier d'univers. Retourne le nouveau total.

    Lève ``ValueError`` sur doublon ou format non reconnu.
    """
    if symbol in universe_symbols(name):
        raise ValueError(f"{symbol} est déjà dans l'univers {name!r}")

    path = universe_path(name)
    # ``newline=""`` À LA LECTURE AUSSI : sans lui, Python traduit les CRLF en
    # LF en entrée, l'écriture brute les ressort en LF, et le diff porte sur les
    # 168 lignes du fichier au lieu de la seule ajoutée. Le fichier du dépôt est
    # en CRLF ; la règle ici est de ne rien changer qu'on n'a pas demandé.
    with open(path, "r", encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    _, last, indent = _members_block(lines)

    fields = [f"symbol: {symbol}"]
    if label:
        fields.append(f"name: {label}")
    fields.extend(f"{k}: {v}" for k, v in extra.items() if v is not None)

    # Reprendre la fin de ligne du VOISIN plutôt que d'imposer "\n" : le
    # fichier est en CRLF, une entrée en LF y ferait une ligne dépareillée.
    eol = "\r\n" if lines[last].endswith("\r\n") else "\n"
    entry = f"{indent}- {{{', '.join(fields)}}}{eol}"

    # Le fichier peut ne pas finir par un saut de ligne (c'est le cas ici) :
    # insérer après la dernière entrée collerait les deux sur la même ligne.
    if not lines[last].endswith(("\n", "\r")):
        lines[last] += eol
    lines.insert(last + 1, entry)

    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(lines)
    clear_cache()
    return len(universe_symbols(name))


def remove_member(name: str, symbol: str) -> int:
    """Retire une entrée. Retourne le nouveau total.

    Ne touche PAS au cache de bougies du symbole : sortir un titre de l'univers
    est une décision de suivi, pas un ordre d'effacement, et le récupérer
    coûterait un backfill complet.
    """
    path = universe_path(name)
    # ``newline=""`` À LA LECTURE AUSSI : sans lui, Python traduit les CRLF en
    # LF en entrée, l'écriture brute les ressort en LF, et le diff porte sur les
    # 168 lignes du fichier au lieu de la seule ajoutée. Le fichier du dépôt est
    # en CRLF ; la règle ici est de ne rien changer qu'on n'a pas demandé.
    with open(path, "r", encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    first, last, _ = _members_block(lines)

    target = None
    for i in range(first, last + 1):
        m = _MEMBER_LINE.match(lines[i].rstrip("\n"))
        if not m:
            continue
        body = m.group(2)
        found = re.search(r"symbol:\s*([^,}\s]+)", body)
        candidate = found.group(1) if found else body.strip().strip("{}\"'")
        if candidate == symbol:
            target = i
            break
    if target is None:
        raise ValueError(f"{symbol} absent de l'univers {name!r}")

    lines.pop(target)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(lines)
    clear_cache()
    return len(universe_symbols(name))


def clear_cache() -> None:
    """Vide le cache mémoire (tests, édition à chaud d'un fichier d'univers)."""
    with _cache_lock:
        _cache.clear()
        _venue_index.clear()
