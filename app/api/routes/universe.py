"""Routes univers d'instruments — lecture et ajout de symboles.

Un univers est un **choix** (un indice, une watchlist), pas une découverte :
sur actions on ne demande pas à la place « que cotes-tu ? », on décide ce qu'on
suit. Le fichier `data/universe/<nom>.yaml` porte donc ce choix, et il était
jusqu'ici modifiable uniquement à la main, sur la machine, dans un éditeur.

Ces routes le rendent modifiable depuis l'UI, ce qui suppose deux garanties :

* **Écriture round-trip.** Le fichier est très commenté — il documente la
  révision trimestrielle de l'indice et la date de son instantané. Une
  réécriture PyYAML les effacerait ; `app.core.yaml_io` préserve commentaires
  et ordre.
* **Le cache mémoire doit être invalidé.** `app.core.universe` mémoïse les
  univers parce que la boucle live les relit à chaque cycle. Ajouter un
  symbole sans vider ce cache le rendrait invisible jusqu'au redémarrage.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api import state
from app.api.helpers import verify_api_key
from app.core.audit_log import audit_log

logger = logging.getLogger(__name__)
router = APIRouter()

#: Un symbole est un identifiant de marché, pas du texte libre : lettres,
#: chiffres, point, tiret, underscore et slash (paires crypto). Sans cette
#: borne, la valeur atterrit dans un YAML versionné et dans des chemins de
#: fichiers du cache Parquet.
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,31}$")


def _list_universe_files() -> List[str]:
    import os

    from app.core.universe import UNIVERSE_DIR
    if not os.path.isdir(UNIVERSE_DIR):
        return []
    return sorted(f.rsplit(".", 1)[0] for f in os.listdir(UNIVERSE_DIR)
                  if f.endswith((".yaml", ".yml")))


@router.get("/api/universe", dependencies=[Depends(verify_api_key)])
def list_universes():
    """Univers disponibles, avec de quoi les choisir sans les ouvrir."""
    from app.core.universe import load_universe, universe_members

    out: List[Dict[str, Any]] = []
    for name in _list_universe_files():
        data = load_universe(name)
        members = universe_members(name)
        out.append({
            "id": name,
            "label": data.get("name") or name,
            "venue": data.get("venue"),
            "asset_class": data.get("asset_class"),
            "quote_currency": data.get("quote_currency"),
            "as_of": str(data.get("as_of") or ""),
            "verified": bool(data.get("verified", False)),
            "n_symbols": len(members),
        })
    return {"universes": out}


@router.get("/api/universe/{name}", dependencies=[Depends(verify_api_key)])
def get_universe(name: str):
    """Membres d'un univers, avec la profondeur de cache de chacun.

    ``bars`` accompagne chaque membre : c'est ce qui décide si un symbole peut
    entrer dans un entraînement poolé. Le renvoyer ici évite à l'UI de croiser
    elle-même deux endpoints pour afficher une liste utilisable.
    """
    from app.core.candle_store import get_store
    from app.core.universe import load_universe, universe_members

    if name not in _list_universe_files():
        raise HTTPException(404, f"Univers inconnu : {name!r} — disponibles : "
                                 f"{_list_universe_files()}")
    members = universe_members(name)
    stats = get_store().all_stats()
    by_symbol: Dict[str, Dict[str, int]] = {}
    for s in stats:
        by_symbol.setdefault(s["symbol"], {})[s["tf"]] = int(s["bars"])

    data = load_universe(name)
    return {
        "id": name,
        "label": data.get("name") or name,
        "venue": data.get("venue"),
        "as_of": str(data.get("as_of") or ""),
        "members": [{**m, "bars": by_symbol.get(m["symbol"], {})} for m in members],
    }


class _AddSymbolBody(BaseModel):
    symbol: str
    name: Optional[str] = ""
    sector: Optional[str] = None
    provider_symbol: Optional[str] = None


@router.post("/api/universe/{name}/symbols", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def add_symbol(request: Request, name: str, body: _AddSymbolBody):
    """Ajoute un symbole à un univers existant.

    Refuse un doublon plutôt que de l'ignorer : l'appelant a demandé une
    modification, lui répondre 200 sans rien changer serait un succès menteur.
    """
    from app.core.universe import add_member, universe_symbols

    if name not in _list_universe_files():
        raise HTTPException(404, f"Univers inconnu : {name!r}")

    symbol = (body.symbol or "").strip()
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(400, f"Symbole invalide : {symbol!r} — attendu des "
                                 f"lettres, chiffres et . _ - / (32 max)")
    if symbol in universe_symbols(name):
        raise HTTPException(409, f"{symbol} est déjà dans l'univers {name!r}")

    extra: Dict[str, Any] = {}
    if body.sector:
        extra["sector"] = body.sector
    if body.provider_symbol:
        extra["provider_symbol"] = body.provider_symbol
    try:
        total = add_member(name, symbol, body.name or "", **extra)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        logger.error(f"[Universe] écriture de l'univers {name} KO : {e}")
        raise HTTPException(500, f"Écriture impossible : {e}")

    audit_log("universe.symbol.add", ip=request.client.host if request.client else "",
              details={"universe": name, "symbol": symbol, "name": body.name, **extra})
    logger.info(f"[Universe] {symbol} ajouté à {name} ({total} membres)")
    return {"status": "added", "universe": name, "symbol": symbol,
            "n_symbols": total}


@router.delete("/api/universe/{name}/symbols/{symbol:path}",
               dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def remove_symbol(request: Request, name: str, symbol: str):
    """Retire un symbole. Le cache Parquet du symbole n'est PAS supprimé —
    sortir un titre de l'univers est une décision de suivi, pas un ordre
    d'effacement, et le récupérer coûterait un nouveau backfill complet."""
    from app.core.universe import remove_member

    if name not in _list_universe_files():
        raise HTTPException(404, f"Univers inconnu : {name!r}")
    try:
        total = remove_member(name, symbol)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error(f"[Universe] écriture de l'univers {name} KO : {e}")
        raise HTTPException(500, f"Écriture impossible : {e}")

    audit_log("universe.symbol.remove",
              ip=request.client.host if request.client else "",
              details={"universe": name, "symbol": symbol})
    return {"status": "removed", "universe": name, "symbol": symbol,
            "n_symbols": total}
