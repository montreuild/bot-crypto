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
import pandas as pd
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
def compute_features(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Calcule les features ML depuis OHLCV.
    Retourne un DataFrame prêt pour sklearn (dropna appliqué).
    """
    if len(df) < window + 15:
        return pd.DataFrame()

    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    v = df["volume"].astype(float)

    feat = pd.DataFrame(index=df.index)

    # Rendements
    feat["ret1"]   = c.pct_change(1)
    feat["ret3"]   = c.pct_change(3)
    feat["ret5"]   = c.pct_change(5)
    feat["ret10"]  = c.pct_change(10)

    # EMAs
    ema8  = c.ewm(span=8,  adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    feat["ema_ratio_8_21"]  = ema8  / ema21.clip(lower=1e-9) - 1
    feat["ema_ratio_21_50"] = ema21 / ema50.clip(lower=1e-9) - 1

    # RSI(14)
    d  = c.diff()
    g  = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    feat["rsi14"] = 100 - 100 / (1 + g / dn.replace(0, 1e-10))

    # MACD(12,26,9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    ml    = ema12 - ema26
    sig   = ml.ewm(span=9, adjust=False).mean()
    feat["macd_hist"]  = ml - sig
    feat["macd_histd"] = feat["macd_hist"].diff()

    # ATR(14)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    feat["atr_pct"] = atr / c.clip(lower=1e-9)

    # Bollinger width
    sma20 = c.rolling(window).mean()
    std20 = c.rolling(window).std()
    feat["bb_width"] = 4 * std20 / sma20.clip(lower=1e-9)

    # Volume ratio
    vol_ma = v.rolling(window).mean()
    feat["vol_ratio"] = v / vol_ma.clip(lower=1e-9)

    # ADX(14)
    up   = h.diff().clip(lower=0)
    down = (-l.diff()).clip(lower=0)
    pdm  = up.where(up > down, 0.0)
    mdm  = down.where(down > up, 0.0)
    atr_ = tr.ewm(span=14, adjust=False).mean().replace(0, np.nan)
    dip  = 100 * pdm.ewm(span=14, adjust=False).mean() / atr_
    dim  = 100 * mdm.ewm(span=14, adjust=False).mean() / atr_
    dx   = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    feat["adx14"] = dx.ewm(span=14, adjust=False).mean().fillna(0)

    return feat.dropna()


# ──────────────────────────────────────────────────────────────────────────────
#  Construction des labels
# ──────────────────────────────────────────────────────────────────────────────
def _make_labels(df: pd.DataFrame, horizon: int = 3,
                 threshold_pct: float = 0.003) -> pd.Series:
    """Label 1 si le rendement futur > threshold, 0 sinon."""
    fwd = df["close"].astype(float).pct_change(horizon).shift(-horizon)
    return (fwd > threshold_pct).astype(int)


# ──────────────────────────────────────────────────────────────────────────────
#  Random Search d'hyperparamètres
# ──────────────────────────────────────────────────────────────────────────────
def optimize_ml_strategy(df: pd.DataFrame, cfg: dict,
                         n_outer: int = 15,
                         horizon: int = 3,
                         feature_window: int = 20) -> Dict[str, Any]:
    """
    Random Search sur RF et LR avec validation walk-forward simple.
    Retourne les meilleurs hyperparamètres et l'AUC OOS.
    """
    feats  = compute_features(df, window=feature_window)
    labels = _make_labels(df, horizon=horizon)
    labels = labels.reindex(feats.index).dropna()
    feats  = feats.loc[labels.index]

    n = len(feats)
    if n < 80:
        raise ValueError(f"Pas assez de données ({n} lignes après feature engineering)")

    split  = int(n * 0.7)
    X_tr, y_tr = feats.iloc[:split].values, labels.iloc[:split].values
    X_oos, y_oos = feats.iloc[split:].values, labels.iloc[split:].values

    scaler = StandardScaler().fit(X_tr)
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
