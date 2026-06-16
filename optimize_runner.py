#!/usr/bin/env python3
"""optimize_runner.py — optimisation séquentielle des stratégies en ligne de commande.

Lance, **une stratégie à la fois**, l'optimisation de paramètres (la même que
celle déclenchée depuis l'interface web, via ``AutoOptimizer``) pour éviter de
devoir le faire manuellement dans l'UI. Pour chaque stratégie et chaque
timeframe : backtest baseline → recherche de paramètres (random/bayésien/grille)
→ sauvegarde des résultats dans ``strategies/<nom>.yaml`` (et application si
``--apply`` et meilleur que le baseline). Pour les stratégies ML, le modèle final
est ré-entraîné et persisté en ``.pkl`` (logique identique à l'UI).

Caractéristiques demandées :
  * **Une à une** : exécution strictement séquentielle (un seul job à la fois),
    douce pour la machine.
  * **Anti-veille** : empêche la mise en veille de l'ordinateur pendant toute la
    durée (macOS ``caffeinate`` / Windows ``SetThreadExecutionState`` /
    Linux ``systemd-inhibit``), avec dégradation gracieuse si indisponible.
  * **Thread-safe** : verrou fichier exclusif (un seul runner à la fois) en plus
    des verrous internes de l'optimiseur (écritures YAML, registre de jobs).
  * **Tâche de fond limitée** : priorité processus abaissée (nice / IDLE) et
    nombre de threads de calcul borné (``--jobs``, défaut 1).

Exemples :
    python optimize_runner.py                          # toutes les stratégies, TFs du config
    python optimize_runner.py --strategies opus_omnibus_v11_no_ml,opus_omnibus_v10_no_ml
    python optimize_runner.py --no-ml-only --apply     # uniquement les jumeaux _no_ml, et applique
    python optimize_runner.py --ml-only --limit 50000 --apply  # toutes les stratégies ML, 50k bougies
    python optimize_runner.py --tfs 1h,15m --method random --trials 30 --jobs 2
"""

import argparse
import atexit
import contextlib
import logging
import os
import sys
import time

# Bornage des threads BLAS/OpenMP/LightGBM AVANT tout import numpy/polars/sklearn,
# pour garder l'exécution discrète en tâche de fond (cf. --jobs).
_DEFAULT_THREADS = os.environ.get("OPT_RUNNER_THREADS", "1")
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "LIGHTGBM_EXEC_NUM_THREADS"):
    os.environ.setdefault(_v, _DEFAULT_THREADS)

logger = logging.getLogger("optimize_runner")

_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".optimize_runner.lock")


# ════════════════════════════════════════════════════════════════════════════
#  Verrou fichier exclusif — un seul runner à la fois (thread/process-safe)
# ════════════════════════════════════════════════════════════════════════════
class _SingleInstanceLock:
    """Verrou exclusif inter-processus basé sur un fichier (fcntl / msvcrt)."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def __enter__(self):
        self._fh = open(self.path, "w")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self._fh.close()
            self._fh = None
            raise RuntimeError(
                "Une autre instance d'optimize_runner est déjà en cours "
                f"(verrou {self.path}). Abandon."
            )
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                if os.name != "nt":
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self._fh.close()
            with contextlib.suppress(Exception):
                os.remove(self.path)


# ════════════════════════════════════════════════════════════════════════════
#  Anti-veille (keep-awake) multi-plateforme — best effort
# ════════════════════════════════════════════════════════════════════════════
@contextlib.contextmanager
def keep_awake(reason: str = "Optimisation des stratégies en cours"):
    """Empêche la mise en veille/idle de la machine pendant le bloc. Best effort."""
    plat = sys.platform
    proc = None
    windows_reset = None
    try:
        if plat == "darwin":
            import subprocess
            # -i empêche l'idle sleep, -m empêche le sommeil disque, -s le système.
            proc = subprocess.Popen(["caffeinate", "-imsu"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("[keep_awake] macOS caffeinate actif")
        elif plat.startswith("win"):
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_AWAYMODE_REQUIRED = 0x00000040
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)

            def windows_reset():
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            logger.info("[keep_awake] Windows SetThreadExecutionState actif")
        else:  # Linux / autres
            import shutil
            import subprocess
            if shutil.which("systemd-inhibit"):
                proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep:handle-lid-switch",
                     f"--why={reason}", "--mode=block", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info("[keep_awake] Linux systemd-inhibit actif")
            else:
                logger.warning("[keep_awake] systemd-inhibit introuvable — "
                               "anti-veille non garanti sur cette machine")
        yield
    finally:
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.terminate()
                proc.wait(timeout=5)
        if windows_reset is not None:
            with contextlib.suppress(Exception):
                windows_reset()


def _lower_priority():
    """Abaisse la priorité du processus (tâche de fond discrète)."""
    try:
        if os.name == "nt":
            import ctypes
            # BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        else:
            os.nice(10)
        logger.info("[runner] Priorité processus abaissée (tâche de fond)")
    except Exception as e:
        logger.debug(f"[runner] Abaissement de priorité KO : {e}")


# ════════════════════════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(description="Optimisation séquentielle des stratégies.")
    p.add_argument("--config", default="config.yaml", help="Chemin du config.yaml")
    p.add_argument("--symbol", default="BTC/USDC", help="Paire de référence")
    p.add_argument("--strategies", default="",
                   help="Liste séparée par des virgules (défaut: toutes les optimisables)")
    p.add_argument("--tfs", "--timeframes", dest="tfs", default="",
                   help="TFs séparés par des virgules (défaut: trading.timeframes du config)")
    p.add_argument("--method", default="bayesian",
                   choices=["bayesian", "random", "grid"], help="Méthode de recherche")
    p.add_argument("--trials", type=int, default=40, help="Trials par (stratégie, TF)")
    p.add_argument("--limit", type=int, default=0,
                   help="Bougies à charger par TF (0 = RECOMMENDED_LIMIT)")
    p.add_argument("--jobs", type=int, default=1,
                   help="Threads de calcul par trial (défaut 1, discret)")
    p.add_argument("--apply", action="store_true",
                   help="Applique les paramètres s'ils battent le baseline (sinon: sauvegarde seule)")
    ml_group = p.add_mutually_exclusive_group()
    ml_group.add_argument("--no-ml-only", action="store_true",
                          help="N'optimise que les jumeaux suffixés _no_ml")
    ml_group.add_argument("--ml-only", action="store_true",
                          help="N'optimise que les stratégies ML (héritant de BaseStrategyML)")
    p.add_argument("--no-keep-awake", action="store_true",
                   help="Désactive l'anti-veille")
    return p.parse_args()


def main():
    args = parse_args()

    from app.core.config import load_config
    from app.core.logger import setup_logging
    from app.core.exchange import create_exchange
    from app.core.candle_store import get_store
    from app.engine.auto_optimizer import AutoOptimizer
    from app.engine.optimizer import PARAM_SPACES, RECOMMENDED_LIMIT

    cfg = load_config(args.config)
    setup_logging(cfg)

    tf_list = ([t.strip() for t in args.tfs.split(",") if t.strip()]
               if args.tfs else cfg["trading"].get(
                   "timeframes", [cfg["trading"].get("timeframe", "1h")]))

    strats = ([s.strip() for s in args.strategies.split(",") if s.strip()]
              if args.strategies else list(PARAM_SPACES.keys()))
    strats = [s for s in strats if s in PARAM_SPACES]
    if args.no_ml_only:
        strats = [s for s in strats if s.endswith("_no_ml")]
    if args.ml_only:
        from app.engine.auto_optimizer import _is_ml_strategy
        strats = [s for s in strats if _is_ml_strategy(s)]
    if not strats:
        print("Aucune stratégie optimisable à traiter.")
        return 1

    _lower_priority()

    # Chargement des données par TF (mémoïsé sur disque par le CandleStore).
    exchange = create_exchange(cfg)
    store = get_store()
    df_map = {}
    for tf in tf_list:
        fetch_limit = args.limit if args.limit > 0 else RECOMMENDED_LIMIT.get(tf, 1000)
        df = store.fetch(exchange, args.symbol, tf, total=fetch_limit, prefer_cache=True)
        if df is not None and len(df) > 0:
            df_map[tf] = df
            print(f"  Données {args.symbol}/{tf} : {len(df)} bougies")
        else:
            print(f"  ⚠ {args.symbol}/{tf} : aucune donnée — TF ignoré")
    if not df_map:
        print("Aucune donnée chargée — abandon.")
        return 1

    print(f"\n  Stratégies ({len(strats)}) : {', '.join(strats)}")
    print(f"  Timeframes : {', '.join(df_map.keys())}")
    print(f"  Méthode : {args.method} | trials : {args.trials} | jobs : {args.jobs} "
          f"| apply : {args.apply}\n")

    opt = AutoOptimizer(cfg, n_trials=args.trials, method=args.method,
                        config_path=args.config, n_jobs=max(1, args.jobs))

    counters = {"done": 0, "error": 0, "applied": 0}

    def _report(job_id, job):
        status = job.get("status")
        res = job.get("result") or {}
        if status == "done":
            counters["done"] += 1
            if job.get("applied"):
                counters["applied"] += 1
            print(f"  ✓ {job_id:55} OOS={res.get('best_oos_score', 0):+.4f} "
                  f"PnL={res.get('best_oos_pnl', 0):+.2f} "
                  f"{'[appliqué]' if job.get('applied') else '[sauvegardé]'}")
        else:
            counters["error"] += 1
            print(f"  ✗ {job_id:55} {status} : {str(job.get('error'))[:60]}")

    t0 = time.time()
    awake = keep_awake() if not args.no_keep_awake else contextlib.nullcontext()
    with _SingleInstanceLock(_LOCK_PATH), awake:
        opt.optimize_sequential(df_map, args.symbol, strategies=strats,
                                timeframes=tf_list, auto_apply=args.apply,
                                on_job_done=_report)

    elapsed = time.time() - t0
    print(f"\n  Terminé en {elapsed:.0f}s — {counters['done']} OK "
          f"({counters['applied']} appliqués), {counters['error']} échecs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
