"""Endpoints de gestion des données OHLCV (cache CandleStore) :
  • GET  /api/data/status  — état du cache (symboles/TF, plage, nb de bougies)
  • POST /api/data/refetch — (re)télécharge les données depuis l'exchange, via
    la MÊME machinerie que le live (schéma canonique garanti). Un symbole/TF
    précis, ou tous les symboles configurés si non précisés.
  • POST /api/data/backfill-equities — peupler le cache des actions (yfinance)
    pour les univers activés dans scanner.universe. Lance en tâche de fond
    (async) et retourne un job_id pour suivre l'avancement.
"""
import logging
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import verify_api_key
from app.core.candle_store import get_store

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Jobs de backfill en cours (mémoire) ─────────────────────────────────────
# S5 (audit V2) : la page /data de Jinja2 avait un bouton pour déclencher
# le backfill des actions depuis l'UI. Le port Next.js manquait cette
# fonctionnalité. On expose maintenant une route async + un suivi d'état.
_backfill_jobs: dict = {}
_backfill_lock = threading.Lock()


@router.get("/api/data/status", dependencies=[Depends(verify_api_key)])
def data_status():
    """État du cache OHLCV local : un descriptif par (symbole, timeframe)."""
    try:
        return JSONResponse({"datasets": get_store().all_stats()})
    except Exception as e:                       # pragma: no cover
        logger.error(f"[data] status KO : {e}")
        err_id = uuid.uuid4().hex[:8]
        logger.error(f"[data] status KO {err_id} : {e}", exc_info=True)
        return JSONResponse({"error": f"Erreur interne ({err_id})"}, status_code=500)


@router.post("/api/data/refetch", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("5/minute")
def data_refetch(request: Request, symbol: str | None = None, tf: str | None = None, bars: int = 6000):
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
        err_id = uuid.uuid4().hex[:8]
        logger.error(f"[data] exchange KO {err_id} : {e}", exc_info=True)
        return JSONResponse({"error": f"Erreur interne ({err_id})"},
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


@router.post("/api/data/backfill-equities", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("1/minute")
def data_backfill_equities(request: Request, tf: str = "1d", years: int = 20):
    """Déclenche en arrière-plan le backfill des actions (yfinance) pour
    les univers activés dans `scanner.universe` (ex. sbf120).

    S5 (audit V2) : la page Jinja2 /data avait un bouton pour déclencher
    le backfill. On rétablit ce service ici en async + suivi d'état.

    Parameters
    ----------
    tf : str
        Timeframe à backfiller (défaut 1d — Yahoo limite intraday à ~88
        bougies pour actions EU).
    years : int
        Nombre d'années d'historique à récupérer (défaut 20 — max pour
        Yahoo Finance daily).

    Returns
    -------
    JSONResponse
        {job_id, status: 'started', tf, years, univers: [...]}
    """
    cfg = state.cfg or {}
    scan = cfg.get("scanner", {}) if isinstance(cfg, dict) else {}
    univers = list(scan.get("universe", []) or [])
    if not univers:
        return JSONResponse(
            {"error": "Aucun univers activé dans scanner.universe. "
                      "Décommenter universe: [sbf120] dans config.yaml."},
            status_code=400,
        )

    job_id = str(uuid.uuid4())[:8]
    job = {
        "job_id": job_id,
        "status": "started",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tf": tf,
        "years": years,
        "univers": univers,
        "progress": {"done": 0, "total": 0, "current_symbol": None},
        "results": [],
        "error": None,
    }
    with _backfill_lock:
        _backfill_jobs[job_id] = job

    # Lance en thread daemon (évite de bloquer l'API)
    def _run_backfill(job_dict):
        try:
            from app.core.timeframes import TF_MINUTES
            from app.core.universe import load_universe
            from app.core.yfinance_provider import YFinanceProvider

            # Calcule le nombre de bougies à fetcher
            minutes_per_bar = TF_MINUTES.get(tf, 1440)
            total_bars = int(years * 365 * 1440 / minutes_per_bar)
            job_dict["progress"]["total"] = total_bars

            # Charge les instruments de l'univers
            all_symbols = []
            for u in univers:
                try:
                    syms = load_universe(u)
                    all_symbols.extend(syms)
                except Exception as e:
                    logger.warning(f"[backfill] univers {u} KO : {e}")
            job_dict["progress"]["total"] = len(all_symbols)

            provider = YFinanceProvider(cfg)
            done = 0
            for sym in all_symbols:
                job_dict["progress"]["current_symbol"] = sym
                try:
                    df = provider.fetch_bars(sym, tf, limit=total_bars)
                    n = len(df) if df is not None else 0
                    job_dict["results"].append({
                        "symbol": sym, "tf": tf, "bars": n, "ok": n > 0
                    })
                except Exception as e:
                    logger.warning(f"[backfill] {sym}/{tf} KO : {e}")
                    job_dict["results"].append({
                        "symbol": sym, "tf": tf, "bars": 0, "ok": False,
                        "error": str(e)[:200]
                    })
                done += 1
                job_dict["progress"]["done"] = done
            job_dict["status"] = "done"
            job_dict["finished_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logger.error(f"[backfill] job {job_id} KO : {e}")
            job_dict["status"] = "error"
            job_dict["error"] = str(e)

    threading.Thread(target=_run_backfill, args=(job,), daemon=True).start()

    logger.info(f"[data] backfill-equities job {job_id} démarré : "
                f"tf={tf}, years={years}, univers={univers}")
    return JSONResponse(job)


@router.get("/api/data/backfill-status/{job_id}", dependencies=[Depends(verify_api_key)])
def data_backfill_status(job_id: str):
    """Suit l'avancement d'un job de backfill lancé via /api/data/backfill-equities."""
    with _backfill_lock:
        job = _backfill_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": f"Job {job_id} introuvable"}, status_code=404)
    return JSONResponse(job)
