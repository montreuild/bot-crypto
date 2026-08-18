"""ML-03 — trace des fenêtres passées à l'entraînement inline.

Le backtest en ``ml_mode=frozen`` sans modèle publié replie sur l'entraînement
en ligne. Si ``fit()`` / ``cached_train`` voyait le DataFrame **complet**
(celui de ``prepare_for_backtest``), le modèle lirait l'avenir et les
backtests ML seraient à rejeter.

Cette trace est un enregistreur process-local, activé par ``start()`` autour
d'un ``Backtester.run``. Chaque appel à ``cached_train`` y dépose la longueur
et les bornes temporelles de la fenêtre réellement entraînée.
"""
from __future__ import annotations

from threading import local
from typing import Any, Dict, List, Optional

_tls = local()


def start() -> None:
    _tls.rec = []


def record(df: Any, *, site: str = "") -> None:
    rec: Optional[List[Dict[str, Any]]] = getattr(_tls, "rec", None)
    if rec is None:
        return
    n = 0 if df is None else len(df)
    t0 = t1 = None
    try:
        if df is not None and n and "time" in df.columns:
            t0, t1 = str(df["time"][0]), str(df["time"][-1])
    except Exception:
        pass
    rec.append({"n_rows": n, "t0": t0, "t1": t1, "site": site})


def summarize(calls: List[Dict[str, Any]], series_len: int) -> Dict[str, Any]:
    max_n = max((int(c.get("n_rows") or 0) for c in calls), default=0)
    return {
        "n_fits": len(calls),
        "max_n_rows": max_n,
        "series_len": int(series_len),
        # P0 si un entraînement a reçu toute la série (ou plus).
        "any_full_series": bool(series_len > 0 and max_n >= int(series_len)),
        "calls": calls,
    }


def stop(series_len: int = 0) -> Dict[str, Any]:
    calls = list(getattr(_tls, "rec", None) or [])
    _tls.rec = None
    return summarize(calls, series_len)
