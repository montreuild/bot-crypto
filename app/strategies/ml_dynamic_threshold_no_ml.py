"""Seuil dynamique — variante *sans ML* (``ml_dynamic_threshold_no_ml``).

Équivalent à base d'indicateurs de :mod:`app.strategies.ml_dynamic_threshold`.
La version ML entraîne un RandomForest / une régression logistique (random search
+ TimeSeriesSplit) pour estimer ``proba_up`` = P(le rendement futur dépasse un
seuil adaptatif à la volatilité), puis ouvre long/short selon
``proba_long`` / ``proba_short`` avec un filtre de régime ADX.

Cette variante supprime tout l'apprentissage (pas de fit, pas de random search,
pas de persistance de modèle) et remplace ``proba_up`` par un **proxy
directionnel déterministe** combinant les mêmes familles d'indicateurs que celles
vues par le modèle (RSI, momentum 5/20, croisements d'EMA, MACD normalisé,
distance EMA/VWAP), normalisé dans ``[0, 1]`` via une sigmoïde. On conserve à
l'identique :

  * le **filtre de régime ADX** (``adx_min``) ;
  * les **seuils de décision** ``proba_long`` / ``proba_short`` et la formule de
    score ;
  * l'esprit « seuil dynamique » via une porte de confirmation : le mouvement
    récent sur ``lookahead`` bougies doit dépasser un seuil proportionnel à la
    volatilité (``vol_multiplier``) pour valider l'entrée.

Le pipeline de features (``compute_features``) et la détection de timeframe sont
importés de ``ml_dynamic_threshold`` ; le cache feature_store ``ml_dyn_threshold``
v1 reste partagé.
"""

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import adx as _adx
from app.strategies.ml_dynamic_threshold import compute_features, _detect_tf
from app.strategies._no_ml_proxy import _f, _sigmoid, _tanh, _clip

logger = logging.getLogger(__name__)


# ── Coefficients du proxy directionnel (features ml_dynamic_threshold) ────────
_DIR_DEFAULTS: Dict[str, float] = {
    "mom20_scale":  20.0,
    "mom5_scale":   40.0,
    "cross_scale":  300.0,
    "macd_scale":   500.0,
    "ema_scale":    30.0,
    "vwap_scale":   30.0,
    "w_rsi":    0.8,
    "w_mom20":  1.0,
    "w_mom5":   0.7,
    "w_cross":  0.9,
    "w_macd":   0.8,
    "w_ema":    0.7,
    "w_vwap":   0.6,
    "gain":     2.2,
}


def _proba_up_proxy(row: Any, cfg: Dict[str, float]) -> float:
    """Proxy directionnel dans ``[0, 1]`` depuis une ligne de features ml_dyn."""
    rsi   = _clip((_f(row, "rsi_14", 0.5) - 0.5) * 2.0, -1.0, 1.0)   # rsi_14 ∈ [0,1]
    mom20 = _tanh(_f(row, "mom_20") * cfg["mom20_scale"])
    mom5  = _tanh(_f(row, "mom_5") * cfg["mom5_scale"])
    cross = _tanh(_f(row, "cross_8_21") * cfg["cross_scale"])
    macd  = _tanh(_f(row, "macd_hist_norm") * cfg["macd_scale"])
    ema   = _tanh(_f(row, "ema21_rel") * cfg["ema_scale"])
    vwap  = _tanh(_f(row, "vwap_dist_20") * cfg["vwap_scale"])

    weights = (cfg["w_rsi"], cfg["w_mom20"], cfg["w_mom5"], cfg["w_cross"],
               cfg["w_macd"], cfg["w_ema"], cfg["w_vwap"])
    signals = (rsi, mom20, mom5, cross, macd, ema, vwap)
    wsum = sum(weights)
    if wsum <= 0:
        return 0.5
    s = sum(w * x for w, x in zip(weights, signals)) / wsum
    return _sigmoid(cfg["gain"] * s)


def _resolve_dir_cfg(p: dict) -> Dict[str, float]:
    cfg = dict(_DIR_DEFAULTS)
    if p:
        for k, v in p.items():
            if k in cfg and v is not None:
                try:
                    cfg[k] = float(v)
                except (TypeError, ValueError):
                    continue
    return cfg


class Strategy(BaseStrategy):
    """Seuil dynamique sans ML — proxy directionnel + filtre ADX + porte de volatilité."""

    name = "ml_dynamic_threshold_no_ml"

    timeframes: List[str] = ["5m", "15m", "1h"]

    param_space: Dict[str, List] = {
        "lookahead":      [2, 3, 5],
        "vol_multiplier": [0.4, 0.6, 0.8],
        "adx_min":        [15.0, 20.0, 25.0],
        "proba_long":     [0.55, 0.60, 0.65],
        "proba_short":    [0.35, 0.40, 0.45],
        "gain":           [1.8, 2.2, 2.8],
    }
    fixed_params: Dict[str, Any] = {
        "min_bars": 150,
    }

    _DEFAULTS = {
        "lookahead":      3,
        "vol_multiplier": 0.6,
        "adx_min":        20.0,
        "proba_long":     0.60,
        "proba_short":    0.40,
        "min_bars":       150,
    }

    def __init__(self):
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("min_bars", self._DEFAULTS["min_bars"])) + 30

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="ml_dyn_threshold", version="1",
                builder=lambda w: compute_features(w),
                in_kind="polars", out_kind="polars", include_time=False)
            if feats is not None and len(feats) > 0:
                self._bt_features = feats
                self._bt_features_len = len(df)
                logger.info(
                    f"[DynThr-NoML] backtest : features pré-calculées sur "
                    f"{self._bt_features_len} bougies ({len(feats.columns)} colonnes)"
                )
        except Exception as e:
            logger.warning(f"[DynThr-NoML] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    def _features(self, df: pl.DataFrame) -> pl.DataFrame:
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            return self._bt_features.head(len(df))
        return compute_features(df)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})
        lookahead      = int(p.get("lookahead",      self._DEFAULTS["lookahead"]))
        vol_multiplier = float(p.get("vol_multiplier", self._DEFAULTS["vol_multiplier"]))
        adx_min        = float(p.get("adx_min",        self._DEFAULTS["adx_min"]))
        proba_long     = float(p.get("proba_long",     self._DEFAULTS["proba_long"]))
        proba_short    = float(p.get("proba_short",    self._DEFAULTS["proba_short"]))

        if df is None or len(df) < self.min_bars_required(params):
            return self._none()

        tf = _detect_tf(df)
        feats = self._features(df)
        if feats is None or len(feats) == 0:
            return self._none()

        # Filtre régime ADX (identique à la version ML).
        try:
            adx_val = float(_adx(df, 14)[0][-1])
        except Exception:
            adx_val = 0.0
        if not math.isfinite(adx_val):
            adx_val = 0.0
        if adx_val < adx_min:
            return {
                "score":      0.0,
                "side":       "none",
                "name":       self.name,
                "reason":     f"Filtre régime : ADX={adx_val:.1f} < {adx_min} [{tf}]",
                "conditions": [f"ADX insuffisant ({adx_val:.1f})"],
                "indicators": {"adx": round(adx_val, 2), "tf": tf, "proxy": True},
            }

        cfg  = _resolve_dir_cfg(p)
        last = feats.row(-1, named=True)
        proba = _proba_up_proxy(last, cfg)

        # Porte « seuil dynamique » : le mouvement récent sur `lookahead` bougies
        # doit dépasser un seuil proportionnel à la volatilité (mime le label ML
        # adaptatif). Sinon le signal est jugé trop ténu → pas de trade.
        close = df["close"]
        if len(close) > lookahead + 21:
            log_ret = (close / close.shift(1).clip(lower_bound=1e-9)).log(math.e)
            vol_20  = float(log_ret.tail(20).std() or 0.0)
            recent  = float(
                math.log(max(float(close[-1]), 1e-9) /
                         max(float(close[-1 - lookahead]), 1e-9))
            )
            dyn_thr = vol_20 * math.sqrt(lookahead) * vol_multiplier
        else:
            recent, dyn_thr = 0.0, 0.0

        if proba >= proba_long:
            side  = "long"
            score = 0.5 + (proba - proba_long) / max(1.0 - proba_long, 1e-9) * 0.5
            # Confirmation : le marché ne doit pas être en fort excès baissier récent.
            if dyn_thr > 0 and recent < -dyn_thr:
                return self._none()
        elif proba <= proba_short:
            side  = "short"
            score = 0.5 + (proba_short - proba) / max(proba_short, 1e-9) * 0.5
            if dyn_thr > 0 and recent > dyn_thr:
                return self._none()
        else:
            return self._none()

        score = round(min(score, 0.99), 3)
        close_v = float(close[-1])

        return {
            "score":      score,
            "side":       side,
            "name":       self.name,
            "reason":     (
                f"DynThreshold-NoML/{tf} — proxy_up={proba:.3f} "
                f"(long≥{proba_long}, short≤{proba_short}, ADX={adx_val:.1f})"
            ),
            "conditions": [
                f"Proba hausse (proxy) : {proba:.3f}",
                f"Filtre ADX : {adx_val:.1f} ≥ {adx_min}",
                f"Porte volatilité : |mvt {lookahead}b|={abs(recent):.4f} vs seuil±{dyn_thr:.4f}",
                "Direction issue d'un proxy d'indicateurs (sans modèle ML)",
            ],
            "indicators": {
                "proba_up":       round(proba, 4),
                "adx":            round(adx_val, 2),
                "tf":             tf,
                "lookahead":      lookahead,
                "vol_multiplier": vol_multiplier,
                "close":          close_v,
                "proxy":          True,
            },
        }

    @staticmethod
    def _none() -> Dict[str, Any]:
        return {
            "score":      0.0,
            "side":       "none",
            "name":       "ml_dynamic_threshold_no_ml",
            "reason":     "Pas de signal (proxy)",
            "conditions": [],
            "indicators": {},
        }
