"""Jobs asynchrones d'entraînement/sweep ML pour l'UI (page « Modèles », ML-02
tâche E7). Même pattern que ``app.engine.auto_optimizer`` (dict de jobs +
thread daemon + polling), mais scope dédié : entraîner un modèle est un job
plus léger que l'optimisation de stratégie (pas d'exclusion mutuelle avec
elle — les deux peuvent tourner en même temps sans contention significative).
"""
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Borne le nombre de jobs conservés en mémoire (comme auto_optimizer) — évite
# une fuite mémoire si l'UI reste ouverte des jours avec des entraînements
# répétés sans jamais nettoyer les jobs terminés.
_MAX_JOBS = 200


def _update_job(job_id: str, **kwargs: Any) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def get_all_jobs() -> Dict[str, dict]:
    with _jobs_lock:
        return {k: dict(v) for k, v in _jobs.items()}


def delete_job(job_id: str) -> bool:
    with _jobs_lock:
        return _jobs.pop(job_id, None) is not None


def _register_job(kind: str, **fields: Any) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"kind": kind, "status": "running", "started_at": time.time(),
                         "result": None, "error": None, **fields}
        while len(_jobs) > _MAX_JOBS:
            oldest = min(_jobs, key=lambda k: _jobs[k]["started_at"])
            del _jobs[oldest]
    return job_id


def _spawn(target: Any, args: tuple, kwargs: Dict[str, Any]) -> None:
    """Démarre le thread du job en lui transportant l'identifiant de corrélation.

    OBS-02 — un ``ContextVar`` ne traverse pas ``threading.Thread`` : sans ce
    relais, la requête HTTP qui lance un entraînement serait tracée et
    l'entraînement lui-même — la partie qui dure des minutes et qui produit
    les lignes intéressantes — repartirait sur un contexte vierge. C'est
    exactement la coupure que la corrélation est censée supprimer.

    Un identifiant NEUF est posé quand il n'y en a pas (job déclenché hors
    requête HTTP : CLI, retrain automatique). Un job sans trace du tout serait
    le cas le moins exploitable.
    """
    from app.core.log_context import (
        get_correlation_id,
        new_correlation_id,
        run_with_correlation,
    )
    cid = get_correlation_id() or new_correlation_id()
    threading.Thread(target=run_with_correlation, daemon=True,
                     args=(cid, target, *args), kwargs=kwargs).start()


# ─────────────────────────────────────────────────────────────────────────────
#  Entraînement simple (dry-run ou publication gatée)
# ─────────────────────────────────────────────────────────────────────────────
def start_train_job(strategy: str, symbol: str, tf: str, *,
                    as_of: Optional[str] = None,
                    window_bars: Optional[int] = None,
                    params: Optional[Dict[str, Any]] = None,
                    publish: bool = False,
                    base_dir: str = "models",
                    recipe: Optional[str] = None,
                    symbols: Optional[List[str]] = None,
                    compare_solo: int = 0) -> str:
    """``recipe`` non nul : entraînement piloté par la recette (étape C), sans
    instancier de stratégie. ``strategy`` ne sert alors qu'à étiqueter le job.

    ``symbols`` non vide : entraînement POOLÉ multi-symboles (ML-16). Exige
    une recette — le pooling n'a de sens que sur le chemin recette, le seul
    qui sache entraîner sans instancier une stratégie par symbole.
    """
    pooled = [s for s in dict.fromkeys(symbols or []) if s]
    job_id = _register_job("train", strategy=recipe or strategy,
                           symbol=(f"pooled:{len(pooled)}" if pooled else symbol),
                           tf=tf, publish=publish, recipe=recipe,
                           pooled_symbols=pooled or None)
    _spawn(_run_train, (job_id, strategy, symbol, tf),
           {"as_of": as_of, "window_bars": window_bars, "params": params,
            "publish": publish, "base_dir": base_dir, "recipe": recipe,
            "symbols": pooled, "compare_solo": int(compare_solo or 0)})
    return job_id


def _run_train(job_id: str, strategy: str, symbol: str, tf: str,
               recipe: Optional[str] = None,
               symbols: Optional[List[str]] = None,
               compare_solo: int = 0, **kwargs: Any) -> None:
    from app.core.metrics import observe_training, timed
    from app.ml.train_runner import (
        train_and_publish,
        train_multi_and_publish,
        train_recipe_and_publish,
    )
    label = recipe or strategy
    try:
        with timed() as t:
            if recipe and symbols:
                kwargs.pop("gate_cfg", None)
                result = train_multi_and_publish(recipe, symbols, tf,
                                                 compare_solo=compare_solo, **kwargs)
            elif recipe:
                kwargs.pop("gate_cfg", None)
                result = train_recipe_and_publish(recipe, symbol, tf, **kwargs)
            else:
                result = train_and_publish(strategy, symbol, tf, **kwargs)
        # OBS-01 : la décision de gate est le libellé qui compte — c'est elle
        # qui dit si un entraînement a servi à quelque chose. Un `refresh`
        # devenu rare, ou un `keep` systématique, se lisent alors dans le temps.
        observe_training(label, tf, str((result or {}).get("decision", "unknown")),
                         t.seconds)
        _update_job(job_id, status="done", result=result, finished_at=time.time())
    except Exception as e:
        logger.error(f"[MLJobs] train {job_id} ({label}/{symbol}/{tf}) KO : {e}")
        observe_training(label, tf, "error", 0.0)
        _update_job(job_id, status="error", error=str(e), finished_at=time.time())


# ─────────────────────────────────────────────────────────────────────────────
#  Window sweep
# ─────────────────────────────────────────────────────────────────────────────
def start_sweep_job(strategy: str, symbol: str, tf: str, windows: List[int], *,
                    as_of: Optional[str] = None,
                    params: Optional[Dict[str, Any]] = None,
                    publish_best: bool = False,
                    base_dir: str = "models") -> str:
    job_id = _register_job("sweep", strategy=strategy, symbol=symbol, tf=tf,
                           windows=windows, publish_best=publish_best)
    _spawn(_run_sweep, (job_id, strategy, symbol, tf, windows),
           {"as_of": as_of, "params": params, "publish_best": publish_best,
            "base_dir": base_dir})
    return job_id


def _run_sweep(job_id: str, strategy: str, symbol: str, tf: str,
              windows: List[int], **kwargs: Any) -> None:
    from app.ml.train_runner import window_sweep
    try:
        result = window_sweep(strategy, symbol, tf, windows, **kwargs)
        _update_job(job_id, status="done", result=result, finished_at=time.time())
    except Exception as e:
        logger.error(f"[MLJobs] sweep {job_id} ({strategy}/{symbol}/{tf}) KO : {e}")
        _update_job(job_id, status="error", error=str(e), finished_at=time.time())
