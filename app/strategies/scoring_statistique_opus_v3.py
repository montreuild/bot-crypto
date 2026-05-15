"""Stratégie Scoring Statistique Opus V3 — LightGBM sur les mêmes features que V1/V2.

Différence clé par rapport à V1/V2 :
- V1/V2 : formule heuristique linéaire (approximation du rapport)
- V3 : modèle LightGBM entraîné inline sur les données réelles du backtest

Protocole d'entraînement :
  - Warmup : les 500 premières barres servent à accumuler un jeu d'entraînement
  - Features : 14 colonnes _pre_* déjà calculées par precompute_df → O(1)
  - Label : direction de la prochaine bougie (close[i+1] > close[i])
  - Filtre régime : on entraîne uniquement sur les barres en Trend Down/Up
                    (régimes cohérents, pas Range/Choppy qui sont moins prédictibles)
  - Réentraînement : toutes les `retrain_every` barres pour couvrir les changements
                     de régime (walk-forward léger)

Performances attendues :
  - AUC ≈ 0.60-0.70 (dataset limité vs 50k du rapport)
  - WR > 45% sur les barres filtrées (vs 37% heuristique)
  - Réentraînement ~300ms/cycle (LightGBM rapide sur <1000 barres)
"""

import logging
import math
import os
import threading
from typing import Dict, Any, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategyML
from app.core.indicators import pre_val

logger = logging.getLogger(__name__)

REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3

REGIME_LABELS = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
}

# Features utilisées (même ensemble que V1/V2)
_FEATURE_COLS = [
    "_pre_atr_pct_r",
    "_pre_volstd20_r",
    "_pre_range_r",
    "_pre_body_abs_r",
    "_pre_upper_wick",
    "_pre_lower_wick",
    "_pre_body",
    "_pre_rsi14",
    "_pre_rsi_vel6",
    "_pre_rsi_vel12",
    "_pre_rsi_accel",
    "_pre_macd_hist_d1",
    "_pre_range_pos20",
    "_pre_volratio20",
    "_pre_adx14",
]


def _detect_regime(adx: float, sma20: float, sma50: float,
                   sma100: float, sma200: float,
                   pdi: float = 0.0, ndi: float = 0.0,
                   adx_threshold: float = 20.0) -> int:
    if adx < adx_threshold:
        return REGIME_RANGE
    if sma20 > sma50 > sma100 > sma200 and pdi > ndi:
        return REGIME_TREND_UP
    if sma20 < sma50 < sma100 < sma200 and ndi > pdi:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _build_feature_matrix(df: pl.DataFrame) -> Optional[np.ndarray]:
    """Extrait les features disponibles en np.ndarray. Retourne None si colonnes manquantes."""
    missing = [c for c in _FEATURE_COLS if c not in df.columns]
    if missing:
        return None
    try:
        arr = df.select(_FEATURE_COLS).to_numpy().astype(np.float32)
        # Remplace NaN/Inf
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        return arr
    except Exception:
        return None


class Strategy(BaseStrategyML):
    name      = "scoring_statistique_opus_v3"
    model_dir = "models"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":  [15, 20, 25],
        "score_threshold": [0.50, 0.55, 0.60],
        "min_prob":        [0.52, 0.55, 0.58, 0.60],
        "retrain_every":   [200, 300, 500],
        "warmup_bars":     [300, 400, 500],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._models:         Dict[str, Any] = {}   # tf_key → lgbm.Booster
        self._scalers:        Dict[str, Any] = {}   # tf_key → sklearn.StandardScaler
        self._trained_tfs:    set = set()           # timeframes entraînés (interface auto_optimizer)
        self._lock            = threading.Lock()
        self._call_cnt:       Dict[str, int] = {}
        self._last_retrain:   Dict[str, int] = {}
        self._managed_externally = False
        # Compatibilité API ML (routes/ml.py lit _best_auc et _best_auc_per_tf)
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("warmup_bars", 300)) + 15

    @property
    def is_trained(self) -> bool:
        return bool(self._trained_tfs)

    @property
    def managed_externally(self) -> bool:
        return self._managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool):
        self._managed_externally = v

    def reset_model(self) -> None:
        with self._lock:
            self._models.clear()
            self._scalers.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._last_retrain.clear()
            self._managed_externally = False
            self._best_auc = 0.0

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        """Entraîne le modèle LightGBM sur df (interface BaseStrategyML publique)."""
        p             = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold", 20.0))
        self._fit(df, adx_threshold=adx_threshold)

    def _fit(self, df: pl.DataFrame, timeframe: str = "", adx_threshold: float = 20.0) -> None:
        """Interface attendue par auto_optimizer (_fit + _trained_tfs)."""
        tf_key = timeframe or "default"
        self._train(df, tf_key, adx_threshold)

    def _train(self, df: pl.DataFrame, tf_key: str, adx_threshold: float) -> bool:
        """Entraîne ou réentraîne le modèle sur df. Retourne True si réussi."""
        try:
            import lightgbm as lgb
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            logger.error("[V3] lightgbm ou scikit-learn non installé — pip install lightgbm scikit-learn")
            return False

        X = _build_feature_matrix(df)
        if X is None or len(X) < 100:
            return False

        # Label : close[i+1] > close[i] → direction haussière
        close = df["close"].to_numpy()
        y = (close[1:] > close[:-1]).astype(np.int8)
        X_train = X[:-1]  # dernier bar pas encore labelisé

        # Filtre régime : entraîner sur barres Trend Down / Trend Up (signal plus prédictible)
        if all(c in df.columns for c in ("_pre_adx14", "_pre_sma20", "_pre_sma50",
                                          "_pre_sma100", "_pre_sma200",
                                          "_pre_pdi14", "_pre_ndi14")):
            adx_arr  = df["_pre_adx14"].to_numpy()
            sma20    = df["_pre_sma20"].to_numpy()
            sma50    = df["_pre_sma50"].to_numpy()
            sma100   = df["_pre_sma100"].to_numpy()
            sma200   = df["_pre_sma200"].to_numpy()
            pdi_arr  = df["_pre_pdi14"].to_numpy()
            ndi_arr  = df["_pre_ndi14"].to_numpy()
            regimes  = np.array([
                _detect_regime(adx_arr[i], sma20[i], sma50[i], sma100[i], sma200[i],
                               pdi_arr[i], ndi_arr[i], adx_threshold)
                for i in range(len(adx_arr) - 1)  # -1 car y est décalé
            ])
            mask = (regimes == REGIME_TREND_DN) | (regimes == REGIME_TREND_UP)
        else:
            mask = np.ones(len(y), dtype=bool)

        if mask.sum() < 50:
            logger.debug(f"[V3] {tf_key} : pas assez de barres tendance ({mask.sum()})")
            mask = np.ones(len(y), dtype=bool)  # fallback : toutes les barres

        X_f = X_train[mask]
        y_f = y[mask]

        if len(X_f) < 50:
            return False

        scaler  = StandardScaler()
        X_s     = scaler.fit_transform(X_f)

        # Pondération des classes pour équilibrer (souvent 50/50 mais pas toujours)
        n_pos   = y_f.sum()
        n_neg   = len(y_f) - n_pos
        scale_pos = float(n_neg) / max(n_pos, 1)

        train_data = lgb.Dataset(X_s, label=y_f)
        params_lgb = {
            "objective":        "binary",
            "metric":           "auc",
            "n_estimators":     200,
            "num_leaves":       15,
            "learning_rate":    0.05,
            "min_child_samples": 10,
            "subsample":        0.8,
            "colsample_bytree": 0.8,
            "scale_pos_weight": scale_pos,
            "verbosity":        -1,
            "n_jobs":           1,
        }

        callbacks = [lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)]

        n = len(X_s)
        split = int(n * 0.8)
        if split < 30:
            split = n
            valid_data = train_data
        else:
            train_data = lgb.Dataset(X_s[:split], label=y_f[:split])
            valid_data = lgb.Dataset(X_s[split:], label=y_f[split:], reference=train_data)

        try:
            booster = lgb.train(
                params_lgb,
                train_data,
                num_boost_round=200,
                valid_sets=[valid_data],
                callbacks=callbacks,
            )
        except Exception as e:
            logger.warning(f"[V3] Entraînement échoué : {e}")
            return False

        auc = booster.best_score.get("valid_0", {}).get("auc", 0.0)
        with self._lock:
            self._models[tf_key]           = booster
            self._scalers[tf_key]          = scaler
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key]  = auc
            self._best_auc                 = auc  # reflète le dernier TF entraîné

        logger.info(f"[V3] {tf_key} entraîné : {len(X_f)} barres | AUC={auc:.3f}")
        return True

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    def save_model(self, path: str) -> None:
        import joblib
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        with self._lock:
            booster = self._models.get(tf_key)
            scaler  = self._scalers.get(tf_key)
            auc     = self._best_auc_per_tf.get(tf_key, 0.0)
        if booster is None:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump({"booster": booster, "scaler": scaler,
                     "feature_cols": _FEATURE_COLS, "best_auc": auc}, path)

    def load_model(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        try:
            import joblib
            data = joblib.load(path)
            auc  = float(data.get("best_auc", 0.0))
            with self._lock:
                self._models[tf_key]          = data["booster"]
                self._scalers[tf_key]         = data["scaler"]
                self._trained_tfs.add(tf_key)
                self._best_auc_per_tf[tf_key] = auc
                self._best_auc                = auc
            logger.info(f"[V3] Modèle chargé depuis {path} (AUC={auc:.3f})")
            return True
        except Exception as e:
            logger.warning(f"[V3] Chargement échoué {path}: {e}")
            return False

    # ── score() ───────────────────────────────────────────────────────────────

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p             = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold",  20.0))
        min_prob      = float(p.get("min_prob",        0.55))
        retrain_every = int(p.get("retrain_every",     300))
        warmup_bars   = int(p.get("warmup_bars",       300))

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        sym     = symbol or "default"
        cnt     = self._call_cnt.get(sym, 0) + 1
        self._call_cnt[sym] = cnt

        # Clé tf = sym (chaque symbole a son propre modèle)
        tf_key = sym

        # ── Entraînement initial ou réentraînement périodique ──────────────
        last = self._last_retrain.get(tf_key, 0)
        need_train = (tf_key not in self._trained_tfs) or (cnt - last >= retrain_every)

        if need_train and not self._managed_externally:
            # Entraîner sur les `warmup_bars * 2` dernières barres disponibles
            n_train = min(len(df) - 1, warmup_bars * 2)
            train_df = df.slice(len(df) - n_train - 1, n_train)
            ok = self._train(train_df, tf_key, adx_threshold)
            if ok:
                self._last_retrain[tf_key] = cnt

        if tf_key not in self._trained_tfs:
            return self._none("Modèle non encore entraîné")

        # ── Régime courant ─────────────────────────────────────────────────
        c_now  = float(df["close"][-1] or 0.0)
        atr_v  = pre_val(df, "_pre_atr14")  or 0.0
        adx_v  = pre_val(df, "_pre_adx14")  or 0.0
        sma20  = pre_val(df, "_pre_sma20")  or pre_val(df, "_pre_ema20")  or c_now
        sma50  = pre_val(df, "_pre_sma50")  or pre_val(df, "_pre_ema50")  or c_now
        sma100 = pre_val(df, "_pre_sma100") or c_now
        sma200 = pre_val(df, "_pre_sma200") or pre_val(df, "_pre_ema200") or c_now
        pdi    = pre_val(df, "_pre_pdi14")  or 0.0
        ndi    = pre_val(df, "_pre_ndi14")  or 0.0

        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime     = _detect_regime(adx_v, sma20, sma50, sma100, sma200, pdi, ndi, adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        # ── Prédiction LightGBM ────────────────────────────────────────────
        X_last = _build_feature_matrix(df.slice(len(df) - 1, 1))
        if X_last is None:
            return self._none("Features manquantes")

        with self._lock:
            booster = self._models.get(tf_key)
            scaler  = self._scalers.get(tf_key)

        if booster is None or scaler is None:
            return self._none("Modèle indisponible")

        try:
            X_scaled = scaler.transform(X_last)
            prob_up  = float(booster.predict(X_scaled)[0])
        except Exception as e:
            logger.warning(f"[V3] Prédiction échouée : {e}")
            return self._none("Erreur prédiction")

        # ── Filtre régime : pas de signal en Range (trop bruité) ──────────
        if regime == REGIME_RANGE:
            return self._none(
                f"Range (ADX={adx_v:.0f} < {adx_threshold:.0f})",
                prob_up=prob_up, regime=regime,
            )

        # ── Décision ──────────────────────────────────────────────────────
        dist_from_50 = abs(prob_up - 0.5)
        if dist_from_50 < (min_prob - 0.5):
            return self._none(
                f"prob={prob_up:.3f} trop proche de 0.5 | {regime_lbl}",
                prob_up=prob_up, regime=regime,
            )

        side = "long" if prob_up > 0.5 else "short"

        # Taille selon régime et confiance du modèle
        if regime == REGIME_TREND_DN and side == "short":
            size_fac = 1.0
        elif regime == REGIME_TREND_UP and side == "long":
            size_fac = 0.75
        else:
            size_fac = 0.5  # contre-tendance ou Choppy

        # Score = distance à 0.5, normalisé vers [0.50, 0.94]
        score = 0.50 + min(dist_from_50 * 2.2, 0.44)
        score = round(score * (size_fac * 0.4 + 0.6), 3)

        stop = (c_now - 1.5 * atr_v) if side == "long" else (c_now + 1.5 * atr_v)

        rsi_v    = pre_val(df, "_pre_rsi14")     or 50.0
        body_v   = pre_val(df, "_pre_body")       or 0.0
        atr_r    = pre_val(df, "_pre_atr_pct_r") or 1.0

        return {
            "score":      score,
            "side":       side,
            "name":       self.name,
            "atr":        atr_v,
            "stop_hint":  round(stop, 2),
            "prob_up":    round(prob_up, 4),
            "regime":     regime,
            "regime_lbl": regime_lbl,
            "indicators": {
                "adx":        round(adx_v, 1),
                "rsi":        round(rsi_v, 1),
                "body":       round(body_v, 4),
                "atr_r":      round(atr_r, 3),
                "prob_up":    round(prob_up, 4),
                "size_factor": size_fac,
                "trained_bars": self._last_retrain.get(tf_key, 0),
            },
            "conditions": [
                f"Régime : {regime_lbl} (ADX={adx_v:.0f})",
                f"LightGBM P(hausse) = {prob_up:.3f} → {side.upper()}",
                f"dist_50 = {dist_from_50:.3f} ≥ {min_prob - 0.5:.3f} ✓",
                f"Taille {size_fac:.0%}",
            ],
            "reason": (
                f"OpusV3-LGBM {side.upper()} | {regime_lbl} | "
                f"prob={prob_up:.3f} ADX={adx_v:.0f} RSI={rsi_v:.0f}"
            ),
        }

    def _none(self, reason: str = "", prob_up: float = 0.5, regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "prob_up":    round(prob_up, 4),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
