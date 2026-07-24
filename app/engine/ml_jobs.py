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


# ─────────────────────────────────────────────────────────────────────────────
#  Entraînement simple (dry-run ou publication gatée)
# ─────────────────────────────────────────────────────────────────────────────
def start_train_job(strategy: str, symbol: str, tf: str, *,
                    as_of: Optional[str] = None,
                    window_bars: Optional[int] = None,
                    params: Optional[Dict[str, Any]] = None,
                    publish: bool = False,
                    base_dir: str = "models") -> str:
    job_id = _register_job("train", strategy=strategy, symbol=symbol, tf=tf,
                           publish=publish)
    threading.Thread(
        target=_run_train, daemon=True,
        args=(job_id, strategy, symbol, tf),
        kwargs={"as_of": as_of, "window_bars": window_bars, "params": params,
               "publish": publish, "base_dir": base_dir},
    ).start()
    return job_id


def _run_train(job_id: str, strategy: str, symbol: str, tf: str, **kwargs: Any) -> None:
    from app.ml.train_runner import train_and_publish
    try:
        result = train_and_publish(strategy, symbol, tf, **kwargs)
        _update_job(job_id, status="done", result=result, finished_at=time.time())
    except Exception as e:
        logger.error(f"[MLJobs] train {job_id} ({strategy}/{symbol}/{tf}) KO : {e}")
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
    threading.Thread(
        target=_run_sweep, daemon=True,
        args=(job_id, strategy, symbol, tf, windows),
        kwargs={"as_of": as_of, "params": params, "publish_best": publish_best,
               "base_dir": base_dir},
    ).start()
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
