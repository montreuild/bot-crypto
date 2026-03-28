"""Routes optimiseur — démarrage, suivi, application et gestion des jobs."""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api import state
from app.api.helpers import verify_api_key, _clean, _discover_strategies
from app.core.candle_store import get_store
from app.core.exchange import create_exchange

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/optimize/start", dependencies=[Depends(verify_api_key)])
def optimizer_start(
    symbol:              str  = "BTC/USDC",
    strategies:          str  = "",
    timeframes:          str  = "",
    method:              str  = "bayesian",
    n_trials:            int  = 40,
    limit:               int  = 0,
    auto_apply:          bool = False,
    n_jobs:              int  = 1,
    early_stop_patience: int  = 0,
):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")

    from app.engine.auto_optimizer import get_all_jobs as _get_all_jobs
    _running = [jid for jid, j in _get_all_jobs().items() if j.get("status") == "running"]
    if _running:
        raise HTTPException(
            429,
            f"Une optimisation est déjà en cours "
            f"({len(_running)} job(s) actif(s) : {', '.join(_running[:3])})."
        )
    if not state._opt_semaphore.acquire(blocking=False):
        raise HTTPException(429, "Une optimisation est déjà en cours.")

    n_trials = max(1, min(n_trials, 200))
    if limit > 0:
        limit = max(100, min(limit, 50000))
    _cpu_count = os.cpu_count() or 1
    if n_jobs <= 0:
        n_jobs = max(1, _cpu_count - 1)
    else:
        n_jobs = min(n_jobs, max(1, _cpu_count - 1))

    try:
        from app.engine.auto_optimizer import AutoOptimizer
        from app.engine.optimizer import PARAM_SPACES, RECOMMENDED_LIMIT

        tf_list = (
            [t.strip() for t in timeframes.split(",") if t.strip()]
            if timeframes.strip()
            else state.cfg["trading"].get("timeframes",
                                          [state.cfg["trading"].get("timeframe", "1h")])
        )
        strats  = (
            [s.strip() for s in strategies.split(",") if s.strip()]
            if strategies
            else list(PARAM_SPACES.keys())
        )
        allowed = _discover_strategies()
        strats  = [s for s in strats if s in PARAM_SPACES and s in allowed]

        exchange      = create_exchange(state.cfg)
        df_map        = {}
        fetch_details    = {}
        received_counts  = {}
        for tf in tf_list:
            fetch_limit = limit if limit > 0 else RECOMMENDED_LIMIT.get(tf, 1000)
            fetch_details[tf] = fetch_limit
            df = get_store().fetch(exchange, symbol, tf, total=fetch_limit)
            n_received = len(df) if df is not None else 0
            received_counts[tf] = n_received
            if df is not None and n_received > 0:
                df_map[tf] = df
                if n_received < fetch_limit:
                    logger.info(
                        f"[Optimizer] TF={tf} : {n_received} bougies reçues "
                        f"(demandées: {fetch_limit})"
                    )
            else:
                logger.warning(f"[Optimizer] TF={tf} : aucune bougie reçue, ignoré")

        if not df_map:
            details = "; ".join(
                f"{tf}: {received_counts.get(tf, 0)} bougies reçues" for tf in tf_list
            )
            raise HTTPException(400, f"Aucune donnée reçue pour les TFs demandés. {details}.")

        def _on_apply(strat_name: str, params: dict):
            try:
                from app.core.config import load_config as _rl
                state.cfg.update(_rl("config.yaml"))
            except Exception as e:
                logger.warning(f"[optimizer/on_apply] rechargement config KO : {e}")
            if state.trader:
                state.trader.strat_params = state.cfg.get("strategy_params", {})
                state.trader.reload_active_strategies()

        opt = AutoOptimizer(
            state.cfg, n_trials=n_trials, method=method,
            on_apply_callback=_on_apply,
            notifier=state.trader.notif if state.trader else None,
            n_jobs=n_jobs,
            early_stop_patience=early_stop_patience,
        )
        job_ids, skipped = opt.start_async(df_map, symbol, strats,
                                           timeframes=tf_list, auto_apply=auto_apply)

        return {
            "status":        "started",
            "job_ids":       job_ids,
            "symbol":        symbol,
            "strategies":    strats,
            "timeframes":    tf_list,
            "method":        method,
            "n_trials":      n_trials,
            "skipped":       skipped,
            "n_jobs_created": len(job_ids),
            "fetch_details": fetch_details,
            "received_bars": received_counts,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] optimizer/start : {e}", exc_info=True)
        raise HTTPException(500, str(e))
    finally:
        state._opt_semaphore.release()


@router.get("/api/optimize/status")
def optimizer_status(job_id: str = ""):
    from app.engine.auto_optimizer import get_job, get_all_jobs
    if job_id:
        job = get_job(job_id)
        if not job:
            raise HTTPException(404, f"Job '{job_id}' introuvable")
        return job
    return get_all_jobs()


@router.get("/api/optimize/stream")
async def optimizer_stream(job_id: str):
    from app.engine.auto_optimizer import get_job
    import asyncio

    async def event_generator():
        last_progress = -1
        for _ in range(600):
            job = get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                break
            progress = job.get("progress", 0)
            if progress != last_progress:
                last_progress = progress
                payload = {
                    "progress":    progress,
                    "status":      job.get("status"),
                    "best_score":  job.get("best_score", 0),
                    "trials":      job.get("trials", [])[-1:],
                    "strategy":    job.get("strategy"),
                    "timeframe":   job.get("timeframe"),
                    "trials_done": job.get("trials_done", 0),
                    "n_trials":    job.get("n_trials", 0),
                }
                if job.get("status") in ("done", "error"):
                    payload["result"]   = job.get("result")
                    payload["error"]    = job.get("error")
                    payload["applied"]  = job.get("applied", False)
                    payload["baseline"] = job.get("baseline", {})
                yield f"data: {json.dumps(_clean(payload))}\n\n"
                if job.get("status") in ("done", "error"):
                    break
            await asyncio.sleep(0.8)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/optimize/apply", dependencies=[Depends(verify_api_key)])
def optimizer_apply(job_id: str, config_path: str = "config.yaml"):
    from app.engine.auto_optimizer import get_job
    from app.engine.optimizer import apply_best_params
    from app.core.config import load_config as _reload_cfg

    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' introuvable")
    if job.get("status") != "done":
        raise HTTPException(400, "Job non terminé")
    result = job.get("result", {})
    best   = result.get("best_params", {})
    strat  = job.get("strategy", "")
    tf     = job.get("timeframe", "")
    if not best or not strat:
        raise HTTPException(400, "Aucun meilleur paramètre")

    ok = apply_best_params(strat, best, config_path,
                           timeframe=tf,
                           oos_score=result.get("best_oos_score", 0.0))
    if not ok:
        raise HTTPException(500, "Erreur écriture config")

    trader_updated = False
    try:
        state.cfg.update(_reload_cfg(config_path))
    except Exception as e:
        logger.warning(f"[apply] reload config KO: {e}")
    if state.trader:
        try:
            state.trader.strat_params = state.cfg.get("strategy_params", {})
            state.trader.reload_active_strategies()
            trader_updated = True
        except Exception as e:
            logger.warning(f"[apply] propagation trader KO: {e}")

    return {"status": "applied", "strategy": strat, "timeframe": tf,
            "params": best, "trader_updated": trader_updated}


@router.post("/api/optimize/cancel", dependencies=[Depends(verify_api_key)])
def optimizer_cancel(job_id: str):
    """Annule un job d'optimisation en cours."""
    from app.engine.auto_optimizer import get_job, cancel_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' introuvable")
    if job.get("status") != "running":
        raise HTTPException(400, f"Le job n'est pas en cours (statut: {job.get('status')})")
    cancel_job(job_id)
    return {"status": "cancelling", "job_id": job_id}


@router.delete("/api/optimize/job", dependencies=[Depends(verify_api_key)])
def optimizer_delete_job(job_id: str):
    """Supprime un job terminé, annulé ou en erreur."""
    from app.engine.auto_optimizer import get_job, delete_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' introuvable")
    if not delete_job(job_id):
        raise HTTPException(400, "Impossible de supprimer un job en cours d'exécution")
    return {"status": "deleted", "job_id": job_id}


@router.get("/api/optimize/results")
def optimizer_results():
    """Retourne les résultats d'optimisation classés par (strategy, tf)."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    import yaml as _yaml
    try:
        with open("config.yaml", encoding="utf-8") as _f:
            _disk_cfg = _yaml.safe_load(_f) or {}
        if _disk_cfg.get("optimizer_results"):
            state.cfg["optimizer_results"] = _disk_cfg["optimizer_results"]
    except Exception as e:
        logger.warning(f"[optimizer/results] lecture config disque KO : {e}")
    raw    = state.cfg.get("optimizer_results") or {}
    from app.engine.optimizer import get_active_strategies_per_tf
    active = get_active_strategies_per_tf(state.cfg)
    result = {
        strat: {tf: entry for tf, entry in tf_map.items()}
        for strat, tf_map in raw.items()
        if isinstance(tf_map, dict)
    }
    return {
        "by_strategy_tf": result,
        "active_per_tf":  {tf: [s["name"] for s in v] for tf, v in active.items()},
    }


@router.get("/api/optimize/spaces")
def optimizer_spaces():
    from app.engine.optimizer import PARAM_SPACES, STRATEGY_TIMEFRAMES
    return {
        strat: {
            "params":     {k: v for k, v in space.items()},
            "timeframes": STRATEGY_TIMEFRAMES.get(strat, []),
            "n_combos":   1,
        }
        for strat, space in PARAM_SPACES.items()
    }
