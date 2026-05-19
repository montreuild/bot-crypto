"""Stratégie Opus Stat Retrained V4 — pipeline V4 entraîné inline.

Variante de ``opus_stat_pretrained_v4`` qui **entraîne son propre modèle**
(comme n'importe quelle autre stratégie ML du bot) au lieu de charger le pkl
embarqué. Reproduit fidèlement la méthodologie qui a produit ``v4_models.pkl``
dans le rapport V4 :

  1. Construction des features via le ``_FeatureBuilder`` V4 (~100 indicateurs
     × lags 1/3/6/12) — réutilisé via import depuis ``opus_stat_pretrained_v4``.
  2. Labellisation :
       - amplitude : ``|return_t+1| > quantile(amp_top_pct)`` (event detection)
       - direction : ``return_t+1 > 0``
  3. Split chronologique 80/20.
  4. Deux LightGBM séparés (amp + dir) entraînés en pure mode booster
     ``binary``, métrique ``auc``, early-stopping 40 rounds.
  5. Imputation des NaN par les médianes du **train** (stockées avec le pkl).
  6. Persistance par TF via ``save_model`` / ``load_model`` (un fichier par TF).
  7. Walk-forward : réentraînement périodique inline géré par le
     ``MLStrategyTrainer`` (intervalle ``retrain_interval_h``).

Tout le reste (filtre temporel, multiplicateurs SL/TP par régime, sizing
demi-Kelly, routing régime, désactivation trailing, TP fixe…) est identique
à la version pré-entraînée — la stratégie y délègue via héritage léger.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import polars as pl

from app.engine.engine import BaseStrategyML
from app.core.indicators import pre_val
from app.strategies.opus_stat_pretrained_v4 import (
    _FeatureBuilder,
    _detect_timeframe,
    _last_bar_hour_dow,
    _to_pandas_window,
    REGIME_RANGE, REGIME_TREND_UP, REGIME_TREND_DN, REGIME_CHOPPY,
    REGIME_LABELS,
)

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h")

# Colonnes du DataFrame produit par FeatureBuilder qu'on **exclut** des features.
# (raw OHLCV + dérivés non-stationnaires utilisés uniquement pour le calcul)
_EXCLUDED_COLS = {
    "time", "open", "high", "low", "close", "volume",
    "log_ret", "OBV",
    # raw MM (on garde leurs dist_/slope_) — non-stationnaires
    "SMA_20", "SMA_50", "SMA_100", "SMA_200",
    "EMA_20", "EMA_50", "EMA_100", "EMA_200",
    "EMA_9", "EMA_21",
    "high_20", "low_20", "high_50", "low_50", "high_100", "low_100",
    # ATR_14 reste car déjà normalisé via ATR_pct
    "ATR_14",
}


def _select_feature_columns(features_df: pd.DataFrame) -> List[str]:
    """Retourne la liste des colonnes utilisables comme features (numériques,
    stationnaires, hors OHLCV/MM brutes)."""
    cols: List[str] = []
    for c in features_df.columns:
        if c in _EXCLUDED_COLS:
            continue
        s = features_df[c]
        if pd.api.types.is_numeric_dtype(s):
            cols.append(c)
    return cols


def _impute_with_medians(X: pd.DataFrame, medians: Dict[str, float]) -> np.ndarray:
    """Remplace NaN/Inf par les médianes du train (fallback 0.0)."""
    arr = X.to_numpy(dtype=np.float64, copy=True)
    if not np.isfinite(arr).all():
        for j, col in enumerate(X.columns):
            mask = ~np.isfinite(arr[:, j])
            if mask.any():
                arr[mask, j] = float(medians.get(col, 0.0))
    return arr


class Strategy(BaseStrategyML):
    """Stratégie ML — pipeline V4 entraîné inline (modèles persistés par TF)."""

    name      = "opus_stat_retrained_v4"
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Paramètres optimisables (seuils + hyperparams d'entraînement V4).
    param_space: Dict[str, Any] = {
        # Seuils de décision (identiques à la version pré-entraînée)
        "thresh_amp_td":    [0.40, 0.45, 0.50, 0.55, 0.60],
        "thresh_dir_td":    [0.05, 0.08, 0.10, 0.12, 0.15],
        "thresh_amp_other": [0.50, 0.55, 0.60, 0.65, 0.70],
        "thresh_dir_other": [0.10, 0.13, 0.15, 0.18, 0.20],
        "sl_atr_mult_td":    [1.5, 1.75, 1.8, 2.0, 2.25],
        "sl_atr_mult_other": [1.0, 1.25, 1.5, 1.75],
        "tp_atr_mult_td":    [1.0, 1.2, 1.4, 1.6],
        "tp_atr_mult_other": [0.8, 1.0, 1.2, 1.4],
        "max_hold_bars":     [1, 2, 4, 6, 8],
        # Hyperparams d'entraînement V4 (cf. méthodologie 13_v4_models.py)
        "amp_top_pct":     [0.25, 0.30, 0.35],   # quantile cut-off pour event detection
        "warmup_bars":     [1000, 2000, 3000],
        "retrain_every":   [500, 800, 1500],
        "n_estimators":    [200, 300, 500],
        "num_leaves":      [15, 31, 63],
        "learning_rate":   [0.02, 0.03, 0.05],
    }
    fixed_params: Dict[str, Any] = {}

    # Défauts V4 — identiques à la version pré-entraînée (sauf gestion modèle).
    _DEFAULTS = {
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),
        "active_days":         [0, 1, 2, 3, 4],
        "use_fixed_tp":        True,
        "disable_trailing":    True,
        "use_exit_after_bars": False,
        "sl_atr_mult_td":      1.8,
        "sl_atr_mult_other":   1.5,
        "tp_atr_mult_td":      1.2,
        "tp_atr_mult_other":   1.0,
        "use_kelly_sizing":    True,
        "kelly_size_other":    0.5,
        "min_confidence":      0.2,
        # Entraînement
        "amp_top_pct":         0.30,
        "warmup_bars":         2000,
        "retrain_every":       800,
        "n_estimators":        500,
        "num_leaves":          31,
        "learning_rate":       0.03,
    }

    # Réutilise le pipeline de features du pré-entraîné.
    _FEATURE_BUILDER = _FeatureBuilder()

    # Intervalle de réentraînement par défaut côté MLStrategyTrainer.
    retrain_interval_h: int = 6

    def __init__(self):
        self._lock = threading.Lock()
        self._amp_models:  Dict[str, Any]                = {}   # tf_key → booster
        self._dir_models:  Dict[str, Any]                = {}
        self._feature_cols: Dict[str, List[str]]         = {}
        self._medians:     Dict[str, Dict[str, float]]   = {}
        self._trained_tfs: set                           = set()
        self._managed_externally:    bool                = False
        # Walk-forward : compteur d'appels & dernier retrain (compat avec v4 inline)
        self._call_cnt:    Dict[str, int]                = {}
        self._last_retrain:Dict[str, int]                = {}
        # API ML (lue par MLStrategyTrainer pour les logs)
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        self._cancel_event = None

    # ── Cycle de vie ML ────────────────────────────────────────────────────
    @property
    def is_trained(self) -> bool:
        return bool(self._trained_tfs)

    @property
    def managed_externally(self) -> bool:
        return self._managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self._managed_externally = bool(v)

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        warmup = int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))
        # Marge FeatureBuilder = 210 + max_lag(12) + buffer
        return max(230, warmup + 30)

    def reset_model(self) -> None:
        with self._lock:
            self._amp_models.clear()
            self._dir_models.clear()
            self._feature_cols.clear()
            self._medians.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._train_meta.clear()
            self._last_retrain.clear()
            self._managed_externally = False
            self._best_auc = 0.0

    # ── Persistance par TF ─────────────────────────────────────────────────
    @staticmethod
    def _tf_from_path(path: str) -> str:
        """Extrait le TF du nom de fichier ``{name}_{tf}.pkl`` (convention
        MLStrategyTrainer._model_path)."""
        base = os.path.splitext(os.path.basename(path))[0]
        return base.rsplit("_", 1)[-1]

    def save_model(self, path: str) -> None:
        import joblib
        tf_key = self._tf_from_path(path)
        with self._lock:
            amp_m = self._amp_models.get(tf_key)
            dir_m = self._dir_models.get(tf_key)
            feats = self._feature_cols.get(tf_key)
            meds  = self._medians.get(tf_key)
            auc   = self._best_auc_per_tf.get(tf_key, 0.0)
            meta  = self._train_meta.get(tf_key, {})
        if amp_m is None or dir_m is None:
            logger.debug(f"[OpusV4-RT] save_model({path}) : pas de modèle pour {tf_key}")
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        joblib.dump({
            "amp_model":  amp_m,
            "dir_model":  dir_m,
            "features":   feats,
            "medians":    meds,
            "best_auc":   auc,
            "train_meta": meta,
            "tf":         tf_key,
        }, path)
        logger.info(f"[OpusV4-RT] Modèles sauvegardés → {path} (AUC={auc:.3f})")

    def load_model(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            import joblib
            data = joblib.load(path)
        except Exception as e:
            logger.warning(f"[OpusV4-RT] Chargement échoué {path}: {e}")
            return False
        tf_key = self._tf_from_path(path)
        with self._lock:
            self._amp_models[tf_key]      = data["amp_model"]
            self._dir_models[tf_key]      = data["dir_model"]
            self._feature_cols[tf_key]    = list(data.get("features") or [])
            self._medians[tf_key]         = dict(data.get("medians") or {})
            self._best_auc_per_tf[tf_key] = float(data.get("best_auc", 0.0))
            self._train_meta[tf_key]      = dict(data.get("train_meta") or {})
            self._trained_tfs.add(tf_key)
            self._best_auc = max(self._best_auc, self._best_auc_per_tf[tf_key])
        logger.info(f"[OpusV4-RT] Modèle {tf_key} chargé depuis {path} (AUC={self._best_auc_per_tf[tf_key]:.3f})")
        return True

    # ── Entraînement (reproduit la méthodologie V4) ────────────────────────
    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        """Détecte le TF de ``df``, construit les features V4, entraîne deux
        boosters LightGBM (amplitude + direction) avec split chronologique 80/20.
        """
        tf = _detect_timeframe(df) or "default"
        p  = (params or {}).get(self.name, {})
        self._train(df, tf_key=tf, params=p)

    def _train(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        try:
            import lightgbm as lgb
        except ImportError:
            logger.error("[OpusV4-RT] lightgbm requis : pip install lightgbm")
            return False

        amp_top_pct   = float(params.get("amp_top_pct",   self._DEFAULTS["amp_top_pct"]))
        n_estimators  = int(params.get("n_estimators",    self._DEFAULTS["n_estimators"]))
        num_leaves    = int(params.get("num_leaves",      self._DEFAULTS["num_leaves"]))
        learning_rate = float(params.get("learning_rate", self._DEFAULTS["learning_rate"]))

        # 1. Conversion polars→pandas + construction des features V4
        n_keep = max(2200, len(df))   # garde toute la fenêtre pour le train
        pdf    = _to_pandas_window(df, n=n_keep)
        feats  = self._FEATURE_BUILDER.build(pdf)
        if feats is None or len(feats) < 250:
            logger.warning(f"[OpusV4-RT] {tf_key} : données insuffisantes pour entraîner")
            return False

        # 2. Sélection des colonnes features (numériques, stationnaires)
        feature_cols = _select_feature_columns(feats)
        if not feature_cols:
            logger.warning(f"[OpusV4-RT] {tf_key} : aucune feature exploitable")
            return False

        # 3. Labels — réplique exacte de la méthodologie V4
        close   = feats["close"].to_numpy(dtype=np.float64)
        n       = len(close) - 1
        if n < 200:
            logger.warning(f"[OpusV4-RT] {tf_key} : pas assez de barres labélisables")
            return False
        ret_t1   = (close[1:] - close[:n]) / np.maximum(close[:n], 1e-9)
        abs_ret  = np.abs(ret_t1)
        # Quantile coupure : top amp_top_pct → label "événement"
        amp_thr  = float(np.quantile(abs_ret, 1.0 - amp_top_pct))
        y_amp    = (abs_ret >= amp_thr).astype(np.int8)
        y_dir    = (ret_t1 > 0).astype(np.int8)

        # 4. Matrice de features (alignée sur n = len-1)
        X_df = feats.iloc[:n][feature_cols].copy()

        # 5. Split chronologique 80/20
        split = max(int(n * 0.8), 100)
        split = min(split, n - 50)
        if split < 100 or n - split < 50:
            logger.warning(f"[OpusV4-RT] {tf_key} : split impossible (n={n})")
            return False

        # 6. Médianes du TRAIN pour imputation cohérente live/test
        medians = {
            c: float(np.nanmedian(X_df[c].iloc[:split].to_numpy(dtype=np.float64)))
            for c in feature_cols
        }
        # Imputation
        X_imp_train = _impute_with_medians(X_df.iloc[:split], medians)
        X_imp_valid = _impute_with_medians(X_df.iloc[split:n], medians)

        # 7. Entraînement LightGBM (paramètres alignés sur 13_v4_models.py)
        common = dict(
            objective="binary", metric="auc",
            num_leaves=num_leaves, learning_rate=learning_rate,
            min_child_samples=20, subsample=0.8, subsample_freq=5,
            colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
            verbosity=-1, n_jobs=1,
        )
        callbacks = [lgb.early_stopping(40, verbose=False), lgb.log_evaluation(-1)]

        # ── Modèle amplitude ───────────────────────────────────────────────
        ds_train_amp = lgb.Dataset(X_imp_train, label=y_amp[:split])
        ds_valid_amp = lgb.Dataset(X_imp_valid, label=y_amp[split:n], reference=ds_train_amp)
        try:
            booster_amp = lgb.train(
                {**common, "scale_pos_weight":
                    (y_amp[:split] == 0).sum() / max((y_amp[:split] == 1).sum(), 1)},
                ds_train_amp,
                num_boost_round=n_estimators,
                valid_sets=[ds_valid_amp],
                callbacks=callbacks,
            )
        except Exception as e:
            logger.warning(f"[OpusV4-RT] {tf_key} : entraînement amp KO ({e})")
            return False
        auc_amp = booster_amp.best_score.get("valid_0", {}).get("auc", 0.0)

        # ── Modèle direction ───────────────────────────────────────────────
        ds_train_dir = lgb.Dataset(X_imp_train, label=y_dir[:split])
        ds_valid_dir = lgb.Dataset(X_imp_valid, label=y_dir[split:n], reference=ds_train_dir)
        try:
            booster_dir = lgb.train(
                {**common, "scale_pos_weight":
                    (y_dir[:split] == 0).sum() / max((y_dir[:split] == 1).sum(), 1)},
                ds_train_dir,
                num_boost_round=n_estimators,
                valid_sets=[ds_valid_dir],
                callbacks=callbacks,
            )
        except Exception as e:
            logger.warning(f"[OpusV4-RT] {tf_key} : entraînement dir KO ({e})")
            return False
        auc_dir = booster_dir.best_score.get("valid_0", {}).get("auc", 0.0)

        auc_combined = (auc_amp + auc_dir) / 2.0

        with self._lock:
            self._amp_models[tf_key]      = booster_amp
            self._dir_models[tf_key]      = booster_dir
            self._feature_cols[tf_key]    = feature_cols
            self._medians[tf_key]         = medians
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key] = auc_combined
            self._best_auc                = max(self._best_auc, auc_combined)
            self._train_meta[tf_key] = {
                "n_train":     int(split),
                "n_valid":     int(n - split),
                "n_features":  len(feature_cols),
                "auc_amp":     round(float(auc_amp), 4),
                "auc_dir":     round(float(auc_dir), 4),
                "amp_thr_pct": round(float(amp_thr) * 100, 3),
                "amp_top_pct": amp_top_pct,
            }

        logger.info(
            f"[OpusV4-RT] {tf_key} entraîné : {split} train / {n - split} val | "
            f"{len(feature_cols)} features | AUC amp={auc_amp:.3f} dir={auc_dir:.3f} | "
            f"amp_thr={amp_thr * 100:.2f}%"
        )
        return True

    # ── Prédictions (API V4) ───────────────────────────────────────────────
    def _predict(self, features_df: pd.DataFrame, tf: str, target: str) -> Optional[float]:
        with self._lock:
            model_map = self._amp_models if target == "amp" else self._dir_models
            booster   = model_map.get(tf)
            feat_cols = self._feature_cols.get(tf)
            medians   = self._medians.get(tf, {})
        if booster is None or not feat_cols:
            return None
        try:
            missing = [c for c in feat_cols if c not in features_df.columns]
            if missing:
                # Ajoute des colonnes manquantes (cas de col absente) à NaN puis impute.
                for c in missing:
                    features_df[c] = np.nan
            X_row = features_df.iloc[-1:][feat_cols]
            X_arr = _impute_with_medians(X_row, medians)
            return float(booster.predict(X_arr)[0])
        except Exception as e:
            logger.warning(f"[OpusV4-RT] Prédiction {tf}/{target} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pd.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pd.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "dir")

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Score (mêmes règles V4 que la version pré-entraînée) ───────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        thresh_amp_td    = float(p.get("thresh_amp_td",    0.50))
        thresh_dir_td    = float(p.get("thresh_dir_td",    0.10))
        thresh_amp_other = float(p.get("thresh_amp_other", 0.55))
        thresh_dir_other = float(p.get("thresh_dir_other", 0.13))
        adx_threshold    = float(p.get("adx_threshold",    20.0))
        max_hold_bars    = int(p.get("max_hold_bars",      4))

        sl_atr_mult_td    = float(p.get("sl_atr_mult_td",    self._DEFAULTS["sl_atr_mult_td"]))
        sl_atr_mult_other = float(p.get("sl_atr_mult_other", self._DEFAULTS["sl_atr_mult_other"]))
        tp_atr_mult_td    = float(p.get("tp_atr_mult_td",    self._DEFAULTS["tp_atr_mult_td"]))
        tp_atr_mult_other = float(p.get("tp_atr_mult_other", self._DEFAULTS["tp_atr_mult_other"]))
        if "sl_atr_mult" in p:
            sl_atr_mult_td = sl_atr_mult_other = float(p["sl_atr_mult"])
        if "tp_atr_mult" in p:
            tp_atr_mult_td = tp_atr_mult_other = float(p["tp_atr_mult"])

        use_kelly_sizing = bool(p.get("use_kelly_sizing", self._DEFAULTS["use_kelly_sizing"]))
        kelly_size_other = float(p.get("kelly_size_other", self._DEFAULTS["kelly_size_other"]))
        min_confidence   = float(p.get("min_confidence",   self._DEFAULTS["min_confidence"]))

        enable_hour_filter  = bool(p.get("enable_hour_filter",  self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",    self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",         self._DEFAULTS["active_days"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",        self._DEFAULTS["use_fixed_tp"]))
        disable_trailing    = bool(p.get("disable_trailing",    self._DEFAULTS["disable_trailing"]))
        use_exit_after_bars = bool(p.get("use_exit_after_bars", self._DEFAULTS["use_exit_after_bars"]))

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        # 0. Filtre temporel
        if enable_hour_filter:
            hour, dow = _last_bar_hour_dow(df)
            if hour is not None and dow is not None:
                if dow not in active_days:
                    return self._none(
                        f"Hors jours actifs (weekday={dow}, autorisés={active_days})"
                    )
                if hour not in active_hours_utc:
                    return self._none(
                        f"Hors session ({hour}h UTC, autorisées={active_hours_utc})"
                    )

        # 1. Détection du TF
        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(
                f"Timeframe non supporté (détecté={tf}, attendus={_SUPPORTED_TFS})"
            )

        # 2. Walk-forward inline (uniquement si le trainer externe ne gère pas)
        cnt = self._call_cnt.get(tf, 0) + 1
        self._call_cnt[tf] = cnt
        last       = self._last_retrain.get(tf, 0)
        need_train = (tf not in self._trained_tfs) or (cnt - last >= retrain_every)
        if need_train and not self._managed_externally:
            n_train  = min(len(df) - 1, warmup_bars * 2)
            train_df = df.slice(len(df) - n_train - 1, n_train)
            if self._train(train_df, tf, p):
                self._last_retrain[tf] = cnt

        if tf not in self._trained_tfs:
            return self._none("Modèle pas encore entraîné (warmup en cours)")

        # 3. Construction des features sur la fenêtre récente
        pdf      = _to_pandas_window(df, n=max(260, self.min_bars_required(params)))
        features = self._FEATURE_BUILDER.build(pdf)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.iloc[-1]
        atr_v = float(last_row.get("ATR_14", 0.0) or 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        # 4. Régime (réplique exacte de la logique V4)
        adx_v = float(last_row.get("ADX", 0.0) or 0.0)
        bull  = int(last_row.get("MM_bullish_align", 0) or 0)
        bear  = int(last_row.get("MM_bearish_align", 0) or 0)
        if adx_v < adx_threshold:
            regime = REGIME_RANGE
        elif bull == 1:
            regime = REGIME_TREND_UP
        elif bear == 1:
            regime = REGIME_TREND_DN
        else:
            regime = REGIME_CHOPPY
        regime_lbl = REGIME_LABELS[regime]

        # 5. Trend Up : pas d'edge (AUC ≈ 0.50)
        if regime == REGIME_TREND_UP:
            return self._none("Trend Up : aucun edge (AUC dir ≈ 0.50)", regime=regime)

        # 6. Prédictions
        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")
        dir_dist = abs(p_up - 0.5)

        # 7. Règle de décision par régime + mults SL/TP par régime
        if regime == REGIME_TREND_DN:
            amp_thresh, dir_thresh = thresh_amp_td, thresh_dir_td
            sl_atr_mult, tp_atr_mult = sl_atr_mult_td, tp_atr_mult_td
            regime_size_fac = 1.0
        else:
            amp_thresh, dir_thresh = thresh_amp_other, thresh_dir_other
            sl_atr_mult, tp_atr_mult = sl_atr_mult_other, tp_atr_mult_other
            regime_size_fac = kelly_size_other

        if p_event < amp_thresh:
            return self._none(
                f"P(event)={p_event:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )
        if dir_dist < dir_thresh:
            return self._none(
                f"|P(up)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side = "long" if p_up > 0.5 else "short"

        # 8. Sizing demi-Kelly
        confidence = dir_dist * 2.0
        if use_kelly_sizing:
            size_factor = min(1.0, max(0.0, regime_size_fac * max(confidence, min_confidence)))
        else:
            size_factor = regime_size_fac

        score_val = round(min(0.55 + p_event * confidence * 0.39, 0.94), 3)
        meta      = self._train_meta.get(tf, {})

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "size_factor":      round(size_factor, 4),
            "disable_trailing": disable_trailing,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult
        if use_exit_after_bars:
            sig["exit_after_bars"] = max_hold_bars
        if not disable_trailing:
            sig["trail_override"] = {
                "trail_wide":  max(1.0, sl_atr_mult),
                "trail_tight": max(0.5, tp_atr_mult * 0.5),
                "breakeven_r": 0.8,
                "lock_r":      max(1.0, tp_atr_mult),
                "tight_r":     max(1.5, tp_atr_mult * 1.5),
                "grace_bars":  1,
            }

        exit_desc = [f"SL fixe = entry ∓ {sl_atr_mult:.2f}×ATR"]
        if use_fixed_tp:
            exit_desc.append(f"TP fixe = entry ± {tp_atr_mult:.2f}×ATR")
        if not disable_trailing:
            exit_desc.append("trailing actif")
        else:
            exit_desc.append("trailing désactivé")
        if use_exit_after_bars:
            exit_desc.append(f"sortie après {max_hold_bars} barres max")

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(float(last_row.get("RSI_14", 50.0) or 50.0), 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "dir_dist":         round(dir_dist, 4),
            "confidence":       round(confidence, 4),
            "size_factor":      round(size_factor, 4),
            "regime_size_fac":  regime_size_fac,
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "use_fixed_tp":     use_fixed_tp,
            "use_kelly_sizing": use_kelly_sizing,
            "disable_trailing": disable_trailing,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
            "n_features":       meta.get("n_features", 0),
            "amp_thr_pct":      meta.get("amp_thr_pct", 0.0),
        }
        sig["conditions"] = [
            f"Modèle V4 entraîné inline / {tf} ({meta.get('n_features', 0)} features, "
            f"AUC amp={meta.get('auc_amp', 0):.2f} dir={meta.get('auc_dir', 0):.2f})",
            f"Régime : {regime_lbl} (ADX={adx_v:.0f}) — autorisé ✓",
            f"P(événement)={p_event:.2f} ≥ {amp_thresh:.2f} ✓",
            f"P(hausse)={p_up:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR (régime {regime_lbl})",
            f"Sizing : régime ×{regime_size_fac:.2f} × confidence {confidence:.2f} "
            f"= size_factor {size_factor:.2f}" if use_kelly_sizing
            else f"Sizing : ×{regime_size_fac:.2f} (Kelly désactivé)",
            f"Sortie : {' + '.join(exit_desc)}",
        ]
        sig["reason"] = (
            f"OpusV4-RT {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f}"
        )
        return sig

    def _none(self, reason: str = "", p_event: float = 0.0, p_up: float = 0.5,
              regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "p_event":    round(p_event, 4),
            "p_up":       round(p_up, 4),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
