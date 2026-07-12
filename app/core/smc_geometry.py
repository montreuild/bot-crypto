"""Géométrie de structure SMC (V4-L / ARCH-14) : premium/discount & OTE,
trendlines, zigzag de structure, projection de cycle, et helpers de ciblage
TP (pools/voids au-dessus/en-dessous du prix)."""
import logging
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

def _premium_discount_at(all_swings: List[dict], trend_arr: np.ndarray,
                         h: np.ndarray, l: np.ndarray, c: np.ndarray,
                         i: int) -> Optional[dict]:
    """Range de travail à la barre ``i`` (causal) : dernier swing high ↔ dernier
    swing low confirmés ≤ i, élargi au max/min des 100 dernières barres,
    équilibre à 50 % et zone OTE (retracement 62–79 %)."""
    sh = sl = None
    for sw in reversed(all_swings):
        if sw["confirmed_at"] > i:
            continue
        if sw["kind"] == "high" and sh is None:
            sh = sw["price"]
        elif sw["kind"] == "low" and sl is None:
            sl = sw["price"]
        if sh is not None and sl is not None:
            break
    if sh is None or sl is None:
        return None
    lo_w = max(0, i - 99)
    range_high = max(sh, float(h[lo_w:i + 1].max()))
    range_low  = min(sl, float(l[lo_w:i + 1].min()))
    if range_high <= range_low:
        return None
    trend = int(trend_arr[i])
    eq = (range_high + range_low) / 2.0
    price = float(c[i])
    span = range_high - range_low
    pos = (price - range_low) / span            # 0 = bas du range, 1 = haut
    zone = "premium" if pos > 0.55 else ("discount" if pos < 0.45 else "equilibrium")
    # OTE : retracement 62–79 % de la jambe dans le sens de la tendance.
    if trend >= 0:
        ote_high = range_high - 0.62 * span
        ote_low  = range_high - 0.79 * span
    else:
        ote_low  = range_low + 0.62 * span
        ote_high = range_low + 0.79 * span
    return {
        "range_high": round(range_high, 8), "range_low": round(range_low, 8),
        "equilibrium": round(eq, 8), "position": round(pos, 4), "zone": zone,
        "ote_low": round(ote_low, 8), "ote_high": round(ote_high, 8),
        "in_ote": bool(ote_low <= price <= ote_high),
    }


def premium_discount_at(result: Dict[str, Any], h: np.ndarray, l: np.ndarray,
                        c: np.ndarray, i: int) -> Optional[dict]:
    """Version publique causale de :func:`_premium_discount_at` sur un résultat
    d'``analyze`` — utilisée par la stratégie pour scorer la barre ``i`` sans
    fuite d'information future."""
    return _premium_discount_at(result["_all_swings"], result["_trend_arr"],
                                h, l, c, i)


def _trendlines(swing_highs: List[dict], swing_lows: List[dict],
                n: int) -> List[dict]:
    """Trendlines automatiques : support par les 2 derniers swing lows,
    résistance par les 2 derniers swing highs, projetées jusqu'à la dernière barre."""
    out = []
    for swl, kind in ((swing_lows, "support"), (swing_highs, "resistance")):
        if len(swl) < 2:
            continue
        a, b = swl[-2], swl[-1]
        if b["index"] == a["index"]:
            continue
        slope = (b["price"] - a["price"]) / (b["index"] - a["index"])
        y_now = b["price"] + slope * (n - 1 - b["index"])
        out.append({
            "kind": kind,
            "x1": int(a["index"]), "y1": round(a["price"], 8),
            "x2": int(n - 1),      "y2": round(y_now, 8),
            "anchor2": int(b["index"]), "slope": slope,
        })
    return out


def _zigzag(all_swings: List[dict]) -> List[dict]:
    """Polyligne de structure (peaks/troughs) : swings triés par index avec
    alternance high/low forcée — en cas de swings consécutifs de même nature,
    seul le plus extrême est conservé. C'est le tracé « market structure »
    classique (HH→HL→HH… ou LH→LL→LH…)."""
    pts: List[dict] = []
    for sw in sorted(all_swings, key=lambda s: s["index"]):
        pt = {"index": sw["index"], "price": sw["price"],
              "kind": sw["kind"], "label": sw["label"]}
        if pts and pts[-1]["index"] == sw["index"]:
            continue    # pivot haut ET bas sur la même bougie : un seul point
        if pts and pts[-1]["kind"] == sw["kind"]:
            keep_new = (sw["price"] > pts[-1]["price"]) if sw["kind"] == "high" \
                else (sw["price"] < pts[-1]["price"])
            if keep_new:
                pts[-1] = pt
            continue
        pts.append(pt)
    return pts


def _cycle_projection(structure_line: List[dict], channel: Optional[dict],
                      trend: int, last_close: float) -> Optional[dict]:
    """Phase du cycle de marché et cible projetée sur le canal de régression.

    Lecture « market cycle » des traders de canaux : après un trough (creux du
    zigzag), le prix avance vers la borne haute du canal (expected peak) ; après
    un peak, il décline vers la borne basse (expected trough)."""
    if not structure_line or channel is None:
        return None
    last_pt = structure_line[-1]
    mid_end = float(channel["mid_end"])
    half = float(channel["half_width"])
    if last_pt["kind"] == "low":
        phase, boundary, target = "advance", "upper", mid_end + half
    else:
        phase, boundary, target = "decline", "lower", mid_end - half
    span = 2 * half if half > 0 else 1e-12
    progress = (last_close - (mid_end - half)) / span      # 0 = borne basse
    if phase == "decline":
        progress = 1.0 - progress
    return {
        "phase": phase,                       # advance | decline
        "boundary": boundary,                 # borne visée du canal
        "target": round(target, 8),           # expected peak/trough
        "from_index": int(last_pt["index"]),
        "from_price": last_pt["price"],
        "progress": round(float(max(0.0, min(progress, 1.5))), 3),
        "trend": int(trend),
    }


def trendline_value_at(result: Dict[str, Any], i: int,
                       kind: str) -> Optional[float]:
    """Valeur CAUSALE à la barre ``i`` de la trendline ``support`` (2 derniers
    swing lows confirmés ≤ i) ou ``resistance`` (2 derniers swing highs).
    Retourne None si moins de deux swings disponibles."""
    want = "low" if kind == "support" else "high"
    a = b = None
    for sw in reversed(result["_all_swings"]):
        if sw["kind"] != want or sw["confirmed_at"] > i:
            continue
        if b is None:
            b = sw
        else:
            a = sw
            break
    if a is None or b is None or b["index"] == a["index"]:
        return None
    slope = (b["price"] - a["price"]) / (b["index"] - a["index"])
    return float(b["price"] + slope * (i - b["index"]))


def recent_sweep(result: Dict[str, Any], created_at: int, want: str,
                 lookback: int) -> bool:
    """Un sweep **rejeté** de type ``want`` (``buy_side``/``sell_side``) dans la
    fenêtre ``[created_at − lookback, created_at]`` (SMC-11 : inducement).

    Prise de liquidité opposée près de l'origine d'une zone = crédibilité du
    move institutionnel (les stops ont été consommés avant l'impulsion).
    Primitive partagée vizion (``require_sweep``) / smart_money
    (``require_inducement``)."""
    for sw in result["_all_sweeps"]:
        if not sw["rejected"] or sw["kind"] != want:
            continue
        if created_at - lookback <= sw["index"] <= created_at:
            return True
    return False


def liquidity_targets_above(result: Dict[str, Any], i: int, price: float,
                            max_age: int = 500) -> List[float]:
    """Niveaux de liquidité buy-side strictement au-dessus de ``price``,
    formés avant la barre ``i``, non sweepés avant ``i``, triés croissants.

    Cibles naturelles de TP pour un long : les stops au-dessus des equal highs
    et des swing highs sont l'endroit où les institutionnels vont chercher la
    contrepartie.
    """
    levels = []
    for pool in result["_all_pools"]:
        if pool["kind"] != "buy_side" or pool["formed_at"] >= i:
            continue
        if pool["swept_at"] is not None and pool["swept_at"] <= i:
            continue
        if i - pool["formed_at"] > max_age:
            continue
        if pool["level"] > price:
            levels.append(float(pool["level"]))
    for sw in result["_all_swings"]:
        if sw["kind"] != "high" or sw["confirmed_at"] >= i:
            continue
        if sw["swept_at"] is not None and sw["swept_at"] <= i:
            continue
        if i - sw["confirmed_at"] > max_age:
            continue
        if sw["price"] > price:
            levels.append(float(sw["price"]))
    return sorted(set(levels))


def void_targets_above(result: Dict[str, Any], i: int, price: float,
                       max_age: int = 500) -> List[float]:
    """Bords supérieurs des liquidity voids non comblés au-dessus de ``price``
    (causal à la barre ``i``). Un void est une zone « fine » : une fois le prix
    dedans, il la traverse vite — le bord opposé est une cible naturelle."""
    levels = []
    for vd in result["_all_voids"]:
        if vd["end_index"] >= i or i - vd["end_index"] > max_age:
            continue
        if vd["filled_at"] is not None and vd["filled_at"] <= i:
            continue
        if vd["top"] > price:
            levels.append(float(vd["top"]))
    return sorted(set(levels))


def void_targets_below(result: Dict[str, Any], i: int, price: float,
                       max_age: int = 500) -> List[float]:
    """Miroir de :func:`void_targets_above` — bords inférieurs des voids non
    comblés sous ``price``."""
    levels = []
    for vd in result["_all_voids"]:
        if vd["end_index"] >= i or i - vd["end_index"] > max_age:
            continue
        if vd["filled_at"] is not None and vd["filled_at"] <= i:
            continue
        if vd["bottom"] < price:
            levels.append(float(vd["bottom"]))
    return sorted(set(levels), reverse=True)


def liquidity_targets_below(result: Dict[str, Any], i: int, price: float,
                            max_age: int = 500) -> List[float]:
    """Miroir de :func:`liquidity_targets_above` — cibles TP pour un short."""
    levels = []
    for pool in result["_all_pools"]:
        if pool["kind"] != "sell_side" or pool["formed_at"] >= i:
            continue
        if pool["swept_at"] is not None and pool["swept_at"] <= i:
            continue
        if i - pool["formed_at"] > max_age:
            continue
        if pool["level"] < price:
            levels.append(float(pool["level"]))
    for sw in result["_all_swings"]:
        if sw["kind"] != "low" or sw["confirmed_at"] >= i:
            continue
        if sw["swept_at"] is not None and sw["swept_at"] <= i:
            continue
        if i - sw["confirmed_at"] > max_age:
            continue
        if sw["price"] < price:
            levels.append(float(sw["price"]))
    return sorted(set(levels), reverse=True)
