"""Seuil dynamique — variante *sans ML* (``dynamic_threshold_no_ml``).

Jumeau déterministe et **autonome** de ``ml_dynamic_threshold``. La version ML
entraîne un RandomForest / une régression logistique pour estimer ``proba_up`` =
P(rendement futur > seuil adaptatif à la volatilité), puis ouvre long/short selon
``proba_long`` / ``proba_short`` avec un filtre de régime ADX.

Cette variante supprime tout l'apprentissage et remplace ``proba_up`` par un
**proxy directionnel d'indicateurs** (DI, RSI, MACD/ATR, ROC, distance SMA50,
vélocité RSI, position dans la range, corps de bougie), normalisé dans ``[0,1]``
via une sigmoïde. On conserve :
  * le filtre de régime ADX (``adx_min``) ;
  * les seuils de décision ``proba_long`` / ``proba_short`` et la formule de score ;
  * l'esprit « seuil dynamique » via une porte de volatilité : le mouvement récent
    sur ``lookahead`` bougies doit dépasser ``vol_20·sqrt(lookahead)·vol_multiplier``.

Autonome : aucune dépendance à un autre module de stratégie ni à un modèle. Tous
les indicateurs proviennent de ``app.core.indicators`` (colonnes ``_pre_*`` via
``precompute_df``, repli idempotent si absentes) → coût O(1).
"""

import logging
import math
from typing import Any, Dict, List

import numpy as np
import polars as pl

from app.core.indicators import pre_val, precompute_df
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)

_SECS_TO_TF = {60: "1m", 180: "3m", 300: "5m", 900: "15m",
               1800: "30m", 3600: "1h", 14400: "4h", 86400: "1d"}


def _sig(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _tanh(x: float) -> float:
    return math.tanh(_clip(x, -30.0, 30.0))


def _detect_tf(df: pl.DataFrame) -> str:
    if "time" not in df.columns or len(df) < 3:
        return "unknown"
    try:
        med_s = float(df["time"].tail(64).diff().drop_nulls().dt.total_microseconds().median()) / 1e6
    except Exception:
        return "unknown"
    if med_s <= 0:
        return "unknown"
    closest = min(_SECS_TO_TF, key=lambda k: abs(k - med_s))
    if abs(closest - med_s) <= max(closest * 0.15, 5):
        return _SECS_TO_TF[closest]
    return f"custom_{int(med_s)}s"


def _proxy_p_up(*, pdi, ndi, rsi, macd_hist, atr, roc, c, sma50,
                rsi_vel, range_pos, body, gain: float) -> float:
    di   = _tanh((pdi - ndi) / 20.0)
    r    = _clip((rsi - 50.0) / 30.0, -1.0, 1.0)
    macd = _tanh(macd_hist / (0.5 * atr + 1e-9))
    rocs = _tanh(roc / 5.0)
    dist = _tanh(((c - sma50) / sma50 * 15.0) if sma50 > 0 else 0.0)
    rvel = _tanh(rsi_vel / 15.0)
    rpos = _clip((range_pos - 0.5) * 2.0, -1.0, 1.0)
    bdy  = _tanh(body * 200.0)
    w = (1.0, 0.7, 0.8, 0.8, 0.8, 0.6, 0.6, 0.4)
    s = (di, r, macd, rocs, dist, rvel, rpos, bdy)
    return _sig(gain * (sum(wi * si for wi, si in zip(w, s)) / sum(w)))


class Strategy(BaseStrategy):
    """Seuil dynamique sans ML — proxy directionnel + filtre ADX + porte de volatilité."""

    name = "dynamic_threshold_no_ml"
    timeframes: List[str] = ["5m", "15m", "1h"]

    param_space: Dict[str, List] = {
        "lookahead":      [2, 3, 5],
        "vol_multiplier": [0.4, 0.6, 0.8],
        "adx_min":        [15.0, 20.0, 25.0],
        "proba_long":     [0.55, 0.60, 0.65],
        "proba_short":    [0.35, 0.40, 0.45],
        "p_up_gain":      [1.8, 2.2, 2.8],
    }
    fixed_params: Dict[str, Any] = {"min_bars": 60}

    _DEFAULTS = {
        "lookahead": 3, "vol_multiplier": 0.6, "adx_min": 20.0,
        "proba_long": 0.60, "proba_short": 0.40, "p_up_gain": 2.2, "min_bars": 60,
    }

    def min_bars_required(self, params: dict = None) -> int:
        return 230  # marge pour les EMA/SMA longues de precompute_df

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required():
            return self._none()
        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)

        p = (params or {}).get(self.name, {})
        lookahead      = int(p.get("lookahead",      self._DEFAULTS["lookahead"]))
        vol_multiplier = float(p.get("vol_multiplier", self._DEFAULTS["vol_multiplier"]))
        adx_min        = float(p.get("adx_min",        self._DEFAULTS["adx_min"]))
        proba_long     = float(p.get("proba_long",     self._DEFAULTS["proba_long"]))
        proba_short    = float(p.get("proba_short",    self._DEFAULTS["proba_short"]))
        gain           = float(p.get("p_up_gain",      self._DEFAULTS["p_up_gain"]))

        tf = _detect_tf(df)
        adx_val = float(pre_val(df, "_pre_adx14") or 0.0)
        if adx_val < adx_min:
            return {
                "score": 0.0, "side": "none", "name": self.name,
                "reason": f"Filtre régime : ADX={adx_val:.1f} < {adx_min} [{tf}]",
                "conditions": [f"ADX insuffisant ({adx_val:.1f})"],
                "indicators": {"adx": round(adx_val, 2), "tf": tf, "proxy": True},
            }

        c = float(df["close"][-1] or 0.0)
        if c <= 0:
            return self._none()
        roc = ((c / float(df["close"][-15]) - 1.0) * 100.0) if len(df) > 15 and float(df["close"][-15]) > 0 else 0.0
        proba = _proxy_p_up(
            pdi=float(pre_val(df, "_pre_pdi14") or 0.0),
            ndi=float(pre_val(df, "_pre_ndi14") or 0.0),
            rsi=float(pre_val(df, "_pre_rsi14") or 50.0),
            macd_hist=float(pre_val(df, "_pre_macd_hist") or 0.0),
            atr=float(pre_val(df, "_pre_atr14") or 0.0),
            roc=roc, c=c, sma50=float(pre_val(df, "_pre_sma50") or 0.0),
            rsi_vel=float(pre_val(df, "_pre_rsi_vel6") or 0.0),
            range_pos=float(pre_val(df, "_pre_range_pos20") or 0.5),
            body=float(pre_val(df, "_pre_body") or 0.0),
            gain=gain,
        )

        # Porte « seuil dynamique » : amplitude récente vs volatilité (mime le
        # label ML adaptatif = rendement futur > vol·sqrt(lookahead)·mult).
        cprev = float(df["close"][-1 - lookahead]) if len(df) > lookahead else c
        recent = math.log(max(c, 1e-9) / max(cprev, 1e-9))
        if len(df) > 22:
            cl = df["close"][-21:].to_numpy().astype(float)
            std20 = float(np.std(np.log(np.clip(cl[1:] / cl[:-1], 1e-9, None))))
        else:
            std20 = 0.0
        dyn_thr = std20 * math.sqrt(lookahead) * vol_multiplier

        if proba >= proba_long:
            side = "long"
            score = 0.5 + (proba - proba_long) / max(1.0 - proba_long, 1e-9) * 0.5
            if dyn_thr > 0 and recent < -dyn_thr:
                return self._none()
        elif proba <= proba_short:
            side = "short"
            score = 0.5 + (proba_short - proba) / max(proba_short, 1e-9) * 0.5
            if dyn_thr > 0 and recent > dyn_thr:
                return self._none()
        else:
            return self._none()

        score = round(min(score, 0.99), 3)
        return {
            "score": score, "side": side, "name": self.name,
            "reason": (f"DynThreshold-NoML/{tf} — proxy_up={proba:.3f} "
                       f"(long≥{proba_long}, short≤{proba_short}, ADX={adx_val:.1f})"),
            "conditions": [
                f"Proba hausse (proxy) : {proba:.3f}",
                f"Filtre ADX : {adx_val:.1f} ≥ {adx_min}",
                f"Porte volatilité : |mvt {lookahead}b|={abs(recent):.4f} vs seuil±{dyn_thr:.4f}",
                "Direction issue d'un proxy d'indicateurs (sans modèle ML)",
            ],
            "indicators": {
                "proba_up": round(proba, 4), "adx": round(adx_val, 2), "tf": tf,
                "lookahead": lookahead, "vol_multiplier": vol_multiplier,
                "close": c, "proxy": True,
            },
        }

    @staticmethod
    def _none() -> Dict[str, Any]:
        return {"score": 0.0, "side": "none", "name": "dynamic_threshold_no_ml",
                "reason": "Pas de signal (proxy)", "conditions": [], "indicators": {}}
