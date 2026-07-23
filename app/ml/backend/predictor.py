"""MLBackend.predictor — inférence LightGBM (single + batch) avec calibration.

Extrait la logique de prédiction commune aux stratégies Opus. Le predictor
lit l'état d'entraînement (boosters + calibrators + medians + feature_cols)
sous lock, puis effectue la prédiction sans le lock (pour ne pas bloquer
le thread de trading pendant l'inférence).

Deux modes :
- `predict_single` : prédiction sur la dernière barre (live, scan cycle).
  Utilise l'extraction `row(0, named=True)` en UN appel polars au lieu de
  ~440 `get_column` par prédiction — critique pour la perf live.
- `predict_series` : prédiction batch sur toutes les barres (scanner).
  Un seul appel booster.predict(X) au lieu d'un par barre.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import polars as pl

from app.ml.backend.trainer import TrainState

logger = logging.getLogger(__name__)


def predict_single(state: TrainState, lock,
                   features_df: pl.DataFrame, tf: str,
                   target: str) -> Optional[float]:
    """Prédit la probabilité calibrée pour la dernière barre de `features_df`.

    Args:
        state: état ML (verrouillé le temps de lire les références).
        lock:  threading.Lock (lecture courte, puis inférence hors lock).
        features_df: DataFrame polars avec les features V4.
        tf:    timeframe (ex. "1h").
        target: "amp" ou "dir".

    Returns:
        Probabilité calibrée dans [0, 1], ou None si modèle indisponible.
    """
    with lock:
        booster   = (state.amp_models if target == "amp" else state.dir_models).get(tf)
        cal       = (state.amp_cal    if target == "amp" else state.dir_cal).get(tf)
        feat_cols = state.feature_cols.get(tf)
        medians   = state.medians.get(tf, {})
    if booster is None or not feat_cols:
        return None
    try:
        # Extraction de la dernière ligne en UN appel (dict col->valeur) au
        # lieu d'un accès colonne polars par feature (~440 get_column par
        # prédiction, x2/barre). row.get(c)=None pour les colonnes absentes
        # → repli sur la médiane d'entraînement.
        row  = features_df.tail(1).row(0, named=True)
        vals = np.empty(len(feat_cols), dtype=np.float32)
        for j, c in enumerate(feat_cols):
            v = row.get(c)
            vals[j] = v if (v is not None and np.isfinite(v)) else medians.get(c, 0.0)
        raw = float(booster.predict(vals.reshape(1, -1))[0])
        if cal is not None:
            return float(cal.predict([raw])[0])
        return raw
    except Exception as e:
        logger.warning(f"[MLBackend] Prédiction {tf}/{target} KO : {e}")
        return None


def predict_series(state: TrainState, lock,
                   features_df: pl.DataFrame, tf: str,
                   target: str) -> Optional[np.ndarray]:
    """Prédit les probabilités calibrées pour TOUTES les barres (batch).

    Utilisé par le scanner pour éviter un appel `predict_single` par barre.
    Un seul appel `booster.predict(X)` sur l'ensemble du DataFrame.
    """
    with lock:
        booster   = (state.amp_models if target == "amp" else state.dir_models).get(tf)
        cal       = (state.amp_cal    if target == "amp" else state.dir_cal).get(tf)
        feat_cols = state.feature_cols.get(tf)
        medians   = state.medians.get(tf, {})
    if booster is None or not feat_cols:
        return None
    try:
        present = set(features_df.columns)
        n = len(features_df)
        X = np.empty((n, len(feat_cols)), dtype=np.float32)
        for j, c in enumerate(feat_cols):
            med = float(medians.get(c, 0.0))
            if c in present:
                col = features_df[c].to_numpy().astype(np.float64)
                col = np.where(np.isfinite(col), col, med)
                X[:, j] = col
            else:
                X[:, j] = med
        raw = np.asarray(booster.predict(X), dtype=float)
        if cal is not None:
            raw = np.asarray(cal.predict(raw), dtype=float)
        return raw
    except Exception as e:
        logger.warning(f"[MLBackend] Prédiction batch {tf}/{target} KO : {e}")
        return None


def predict_amplitude(state: TrainState, lock,
                      features_df: pl.DataFrame, tf: str) -> Optional[float]:
    """P(événement |return| > seuil) pour la dernière barre."""
    return predict_single(state, lock, features_df, tf, "amp")


def predict_direction(state: TrainState, lock,
                      features_df: pl.DataFrame, tf: str) -> Optional[float]:
    """P(hausse) pour la dernière barre."""
    return predict_single(state, lock, features_df, tf, "dir")
