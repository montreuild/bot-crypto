"""Sessions, killzones, biais HTF et divergence SMT du moteur SMC
(V4-L / ARCH-14). L'analyse HTF réutilise ``analyze`` (smc_structure) sur des
buckets horloge causaux — aucune fuite du futur."""
import logging
from typing import Dict, Optional

import numpy as np
import polars as pl

from app.core.smc_structure import analyze

logger = logging.getLogger(__name__)

# Killzones ICT (heures UTC) : ouvertures de Londres et de New York — les
# fenêtres où les desks institutionnels génèrent l'essentiel des sweeps.
KILLZONES = {"london": (7, 10), "newyork": (12, 15)}
SESSIONS  = {"asia": (0, 7), "london": (7, 12), "newyork": (12, 21),
             "late": (21, 24)}


def killzone_flags(times_epoch: np.ndarray) -> np.ndarray:
    """1 si l'heure UTC d'ouverture de la barre tombe dans une killzone
    (Londres 07-10 UTC, New York 12-15 UTC), 0 sinon."""
    hours = (times_epoch // 3600) % 24
    out = np.zeros(len(times_epoch), dtype=np.int8)
    for lo, hi in KILLZONES.values():
        out |= ((hours >= lo) & (hours < hi)).astype(np.int8)
    return out


def session_label(epoch: float) -> str:
    hour = int((epoch // 3600) % 24)
    for name, (lo, hi) in SESSIONS.items():
        if lo <= hour < hi:
            return name
    return "late"


def htf_trend_series(df: pl.DataFrame, params: Optional[dict] = None,
                     mult: int = 4,
                     htf_sec_map: Optional[Dict[int, int]] = None) -> tuple:
    """Biais de structure MULTI-TIMEFRAME, causal barre par barre.

    Agrège l'OHLCV en buckets HTF (bornes horloge : epoch // htf_sec —
    identiques en live et en backtest), lance la même analyse de structure
    (BOS/CHoCH) sur le HTF, puis mappe sur chaque barre LTF le trend du DERNIER
    bucket HTF **entièrement clôturé** à cet instant — aucune fuite du futur.

    Le HTF cible est ``htf_sec_map[ltf_sec]`` si fourni (aligné sur la « source
    unique de vérité » ``_HTF_MAP`` d'app/live/utils via la stratégie), sinon
    ``ltf_sec × mult`` (fallback autonome).

    Retourne ``(trend_arr, meta)`` : trend ∈ {+1, −1, 0} par barre LTF,
    meta = {"htf_sec", "n_htf", "trend"} (état à la dernière barre).
    """
    n = len(df)
    htf_df, idx, htf_sec, n_htf = _htf_buckets(df, htf_sec_map, mult)
    if htf_df is None:
        return np.zeros(n, dtype=np.int8), {"htf_sec": htf_sec, "n_htf": n_htf,
                                            "trend": 0}
    trend_htf = analyze(htf_df, params)["_trend_arr"]
    out = np.zeros(n, dtype=np.int8)
    valid = idx >= 0
    out[valid] = trend_htf[idx[valid]]
    return out, {"htf_sec": int(htf_sec), "n_htf": int(n_htf),
                 "trend": int(out[-1])}


def _htf_buckets(df: pl.DataFrame, htf_sec_map: Optional[Dict[int, int]] = None,
                 mult: int = 4):
    """Agrégation OHLCV en buckets HTF (bornes horloge ``epoch // htf_sec``) +
    mapping CAUSAL LTF→dernier bucket HTF entièrement clôturé. Source partagée
    par ``htf_trend_series`` et ``htf_analysis`` (aucune fuite du futur).

    Retourne ``(htf_df|None, ltf_to_htf_idx, htf_sec, n_htf)`` — htf_df None si
    données insuffisantes ; ``idx[i]`` = index du bucket HTF clôturé à la fin de
    la barre LTF i (−1 si aucun)."""
    n = len(df)
    fail = (None, np.full(n, -1, dtype=int), 0, 0)
    if n < 40 or "time" not in df.columns:
        return fail
    epoch = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    deltas = np.diff(epoch[-64:])
    deltas = deltas[deltas > 0]
    if len(deltas) == 0:
        return fail
    ltf_sec = int(np.median(deltas))
    htf_sec = (htf_sec_map or {}).get(ltf_sec) or ltf_sec * max(int(mult), 2)
    bucket = epoch // htf_sec
    o = df["open"].to_numpy().astype(float)
    h = df["high"].to_numpy().astype(float)
    l = df["low"].to_numpy().astype(float)
    c = df["close"].to_numpy().astype(float)
    v = df["volume"].to_numpy().astype(float) if "volume" in df.columns \
        else np.ones(n)
    starts = np.flatnonzero(np.diff(bucket, prepend=bucket[0] - 1))
    ends = np.append(starts[1:], n)
    if len(starts) < 12:
        return None, np.full(n, -1, dtype=int), htf_sec, len(starts)
    ho = o[starts]
    hc = c[ends - 1]
    hh = np.array([h[s:e].max() for s, e in zip(starts, ends)])
    hl = np.array([l[s:e].min() for s, e in zip(starts, ends)])
    hv = np.array([v[s:e].sum() for s, e in zip(starts, ends)])
    htf_df = pl.DataFrame({"open": ho, "high": hh, "low": hl,
                           "close": hc, "volume": hv})
    bucket_end = (bucket[starts] + 1) * htf_sec
    close_times = epoch + ltf_sec
    idx = np.searchsorted(bucket_end, close_times, side="right") - 1
    return htf_df, idx, int(htf_sec), int(len(starts))


def htf_analysis(df: pl.DataFrame, params: Optional[dict] = None,
                 htf_sec_map: Optional[Dict[int, int]] = None, mult: int = 4):
    """Analyse SMC complète sur le HTF (mêmes buckets causaux que
    ``htf_trend_series``) → ``(res_htf|None, ltf_to_htf_idx)``. Permet à une
    stratégie de récupérer les Order Blocks HTF ACTIFS à chaque barre LTF (pour
    l'imbrication multi-timeframe : ``res_htf['_all_obs']`` filtrés par
    ``created_at <= idx[i]`` et non invalidés). Aucune fuite du futur."""
    htf_df, idx, _htf_sec, _n = _htf_buckets(df, htf_sec_map, mult)
    if htf_df is None:
        return None, idx
    return analyze(htf_df, params), idx


def smt_series(df: pl.DataFrame, correlate_path: str,
               lookback: int = 20) -> Optional[np.ndarray]:
    """Série SMT divergence ``{−1, 0, +1}`` sur le référentiel de ``df``, contre
    un actif CORRÉLÉ chargé depuis ``correlate_path`` (Parquet ou CSV), aligné
    CAUSALEMENT par timestamp (dernière clôture connue ≤ t). Générique : n'importe
    quel couple corrélé (ex. ETH pour trader BTC).

      +1  ``df`` inscrit un nouveau plus-BAS sur ``lookback`` mais le corrélé NON
          → biais haussier (smart money accumule) ;
      −1  ``df`` inscrit un nouveau plus-HAUT mais le corrélé NON → biais baissier.

    Retourne ``None`` (dégradation gracieuse) si le chemin est vide/absent, si le
    fichier est illisible ou dépourvu de colonnes temps/clôture, ou si ``df`` n'a
    pas de colonne ``time``. Primitive réutilisable (moteur) : câblée par
    ``smart_money`` et ``vizion`` en confluence/filtre optionnel."""
    import os
    from app.core import ict
    path = str(correlate_path or "")
    if not path or not os.path.exists(path) or "time" not in df.columns:
        return None
    try:
        cdf = (pl.read_parquet(path) if path.endswith((".parquet", ".pq"))
               else pl.read_csv(path, try_parse_dates=True))
        cdf = cdf.rename({c: c.strip().lower() for c in cdf.columns})
        tcol = "time" if "time" in cdf.columns else (
            "date" if "date" in cdf.columns else None)
        ccol = "close" if "close" in cdf.columns else (
            "adj close" if "adj close" in cdf.columns else None)
        if tcol is None or ccol is None:
            return None
        ct = cdf[tcol].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
        cc = cdf[ccol].cast(pl.Float64).to_numpy()
        t = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
        aligned = ict.align_series(t, ct, cc)
        close = df["close"].to_numpy().astype(float)
        return ict.smt_divergence(close, aligned, int(lookback))
    except Exception as e:                           # pragma: no cover
        logger.warning(f"[smc] smt_series KO ({path}) : {e}")
        return None
