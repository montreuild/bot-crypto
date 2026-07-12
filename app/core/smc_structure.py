"""Passe principale du moteur SMC (V4-L / ARCH-14) : ``analyze`` — détection
causale O(n) des swings, BOS/CHoCH, pools de liquidité, sweeps, order blocks,
FVG, voids, breakers, rejection blocks, premium/discount, trendlines, canal
et projection de cycle. Voir la docstring de ``app/core/smc.py`` (façade)."""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.smc_primitives import (
    _MAX_KEEP, _empty_result, _flag_ob_structure, _last_opposite_candle,
    _params, _try_cluster_pool, _wilder_atr,
)
from app.core.smc_geometry import (
    _cycle_projection, _premium_discount_at, _trendlines, _zigzag,
)
from app.core.smc_volume import _regression_channel

logger = logging.getLogger(__name__)

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
    voids: List[dict] = []           # liquidity voids (runs directionnels)
    breakers: List[dict] = []        # order blocks invalidés → polarité inversée
    rejections: List[dict] = []      # rejection blocks (mèches de swing)
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
    active_voids: List[dict] = []
    active_breakers: List[dict] = []
    active_rejections: List[dict] = []
    rb_wick = float(p["rb_wick_atr"])
    # État du run directionnel courant (détection des liquidity voids)
    run_dir = 0          # +1 haussier, −1 baissier, 0 neutre
    run_start = 0        # index de la première bougie du run
    run_void: Optional[dict] = None   # void en cours d'extension (ou None)

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
            # Rejection block : mèche haute marquée au sommet → zone d'offre
            body_top = max(o[pi], c[pi])
            if h[pi] - body_top >= rb_wick * atr[pi]:
                rb = {"kind": "bearish", "index": pi,
                      "top": float(h[pi]), "bottom": float(body_top),
                      "created_at": i, "touched_at": None,
                      "invalidated_at": None}
                rejections.append(rb)
                active_rejections.append(rb)

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
            # Rejection block : mèche basse marquée au creux → zone de demande
            body_bot = min(o[pi], c[pi])
            if body_bot - l[pi] >= rb_wick * atr[pi]:
                rb = {"kind": "bullish", "index": pi,
                      "top": float(body_bot), "bottom": float(l[pi]),
                      "created_at": i, "touched_at": None,
                      "invalidated_at": None}
                rejections.append(rb)
                active_rejections.append(rb)

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
        # Un OB invalidé sur clôture devient un BREAKER BLOCK : la zone inverse
        # sa polarité (demande transpercée → offre, et réciproquement). Les
        # stops piégés dans la zone alimentent le retest en sens inverse.
        for ob in active_obs[:]:
            if i <= ob["created_at"]:
                continue
            if ob["kind"] == "bullish":
                if ob["touched_at"] is None and l[i] <= ob["top"]:
                    ob["touched_at"] = i
                if c[i] < ob["bottom"]:
                    ob["invalidated_at"] = i
                    active_obs.remove(ob)
                    brk = {"kind": "bearish", "top": ob["top"],
                           "bottom": ob["bottom"], "index": ob["index"],
                           "created_at": i, "touched_at": None,
                           "invalidated_at": None}
                    breakers.append(brk)
                    active_breakers.append(brk)
            else:
                if ob["touched_at"] is None and h[i] >= ob["bottom"]:
                    ob["touched_at"] = i
                if c[i] > ob["top"]:
                    ob["invalidated_at"] = i
                    active_obs.remove(ob)
                    brk = {"kind": "bullish", "top": ob["top"],
                           "bottom": ob["bottom"], "index": ob["index"],
                           "created_at": i, "touched_at": None,
                           "invalidated_at": None}
                    breakers.append(brk)
                    active_breakers.append(brk)

        # ── Rejection blocks : cycle de vie (même sémantique que les OB) ─────
        for rb in active_rejections[:]:
            if i <= rb["created_at"]:
                continue
            if rb["kind"] == "bullish":
                if rb["touched_at"] is None and l[i] <= rb["top"]:
                    rb["touched_at"] = i
                if c[i] < rb["bottom"]:
                    rb["invalidated_at"] = i
                    active_rejections.remove(rb)
            else:
                if rb["touched_at"] is None and h[i] >= rb["bottom"]:
                    rb["touched_at"] = i
                if c[i] > rb["top"]:
                    rb["invalidated_at"] = i
                    active_rejections.remove(rb)

        # ── Breaker blocks : cycle de vie (retest / re-cassure) ──────────────
        for brk in active_breakers[:]:
            if i <= brk["created_at"]:
                continue
            if brk["kind"] == "bullish":
                # Zone devenue support : touch par le haut, invalidée sous le bottom
                if brk["touched_at"] is None and l[i] <= brk["top"]:
                    brk["touched_at"] = i
                if c[i] < brk["bottom"]:
                    brk["invalidated_at"] = i
                    active_breakers.remove(brk)
            else:
                # Zone devenue résistance : touch par le bas, invalidée au-dessus
                if brk["touched_at"] is None and h[i] >= brk["bottom"]:
                    brk["touched_at"] = i
                if c[i] > brk["top"]:
                    brk["invalidated_at"] = i
                    active_breakers.remove(brk)

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

        # ── Liquidity voids : cycle de vie (retour du prix dans la zone) ─────
        for vd in active_voids[:]:
            if vd is run_void or i <= vd["end_index"]:
                continue
            if vd["kind"] == "bullish":
                # Zone traversée à la hausse : fill par retracement baissier
                if vd["mitigated_at"] is None and l[i] <= vd["top"]:
                    vd["mitigated_at"] = i
                if l[i] <= vd["bottom"]:
                    vd["filled_at"] = i
                    active_voids.remove(vd)
            else:
                if vd["mitigated_at"] is None and h[i] >= vd["bottom"]:
                    vd["mitigated_at"] = i
                if h[i] >= vd["top"]:
                    vd["filled_at"] = i
                    active_voids.remove(vd)

        # ── Liquidity voids : détection du run directionnel courant ──────────
        # Un run de ≥ void_min_bars bougies de même couleur traversant
        # ≥ void_min_atr×ATR crée un void, étendu tant que le run continue.
        bar_dir = 1 if c[i] > o[i] else (-1 if c[i] < o[i] else 0)
        if bar_dir != 0 and bar_dir == run_dir:
            pass                                    # le run continue
        else:
            run_dir, run_start, run_void = bar_dir, i, None
        if run_dir != 0 and (i - run_start + 1) >= int(p["void_min_bars"]):
            span_lo = float(min(o[run_start], c[i]))
            span_hi = float(max(o[run_start], c[i]))
            if (span_hi - span_lo) >= float(p["void_min_atr"]) * atr[i]:
                if run_void is None:
                    run_void = {
                        "kind": "bullish" if run_dir == 1 else "bearish",
                        "start_index": run_start, "end_index": i,
                        "top": span_hi, "bottom": span_lo,
                        "mitigated_at": None, "filled_at": None,
                    }
                    voids.append(run_void)
                    active_voids.append(run_void)
                else:                               # extension du void en cours
                    run_void["end_index"] = i
                    run_void["top"] = max(run_void["top"], span_hi)
                    run_void["bottom"] = min(run_void["bottom"], span_lo)

    # ── 3. Premium / Discount + OTE (état à la dernière barre) ────────────────
    pd_zone = _premium_discount_at(swings, trend_arr, h, l, c, n - 1)

    # ── 4. Trendlines + canal de régression ──────────────────────────────────
    tls = _trendlines(swing_highs, swing_lows, n)
    channel = _regression_channel(c, int(p["channel_lookback"]))

    # ── 5. Structure line (zigzag peaks/troughs) + projection de cycle ───────
    structure_line = _zigzag(swings)
    cycle = _cycle_projection(structure_line, channel, trend, float(c[-1]))

    bias_label = {1: "haussier", -1: "baissier", 0: "neutre"}[int(trend)]
    result = {
        "n_bars": n,
        "swings": swings[-_MAX_KEEP:],
        "structure_events": struct_events[-_MAX_KEEP:],
        "liquidity_pools": pools[-_MAX_KEEP:],
        "sweeps": sweeps[-_MAX_KEEP:],
        "order_blocks": obs[-_MAX_KEEP:],
        "fvgs": fvgs[-_MAX_KEEP:],
        "liquidity_voids": voids[-_MAX_KEEP:],
        "breakers": breakers[-_MAX_KEEP:],
        "rejection_blocks": rejections[-_MAX_KEEP:],
        "structure_line": structure_line[-2 * _MAX_KEEP:],
        "cycle": cycle,
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
        "_all_voids": voids,
        "_all_breakers": breakers,
        "_all_rejections": rejections,
    }
    return result
