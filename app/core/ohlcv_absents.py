"""Créneaux confirmés absents à la source — registre persistant.

Un « trou » calendaire n'est pas toujours une donnée manquante. Sur une valeur
peu liquide, un quart d'heure sans transaction ne produit **aucune bougie** :
le marché était ouvert, personne n'a traité. Mesuré sur le parc SBF 120 en
15 m, la corrélation est monotone — BNP.PA (volume médian 17 234) n'a aucun
trou, RCO.PA (273) en a 120, et 100 % des créneaux manquants tombent quand une
valeur liquide, elle, a bien une barre.

Le calendrier ne peut pas trancher : il sait que le marché était ouvert, pas
qu'il ne s'est rien passé. Seule la source le sait. Ce registre enregistre donc
sa **réponse**, pas une heuristique de liquidité : quand un rattrapage couvre
un créneau et ne le rend pas, ce créneau est confirmé absent.

Deux garde-fous sur ce qu'on a le droit de confirmer :

- **uniquement dans le span réellement couvert** par la réponse. Yahoo plafonne
  le 15 m à 60 jours ; au-delà il ne dit pas « ça n'existe pas », il ne dit
  rien. Confirmer là serait inventer une réponse ;
- **jamais la dernière barre** de ce span : elle peut être en cours de
  formation au moment de l'appel.

Vaut pour toutes les venues. Sur un exchange crypto, une maintenance produit
exactement la même situation : le créneau n'existera jamais.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Iterable, Set, Tuple

logger = logging.getLogger(__name__)

_verrou = threading.Lock()
_cache: Dict[Tuple[str, str], Set[int]] = {}

#: Au-delà, on cesse d'accumuler : un symbole aussi troué relève d'un choix de
#: timeframe, pas d'un rattrapage (cf. RCO.PA en 15 m, une bougie sur trois).
_MAX_ABSENTS = 50_000


def chemin(path_parquet: Path) -> Path:
    """`<dir>/<tf>.absent.json`, à côté du parquet dont il décrit les trous."""
    return path_parquet.with_suffix(".absent.json")


def charger(path_parquet: Path, symbol: str, tf: str) -> Set[int]:
    cle = (symbol, tf)
    with _verrou:
        connu = _cache.get(cle)
    if connu is not None:
        return connu
    absents: Set[int] = set()
    f = chemin(path_parquet)
    try:
        if f.exists():
            brut = json.loads(f.read_text(encoding="utf-8"))
            absents = {int(t) for t in (brut.get("absents") or [])}
    except Exception as e:
        logger.debug("[Absents] lecture %s KO : %s", f, e)
    with _verrou:
        _cache[cle] = absents
    return absents


def ajouter(path_parquet: Path, symbol: str, tf: str,
            nouveaux: Iterable[int]) -> int:
    """Enregistre des créneaux confirmés absents. Retourne le nombre ajouté."""
    nouveaux = {int(t) for t in nouveaux}
    if not nouveaux:
        return 0
    absents = charger(path_parquet, symbol, tf)
    avant = len(absents)
    with _verrou:
        absents |= nouveaux
        if len(absents) > _MAX_ABSENTS:
            absents = set(sorted(absents)[-_MAX_ABSENTS:])
            _cache[(symbol, tf)] = absents
        gele = sorted(absents)
    ajoutes = len(gele) - avant
    if ajoutes <= 0:
        return 0
    f = chemin(path_parquet)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"symbol": symbol, "tf": tf,
                                   "absents": gele}), encoding="utf-8")
        tmp.replace(f)
    except Exception as e:
        logger.warning("[Absents] écriture %s KO : %s", f, e)
    return ajoutes


def oublier(path_parquet: Path, symbol: str, tf: str) -> None:
    """Repart de zéro — pour un refetch explicite de l'utilisateur."""
    with _verrou:
        _cache.pop((symbol, tf), None)
    try:
        chemin(path_parquet).unlink(missing_ok=True)
    except Exception:
        pass


def _reset_for_tests() -> None:
    with _verrou:
        _cache.clear()
