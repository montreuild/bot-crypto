"""Workers d'évaluation parallèle de l'optimiseur (ProcessPoolExecutor spawn).

Extrait de ``optimizer.py`` (découpage V13 : recherche / scoring / persistance) :
  - ``_worker_init`` / ``_eval_worker`` : évaluation d'un jeu de paramètres
    dans un process séparé, avec état partagé ``_W`` (DataFrames + features
    pré-calculés une fois par worker, réutilisés pour tous ses trials) ;
  - plafonnement mémoire des workers (``mem_aware_max_workers``).

⚠️ ``_eval_worker`` est picklé par référence (chemin de module) pour le pool
spawn : il doit rester au niveau module de ce fichier.
"""
import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# État partagé par worker : initialisé une seule fois via ``_worker_init``,
# réutilisé pour tous les trials du même job. Évite de redésérialiser les
# DataFrames et de reconstruire les features (qui ne dépendent pas des params)
# à chaque trial — typiquement le poste de coût dominant pour les stratégies
# à features lourdes (~462 colonnes type opus_omnibus_v8 / v10_retrained).
_W: dict = {}


# ── Plafonnement mémoire ─────────────────────────────────────────────────────

def available_memory_bytes() -> Optional[int]:
    """Mémoire disponible (octets). psutil si présent, sinon /proc/meminfo
    (Linux) ou GlobalMemoryStatusEx (Windows), sinon None (cap mémoire désactivé).

    Le fallback Windows est essentiel : sans lui (et sans psutil, qui n'est pas
    une dépendance obligatoire), le cap mémoire anti-OOM serait silencieusement
    désactivé sur Windows — là où il est justement le plus utile (pas d'OOM-killer
    noyau : un std::bad_alloc LightGBM tue tout le process sans traceback)."""
    try:
        import psutil  # type: ignore
        return int(psutil.virtual_memory().available)
    except Exception:
        pass
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024  # kB → octets
    except Exception:
        pass
    # Windows : GlobalMemoryStatusEx (ullAvailPhys) via ctypes.
    try:
        if os.name == "nt":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullAvailPhys)
    except Exception:
        pass
    return None


def mem_aware_max_workers(requested: int, per_worker_bytes: int) -> int:
    """Plafonne le nombre de workers selon la mémoire disponible.

    Chaque worker spawn duplique les DataFrames IS/OOS + reconstruit ~462 colonnes
    de features + charge LightGBM. Sur les stratégies ML lourdes, lancer cpu-1
    workers peut épuiser la RAM : tous les process meurent (OOM) et l'optimiseur
    ne récolte AUCUN trial. On garde une marge de sécurité (60 % du dispo) et au
    moins 1 worker.
    """
    if per_worker_bytes <= 0:
        return max(1, requested)
    avail = available_memory_bytes()
    if not avail:
        return max(1, requested)
    budget = int(avail * 0.60)
    fit = max(1, budget // per_worker_bytes)
    capped = max(1, min(requested, fit))
    if capped < requested:
        logger.warning(
            "[Optimizer] workers plafonnés %d→%d (RAM dispo %.1f Go, "
            "~%.0f Mo/worker) pour éviter l'OOM",
            requested, capped, avail / 1e9, per_worker_bytes / 1e6,
        )
    return capped


# ── Initialisation et évaluation ─────────────────────────────────────────────

def _worker_init(strategy_name: str, cfg_yaml: str,
                 df_is_ipc: bytes, df_oos_ipc: bytes,
                 symbol: str, timeframe: str) -> None:
    """Initialiseur de worker : pré-calcule les features une fois par process."""
    import os as _os
    # Force mono-thread BLAS/OpenMP : empêche les std::bad_alloc côté LightGBM
    # quand plusieurs workers tournent en parallèle.
    for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "LIGHTGBM_EXEC_NUM_THREADS"):
        _os.environ.setdefault(_v, "1")

    import importlib as _imp

    import polars as _pl
    import yaml as _yaml

    _W["strategy_name"] = strategy_name
    _W["cfg"]           = _yaml.safe_load(cfg_yaml)
    # AVANT tout pré-calcul de features : la convention de lissage ATR/ADX/DI
    # doit être posée d'abord, sinon les colonnes ``_pre_*`` capturées dans le
    # snapshot ci-dessous seraient figées sous la mauvaise convention pour tous
    # les trials de ce worker (cf. §8ter).
    #
    # Surcharge SEULEMENT si la clé est présente : le worker est un process
    # neuf, son défaut de module est déjà le bon (Wilder). Forcer un défaut
    # ``False`` ici ferait tourner tous les workers en ancienne convention
    # pendant que le parent, lui, serait en Wilder — divergence silencieuse
    # entre optimisation parallèle et backtest in-process.
    _w = (_W["cfg"].get("indicators") or {}).get("wilder_atr_adx")
    if _w is not None:
        from app.core.indicators_precompute import set_wilder_atr_adx as _swaa
        _swaa(bool(_w))
    _W["df_is"]         = _pl.read_ipc(io.BytesIO(df_is_ipc))
    _W["df_oos"]        = _pl.read_ipc(io.BytesIO(df_oos_ipc))
    _W["symbol"]        = symbol
    _W["timeframe"]     = timeframe
    _W["strategy_mod"]  = _imp.import_module(f"app.strategies.{strategy_name}")

    # Pré-calcul des features IS / OOS via une instance jetable.
    # On capture l'état complet de l'instance après ``prepare_for_backtest``
    # pour pouvoir le restaurer à chaque trial sans re-builder.
    # On capture aussi les attributs des sous-stratégies imbriquées (ex:
    # ``v12._mldyn``) pour éviter les rebuilds de features nested.
    _W["snap_is"]  = None
    _W["snap_oos"] = None

    def _snap_state(obj) -> dict:
        snap: dict = {}
        try:
            for k, v in vars(obj).items():
                if k.startswith("_bt_") or "features" in k.lower() or "feats" in k.lower():
                    snap[k] = ("self", v)
            # Sous-stratégies imbriquées (ex: opus_omnibus_v12._mldyn).
            for k, v in vars(obj).items():
                if k.startswith("__") or not hasattr(v, "__class__"):
                    continue
                cls_mod = getattr(v.__class__, "__module__", "") or ""
                if cls_mod.startswith("app.strategies."):
                    sub: dict = {}
                    for sk, sv in vars(v).items():
                        if sk.startswith("_bt_") or "features" in sk.lower() or "feats" in sk.lower():
                            sub[sk] = sv
                    if sub:
                        snap[k] = ("nested", sub)
        except Exception:
            pass
        return snap

    try:
        _tmp = _W["strategy_mod"].Strategy()
        # Symbole/TF pour le catalogue FeatureStore (cache disque partagé).
        _tmp._bt_symbol = symbol
        _tmp._bt_tf = timeframe
        # Paramètres résolus exposés au pré-calcul (ex: signal_consensus pré-calcule
        # les votes des sous-stratégies selon ce paramétrage, invariant entre trials).
        try:
            from app.core.param_resolution import resolve_strategy_params as _resolve_sp
            _tmp._bt_params = _resolve_sp(_W["cfg"], timeframe)
        except Exception:
            _tmp._bt_params = None
        prep = getattr(_tmp, "prepare_for_backtest", None)
        if callable(prep):
            prep(_W["df_is"])
            _W["snap_is"] = _snap_state(_tmp)
            # Reset l'instance pour OOS
            reset = getattr(_tmp, "reset_model", None)
            if callable(reset):
                try:
                    reset()
                except Exception as _re:
                    logger.warning(
                        f"[Optimizer] reset_model({getattr(_tmp, 'name', '?')}) KO "
                        f"— le snapshot OOS peut hériter de l'état IS : {_re}"
                    )
            prep(_W["df_oos"])
            _W["snap_oos"] = _snap_state(_tmp)
        del _tmp
    except Exception:
        # En cas d'échec on retombe simplement sur le comportement sans cache.
        _W["snap_is"]  = None
        _W["snap_oos"] = None


def _install_features_cache(strat) -> None:
    """Monkey-patch ``prepare_for_backtest`` sur l'instance pour réutiliser
    le cache features du worker au lieu de rebuilder à chaque trial."""
    snap_is  = _W.get("snap_is")
    snap_oos = _W.get("snap_oos")
    df_is    = _W.get("df_is")
    df_oos   = _W.get("df_oos")
    if not (snap_is or snap_oos):
        return  # cache indisponible, comportement normal
    _orig = getattr(strat, "prepare_for_backtest", None)

    def _apply_snap(snap: dict) -> None:
        for k, payload in snap.items():
            try:
                kind, val = payload
            except Exception:
                # Format legacy (k → valeur directe) : applique tel quel.
                setattr(strat, k, payload)
                continue
            if kind == "self":
                setattr(strat, k, val)
            elif kind == "nested":
                sub = getattr(strat, k, None)
                if sub is None:
                    continue
                for sk, sv in val.items():
                    setattr(sub, sk, sv)

    def _cached_prepare(df):
        # Identité d'objet : df IS et OOS sont les DataFrames Polars du worker.
        if df is df_is and snap_is:
            _apply_snap(snap_is)
            return
        if df is df_oos and snap_oos:
            _apply_snap(snap_oos)
            return
        if callable(_orig):
            _orig(df)

    try:
        strat.prepare_for_backtest = _cached_prepare  # type: ignore[assignment]
    except Exception:
        pass


def _eval_worker(args: tuple) -> dict:
    """
    Évalue un jeu de paramètres dans un processus séparé.
    Réutilise les DataFrames + features pré-calculés par ``_worker_init``.
    Fallback pickable : si l'initializer n'a pas été appelé (cas non
    parallèle ou test), on redésérialise tout depuis les args.
    """
    strategy_name, cfg_yaml, df_is_ipc, df_oos_ipc, symbol, params, timeframe = args
    try:
        import os as _os
        for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                   "NUMEXPR_NUM_THREADS", "LIGHTGBM_EXEC_NUM_THREADS"):
            _os.environ.setdefault(_v, "1")
        from copy import deepcopy as _dp

        from app.core.stats_thresholds import MIN_TRADES_DEGENERATE
        from app.engine.backtest import Backtester as _Backtester
        from app.engine.engine import Engine as _Engine
        from app.engine.opt_scoring import composite_score, overfitting_ratio

        # Fast-path : état partagé pré-chargé par ``_worker_init``.
        if _W and _W.get("strategy_name") == strategy_name:
            _cfg    = _W["cfg"]
            _df_is  = _W["df_is"]
            _df_oos = _W["df_oos"]
            _mod    = _W["strategy_mod"]
        else:
            import importlib as _imp

            import polars as _pl
            import yaml as _yaml
            _cfg = _yaml.safe_load(cfg_yaml)
            _df_is  = _pl.read_ipc(io.BytesIO(df_is_ipc))
            _df_oos = _pl.read_ipc(io.BytesIO(df_oos_ipc))
            _mod = _imp.import_module(f"app.strategies.{strategy_name}")

        _strat = _mod.Strategy()
        _install_features_cache(_strat)
        _eng = _Engine()
        _eng.register(_strat, silent=True)
        _cfg_copy = _dp(_cfg)
        _cfg_copy.setdefault("strategy_params", {})[strategy_name] = params
        # Empêche resolve_strategy_params() d'écraser les params échantillonnés :
        # l'entrée optimizer_results sauvegardée dans le YAML stratégie a une
        # priorité supérieure et avalerait silencieusement les params du trial.
        if strategy_name in _cfg_copy.get("optimizer_results", {}):
            del _cfg_copy["optimizer_results"][strategy_name]
        # ML-02 : ml_mode dérivé de cfg["optimizer"]["ml_mode"] (défaut "inline",
        # comportement historique inchangé) — même clé que côté in-process
        # (OptimizerSearchEngine.ml_mode), pas de paramètre supplémentaire à
        # faire traverser la frontière de process : _cfg contient déjà tout
        # cfg["optimizer"] via le YAML dumpé par l'engine.
        _ml_mode = (_cfg.get("optimizer") or {}).get("ml_mode", "inline")
        # Convention de lissage ATR/ADX/DI — même mécanisme que ml_mode : le
        # worker est spawné, donc il n'hérite AUCUN global du parent. Absente
        # de cfg, le défaut de module (Wilder) s'applique ; présente, elle
        # gagne — c'est ce qui permet à la mesure §8ter de forcer l'ancienne
        # convention sur un bras de comparaison.
        _w = (_cfg.get("indicators") or {}).get("wilder_atr_adx")
        if _w is not None:
            from app.core.indicators_precompute import set_wilder_atr_adx as _swaa
            _swaa(bool(_w))
        _bt = _Backtester(_eng, _cfg_copy, ml_mode=_ml_mode)

        _res_is  = _bt.run(_df_is,  symbol, timeframe=timeframe)
        _res_oos = _bt.run(_df_oos, symbol, timeframe=timeframe)

        # Même plancher de sélection que le chemin séquentiel : les workers
        # redérivent la clé du YAML, aucun paramètre à faire traverser la
        # frontière de process.
        _min_tr = int((_cfg_copy.get("optimizer") or {}).get(
            "min_trades", MIN_TRADES_DEGENERATE))
        _is_score  = composite_score(_res_is,  min_trades=_min_tr)
        _oos_score = composite_score(_res_oos, min_trades=_min_tr)
        _overfit   = overfitting_ratio(_is_score, _oos_score)

        return {
            "params":     params,
            "is_score":   _is_score,
            "oos_score":  _oos_score,
            "overfit":    _overfit,
            "is_pnl":     _res_is.total_pnl,
            "oos_pnl":    _res_oos.total_pnl,
            "is_sharpe":  _res_is.sharpe,
            "oos_sharpe": _res_oos.sharpe,
            "is_trades":  _res_is.total_trades,
            "oos_trades": _res_oos.total_trades,
            "is_wr":      _res_is.win_rate,
            "oos_wr":     _res_oos.win_rate,
            "oos_dd":     _res_oos.max_drawdown,
            "oos_alpha":  getattr(_res_oos, "alpha", None),
        }
    except Exception as _exc:
        # Retourner une erreur sérialisable plutôt que de laisser le process crasher,
        # ce qui évite les BrokenProcessPool et les processus orphelins.
        return {"error": str(_exc), "params": params}
