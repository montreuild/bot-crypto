"""Routes optimiseur — démarrage, suivi, application et gestion des jobs."""
import json
import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api import state
from app.api.helpers import _clean, _discover_strategies, verify_api_key
from app.core.candle_store import get_store
from app.core.exchange import create_exchange
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/optimize/start", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("5/minute")
def optimizer_start(
    request:             Request,
    symbol:              str  = DEFAULT_CONFIG_SYMBOL,
    symbols:             str  = "",
    strategies:          str  = "",
    timeframes:          str  = "",
    method:              str  = "bayesian",
    n_trials:            int  = 40,
    limit:               int  = 0,
    auto_apply:          bool = False,
    n_jobs:              int  = 1,
    early_stop_patience: int  = 0,
    ml_tune_hp:          bool = False,
    param_search_optim:  bool = True,
):
    """
    Démarre un ou plusieurs jobs d'optimisation.

    ``symbol`` : symbole unique (comportement historique, défaut "BTC/USDC").
    ``symbols``: liste CSV optionnelle (ex. "BTC/USDC,ETH/USDC") — si fournie,
    prime sur ``symbol`` et boucle sur chaque symbole (fetch + jobs par
    symbole), comme le fait `LiveTrader._auto_opt_thread` (cf. BT-12). Le
    comportement mono-symbole existant (``symbols`` non fourni) reste
    STRICTEMENT inchangé (même réponse plate qu'avant ce correctif).

    ``param_search_optim`` (activé par défaut) : les premiers essais de la
    recherche ``method`` (random/bayesian/grid) servent de dépistage — dans
    le budget ``n_trials``, sur la fenêtre complète — puis les paramètres à
    faible impact sont gelés pour les essais restants. Réduit la
    dimensionnalité effective de l'espace pour les stratégies à beaucoup de
    paramètres, pas un mode de recherche en plus ; sans effet (aucun essai
    dépensé) sur les petits espaces déjà bien couverts ou quand le dépistage
    est trop court pour être fiable. Désactivable pour retrouver le
    comportement historique exact.
    """
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
    # Un backtest tourne dans le MÊME process, sans le portillon mémoire/CPU
    # inter-jobs de AutoOptimizer (§_job_semaphore/_acquire_mem_slot, scopé aux
    # jobs d'optimisation) — le laisser tourner en même temps qu'un batch
    # d'optimisation (potentiellement des dizaines de jobs LightGBM) risque la
    # contention CPU/OOM. On refuse plutôt que de laisser les deux se marcher
    # dessus (cf. pattern peek de ``backtest_status()``).
    if not state._bt_semaphore.acquire(blocking=False):
        raise HTTPException(
            429,
            "Un backtest est en cours — patientez avant de lancer une "
            "optimisation (contention CPU/mémoire)."
        )
    state._bt_semaphore.release()
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
        from app.engine.optimizer_search import PARAM_SPACES, auto_fetch_limit

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

        # `symbols` (CSV) prime sur `symbol` : bascule en mode multi-symbole
        # (BT-12). Non fourni → un seul symbole, réponse legacy inchangée.
        multi_symbol = bool(symbols.strip())
        symbol_list  = (
            [s.strip() for s in symbols.split(",") if s.strip()]
            if multi_symbol
            else [symbol]
        )

        exchange = create_exchange(state.cfg)

        def _fetch_df_map(sym: str):
            """Récupère les bougies par TF pour `sym`. Logique commune aux
            modes mono/multi-symbole (extraite pour BT-12)."""
            df_map          = {}
            fetch_details   = {}
            received_counts = {}
            for tf in tf_list:
                # Limite auto dérivée du besoin réel des stratégies (cf. #2) : évite
                # que les stratégies ML (omnibus) soient ignorées faute de bougies.
                fetch_limit = limit if limit > 0 else auto_fetch_limit(tf, strats)
                fetch_details[tf] = fetch_limit
                df = get_store().fetch(exchange, sym, tf, total=fetch_limit, prefer_cache=True)
                n_received = len(df) if df is not None else 0
                received_counts[tf] = n_received
                if df is not None and n_received > 0:
                    df_map[tf] = df
                    if n_received < fetch_limit:
                        logger.info(
                            f"[Optimizer] {sym} TF={tf} : {n_received} bougies reçues "
                            f"(demandées: {fetch_limit})"
                        )
                else:
                    logger.warning(f"[Optimizer] {sym} TF={tf} : aucune bougie reçue, ignoré")
            return df_map, fetch_details, received_counts

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
            ml_tune_hp=ml_tune_hp,
            param_search_optim=param_search_optim,
        )

        if not multi_symbol:
            # ── Mono-symbole : comportement historique STRICTEMENT inchangé ──
            df_map, fetch_details, received_counts = _fetch_df_map(symbol)
            if not df_map:
                details = "; ".join(
                    f"{tf}: {received_counts.get(tf, 0)} bougies reçues" for tf in tf_list
                )
                raise HTTPException(400, f"Aucune donnée reçue pour les TFs demandés. {details}.")

            job_ids, skipped = opt.start_async(df_map, symbol, strats,
                                               timeframes=tf_list, auto_apply=auto_apply)

            return {
                "status":        "started",
                "job_ids":       job_ids,
                "symbol":        symbol,
                "strategies":    strats,
                "timeframes":    tf_list,
                "method":        method,
                "param_search_optim": param_search_optim,
                "n_trials":      n_trials,
                "skipped":       skipped,
                "n_jobs_created": len(job_ids),
                "fetch_details": fetch_details,
                "received_bars": received_counts,
            }

        # ── Multi-symbole (BT-12) : une passe fetch + start_async par symbole,
        # comme LiveTrader._auto_opt_thread — chaque symbole écrit ses propres
        # jobs `strategy@tf@symbol`, la concurrence restant bornée par le
        # sémaphore de l'optimiseur (AutoOptimizer partagé pour tout le lot). ──
        all_job_ids = []
        all_skipped = []
        per_symbol  = {}
        for sym in symbol_list:
            df_map, fetch_details, received_counts = _fetch_df_map(sym)
            if not df_map:
                per_symbol[sym] = {
                    "job_ids": [], "skipped": [],
                    "fetch_details": fetch_details, "received_bars": received_counts,
                    "error": "Aucune donnée reçue pour les TFs demandés.",
                }
                logger.warning(f"[Optimizer] {sym} : aucune donnée reçue, symbole ignoré.")
                continue
            job_ids, skipped = opt.start_async(df_map, sym, strats,
                                               timeframes=tf_list, auto_apply=auto_apply)
            all_job_ids.extend(job_ids)
            all_skipped.extend(skipped)
            per_symbol[sym] = {
                "job_ids": job_ids, "skipped": skipped,
                "fetch_details": fetch_details, "received_bars": received_counts,
            }

        if not all_job_ids:
            details = "; ".join(
                f"{s}: {d.get('error', 'aucun job créé')}" for s, d in per_symbol.items()
            )
            raise HTTPException(400, f"Aucune donnée reçue pour les symboles demandés. {details}.")

        return {
            "status":         "started",
            "job_ids":        all_job_ids,
            "symbols":        symbol_list,
            "strategies":     strats,
            "timeframes":     tf_list,
            "method":         method,
            "param_search_optim": param_search_optim,
            "n_trials":       n_trials,
            "skipped":        all_skipped,
            "n_jobs_created": len(all_job_ids),
            "per_symbol":     per_symbol,
        }
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} optimizer/start : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
    finally:
        state._opt_semaphore.release()


@router.get("/api/optimize/status", dependencies=[Depends(verify_api_key)])
def optimizer_status(job_id: str = ""):
    from app.engine.auto_optimizer import get_all_jobs, get_job
    if job_id:
        job = get_job(job_id)
        if not job:
            raise HTTPException(404, f"Job '{job_id}' introuvable")
        return job
    return get_all_jobs()


@router.get("/api/optimize/stream", dependencies=[Depends(verify_api_key)])
async def optimizer_stream(job_id: str):
    import asyncio

    from app.engine.auto_optimizer import get_job

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
@state.limiter.limit("10/minute")
def optimizer_apply(request: Request, job_id: str, config_path: str = "config.yaml",
                    force: bool = False):
    """Applique le meilleur paramétrage d'un job terminé.

    Garde-fou qualité (BT-04) : mêmes exigences que l'auto-apply
    (``opt_scoring.beats_baseline`` — ≥ MIN_SIGNIFICANT_TRADES trades OOS,
    PnL OOS positif et meilleur que le baseline, amélioration WR ou Sharpe).
    En cas de refus → HTTP 409 avec la raison. ``force=true`` = override
    utilisateur explicite et assumé.
    """
    from app.core.config import load_config as _reload_cfg
    from app.engine.auto_optimizer import get_job
    from app.engine.opt_scoring import beats_baseline
    from app.engine.optimizer_search import apply_best_params

    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' introuvable")
    if job.get("status") != "done":
        raise HTTPException(400, "Job non terminé")
    result = job.get("result", {})
    best   = result.get("best_params", {})
    strat  = job.get("strategy", "")
    tf     = job.get("timeframe", "")
    # Le job stocke le symbole sur lequel il a tourné (cf. AutoOptimizer.start_async) :
    # il FAUT le transmettre à apply_best_params, sinon la branche legacy (symbol=None)
    # écrase tout le mapping par symbole de optimizer_results[tf] (cf. BT-01).
    symbol = job.get("symbol")
    if not best or not strat:
        raise HTTPException(400, "Aucun meilleur paramètre")

    ok_quality, reason = beats_baseline(
        result.get("best_oos_trades", 0), result.get("best_oos_pnl", 0),
        result.get("best_oos_wr", 0), result.get("best_oos_sharpe", 0),
        job.get("baseline", {}),
        # P0 — Deflated Sharpe gate (cf. auto_optimizer.py)
        n_trials=int(job.get("n_trials", 1)) or 1,
        min_deflated_sharpe=(
            float(state.cfg.get("optimizer", {}).get("deflated_sharpe_min", 0.5))
            if state.cfg.get("optimizer", {}).get("deflated_sharpe_gate", True)
            else None
        ),
    )
    if not ok_quality and not force:
        raise HTTPException(
            409, f"Application refusée ({reason}). Utilisez force=true pour "
                 f"passer outre en connaissance de cause.")
    if not ok_quality and force:
        logger.warning(f"[apply] {job_id} : garde-fou contourné (force=true) — {reason}")

    ok = apply_best_params(strat, best, config_path,
                           timeframe=tf,
                           oos_score=result.get("best_oos_score", 0.0),
                           symbol=symbol)
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

    return {"status": "applied", "strategy": strat, "timeframe": tf, "symbol": symbol,
            "params": best, "trader_updated": trader_updated}


@router.post("/api/optimize/cancel", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def optimizer_cancel(request: Request, job_id: str):
    """Annule un job d'optimisation en cours."""
    from app.engine.auto_optimizer import cancel_job, get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' introuvable")
    if job.get("status") != "running":
        raise HTTPException(400, f"Le job n'est pas en cours (statut: {job.get('status')})")
    cancel_job(job_id)
    return {"status": "cancelling", "job_id": job_id}


@router.delete("/api/optimize/job", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def optimizer_delete_job(request: Request, job_id: str):
    """Supprime un job terminé, annulé ou en erreur."""
    from app.engine.auto_optimizer import delete_job, get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' introuvable")
    if not delete_job(job_id):
        raise HTTPException(400, "Impossible de supprimer un job en cours d'exécution")
    return {"status": "deleted", "job_id": job_id}


@router.get("/api/optimize/results", dependencies=[Depends(verify_api_key)])
def optimizer_results():
    """Retourne les résultats d'optimisation classés par (strategy, tf)."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        # S11 : passer par la fusion — `optimizer_results` peut vivre dans
        # config.yaml (héritage) comme dans un fichier inclus.
        from app.core.config import _load_and_merge
        _disk_cfg = _load_and_merge("config.yaml")
        if _disk_cfg.get("optimizer_results"):
            state.cfg["optimizer_results"] = _disk_cfg["optimizer_results"]
    except Exception as e:
        logger.warning(f"[optimizer/results] lecture config disque KO : {e}")
    raw    = state.cfg.get("optimizer_results") or {}
    from app.engine.optimizer_search import get_active_strategies_per_tf
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


@router.get("/api/optimize/spaces", dependencies=[Depends(verify_api_key)])
def optimizer_spaces():
    from app.engine.auto_optimizer import _is_ml_strategy
    from app.engine.optimizer_search import PARAM_SPACES, STRATEGY_TIMEFRAMES
    # P0-1 : calculer la vraie cardinalité de l'espace de params au lieu du
    # placeholder hardcodé "n_combos": 1. Pour chaque paramètre, on compte le
    # nombre de valeurs distinctes dans l'espace (ex: {ema_fast: [10,15,20,25]}
    # → 4 valeurs). Le produit donne le nombre total de combinaisons.
    # Pour les espaces continus (bornes min/max/step), on calcule aussi.
    def _count_combos(space: dict) -> int:
        n = 1
        for k, v in space.items():
            if isinstance(v, (list, tuple)):
                n *= len(v)
            elif isinstance(v, dict) and 'values' in v:
                n *= len(v['values'])
            elif isinstance(v, dict) and all(k2 in v for k2 in ('min', 'max', 'step')):
                step = float(v.get('step', 1))
                if step > 0:
                    n *= max(1, int((float(v['max']) - float(v['min'])) / step) + 1)
            # sinon : paramètre non dénombrable (ex: bool, const) → n *= 1
        return max(1, n)
    return {
        strat: {
            "params":     {k: v for k, v in space.items()},
            "timeframes": STRATEGY_TIMEFRAMES.get(strat, []),
            # P0-1 : cardinalité réelle de l'espace (avant : hardcodé 1)
            "n_combos":   _count_combos(space),
            "is_ml":      _is_ml_strategy(strat),
        }
        for strat, space in PARAM_SPACES.items()
    }
