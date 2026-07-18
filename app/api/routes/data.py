"""Endpoints de gestion des données OHLCV (cache CandleStore) :
  • GET  /api/data/status  — état du cache (symboles/TF, plage, nb de bougies)
  • POST /api/data/refetch — (re)télécharge les données depuis l'exchange, via
    la MÊME machinerie que le live (schéma canonique garanti). Un symbole/TF
    précis, ou tous les symboles configurés si non précisés.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import verify_api_key
from app.core.candle_store import get_store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/data/status", dependencies=[Depends(verify_api_key)])
def data_status():
    """État du cache OHLCV local : un descriptif par (symbole, timeframe)."""
    try:
        return JSONResponse({"datasets": get_store().all_stats()})
    except Exception as e:                       # pragma: no cover
        logger.error(f"[data] status KO : {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/data/refetch", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("5/minute")
def data_refetch(request: Request, symbol: str = None, tf: str = None, bars: int = 6000):
    """(Re)télécharge les bougies. ``symbol``/``tf`` optionnels : si absents, on
    reprend les symboles/timeframes du scanner configuré. Réutilise
    ``CandleStore.fetch`` (pagination robuste + schéma canonique)."""
    cfg = state.cfg or {}
    scan = cfg.get("scanner", {}) if isinstance(cfg, dict) else {}
    symbols = [symbol] if symbol else list(scan.get("symbols", []) or [])
    tfs = [tf] if tf else list(scan.get("timeframes", []) or ["4h"])
    if not symbols:
        return JSONResponse(
            {"error": "Aucun symbole : préciser ?symbol=… ou configurer "
                      "scanner.symbols."}, status_code=400)
    try:
        from app.core.exchange import create_exchange
        exchange = create_exchange(cfg)
    except Exception as e:
        logger.error(f"[data] exchange KO : {e}")
        return JSONResponse({"error": f"Exchange indisponible : {e}"},
                            status_code=500)

    store = get_store()
    results = []
    for s in symbols:
        for t in tfs:
            try:
                df = store.fetch(exchange, s, t, total=int(bars))
                n = df.height if df is not None else 0
                results.append({"symbol": s, "tf": t, "bars": n, "ok": n > 0})
            except Exception as e:               # pragma: no cover
                logger.error(f"[data] refetch {s}/{t} KO : {e}")
                results.append({"symbol": s, "tf": t, "bars": 0, "ok": False,
                                "error": str(e)[:200]})
    ok = sum(1 for r in results if r["ok"])
    return JSONResponse({"results": results, "ok_count": ok,
                         "total": len(results)})
