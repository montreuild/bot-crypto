"""Primitives partagées du moteur SMC (V4-L / ARCH-14) : défauts de
paramètres, ATR Wilder, résultat vide et petits helpers de la passe
principale (clustering de pools, bougie opposée, flags OB)."""
import logging
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
