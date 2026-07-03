"""Moteur d'analyse Smart Money Concepts (SMC).

Détection automatique, en une passe causale O(n), des structures institutionnelles :

  - Swings (pivots fractals) confirmés ``swing_right`` barres après le pivot,
    étiquetés HH / HL / LH / LL par rapport au swing précédent de même nature.
  - Structure de marché : cassures de structure (BOS — Break of Structure,
    continuation) et changements de caractère (CHoCH — Change of Character,
    retournement), sur clôture au-delà du dernier swing confirmé.
  - Zones de liquidité (Liquidity Pools) : doubles/triples sommets et fonds
    (equal highs/lows à ``eq_tol_atr``×ATR près) → Buy-side liquidity au-dessus
    des sommets égaux, Sell-side liquidity sous les fonds égaux. Chaque swing
    isolé reste une poche de liquidité mineure (stops résiduels).
  - Sweeps (prises de liquidité / stop hunts) : mèche qui perce un pool ou un
    swing puis clôture de retour du bon côté (rejet).
  - Order Blocks (zones d'Offre/Demande) : dernière bougie opposée avant une
    impulsion forte (displacement : corps ≥ ``disp_body_atr``×ATR qui engloutit
    l'extrême précédent). Statut suivi : fresh → touché (mitigé) → invalidé.
  - Fair Value Gaps (FVG / imbalances) : gap entre high[i−2] et low[i] (et
    miroir), suivi comblé/mitigé.
  - Premium / Discount : range de travail (dernier swing high ↔ swing low),
    équilibre à 50 %, zone OTE (Optimal Trade Entry, retracement 62–79 %).
  - Tendances : trendline support (2 derniers swing lows), résistance
    (2 derniers swing highs) et canal de régression linéaire.

Toutes les entités portent des indices de barres (``index``, ``formed_at``,
``swept_at``…) : à la barre ``i``, seules les données ≤ i ont été utilisées, ce
qui rend le résultat directement exploitable par un backtest sans lookahead.

Consommateurs :
  - ``app/strategies/smart_money.py`` (stratégie de trading basée sur ce moteur)
  - ``/api/scanner/smc``           (overlay graphique de la page scanner)
"""
import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.indicators_core import _true_range

logger = logging.getLogger(__name__)

# Nombre max d'entités conservées par catégorie dans le résultat final
# (les plus récentes) — borne la taille du payload JSON et le coût mémoire.
_MAX_KEEP = 60

DEFAULTS: Dict[str, Any] = {
    "swing_left":     3,      # barres strictement plus basses/hautes à gauche du pivot
    "swing_right":    3,      # barres à droite (délai de confirmation du pivot)
    "eq_tol_atr":     0.25,  # tolérance equal highs/lows, en fraction d'ATR
    "disp_body_atr":  1.3,   # corps minimal d'une bougie de displacement (×ATR)
    "ob_lookback":    5,     # recherche de la bougie opposée avant l'impulsion
    "fvg_min_atr":    0.2,   # taille minimale d'un FVG (×ATR)
    "atr_len":        14,
    "channel_lookback": 120, # fenêtre du canal de régression
    "max_pool_age":   500,   # âge max (barres) d'un pool utilisable comme cible
    "max_ob_age":     250,   # âge max d'un order block « frais »
}


def _params(p: Optional[dict]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    if p:
        for k in DEFAULTS:
            if p.get(k) is not None:
                out[k] = p[k]
    return out


def _wilder_atr(df: pl.DataFrame, n: int) -> np.ndarray:
    return (_true_range(df).ewm_mean(alpha=1.0 / n, adjust=False)
            .fill_null(0.0).to_numpy().astype(float))


# ══════════════════════════════════════════════════════════════════════════════
#  Passe principale
# ══════════════════════════════════════════════════════════════════════════════

def analyze(df: pl.DataFrame, params: Optional[dict] = None) -> Dict[str, Any]:
    """Analyse SMC complète du DataFrame OHLCV (colonnes open/high/low/close/volume).

    Retourne un dict JSON-compatible (listes d'entités indexées par barre) plus
    deux clés privées numpy pour la stratégie : ``_trend_arr`` (structure par
    barre : +1 haussier / −1 baissier / 0 indéterminé) et ``_atr_arr``.
    """
    p = _params(params)
    n = len(df)
    if n < 10:
        return _empty_result(n)

    o = df["open"].to_numpy().astype(float)
    h = df["high"].to_numpy().astype(float)
    l = df["low"].to_numpy().astype(float)
    c = df["close"].to_numpy().astype(float)
    atr = _wilder_atr(df, int(p["atr_len"]))
    # ATR de secours pour les premières barres (ewm ≈ 0 au tout début)
    atr_floor = max(float(np.median(atr[atr > 0])) * 0.05, 1e-12) if (atr > 0).any() else 1e-12
    atr = np.maximum(atr, atr_floor)

    L, R = int(p["swing_left"]), int(p["swing_right"])

    # ── 1. Pivots fractals (pré-calcul, chaque pivot confirmé à p_idx+R) ──────
    piv_high: List[int] = []
    piv_low:  List[int] = []
    for i in range(L, n - R):
        win_h = h[i - L:i + R + 1]
        win_l = l[i - L:i + R + 1]
        if h[i] >= win_h.max() and (win_h == h[i]).sum() == 1:
            piv_high.append(i)
        if l[i] <= win_l.min() and (win_l == l[i]).sum() == 1:
            piv_low.append(i)
    conf_high = {i + R: i for i in piv_high}   # barre de confirmation → pivot
    conf_low  = {i + R: i for i in piv_low}

    # ── 2. Boucle chronologique : structure, pools, sweeps, OB, FVG ──────────
    swings: List[dict] = []          # {index, kind, price, label, confirmed_at}
    struct_events: List[dict] = []   # {index, kind BOS/CHoCH, direction, level, swing_index}
    pools: List[dict] = []           # {kind, level, top, bottom, indices, formed_at, swept_at}
    sweeps: List[dict] = []          # {index, kind, level, rejected, source, ref_index}
    obs: List[dict] = []             # order blocks
    fvgs: List[dict] = []
    trend_arr = np.zeros(n, dtype=np.int8)

    trend = 0
    last_sh: Optional[dict] = None   # dernier swing high confirmé non cassé
    last_sl: Optional[dict] = None
    prev_high_price: Optional[float] = None   # pour labels HH/LH
    prev_low_price:  Optional[float] = None   # pour labels HL/LL
    swing_highs: List[dict] = []     # swings kind=high (réfs partagées avec `swings`)
    swing_lows:  List[dict] = []
    # Listes d'entités « actives » (non consommées) — la boucle ne parcourt que
    # celles-ci, pas l'historique complet : passe O(n × actives) au lieu de O(n²).
    active_pools: List[dict] = []
    active_obs:   List[dict] = []
    active_fvgs:  List[dict] = []

    for i in range(n):
        # ── Confirmation des pivots dont le délai expire à la barre i ────────
        pi = conf_high.get(i)
        if pi is not None:
            label = None
            if prev_high_price is not None:
                label = "HH" if h[pi] > prev_high_price else "LH"
            sw = {"index": pi, "kind": "high", "price": float(h[pi]),
                  "label": label, "confirmed_at": i, "swept_at": None}
            swings.append(sw)
            swing_highs.append(sw)
            prev_high_price = float(h[pi])
            last_sh = sw
            _try_cluster_pool(pools, active_pools, swing_highs, sw,
                              atr[pi] * float(p["eq_tol_atr"]),
                              kind="buy_side", formed_at=i)

        pi = conf_low.get(i)
        if pi is not None:
            label = None
            if prev_low_price is not None:
                label = "HL" if l[pi] > prev_low_price else "LL"
            sw = {"index": pi, "kind": "low", "price": float(l[pi]),
                  "label": label, "confirmed_at": i, "swept_at": None}
            swings.append(sw)
            swing_lows.append(sw)
            prev_low_price = float(l[pi])
            last_sl = sw
            _try_cluster_pool(pools, active_pools, swing_lows, sw,
                              atr[pi] * float(p["eq_tol_atr"]),
                              kind="sell_side", formed_at=i)

        # ── Cassures de structure (sur clôture, corps > mèche) ────────────────
        if last_sh is not None and c[i] > last_sh["price"]:
            kind = "CHoCH" if trend == -1 else "BOS"
            struct_events.append({
                "index": i, "kind": kind, "direction": "up",
                "level": last_sh["price"], "swing_index": last_sh["index"],
            })
            trend = 1
            last_sh = None            # niveau consommé, attendre le prochain swing
            _flag_ob_structure(obs, "bullish", i)
        if last_sl is not None and c[i] < last_sl["price"]:
            kind = "CHoCH" if trend == 1 else "BOS"
            struct_events.append({
                "index": i, "kind": kind, "direction": "down",
                "level": last_sl["price"], "swing_index": last_sl["index"],
            })
            trend = -1
            last_sl = None
            _flag_ob_structure(obs, "bearish", i)
        trend_arr[i] = trend

        # ── Sweeps de pools (mèche au-delà, clôture de retour) ────────────────
        for pool in active_pools[:]:
            if pool["formed_at"] >= i:
                continue
            if pool["kind"] == "buy_side" and h[i] > pool["level"]:
                pool["swept_at"] = i
                active_pools.remove(pool)
                sweeps.append({
                    "index": i, "kind": "buy_side", "level": pool["level"],
                    "rejected": bool(c[i] < pool["level"]),
                    "source": "pool", "ref_index": pool["indices"][-1],
                })
            elif pool["kind"] == "sell_side" and l[i] < pool["level"]:
                pool["swept_at"] = i
                active_pools.remove(pool)
                sweeps.append({
                    "index": i, "kind": "sell_side", "level": pool["level"],
                    "rejected": bool(c[i] > pool["level"]),
                    "source": "pool", "ref_index": pool["indices"][-1],
                })

        # ── Sweeps de swings isolés (liquidité mineure) ──────────────────────
        # Zone morte ±eq_tol×ATR : un dépassement marginal du swing n'est pas
        # un sweep mais un candidat equal highs/lows (le pool se formera à la
        # confirmation du nouveau pivot) — comportement stop-hunt réaliste.
        tol_i = float(p["eq_tol_atr"]) * atr[i]
        for sw in swing_highs[-4:]:
            if sw["swept_at"] is None and sw["confirmed_at"] < i \
                    and h[i] > sw["price"] + tol_i:
                sw["swept_at"] = i
                sweeps.append({
                    "index": i, "kind": "buy_side", "level": sw["price"],
                    "rejected": bool(c[i] < sw["price"]),
                    "source": "swing", "ref_index": sw["index"],
                })
        for sw in swing_lows[-4:]:
            if sw["swept_at"] is None and sw["confirmed_at"] < i \
                    and l[i] < sw["price"] - tol_i:
                sw["swept_at"] = i
                sweeps.append({
                    "index": i, "kind": "sell_side", "level": sw["price"],
                    "rejected": bool(c[i] > sw["price"]),
                    "source": "swing", "ref_index": sw["index"],
                })

        # ── Order blocks : cycle de vie des zones existantes ──────────────────
        for ob in active_obs[:]:
            if i <= ob["created_at"]:
                continue
            if ob["kind"] == "bullish":
                if ob["touched_at"] is None and l[i] <= ob["top"]:
                    ob["touched_at"] = i
                if c[i] < ob["bottom"]:
                    ob["invalidated_at"] = i
                    active_obs.remove(ob)
            else:
                if ob["touched_at"] is None and h[i] >= ob["bottom"]:
                    ob["touched_at"] = i
                if c[i] > ob["top"]:
                    ob["invalidated_at"] = i
                    active_obs.remove(ob)

        # ── Détection de displacement → nouvel order block ────────────────────
        body = c[i] - o[i]
        if i >= 1 and body >= float(p["disp_body_atr"]) * atr[i] and c[i] > h[i - 1]:
            j = _last_opposite_candle(o, c, i, int(p["ob_lookback"]), bullish=True)
            if j is not None and not any(x["index"] == j and x["kind"] == "bullish"
                                         for x in obs[-12:]):
                new_ob = {
                    "kind": "bullish", "index": j,
                    "top": float(max(o[j], c[j])), "bottom": float(l[j]),
                    "created_at": i, "touched_at": None, "invalidated_at": None,
                    "broke_structure": False, "strength": 1,
                }
                obs.append(new_ob)
                active_obs.append(new_ob)
        elif i >= 1 and -body >= float(p["disp_body_atr"]) * atr[i] and c[i] < l[i - 1]:
            j = _last_opposite_candle(o, c, i, int(p["ob_lookback"]), bullish=False)
            if j is not None and not any(x["index"] == j and x["kind"] == "bearish"
                                         for x in obs[-12:]):
                new_ob = {
                    "kind": "bearish", "index": j,
                    "top": float(h[j]), "bottom": float(min(o[j], c[j])),
                    "created_at": i, "touched_at": None, "invalidated_at": None,
                    "broke_structure": False, "strength": 1,
                }
                obs.append(new_ob)
                active_obs.append(new_ob)

        # ── FVG : cycle de vie puis détection ────────────────────────────────
        for fv in active_fvgs[:]:
            if i <= fv["index"] + 1:
                continue
            if fv["kind"] == "bullish":
                if fv["mitigated_at"] is None and l[i] <= fv["top"]:
                    fv["mitigated_at"] = i
                if l[i] <= fv["bottom"]:
                    fv["filled_at"] = i
                    active_fvgs.remove(fv)
            else:
                if fv["mitigated_at"] is None and h[i] >= fv["bottom"]:
                    fv["mitigated_at"] = i
                if h[i] >= fv["top"]:
                    fv["filled_at"] = i
                    active_fvgs.remove(fv)

        if i >= 2:
            gap_up = l[i] - h[i - 2]
            if gap_up >= float(p["fvg_min_atr"]) * atr[i]:
                new_fvg = {"kind": "bullish", "index": i - 1,
                           "top": float(l[i]), "bottom": float(h[i - 2]),
                           "mitigated_at": None, "filled_at": None}
                fvgs.append(new_fvg)
                active_fvgs.append(new_fvg)
            gap_dn = l[i - 2] - h[i]
            if gap_dn >= float(p["fvg_min_atr"]) * atr[i]:
                new_fvg = {"kind": "bearish", "index": i - 1,
                           "top": float(l[i - 2]), "bottom": float(h[i]),
                           "mitigated_at": None, "filled_at": None}
                fvgs.append(new_fvg)
                active_fvgs.append(new_fvg)

    # ── 3. Premium / Discount + OTE (état à la dernière barre) ────────────────
    pd_zone = _premium_discount_at(swings, trend_arr, h, l, c, n - 1)

    # ── 4. Trendlines + canal de régression ──────────────────────────────────
    tls = _trendlines(swing_highs, swing_lows, n)
    channel = _regression_channel(c, int(p["channel_lookback"]))

    bias_label = {1: "haussier", -1: "baissier", 0: "neutre"}[int(trend)]
    result = {
        "n_bars": n,
        "swings": swings[-_MAX_KEEP:],
        "structure_events": struct_events[-_MAX_KEEP:],
        "liquidity_pools": pools[-_MAX_KEEP:],
        "sweeps": sweeps[-_MAX_KEEP:],
        "order_blocks": obs[-_MAX_KEEP:],
        "fvgs": fvgs[-_MAX_KEEP:],
        "premium_discount": pd_zone,
        "trendlines": tls,
        "channel": channel,
        "bias": {
            "trend": int(trend),
            "label": bias_label,
            "last_event": struct_events[-1] if struct_events else None,
        },
        # Clés privées (non sérialisées par l'API) — consommées par la stratégie.
        "_trend_arr": trend_arr,
        "_atr_arr": atr,
        "_all_pools": pools,
        "_all_swings": swings,
        "_all_obs": obs,
        "_all_fvgs": fvgs,
        "_all_sweeps": sweeps,
        "_all_struct_events": struct_events,
    }
    return result


def _empty_result(n: int) -> Dict[str, Any]:
    return {
        "n_bars": n, "swings": [], "structure_events": [], "liquidity_pools": [],
        "sweeps": [], "order_blocks": [], "fvgs": [],
        "premium_discount": None, "trendlines": [], "channel": None,
        "bias": {"trend": 0, "label": "neutre", "last_event": None},
        "_trend_arr": np.zeros(max(n, 0), dtype=np.int8),
        "_atr_arr": np.zeros(max(n, 0), dtype=float),
        "_all_pools": [], "_all_swings": [], "_all_obs": [], "_all_fvgs": [],
        "_all_sweeps": [], "_all_struct_events": [],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Sous-détections
# ══════════════════════════════════════════════════════════════════════════════

def _try_cluster_pool(pools: List[dict], active_pools: List[dict],
                      same_kind_swings: List[dict], new_swing: dict,
                      tol: float, kind: str, formed_at: int):
    """Regroupe le swing confirmé avec les précédents de même nature si leurs
    prix sont à ``tol`` près → pool de liquidité (equal highs/lows).

    Étend un pool actif existant plutôt que d'en créer un doublon.
    """
    price = new_swing["price"]
    # Étendre un pool actif proche
    for pool in reversed(active_pools):
        if pool["kind"] != kind:
            continue
        if abs(price - pool["level"]) <= tol:
            pool["indices"].append(new_swing["index"])
            if kind == "buy_side":
                pool["level"] = max(pool["level"], price)
                pool["bottom"] = min(pool["bottom"], price)
                pool["top"] = pool["level"]
            else:
                pool["level"] = min(pool["level"], price)
                pool["top"] = max(pool["top"], price)
                pool["bottom"] = pool["level"]
            pool["formed_at"] = formed_at
            return
    # Sinon chercher un swing récent (non sweepé) assez proche pour former un pool
    for sw in reversed(same_kind_swings[-12:-1]):
        if sw["swept_at"] is not None:
            continue
        if abs(price - sw["price"]) <= tol:
            lo, hi = sorted((price, sw["price"]))
            pool = {
                "kind": kind,
                "level": hi if kind == "buy_side" else lo,
                "top": hi, "bottom": lo,
                "indices": [sw["index"], new_swing["index"]],
                "formed_at": formed_at, "swept_at": None,
            }
            pools.append(pool)
            active_pools.append(pool)
            return


def _last_opposite_candle(o: np.ndarray, c: np.ndarray, i: int,
                          lookback: int, bullish: bool) -> Optional[int]:
    """Dernière bougie de couleur opposée avant l'impulsion à la barre ``i``."""
    for j in range(i - 1, max(i - 1 - lookback, -1), -1):
        if bullish and c[j] < o[j]:
            return j
        if not bullish and c[j] > o[j]:
            return j
    return None


def _flag_ob_structure(obs: List[dict], kind: str, i: int, window: int = 3):
    """Marque les order blocks récents dont l'impulsion a cassé la structure
    (BOS/CHoCH dans les ``window`` barres suivant leur création) → strength 2."""
    for ob in reversed(obs):
        if ob["kind"] != kind:
            continue
        if i - ob["created_at"] > window:
            break
        ob["broke_structure"] = True
        ob["strength"] = 2


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


def _regression_channel(c: np.ndarray, lookback: int) -> Optional[dict]:
    """Canal de régression linéaire sur les ``lookback`` dernières clôtures :
    droite médiane ± 2 écarts-types des résidus."""
    n = len(c)
    lb = min(lookback, n)
    if lb < 20:
        return None
    y = c[n - lb:]
    x = np.arange(lb, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    half = float(2.0 * resid.std())
    if not math.isfinite(half):
        return None
    return {
        "start_index": int(n - lb), "end_index": int(n - 1),
        "mid_start": round(float(intercept), 8),
        "mid_end": round(float(intercept + slope * (lb - 1)), 8),
        "half_width": round(half, 8),
        "slope": float(slope),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers de ciblage (utilisés par la stratégie pour construire les TP)
# ══════════════════════════════════════════════════════════════════════════════

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
