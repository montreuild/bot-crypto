"""ML-03 — trace des fenêtres passées à l'entraînement inline.

Le backtest en ``ml_mode=frozen`` sans modèle publié replie sur l'entraînement
en ligne. Si ``fit()`` / ``cached_train`` voyait le DataFrame **complet**
(celui de ``prepare_for_backtest``), le modèle lirait l'avenir et les
backtests ML seraient à rejeter.

L'enregistreur est par THREAD : ``start()`` l'arme dans le thread qui lance
``Backtester.run``, et seuls les appels de ce thread y atterrissent.

ML-03b — un entraînement délégué à un autre thread (ou à un processus) échappe
donc à la trace. Sans garde-fou, il produisait `n_fits=0` et
`any_full_series=False` : une absence de mesure présentée comme une absence de
fuite. Un compteur process-wide recense désormais ces appels non attribuables,
et ``mesurable`` dit si le verdict porte sur quelque chose. **Ne pas conclure
de ``any_full_series is False`` sans vérifier ``mesurable``.**
"""
from __future__ import annotations

import threading
from threading import local
from typing import Any, Dict, List, Optional

_tls = local()

#: Appels à ``record()`` tombés hors de toute trace armée — comptés
#: process-wide, seule façon de voir ce qui se passe dans un autre thread.
_verrou = threading.Lock()
_hors_trace = 0


def start() -> None:
    _tls.rec = []
    with _verrou:
        _tls.hors_trace_debut = _hors_trace


def record(df: Any, *, site: str = "") -> None:
    global _hors_trace
    rec: Optional[List[Dict[str, Any]]] = getattr(_tls, "rec", None)
    if rec is None:
        with _verrou:
            _hors_trace += 1
        return
    n = 0 if df is None else len(df)
    t0 = t1 = None
    try:
        if df is not None and n and "time" in df.columns:
            t0, t1 = str(df["time"][0]), str(df["time"][-1])
    except Exception:
        pass
    rec.append({"n_rows": n, "t0": t0, "t1": t1, "site": site})


def summarize(calls: List[Dict[str, Any]], series_len: int,
              fits_non_traces: int = 0) -> Dict[str, Any]:
    max_n = max((int(c.get("n_rows") or 0) for c in calls), default=0)
    return {
        "n_fits": len(calls),
        "max_n_rows": max_n,
        "series_len": int(series_len),
        # P0 si un entraînement a reçu toute la série (ou plus).
        "any_full_series": bool(series_len > 0 and max_n >= int(series_len)),
        # Entraînements survenus hors du thread tracé : la trace ne dit rien
        # d'eux, ni dans un sens ni dans l'autre.
        "fits_non_traces": int(fits_non_traces),
        "mesurable": int(fits_non_traces) == 0,
        "calls": calls,
    }


def stop(series_len: int = 0) -> Dict[str, Any]:
    calls = list(getattr(_tls, "rec", None) or [])
    debut = getattr(_tls, "hors_trace_debut", None)
    with _verrou:
        courant = _hors_trace
    _tls.rec = None
    _tls.hors_trace_debut = None
    manques = 0 if debut is None else max(0, courant - int(debut))
    return summarize(calls, series_len, fits_non_traces=manques)


def _reset_for_tests() -> None:
    global _hors_trace
    with _verrou:
        _hors_trace = 0
    _tls.rec = None
    _tls.hors_trace_debut = None
