"""Stratégie Derivatives Reversion — directionnelle (mean-reversion + dérivés).

Exploite le SEUL edge directionnel robuste mesuré sur OHLCV
(research/directional_hunt.py) : la **mean-reversion ancrée sur la position dans
le range** — bas de range → biais UP (P(up)≈57%, z=7.6), haut de range → DOWN
(z=-7.3), cohérent 1h/4h/1d. On fade les extrêmes UNIQUEMENT en régime range
(jamais contre une tendance forte), avec stop au-delà de l'extrême (gère la
queue de cassure qui fait perdre les fadeurs naïfs).

Couche dérivés (quand les colonnes sont présentes dans le df — mergées en live
par app.core.derivatives.DerivativesStore.align_to_ohlcv) : le funding et le
sentiment confirment ou opposent leur veto au signal de reversion, là où vit le
vrai alpha directionnel crypto :
  • funding_z très positif (longs surchargés) → confirme SHORT / VETO LONG
  • funding_z très négatif (shorts surchargés) → confirme LONG / VETO SHORT
  • taker_z / lsr_z extrêmes → contrarian (sentiment euphorique = reversion)
Dégradation gracieuse : sans colonnes dérivées, la stratégie tourne en OHLCV pur
(cœur range-reversion, backtestable). C'est l'enhancement live qui ajoute l'edge
directionnel des dérivés (≈AUC>0.55 selon la littérature funding-reversion).

Règle, pas ML. Long-biais (dérive haussière de BTC) mais shorts autorisés au
sommet de range hors tendance haussière.
"""

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import precompute_df, pre_val

logger = logging.getLogger(__name__)


def _safe(v, fb=0.0) -> float:
    try:
        f = float(v)
        return fb if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return fb


def _col_last(df: pl.DataFrame, name: str) -> Optional[float]:
    """Dernière valeur d'une colonne dérivée si présente et finie, sinon None."""
    if name not in df.columns or len(df) == 0:
        return None
    v = df[name][-1]
    if v is None:
        return None
    f = float(v)
    return None if (math.isnan(f) or math.isinf(f)) else f


class Strategy(BaseStrategy):
    name = "derivatives_reversion"

    timeframes: List[str] = ["1h", "4h"]

    warmup_bars = 230

    param_space: Dict[str, List] = {
        "range_lookback":  [36, 48, 72],
        "low_thr":         [0.12, 0.15, 0.20],
        "high_thr":        [0.80, 0.85, 0.88],
        "adx_range_max":   [20, 25, 30],
        "rsi_os":          [30, 35, 40],
        "rsi_ob":          [60, 65, 70],
        "sl_atr":          [1.2, 1.5, 2.0],
        "tp_atr":          [1.5, 2.0, 3.0],
        "max_hold":        [6, 10, 16],
        "min_quality":     [0.56, 0.60, 0.64],
        "enable_short":    [True, False],
        "use_derivatives": [True, False],
        "funding_z_extreme": [1.5, 2.0, 2.5],
        "cooldown":        [1, 2, 3],
        "score_threshold": [0.55, 0.60],
    }

    fixed_params: Dict[str, Any] = {}

    _D = {
        "range_lookback":  48,
        "low_thr":         0.15,
        "high_thr":        0.85,
        "adx_range_max":   25.0,     # ne fader QUE si ADX < ce seuil (régime range)
        "rsi_os":          35.0,
        "rsi_ob":          65.0,
        "sl_atr":          1.5,
        "tp_atr":          2.0,
        "max_hold":        10,
        "min_quality":     0.60,
        "enable_short":    True,
        "use_derivatives": True,
        "funding_z_extreme": 2.0,
        "sentiment_z_extreme": 1.5,
        "cooldown":        2,
    }

    def __init__(self):
        self._call: Dict[str, int] = {}
        self._last: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("range_lookback", self._D["range_lookback"])) + 210

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = {**self._D, **((params or {}).get(self.name, {}))}
        sym = symbol or "default"

        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)
        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        cnt = self._call.get(sym, 0) + 1
        self._call[sym] = cnt
        if cnt - self._last.get(sym, -10**9) < int(p["cooldown"]):
            return self._none("Cooldown")

        close = df["close"]; high = df["high"]; low = df["low"]
        c = _safe(close[-1])
        atr = _safe(pre_val(df, "_pre_atr14"))
        if c <= 0 or atr <= 0:
            return self._none("Prix/ATR invalide")
        rsi = _safe(pre_val(df, "_pre_rsi14"), 50.0)
        adx = _safe(pre_val(df, "_pre_adx14"))
        ema50 = _safe(pre_val(df, "_pre_ema50"), c)
        ema200 = _safe(pre_val(df, "_pre_ema200"), c)
        sma200_s = df["_pre_sma200"]
        sma_now = _safe(sma200_s[-1], c)
        sma_prev = _safe(sma200_s[-21] if len(sma200_s) > 20 else sma200_s[0], sma_now)
        slope = sma_now - sma_prev

        # ── Position dans le range (l'edge directionnel mesuré) ───────────────
        L = int(p["range_lookback"])
        hh = _safe(high[-L:].max(), c)
        ll = _safe(low[-L:].min(), c)
        rng = max(hh - ll, 1e-9)
        range_pos = (c - ll) / rng                      # 0 = plancher, 1 = plafond

        ranging = adx < p["adx_range_max"]
        strong_up = c > ema200 and ema50 > ema200 and slope > 0 and adx >= p["adx_range_max"]
        strong_dn = c < ema200 and ema50 < ema200 and slope < 0 and adx >= p["adx_range_max"]

        # ── Couche dérivés (optionnelle, présente seulement en live aligné) ───
        funding_z = _col_last(df, "funding_z") if p["use_derivatives"] else None
        lsr_z = _col_last(df, "lsr_z") if p["use_derivatives"] else None
        taker_z = _col_last(df, "taker_z") if p["use_derivatives"] else None
        oi_chg = _col_last(df, "oi_change_pct") if p["use_derivatives"] else None
        fz_ext = p["funding_z_extreme"]
        sz_ext = p.get("sentiment_z_extreme", 1.5)
        derivs_present = any(v is not None for v in (funding_z, lsr_z, taker_z))

        ctx = {
            "range_pos": round(range_pos, 3), "adx": round(adx, 1), "rsi": round(rsi, 1),
            "ranging": ranging, "regime": ("up" if strong_up else "down" if strong_dn else "range"),
            "funding_z": round(funding_z, 2) if funding_z is not None else None,
            "lsr_z": round(lsr_z, 2) if lsr_z is not None else None,
            "taker_z": round(taker_z, 2) if taker_z is not None else None,
            "oi_change_pct": round(oi_chg, 3) if oi_chg is not None else None,
            "derivs": derivs_present,
        }

        candidates = []

        # ── LONG : fade du bas de range (hors tendance baissière forte) ───────
        if ranging and not strong_dn and range_pos <= p["low_thr"]:
            q = 0.46 + 0.16 * (p["low_thr"] - range_pos) / max(p["low_thr"], 1e-9)
            q += 0.10 if rsi <= p["rsi_os"] else 0.0
            veto = False
            if funding_z is not None and funding_z >= fz_ext:   # longs surchargés → pas de long
                veto = True
            if funding_z is not None and funding_z <= -1.0:     # shorts surchargés → confirme long
                q += 0.10
            if lsr_z is not None and lsr_z <= -sz_ext:
                q += 0.06
            if taker_z is not None and taker_z <= -sz_ext:      # capitulation taker → rebond
                q += 0.05
            if not veto:
                candidates.append(self._mk("long", q, "FADE_RANGE_LOW", p, atr,
                                           derivs_present, p["min_quality"]))

        # ── SHORT : fade du haut de range (hors tendance haussière forte) ─────
        if p["enable_short"] and ranging and not strong_up and range_pos >= p["high_thr"]:
            q = 0.46 + 0.16 * (range_pos - p["high_thr"]) / max(1 - p["high_thr"], 1e-9)
            q += 0.10 if rsi >= p["rsi_ob"] else 0.0
            veto = False
            if funding_z is not None and funding_z <= -fz_ext:  # shorts surchargés → pas de short
                veto = True
            if funding_z is not None and funding_z >= 1.0:      # longs surchargés → confirme short
                q += 0.10
            if lsr_z is not None and lsr_z >= sz_ext:
                q += 0.06
            if taker_z is not None and taker_z >= sz_ext:       # euphorie taker → reversion
                q += 0.05
            if not veto:
                candidates.append(self._mk("short", q, "FADE_RANGE_HIGH", p, atr,
                                           derivs_present, p["min_quality"], size_scale=0.7))

        valid = [x for x in candidates if x is not None]
        if not valid:
            return self._none(f"Pas d'extrême de range fadable ({ctx['regime']})", ctx=ctx)
        best = max(valid, key=lambda x: x["score"])
        best["indicators"] = ctx
        self._last[sym] = cnt
        return best

    def _mk(self, side, q, setup, p, atr, derivs, min_q, size_scale=1.0) -> Optional[dict]:
        score = round(min(max(q, 0.0), 0.95), 3)
        if score < min_q:
            return None
        # Mean-reversion : TP/SL fixes (pas de trailing — on vise le retour à la moyenne).
        size_factor = round(max(0.3, min(size_scale * (0.8 + (0.3 if derivs else 0.0)), 1.5)), 3)
        return {
            "score": score, "side": side, "name": self.name, "atr": atr,
            "sl_atr_mult": float(p["sl_atr"]), "tp_atr_mult": float(p["tp_atr"]),
            "exit_after_bars": int(p["max_hold"]), "disable_trailing": True,
            "size_factor": size_factor, "setup": setup,
            "reason": (f"DerivReversion {side.upper()} | {setup} | q={score:.2f} "
                       f"{'+dérivés' if derivs else 'OHLCV'} TP={p['tp_atr']:.1f}/SL={p['sl_atr']:.1f}ATR"),
            "conditions": [
                f"Fade extrême de range ({setup}) en régime range",
                f"Edge directionnel : position-dans-le-range (mesuré z≈7.6)",
                (f"Confirmation dérivés (funding/sentiment)" if derivs
                 else "OHLCV pur (dérivés absents → fallback)"),
                f"TP {p['tp_atr']:.1f}×ATR / SL {p['sl_atr']:.1f}×ATR (gère la cassure)",
            ],
        }

    def _none(self, reason: str = "", ctx: dict = None) -> dict:
        d = {"score": 0, "side": "none", "name": self.name, "reason": reason}
        if ctx is not None:
            d["indicators"] = ctx
        return d
