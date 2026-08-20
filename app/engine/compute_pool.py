"""ProcessPool dédié aux calculs lourds (A-02).

Le handler FastAPI ``def`` tourne dans le threadpool anyio. Un
``Backtester.run`` de 50k barres y tient le GIL et retarde LiveTrader
ainsi que ``/api/status``.

On soumet le calcul à un ``ProcessPoolExecutor`` (spawn). Le thread API
attend le Future : le GIL du process API est libre.

En pytest (ou si ``CRYPTO_BOT_INLINE_COMPUTE=1``), le calcul reste
in-process pour éviter le coût spawn et simplifier le débogage.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_pool_lock = threading.Lock()
_pool: Optional[ProcessPoolExecutor] = None
_cancel_event = None
_ctx = None


def _use_process_pool() -> bool:
    if os.environ.get("CRYPTO_BOT_INLINE_COMPUTE") == "1":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def _mp_context():
    global _ctx
    if _ctx is None:
        import multiprocessing as mp
        _ctx = mp.get_context("spawn")
    return _ctx


def get_cancel_event():
    """Event partagé parent/enfants (multiprocessing.Event, picklable)."""
    global _cancel_event
    if _cancel_event is None:
        _cancel_event = _mp_context().Event()
    return _cancel_event


def request_cancel() -> None:
    get_cancel_event().set()


def clear_cancel() -> None:
    ev = get_cancel_event()
    if ev.is_set():
        ev.clear()


def get_pool(max_workers: int = 2) -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            workers = max(1, min(int(max_workers), os.cpu_count() or 2, 4))
            _pool = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=_mp_context(),
            )
            logger.info("[compute_pool] ProcessPool spawn, %d worker(s)", workers)
        return _pool


def shutdown_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


def map_jobs(payloads: Iterable[dict], *, max_workers: int = 2) -> List[Tuple[str, dict]]:
    """Exécute les jobs (process pool en prod, in-process sous pytest)."""
    from app.engine.compute_jobs import run_strategy_job

    jobs = list(payloads)
    if not jobs:
        return []

    cancel = get_cancel_event()
    # In-process : l'Event reste dans le payload. ProcessPool spawn (Python 3.14) :
    # un Event/Condition dans l'argument de submit() n'est pas héritable
    # (« Condition objects should only be shared between processes through
    # inheritance ») — on le retire avant pickle.
    if not _use_process_pool():
        for p in jobs:
            p.setdefault("cancel_event", cancel)
        return [run_strategy_job(p) for p in jobs]

    pool = get_pool(max_workers=min(max_workers, len(jobs)))
    results: List[Tuple[str, dict]] = []
    try:
        pickled = []
        for p in jobs:
            q = dict(p)
            q.pop("cancel_event", None)
            pickled.append(q)
        futures = {pool.submit(run_strategy_job, q): q.get("name") for q in pickled}
        for fut in as_completed(futures):
            if cancel.is_set():
                for f in futures:
                    f.cancel()
                break
            results.append(fut.result())
    except Exception as e:
        logger.error("[compute_pool] pool KO (%s) — repli in-process", e)
        remaining = [p for p in jobs if p.get("name") not in {n for n, _ in results}]
        results.extend(run_strategy_job(p) for p in remaining)
    return results
