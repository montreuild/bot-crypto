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
  - Liquidity Voids : runs d'au moins ``void_min_bars`` bougies directionnelles
    consécutives traversant ≥ ``void_min_atr``×ATR — zone « fine » que le prix
    a parcourue sans trader, aimant naturel pour un retour (fill).
  - Breaker Blocks : order block invalidé → la zone inverse sa polarité
    (une demande transpercée devient offre, et réciproquement).
  - Premium / Discount : range de travail (dernier swing high ↔ swing low),
    équilibre à 50 %, zone OTE (Optimal Trade Entry, retracement 62–79 %).
  - Tendances : trendline support (2 derniers swing lows), résistance
    (2 derniers swing highs) et canal de régression linéaire.
  - Structure line (zigzag) : polyligne des swings alternés (peaks/troughs)
    pour le tracé « market structure », + projection de cycle sur le canal
    (expected peak/trough à la borne du canal).

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

from app.core.indicators_core import atr_wilder as _atr_wilder_series

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
    "void_min_bars":  3,     # bougies directionnelles consécutives min d'un void
    "void_min_atr":   2.5,   # déplacement minimal du run (×ATR)
    "rb_wick_atr":    0.5,   # mèche minimale d'un rejection block (×ATR)
    "atr_len":        14,
    "channel_lookback": 120,  # fenêtre du canal de régression
    # NB : le vieillissement des zones (âge max d'un pool/OB utilisable comme
    # cible) est un paramètre de STRATÉGIE (``pool_max_age``/``ob_max_age`` dans
    # smart_money.fixed_params), pas du moteur — il n'apparaît donc pas ici.
}


def _params(p: Optional[dict]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    if p:
        for k in DEFAULTS:
            if p.get(k) is not None:
                out[k] = p[k]
    return out


def _wilder_atr(df: pl.DataFrame, n: int) -> np.ndarray:
    # ATR de Wilder (RMA) — source unique : indicators_core.atr_wilder. Le
    # lissage Wilder (≠ EMA span=n) est intentionnel : il aligne les seuils
    # « ×ATR » du SMC sur l'ATR de TradingView (ta.atr).
    return _atr_wilder_series(df, n).fill_null(0.0).to_numpy().astype(float)


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


def _empty_result(n: int) -> Dict[str, Any]:
    return {
        "n_bars": n, "swings": [], "structure_events": [], "liquidity_pools": [],
        "sweeps": [], "order_blocks": [], "fvgs": [],
        "liquidity_voids": [], "breakers": [], "rejection_blocks": [],
        "structure_line": [], "cycle": None,
        "premium_discount": None, "trendlines": [], "channel": None,
        "bias": {"trend": 0, "label": "neutre", "last_event": None},
        "_trend_arr": np.zeros(max(n, 0), dtype=np.int8),
        "_atr_arr": np.zeros(max(n, 0), dtype=float),
        "_all_pools": [], "_all_swings": [], "_all_obs": [], "_all_fvgs": [],
        "_all_sweeps": [], "_all_struct_events": [], "_all_voids": [],
        "_all_breakers": [], "_all_rejections": [],
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


# ══════════════════════════════════════════════════════════════════════════════
#  Volume profile, sessions et biais multi-timeframe
# ══════════════════════════════════════════════════════════════════════════════

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
    return {"poc": poc, "hvns": hvns, "lvns": lvns,
            "bin_size": float(edges[1] - edges[0]),
            "range_low": p_min, "range_high": p_max}


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
    empty = np.zeros(n, dtype=np.int8)
    if n < 40 or "time" not in df.columns:
        return empty, {"htf_sec": 0, "n_htf": 0, "trend": 0}
    epoch = df["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
    deltas = np.diff(epoch[-64:])
    deltas = deltas[deltas > 0]
    if len(deltas) == 0:
        return empty, {"htf_sec": 0, "n_htf": 0, "trend": 0}
    ltf_sec = int(np.median(deltas))
    htf_sec = (htf_sec_map or {}).get(ltf_sec) or ltf_sec * max(int(mult), 2)

    bucket = epoch // htf_sec
    o = df["open"].to_numpy().astype(float)
    h = df["high"].to_numpy().astype(float)
    l = df["low"].to_numpy().astype(float)
    c = df["close"].to_numpy().astype(float)
    v = df["volume"].to_numpy().astype(float) if "volume" in df.columns \
        else np.ones(n)

    # Agrégation par bucket (une passe, buckets croissants)
    starts = np.flatnonzero(np.diff(bucket, prepend=bucket[0] - 1))
    ends = np.append(starts[1:], n)
    if len(starts) < 12:
        return empty, {"htf_sec": htf_sec, "n_htf": len(starts), "trend": 0}
    ho = o[starts]
    hc = c[ends - 1]
    hh = np.array([h[s:e].max() for s, e in zip(starts, ends)])
    hl = np.array([l[s:e].min() for s, e in zip(starts, ends)])
    hv = np.array([v[s:e].sum() for s, e in zip(starts, ends)])
    htf_df = pl.DataFrame({"open": ho, "high": hh, "low": hl,
                           "close": hc, "volume": hv})
    res = analyze(htf_df, params)
    trend_htf = res["_trend_arr"]

    # Mapping causal : dernier bucket HTF clôturé au moment où la barre LTF i
    # est terminée (close de la barre = epoch + ltf_sec).
    bucket_end = (bucket[starts] + 1) * htf_sec
    close_times = epoch + ltf_sec
    idx = np.searchsorted(bucket_end, close_times, side="right") - 1
    out = np.zeros(n, dtype=np.int8)
    valid = idx >= 0
    out[valid] = trend_htf[idx[valid]]
    meta = {"htf_sec": int(htf_sec), "n_htf": int(len(starts)),
            "trend": int(out[-1])}
    return out, meta


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
