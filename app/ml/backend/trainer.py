"""MLBackend.trainer — entraînement LightGBM (amp + dir) avec calibration et pruning.

Extrait la logique d'entraînement commune aux stratégies Opus V11/V12,
opus_stat_retrained_v4 et variantes. Le trainer supporte :

- Labellisation multi-horizon (V11+) ou single-horizon (V4 retrained simple).
- Calibration isotone optionnelle sur le set de validation.
- Pruning de features optionnel (garde les features à gain > 0).
- Cache process-wide via `app.core.train_cache.cached_train` : la clé de cache
  est basée sur `type(strategy).__module__` + fenêtre + hyperparams — le
  trainer reçoit donc la `strategy` en paramètre pour préserver le cache.

Le trainer ne persiste PAS les modèles — c'est le rôle de `persistence.py`.
"""
from __future__ import annotations

import gc
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.ml.backend.features import (
    build_features,
    impute_inplace,
    multi_horizon_labels,
    select_feature_columns,
    single_horizon_labels,
    window_polars,
)

logger = logging.getLogger(__name__)


# ── Configuration d'entraînement ────────────────────────────────────────────
class TrainConfig:
    """Hyperparamètres d'entraînement LightGBM (figés, hors param_space).

    Ces valeurs sont les mêmes pour toutes les stratégies qui utilisent le
    MLBackend — elles font partie de la clé de cache process-wide. Les
    échantillonner invaliderait le cache et ferait repayer chaque trial de
    l'optimiseur l'intégralité des retrains walk-forward (rédhibitoire sur
    50k bougies).
    """

    def __init__(self,
                 amp_top_pct: float = 0.30,
                 n_estimators: int = 500,
                 num_leaves: int = 31,
                 learning_rate: float = 0.03,
                 label_horizons: Optional[List[int]] = None,
                 calibrate: bool = True,
                 prune_features: bool = True,
                 log_training: bool = True,
                 importance_top_n: int = 15,
                 warmup_bars: int = 750,
                 retrain_every: int = 800,
                 min_bars: int = 230):
        self.amp_top_pct      = float(amp_top_pct)
        self.n_estimators     = int(n_estimators)
        self.num_leaves       = int(num_leaves)
        self.learning_rate    = float(learning_rate)
        self.label_horizons   = list(label_horizons or [1, 3, 6])
        self.calibrate        = bool(calibrate)
        self.prune_features   = bool(prune_features)
        self.log_training     = bool(log_training)
        self.importance_top_n = int(importance_top_n)
        self.warmup_bars      = int(warmup_bars)
        self.retrain_every    = int(retrain_every)
        self.min_bars         = int(min_bars)

    @classmethod
    def from_params(cls, params: Dict[str, Any], defaults: Dict[str, Any]) -> "TrainConfig":
        """Construit la config depuis un dict de params + défauts."""
        g = lambda k, d: params.get(k, defaults.get(k, d))  # noqa: E731
        return cls(
            amp_top_pct      = float(g("amp_top_pct", 0.30)),
            n_estimators     = int(g("n_estimators", 500)),
            num_leaves       = int(g("num_leaves", 31)),
            learning_rate    = float(g("learning_rate", 0.03)),
            label_horizons   = list(g("label_horizons", [1, 3, 6])),
            calibrate        = bool(g("calibrate", True)),
            prune_features   = bool(g("prune_features", True)),
            log_training     = bool(g("log_training", True)),
            importance_top_n = int(g("importance_top_n", 15)),
            warmup_bars      = int(g("warmup_bars", 750)),
            retrain_every    = int(g("retrain_every", 800)),
            min_bars         = int(g("min_bars", 230)),
        )


# ── État d'entraînement (mutable, porté par le backend) ─────────────────────
class TrainState:
    """Conteneur pour l'état ML mutable, accédé sous lock."""

    __slots__ = (
        "amp_models", "dir_models", "amp_cal", "dir_cal",
        "feature_cols", "kept_features", "medians",
        "trained_tfs", "best_auc_per_tf", "train_meta",
        "last_retrain", "call_cnt", "best_auc", "managed_externally",
    )

    def __init__(self):
        self.amp_models:   Dict[str, Any]              = {}
        self.dir_models:   Dict[str, Any]              = {}
        self.amp_cal:      Dict[str, Any]              = {}
        self.dir_cal:      Dict[str, Any]              = {}
        self.feature_cols: Dict[str, List[str]]        = {}
        self.kept_features: Dict[str, List[str]]       = {}
        self.medians:      Dict[str, Dict[str, float]] = {}
        self.trained_tfs:  set                         = set()
        self.best_auc_per_tf: Dict[str, float]         = {}
        self.train_meta:   Dict[str, dict]             = {}
        self.last_retrain: Dict[str, int]              = {}
        self.call_cnt:     Dict[str, int]              = {}
        self.best_auc:     float                       = 0.0
        self.managed_externally: bool                  = False

    def reset(self) -> None:
        self.amp_models.clear()
        self.dir_models.clear()
        self.amp_cal.clear()
        self.dir_cal.clear()
        self.feature_cols.clear()
        self.kept_features.clear()
        self.medians.clear()
        self.trained_tfs.clear()
        self.best_auc_per_tf.clear()
        self.train_meta.clear()
        self.last_retrain.clear()
        self.call_cnt.clear()
        self.best_auc = 0.0
        self.managed_externally = False

    # Attributs à sauvegarder/restaurer pour le cache process-wide.
    STATE_ATTRS: Tuple[str, ...] = (
        "amp_models", "dir_models", "amp_cal", "dir_cal", "feature_cols",
        "kept_features", "medians", "best_auc_per_tf", "train_meta",
    )

    # Clés de params qui invalident le cache si elles changent.
    PARAM_KEYS: Tuple[str, ...] = (
        "amp_top_pct", "n_estimators", "num_leaves", "learning_rate",
        "label_horizons", "calibrate", "prune_features",
    )


# ── Trainer ─────────────────────────────────────────────────────────────────
def train(state: TrainState, lock, df: pl.DataFrame, tf_key: str,
          params: Dict[str, Any], defaults: Dict[str, Any],
          bt_features: Optional[pl.DataFrame] = None,
          bt_features_len: int = 0,
          bt_train_offset: Optional[int] = None) -> bool:
    """Entraîne les 2 boosters (amp + dir) pour un TF donné.

    Args:
        state: état ML mutable (verrouillé par `lock`).
        lock:  threading.Lock pour sérialiser les mutations de `state`.
        df:    DataFrame OHLCV polars (fenêtre d'entraînement).
        tf_key: timeframe (ex. "1h").
        params: paramètres de la stratégie (peut contenir des overrides).
        defaults: defaults de la stratégie (_DEFAULTS).
        bt_features: cache de features pré-calculé pour backtest (optionnel).
        bt_features_len: longueur du cache backtest.
        bt_train_offset: offset de la fenêtre d'entraînement dans le cache.

    Returns:
        True si l'entraînement a réussi, False sinon.
    """
    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("[MLBackend] lightgbm requis : pip install lightgbm")
        return False

    cfg = TrainConfig.from_params(params, defaults)

    n_keep = max(2200, len(df))
    # Réutilise le cache backtest si dispo. ``bt_train_offset`` repère la
    # position de la fenêtre d'entraînement dans la fenêtre complète.
    _off = int(bt_train_offset or 0)
    if (bt_features is not None and bt_features_len > 0 and
            _off + len(df) <= bt_features_len):
        feats = bt_features.slice(_off, len(df))
    else:
        feats = build_features(window_polars(df, n=n_keep))
    if feats is None or len(feats) < 250:
        logger.warning(f"[MLBackend] {tf_key} : données insuffisantes")
        return False

    feature_cols = select_feature_columns(feats)
    # Pruning : ne garder que les features non nulles du cycle précédent.
    if cfg.prune_features and state.kept_features.get(tf_key):
        kept = [c for c in feature_cols if c in set(state.kept_features[tf_key])]
        if len(kept) >= 50:
            feature_cols = kept
    if not feature_cols:
        logger.warning(f"[MLBackend] {tf_key} : aucune feature exploitable")
        return False

    close = feats["close"].to_numpy().astype(np.float64)
    if cfg.label_horizons and len(cfg.label_horizons) > 1:
        y_amp, y_dir, n, amp_thr, lbl_stats = multi_horizon_labels(
            close, cfg.label_horizons, cfg.amp_top_pct,
        )
    else:
        y_amp, y_dir, n, amp_thr = single_horizon_labels(close, cfg.amp_top_pct)
        lbl_stats = {"horizons": [1], "n_labels": int(n),
                     "amp_thr_pct": round(amp_thr * 100, 4)}
    if n < 200:
        logger.warning(f"[MLBackend] {tf_key} : pas assez de barres labélisables (n={n})")
        return False

    X_full = feats.head(n).select(feature_cols).to_numpy().astype(np.float32)
    split = max(int(n * 0.8), 100)
    split = min(split, n - 50)
    if split < 100 or n - split < 50:
        logger.warning(f"[MLBackend] {tf_key} : split impossible (n={n})")
        return False

    medians: Dict[str, float] = {}
    for j, col in enumerate(feature_cols):
        col_train = X_full[:split, j]
        mask = np.isfinite(col_train)
        medians[col] = float(np.median(col_train[mask])) if mask.any() else 0.0

    X_train = X_full[:split].copy()
    X_valid = X_full[split:n].copy()
    del X_full
    impute_inplace(X_train, feature_cols, medians)
    impute_inplace(X_valid, feature_cols, medians)

    if len(np.unique(y_amp[:split])) < 2 or len(np.unique(y_dir[:split])) < 2:
        from app.core.log_throttle import log_throttled
        log_throttled(logger, f"mlbackend:monoclass:{tf_key}",
                      f"[MLBackend] {tf_key} : labels mono-classe, fit ignoré")
        return False

    common = dict(
        objective="binary", metric="auc",
        num_leaves=cfg.num_leaves, learning_rate=cfg.learning_rate,
        min_child_samples=20, subsample=0.8, subsample_freq=5,
        colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
        max_bin=63, force_col_wise=True, verbosity=-1, n_jobs=1,
    )

    boosters: Dict[str, Any] = {}
    aucs: Dict[str, float] = {}
    cal_err: Dict[str, float] = {}
    calibrators: Dict[str, Any] = {}
    importances: Dict[str, List[tuple]] = {}

    for target, y in (("amp", y_amp), ("dir", y_dir)):
        spw = (y[:split] == 0).sum() / max((y[:split] == 1).sum(), 1)
        ds_tr = lgb.Dataset(X_train, label=y[:split], feature_name=feature_cols,
                            free_raw_data=False)
        ds_va = lgb.Dataset(X_valid, label=y[split:n], reference=ds_tr,
                            feature_name=feature_cols, free_raw_data=False)
        try:
            try:
                booster = lgb.train(
                    {**common, "scale_pos_weight": spw},
                    ds_tr, num_boost_round=cfg.n_estimators, valid_sets=[ds_va],
                    callbacks=[lgb.early_stopping(20, verbose=False),
                               lgb.log_evaluation(-1)],
                )
            except Exception as e:
                logger.warning(f"[MLBackend] {tf_key} : entraînement {target} KO ({e})")
                return False
            aucs[target] = float(booster.best_score.get("valid_0", {}).get("auc", 0.0))
            boosters[target] = booster

            # Calibration isotone sur le set de validation.
            if cfg.calibrate:
                try:
                    from sklearn.isotonic import IsotonicRegression
                    raw_va = booster.predict(X_valid)
                    y_va = y[split:n]
                    if len(np.unique(y_va)) >= 2:
                        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                        iso.fit(raw_va, y_va)
                        cal_va = iso.predict(raw_va)
                        cal_err[target] = round(float(np.mean(np.abs(cal_va - y_va))), 4)
                        calibrators[target] = iso
                except Exception as ce:
                    logger.debug(f"[MLBackend] {tf_key} calibration {target} KO : {ce}")

            # Importance des features (gain).
            try:
                gains = booster.feature_importance(importance_type="gain")
                pairs = sorted(zip(feature_cols, gains), key=lambda kv: -kv[1])
                importances[target] = [(c, float(g)) for c, g in pairs]
            except Exception:
                importances[target] = []
        finally:
            del ds_tr, ds_va
            gc.collect()

    del X_train, X_valid

    # Pruning : features avec gain > 0 sur au moins un des deux modèles.
    kept_set = set()
    for tgt in ("amp", "dir"):
        for c, g in importances.get(tgt, []):
            if g > 0:
                kept_set.add(c)
    kept_features = [c for c in feature_cols if c in kept_set]

    auc_combined = (aucs.get("amp", 0.0) + aucs.get("dir", 0.0)) / 2.0
    top_feats = {
        tgt: [c for c, _ in importances.get(tgt, [])[:cfg.importance_top_n]]
        for tgt in ("amp", "dir")
    }
    meta = {
        "n_train":      int(split),
        "n_valid":      int(n - split),
        "n_features":   len(feature_cols),
        "auc_amp":      round(aucs.get("amp", 0.0), 4),
        "auc_dir":      round(aucs.get("dir", 0.0), 4),
        "amp_thr_pct":  round(float(amp_thr) * 100, 4),
        "amp_top_pct":  cfg.amp_top_pct,
        "horizons":     lbl_stats.get("horizons"),
        "label_stats":  lbl_stats,
        "calibrated":   bool(calibrators),
        "cal_err":      cal_err,
        "n_kept_features": len(kept_features),
        "top_features_amp": top_feats["amp"],
        "top_features_dir": top_feats["dir"],
    }

    with lock:
        state.amp_models[tf_key]    = boosters["amp"]
        state.dir_models[tf_key]    = boosters["dir"]
        state.amp_cal[tf_key]       = calibrators.get("amp")
        state.dir_cal[tf_key]       = calibrators.get("dir")
        state.feature_cols[tf_key]  = feature_cols
        state.kept_features[tf_key] = kept_features
        state.medians[tf_key]       = medians
        state.trained_tfs.add(tf_key)
        state.best_auc_per_tf[tf_key] = auc_combined
        state.best_auc = max(state.best_auc, auc_combined)
        state.train_meta[tf_key]    = meta
    gc.collect()

    logger.info(
        f"[MLBackend] {tf_key} entraîné : {split} train / {n - split} val | "
        f"{len(feature_cols)} feats (gardées {len(kept_features)}) | "
        f"AUC amp={aucs.get('amp', 0):.3f} dir={aucs.get('dir', 0):.3f} | "
        f"horizons={meta['horizons']} | calib={meta['calibrated']} cal_err={cal_err} | "
        f"top_dir={top_feats['dir'][:5]}"
    )
    return True
