"""
ml_strategy.py — Stratégie ML autonome (Random Forest / Logistic Regression)

Module compagnon de ml_dynamic_threshold.py.
Expose les helpers nécessaires aux routes /api/ml/* :
  - compute_features()          → DataFrame de features ML
  - optimize_ml_strategy()      → Random Search sur hyperparamètres RF/LR
  - ML_STRATEGY_PARAM_SPACE     → espace de recherche global
  - PARAM_SPACE_RF               → hyperparamètres Random Forest
  - PARAM_SPACE_LR               → hyperparamètres Logistic Regression
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Tuple

import numpy as np
import polars as pl
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  Espaces de paramètres
# ──────────────────────────────────────────────────────────────────────────────
PARAM_SPACE_RF: Dict[str, List] = {
    "n_estimators":   [50, 100, 200, 300],
    "max_depth":      [3, 5, 7, 10, None],
    "min_samples_leaf": [1, 2, 5, 10],
    "max_features":   ["sqrt", "log2", 0.5],
    "class_weight":   [None, "balanced"],
}

PARAM_SPACE_LR: Dict[str, List] = {
    "C":              [0.01, 0.1, 1.0, 10.0],
    "penalty":        ["l2"],
    "solver":         ["lbfgs", "liblinear"],
    "max_iter":       [200, 500],
    "class_weight":   [None, "balanced"],
}

ML_STRATEGY_PARAM_SPACE: Dict[str, List] = {
    "model_type":     ["rf", "lr"],
    "threshold":      [0.45, 0.50, 0.55, 0.60],
    "horizon":        [1, 2, 3, 5],
    "feature_window": [10, 20, 30],
    **{f"rf_{k}": v for k, v in PARAM_SPACE_RF.items()},
    **{f"lr_{k}": v for k, v in PARAM_SPACE_LR.items()},
}


# ──────────────────────────────────────────────────────────────────────────────
#  Feature engineering
# ──────────────────────────────────────────────────────────────────────────────
def compute_features(df: pl.DataFrame, window: int = 20) -> pl.DataFrame:
    """
    Calcule les features ML depuis OHLCV.
    Retourne un DataFrame prêt pour sklearn (drop_nulls appliqué).
    """
    if len(df) < window + 15:
        return pl.DataFrame()

    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # Rendements
    ret1  = c.pct_change(1)
    ret3  = c.pct_change(3)
    ret5  = c.pct_change(5)
    ret10 = c.pct_change(10)

    # EMAs
    ema8  = c.ewm_mean(span=8,  adjust=False)
    ema21 = c.ewm_mean(span=21, adjust=False)
    ema50 = c.ewm_mean(span=50, adjust=False)
    ema_ratio_8_21  = ema8  / ema21.clip(lower_bound=1e-9) - 1
    ema_ratio_21_50 = ema21 / ema50.clip(lower_bound=1e-9) - 1

    # RSI(14)
    d   = c.diff(1)
    g   = d.clip(lower_bound=0).ewm_mean(alpha=1/14, adjust=False)
    dn  = (-d.clip(upper_bound=0)).ewm_mean(alpha=1/14, adjust=False)
    # Avoid division by zero: add tiny epsilon to dn
    dn_safe = dn + 1e-15
    rsi14   = 100 - 100 / (1 + g / dn_safe)

    # MACD(12,26,9)
    ema12 = c.ewm_mean(span=12, adjust=False)
    ema26 = c.ewm_mean(span=26, adjust=False)
    ml_   = ema12 - ema26
    sig_  = ml_.ewm_mean(span=9, adjust=False)
    macd_hist  = ml_ - sig_
    macd_histd = macd_hist.diff(1)

    # ATR(14)
    c_prev = c.shift(1)
    tr = pl.Series(np.maximum((h - l).to_numpy(), np.maximum((h - c_prev).abs().to_numpy(), (l - c_prev).abs().to_numpy())))
    atr    = tr.ewm_mean(span=14, adjust=False)
    atr_pct = atr / c.clip(lower_bound=1e-9)

    # Bollinger width
    sma20   = c.rolling_mean(window)
    std20   = c.rolling_std(window)
    bb_width = 4 * std20 / sma20.clip(lower_bound=1e-9)

    # Volume ratio
    vol_ma    = v.rolling_mean(window)
    vol_ratio = v / vol_ma.clip(lower_bound=1e-9)

    # ADX(14) — using intermediate DataFrame for conditional DM computation
    up_raw  = h.diff(1).clip(lower_bound=0)
    dn_raw  = (-l.diff(1)).clip(lower_bound=0)
    _dm = pl.DataFrame({"up": up_raw, "dn": dn_raw}).with_columns([
        pl.when(pl.col("up") > pl.col("dn")).then(pl.col("up")).otherwise(0.0).alias("pdm"),
        pl.when(pl.col("dn") > pl.col("up")).then(pl.col("dn")).otherwise(0.0).alias("mdm"),
    ])
    pdm, mdm = _dm["pdm"], _dm["mdm"]
    atr_   = tr.ewm_mean(span=14, adjust=False).replace(0, None)
    dip_   = 100 * pdm.ewm_mean(span=14, adjust=False) / atr_
    dim_   = 100 * mdm.ewm_mean(span=14, adjust=False) / atr_
    dx_sum = dip_ + dim_
    dx_    = 100 * (dip_ - dim_).abs() / dx_sum.replace(0, None)
    adx14  = dx_.ewm_mean(span=14, adjust=False).fill_null(0.0)

    feat = pl.DataFrame({
        "ret1":           ret1,
        "ret3":           ret3,
        "ret5":           ret5,
        "ret10":          ret10,
        "ema_ratio_8_21": ema_ratio_8_21,
        "ema_ratio_21_50":ema_ratio_21_50,
        "rsi14":          rsi14,
        "macd_hist":      macd_hist,
        "macd_histd":     macd_histd,
        "atr_pct":        atr_pct,
        "bb_width":       bb_width,
        "vol_ratio":      vol_ratio,
        "adx14":          adx14,
    })
    return feat.drop_nulls()


# ──────────────────────────────────────────────────────────────────────────────
#  Construction des labels
# ──────────────────────────────────────────────────────────────────────────────
def _make_labels(df: pl.DataFrame, horizon: int = 3,
                 threshold_pct: float = 0.003) -> pl.Series:
    """Label 1 si le rendement futur > threshold, 0 sinon."""
    fwd = df["close"].pct_change(horizon).shift(-horizon)
    return (fwd > threshold_pct).cast(pl.Int32)


# ──────────────────────────────────────────────────────────────────────────────
#  Random Search d'hyperparamètres
# ──────────────────────────────────────────────────────────────────────────────
def optimize_ml_strategy(df: pl.DataFrame, cfg: dict,
                         n_outer: int = 15,
                         horizon: int = 3,
                         feature_window: int = 20) -> Dict[str, Any]:
    """
    Random Search sur RF et LR avec validation walk-forward simple.
    Retourne les meilleurs hyperparamètres et l'AUC OOS.
    """
    feats  = compute_features(df, window=feature_window)
    labels = _make_labels(df, horizon=horizon)

    # Position-based alignment (no index in Polars):
    # feats = last n_feat rows of df (leading NaN rows dropped by drop_nulls)
    # labels has null at last `horizon` positions (from shift(-horizon))
    n_total = len(df)
    n_feat  = len(feats)
    offset  = n_total - n_feat
    n_valid = n_feat - horizon

    if n_valid < 80:
        raise ValueError(f"Pas assez de données ({n_valid} lignes après feature engineering)")

    feats_aligned  = feats[:n_valid]
    labels_aligned = labels[offset : n_total - horizon]

    n      = len(feats_aligned)
    split  = int(n * 0.7)
    X_tr   = feats_aligned[:split].to_numpy()
    y_tr   = labels_aligned[:split].to_numpy()
    X_oos  = feats_aligned[split:].to_numpy()
    y_oos  = labels_aligned[split:].to_numpy()

    scaler  = StandardScaler().fit(X_tr)
    X_tr_s  = scaler.transform(X_tr)
    X_oos_s = scaler.transform(X_oos)

    best_auc    = -1.0
    best_params: Dict[str, Any] = {}
    best_model  = None
    results: List[Dict] = []

    for trial in range(n_outer):
        model_type = random.choice(["rf", "lr"])
        try:
            if model_type == "rf":
                p = {k: random.choice(v) for k, v in PARAM_SPACE_RF.items()}
                p["n_jobs"] = 1
                model = RandomForestClassifier(**p, random_state=trial)
                model.fit(X_tr, y_tr)          # RF: no scaling needed
                proba = model.predict_proba(X_oos)[:, 1]
            else:
                p = {k: random.choice(v) for k, v in PARAM_SPACE_LR.items()}
                model = LogisticRegression(**p, random_state=trial)
                model.fit(X_tr_s, y_tr)
                proba = model.predict_proba(X_oos_s)[:, 1]

            if len(np.unique(y_oos)) < 2:
                continue
            auc = float(roc_auc_score(y_oos, proba))
            results.append({"model_type": model_type, "params": p, "auc_oos": auc})
            if auc > best_auc:
                best_auc    = auc
                best_params = {"model_type": model_type, **p}
                best_model  = model
        except Exception as e:
            logger.warning(f"[MLStrategy] Trial {trial} échoué : {e}")
            continue

    results.sort(key=lambda x: x["auc_oos"], reverse=True)
    return {
        "best_auc_oos":  round(best_auc, 4),
        "best_params":   best_params,
        "n_trials":      len(results),
        "top3":          results[:3],
        "n_features":    len(feats.columns),
        "feature_names": list(feats.columns),
        "train_size":    split,
        "oos_size":      n - split,
    }
