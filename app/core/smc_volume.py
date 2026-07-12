"""Profil de volume et canal de régression du moteur SMC (V4-L / ARCH-14)."""
import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

def volume_profile(h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
                   i: int, lookback: int = 240, n_bins: int = 40,
                   hvn_factor: float = 1.5,
                   lvn_factor: float = 0.5) -> Optional[dict]:
    """Profil de volume CAUSAL sur les ``lookback`` barres terminées à ``i`` :
    histogramme du volume par tranche de prix (prix typique hlc3).

    Retourne :
      - ``poc``  : Point of Control (tranche la plus tradée — aimant naturel) ;
      - ``hvns`` : High Volume Nodes (≥ ``hvn_factor``×moyenne, maxima locaux)
                   — zones d'acceptation, support/résistance volumétriques ;
      - ``lvns`` : Low Volume Nodes (≤ ``lvn_factor``×moyenne) — zones de
                   rejet que le prix traverse vite (équivalent volumétrique
                   des liquidity voids).
    """
    lo_w = max(0, i + 1 - lookback)
    if i + 1 - lo_w < 30:
        return None
    hh = h[lo_w:i + 1]
    ll = l[lo_w:i + 1]
    tp = (hh + ll + c[lo_w:i + 1]) / 3.0
    vv = v[lo_w:i + 1]
    p_min, p_max = float(ll.min()), float(hh.max())
    if p_max <= p_min:
        return None
    hist, edges = np.histogram(tp, bins=n_bins, range=(p_min, p_max),
                               weights=vv)
    centers = (edges[:-1] + edges[1:]) / 2.0
    mean_v = float(hist.mean()) if hist.sum() > 0 else 0.0
    if mean_v <= 0:
        return None
    poc = float(centers[int(np.argmax(hist))])
    hvns, lvns = [], []
    for k in range(n_bins):
        left = hist[k - 1] if k > 0 else -1.0
        right = hist[k + 1] if k < n_bins - 1 else -1.0
        if hist[k] >= hvn_factor * mean_v and hist[k] >= left and hist[k] >= right:
            hvns.append(float(centers[k]))
        elif hist[k] <= lvn_factor * mean_v:
            lvns.append(float(centers[k]))
    # Value Area : plage contenant 70 % du volume, par expansion gloutonne
    # bilatérale depuis le POC (côté au plus fort volume d'abord). Le prix tend
    # à rester dans la VA ; en sortir signale un changement de régime.
    total = float(hist.sum())
    poc_k = int(np.argmax(hist))
    lo_k = hi_k = poc_k
    captured = float(hist[poc_k])
    while captured < 0.70 * total and (lo_k > 0 or hi_k < n_bins - 1):
        left_v = float(hist[lo_k - 1]) if lo_k > 0 else -1.0
        right_v = float(hist[hi_k + 1]) if hi_k < n_bins - 1 else -1.0
        if right_v >= left_v:
            hi_k += 1
            captured += float(hist[hi_k])
        else:
            lo_k -= 1
            captured += float(hist[lo_k])
    return {"poc": poc, "hvns": hvns, "lvns": lvns,
            "va_low": float(edges[lo_k]), "va_high": float(edges[hi_k + 1]),
            "bin_size": float(edges[1] - edges[0]),
            "range_low": p_min, "range_high": p_max}


def regression_channel_at(c: np.ndarray, i: int,
                          lookback: int = 120) -> Optional[dict]:
    """Canal de régression CAUSAL terminé à la barre ``i`` (mêmes conventions
    que le canal du résultat d'analyse, mais calculable à n'importe quelle
    barre pour un usage backtest sans lookahead)."""
    lb = min(lookback, i + 1)
    if lb < 20:
        return None
    y = c[i + 1 - lb:i + 1]
    x = np.arange(lb, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    half = float(2.0 * resid.std())
    if not math.isfinite(half) or half <= 0:
        return None
    mid_i = float(intercept + slope * (lb - 1))
    return {"mid": mid_i, "upper": mid_i + half, "lower": mid_i - half,
            "half_width": half, "slope": float(slope)}


def _regression_channel(c: np.ndarray, lookback: int) -> Optional[dict]:
    """Canal de régression pour le résultat d'``analyze`` (dernière barre) —
    fin wrapper autour de :func:`regression_channel_at` qui reshape la sortie
    en {start_index, end_index, mid_start, mid_end}. Source unique du calcul
    (droite médiane ± 2σ des résidus) : les deux vues ne peuvent plus diverger."""
    n = len(c)
    ch = regression_channel_at(c, n - 1, lookback)
    if ch is None:
        return None
    lb = min(lookback, n)
    mid_end = ch["mid"]
    mid_start = mid_end - ch["slope"] * (lb - 1)
    return {
        "start_index": int(n - lb), "end_index": int(n - 1),
        "mid_start": round(float(mid_start), 8),
        "mid_end": round(float(mid_end), 8),
        "half_width": round(float(ch["half_width"]), 8),
        "slope": float(ch["slope"]),
    }
