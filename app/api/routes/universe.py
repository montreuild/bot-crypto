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
import threading
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

# Timeframes peuplés à l'ajout d'un ticker (alignés sur le trading / backfill).
_BACKFILL_TFS = ("15m", "30m", "1h", "4h", "1d")
# Volume max demandé par TF — yfinance tronque selon ses plafonds (ex. 60 j
# en 15m) ; CandleStore.fetch gère la pagination / profondeur max.
_BACKFILL_BARS = 50_000


def _start_symbol_backfill(symbol: str, cfg: dict) -> Dict[str, Any]:
    """Lance en thread daemon le fetch max multi-TF pour un symbole nouvellement
    ajouté. Retourne un résumé immédiat (le travail continue en arrière-plan)."""
    job: Dict[str, Any] = {
        "status": "started",
        "symbol": symbol,
        "timeframes": list(_BACKFILL_TFS),
        "results": [],
    }

    def _run() -> None:
        try:
            from app.core.candle_store import get_store
            from app.core.exchange import create_exchange

            exchange = create_exchange(cfg)
            store = get_store()
            for tf in _BACKFILL_TFS:
                try:
                    df = store.fetch(exchange, symbol, tf, total=_BACKFILL_BARS)
                    n = int(df.height) if df is not None else 0
                    job["results"].append({"tf": tf, "bars": n, "ok": n > 0})
                    logger.info(f"[Universe] backfill {symbol}/{tf} → {n} bougies")
                except Exception as e:
                    logger.warning(f"[Universe] backfill {symbol}/{tf} KO : {e}")
                    job["results"].append({
                        "tf": tf, "bars": 0, "ok": False, "error": str(e)[:200],
                    })
            job["status"] = "done"
        except Exception as e:
            logger.error(f"[Universe] backfill {symbol} KO : {e}")
            job["status"] = "error"
            job["error"] = str(e)[:300]

    threading.Thread(target=_run, daemon=True, name=f"univ-bf-{symbol}").start()
    return job


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
def get_universe(name: str, include_bars: bool = False):
    """Membres d'un univers.

    Par défaut **sans** lecture Parquet (réponse < 100 ms pour le SBF 120).
    ``include_bars=true`` ajoute le nombre de bougies en cache par TF — plus
    lent sous Docker/Windows (centaines de fichiers montés). L'UI charge d'abord
    la liste, puis peut re-fetch avec bars en arrière-plan.
    """
    from app.core.candle_store import get_store
    from app.core.universe import load_universe, universe_members

    if name not in _list_universe_files():
        raise HTTPException(404, f"Univers inconnu : {name!r} — disponibles : "
                                 f"{_list_universe_files()}")
    members = universe_members(name)
    data = load_universe(name)

    if not include_bars:
        return {
            "id": name,
            "label": data.get("name") or name,
            "venue": data.get("venue"),
            "as_of": str(data.get("as_of") or ""),
            "members": [{**m, "bars": {}} for m in members],
            "bars_included": False,
        }

    # Un seul parcours disque + count_bars (métadonnées) uniquement sur les
    # fichiers existants — évite 5×N appels inutiles.
    store = get_store()
    tfs = ("15m", "30m", "1h", "4h", "1d")
    enriched: List[Dict[str, Any]] = []
    for m in members:
        bars: Dict[str, int] = {}
        for tf in tfs:
            try:
                path = store._path(m["symbol"], tf)
                if not path.exists():
                    continue
                n = int(store.count_bars(m["symbol"], tf) or 0)
                if n > 0:
                    bars[tf] = n
            except Exception:
                pass
        enriched.append({**m, "bars": bars})

    return {
        "id": name,
        "label": data.get("name") or name,
        "venue": data.get("venue"),
        "as_of": str(data.get("as_of") or ""),
        "members": enriched,
        "bars_included": True,
    }


class _AddSymbolBody(BaseModel):
    """Symbole à suivre. ``sector`` n'existe pas dans le YAML d'univers (retiré)."""
    symbol: str
    name: Optional[str] = ""
    # Alias optionnel si le ticker exchange ≠ ticker provider (yfinance).
    provider_symbol: Optional[str] = None


@router.post("/api/universe/{name}/symbols", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def add_symbol(request: Request, name: str, body: _AddSymbolBody):
    """Ajoute un symbole à un univers existant.

    Refuse un doublon plutôt que de l'ignorer : l'appelant a demandé une
    modification, lui répondre 200 sans rien changer serait un succès menteur.

    L'écriture est textuelle (``add_member``) : commentaires et alignement du
    YAML d'univers sont préservés (pas de round-trip PyYAML complet).
    """
    from app.core.universe import add_member, universe_symbols

    if name not in _list_universe_files():
        raise HTTPException(404, f"Univers inconnu : {name!r}")

    symbol = (body.symbol or "").strip()
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(400, f"Symbole invalide : {symbol!r} — attendu des "
                                 f"lettres, chiffres et . _ - / (32 max). "
                                 f"Pour les actions, utiliser le ticker yfinance "
                                 f"(ex. ALO.PA, AIR.PA).")
    if symbol in universe_symbols(name):
        raise HTTPException(409, f"{symbol} est déjà dans l'univers {name!r}")

    extra: Dict[str, Any] = {}
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

    # Backfill max multi-TF en arrière-plan (yfinance pour actions / exchange
    # pour crypto) — même esprit que /api/data/backfill-equities.
    fetch_job = _start_symbol_backfill(symbol, state.cfg or {})

    return {
        "status": "added",
        "universe": name,
        "symbol": symbol,
        "n_symbols": total,
        "backfill": fetch_job,
    }


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
