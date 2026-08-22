"""Registre des jobs d'optimisation (DETTE-04).

État global thread-safe : un job vit plus longtemps que la requête qui l'a
créé, et l'UI le suit par sondage. Extrait d'`auto_optimizer.py` (1 000 lignes),
qui portait trois constats de la revue.
"""

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  État global des jobs (thread-safe)
# ════════════════════════════════════════════════════════════════════════════
_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()
_cancel_flags: Dict[str, threading.Event] = {}

# Borne le nombre de jobs d'optimisation exécutés *simultanément*, toutes sources
# confondues (auto-optimisation planifiée + API). start_async peut créer des
# centaines de threads (n_stratégies × n_TF) ; sans cette borne ils saturent le
# CPU/la mémoire du serveur pendant le live. Les threads en excès attendent
# (bloqués sur le sémaphore) au lieu de tourner tous en même temps.
def _max_concurrent_opt_jobs() -> int:
    import os
    cpu = os.cpu_count() or 2
    return max(1, cpu - 1)

_job_semaphore = threading.BoundedSemaphore(_max_concurrent_opt_jobs())


def _job_id(strategy: str, timeframe: str, symbol: str) -> str:
    return f"{strategy}@{timeframe}@{symbol}"


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return dict(_jobs.get(job_id, {}))


def get_all_jobs() -> dict:
    with _jobs_lock:
        return {k: dict(v) for k, v in _jobs.items()}


def any_optimization_running() -> bool:
    """True si au moins un job d'optimisation est en cours ou en file.

    Sert aux tâches de fond (forward-test, cycle de vie) à se mettre en attente
    pendant une optimisation lourde, pour ne pas saturer mémoire/CPU.
    """
    with _jobs_lock:
        return any(j.get("status") in ("running", "queued") for j in _jobs.values())


def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id not in _jobs:
            _jobs[job_id] = {}
        _jobs[job_id].update(kwargs)


def cancel_job(job_id: str) -> bool:
    """Signal a running job to stop. Returns True if the job was running."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") != "running":
            return False
    event = _cancel_flags.get(job_id)
    if event:
        event.set()
    return True


def delete_job(job_id: str) -> bool:
    """Remove a job from the registry (only if not running). Returns True if deleted."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        if job.get("status") == "running":
            return False
        del _jobs[job_id]
        _cancel_flags.pop(job_id, None)
        return True


# ════════════════════════════════════════════════════════════════════════════
#  Baseline (snapshot avant optimisation)
# ════════════════════════════════════════════════════════════════════════════
