"""Pool de process d'évaluation de l'optimiseur (DETTE-04c).

Un pool ouvert une fois et partagé entre les vagues d'un même appel : le spawn
et le ré-import complet de l'appli (lightgbm, polars…) sont un coût quasi fixe
par pool, dominant pour les stratégies ML multi-modèles.
"""
import io
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from app.engine.opt_workers import (
    _eval_worker,
    _worker_init,
    mem_aware_max_workers,
)

logger = logging.getLogger(__name__)




@dataclass
class _PoolHandle:
    """Pool de process ouvert et déjà initialisé (workers avec features
    pré-calculées), partageable entre plusieurs vagues d'évaluation d'un même
    appel (ex: dépistage puis recherche réduite) — évite de repayer le spawn
    + ré-import complet de l'appli (lightgbm, sklearn, polars…) à chaque
    phase, coût quasi fixe par pool et dominant pour les stratégies ML
    multi-modèles (ex: opus_omnibus_v12)."""
    executor: Any
    cfg_yaml: str
    df_is_ipc: bytes
    df_oos_ipc: bytes
    safe_jobs: int


class OptimizerPoolMixin:
    """Contrat d'hôte : `OptimizerSearchEngine` fournit les données et `_eval`."""

    cfg: dict
    strategy_name: str
    symbol: str
    timeframe: Any
    df_is: Any
    df_oos: Any
    _eval: Any
    _pool_ipc_sizes: Any
    #: LAB-05 — pourquoi la recherche s'est arrêtée, et sur combien
    #: d'évaluations le score repose vraiment.
    stop_reason: str
    trials_failed: int

    @contextmanager
    def _open_pool(self, n_jobs: int):
        """Pool persistant pour le bloc ``with``. ``None`` si n_jobs<=1."""
        safe_jobs = self._safe_worker_count(n_jobs)
        if safe_jobs <= 1:
            yield None
            return
        import concurrent.futures
        import multiprocessing as _mp
        cfg_yaml, df_is_ipc, df_oos_ipc, init_args = self._serialize_pool_inputs()
        ctx = _mp.get_context("spawn")
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=safe_jobs, mp_context=ctx,
            initializer=_worker_init, initargs=init_args)
        try:
            yield _PoolHandle(executor, cfg_yaml, df_is_ipc, df_oos_ipc, safe_jobs)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    def _submit_wave(self, pool: "_PoolHandle", params_list: List[dict],
                     timeout: int = 300) -> Tuple[List[dict], bool, List[dict]]:
        """Soumet une vague de ``params_list`` au pool DÉJÀ OUVERT (jamais
        créé ici) et attend leurs résultats. Retourne (résultats OK, pool
        cassé ?, params non traités si cassé). Primitive bas niveau partagée
        par ``_run_parallel`` (random/grid) et ``_optuna_parallel`` (bayésien
        parallèle)."""
        import concurrent.futures
        try:
            from concurrent.futures.process import BrokenProcessPool
        except ImportError:
            BrokenProcessPool = Exception  # type: ignore

        worker_args = [self._worker_args(p, pool.cfg_yaml, pool.df_is_ipc, pool.df_oos_ipc)
                      for p in params_list]
        try:
            futures_map = {pool.executor.submit(_eval_worker, a): i
                           for i, a in enumerate(worker_args)}
        except BrokenProcessPool as _bp:
            logger.error("[Optimizer] pool déjà cassé, bascule séquentielle : %s", _bp)
            return [], True, list(params_list)

        results: List[Optional[dict]] = [None] * len(params_list)
        broken = False
        remaining: List[dict] = []
        timed_out: List[Tuple[int, dict]] = []
        for fut in concurrent.futures.as_completed(futures_map):
            i = futures_map[fut]
            try:
                r = fut.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning("[Optimizer] worker timeout (>%ds) — retry in-process", timeout)
                timed_out.append((i, params_list[i]))
                continue
            except BrokenProcessPool as _bp:
                logger.error("[Optimizer] BrokenProcessPool (worker tué, ex: OOM) : %s", _bp)
                broken = True
                for f, idx in futures_map.items():
                    if not f.done():
                        remaining.append(params_list[idx])
                        f.cancel()
                break
            except Exception as _e:
                logger.warning(f"[Optimizer] worker KO : {_e}")
                continue
            if "error" in r:
                logger.warning("[Optimizer] worker erreur : %s", r["error"])
                continue
            results[i] = r
        # O-11 : un timeout n'est plus ignoré — un retry in-process.
        for i, params in timed_out:
            try:
                results[i] = self._eval(params)
                logger.info("[Optimizer] trial rejoué après timeout")
            except Exception as _re:
                logger.warning("[Optimizer] retry timeout KO : %s", _re)
        ok_results = [r for r in results if r is not None]
        return ok_results, broken, remaining

    def _serialize_pool_inputs(self):
        """Sérialise (une fois) cfg + DataFrames IS/OOS pour les workers spawn.
        Retourne ``(cfg_yaml, df_is_ipc, df_oos_ipc, init_args)``."""
        import yaml as _yaml  # type: ignore[import-untyped,unused-ignore]
        _buf_is = io.BytesIO()
        self.df_is.write_ipc(_buf_is)
        df_is_ipc  = _buf_is.getvalue()
        _buf_oos = io.BytesIO()
        self.df_oos.write_ipc(_buf_oos)
        df_oos_ipc = _buf_oos.getvalue()
        cfg_yaml = _yaml.dump(self.cfg)
        init_args = (self.strategy_name, cfg_yaml,
                     df_is_ipc, df_oos_ipc, self.symbol, self.timeframe)
        return cfg_yaml, df_is_ipc, df_oos_ipc, init_args

    def _worker_args(self, params: dict, cfg_yaml: str,
                     df_is_ipc: bytes, df_oos_ipc: bytes) -> tuple:
        return (self.strategy_name, cfg_yaml, df_is_ipc, df_oos_ipc,
                self.symbol, params, self.timeframe)

    def _safe_worker_count(self, n_jobs: int) -> int:
        """Plafonne le nombre de workers : cpu-1 puis cap mémoire anti-OOM."""
        if n_jobs <= 1:
            return 1
        _cpu = os.cpu_count() or 1
        safe = max(1, min(n_jobs, max(1, _cpu - 1)))
        # Estimation prudente ~5× le payload IPC + 256 Mo (features + LightGBM).
        try:
            # P-06 : réutilise la sérialisation déjà faite, pas un 2e write_ipc.
            cached = getattr(self, "_pool_ipc_sizes", None)
            if cached is None:
                _buf_is = io.BytesIO()
                self.df_is.write_ipc(_buf_is)
                _buf_oos = io.BytesIO()
                self.df_oos.write_ipc(_buf_oos)
                cached = _buf_is.tell() + _buf_oos.tell()
                self._pool_ipc_sizes = cached
            per_worker = int(cached * 5) + 256 * 1024 * 1024
            safe = mem_aware_max_workers(safe, per_worker)
        except Exception:
            pass
        return safe

