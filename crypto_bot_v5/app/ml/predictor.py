"""
Module ML — Régression Logistique, Random Forest, XGBoost.
Adaptabilité au régime de marché, pondération des signaux, réentraînement auto.
"""
import logging
from typing import Dict, Any, Optional, List
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Imports optionnels
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logger.warning("[ML] scikit-learn absent — modèles ML indisponibles")

try:
    from xgboost import XGBClassifier
    HAS_XGB = True
except ImportError:
    HAS_XGB = False


class MLPredictor:
    """
    Prédicateur ML adaptatif.
    Cible : prédire si le prochain trade sera gagnant (1) ou perdant (0).
    Supporte la pondération par régime de marché.
    """

    def __init__(self, cfg: dict):
        self.cfg       = cfg.get("ml", {})
        self.model_name= self.cfg.get("model", "random_forest")
        self.min_samp  = self.cfg.get("min_samples", 500)
        self.window    = self.cfg.get("feature_window", 20)
        self.enabled   = self.cfg.get("enabled", False) and HAS_SKLEARN
        self._model    = None
        self._scaler   = None
        self._regime_weights: Dict[str, float] = {"trending": 1.0, "ranging": 0.8, "volatile": 0.6}
        self._trained  = False
        self._feature_cols: List[str] = []

    def _make_model(self):
        if self.model_name == "xgboost" and HAS_XGB:
            return XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.1,
                                  use_label_encoder=False, eval_metric="logloss",
                                  random_state=42, n_jobs=-1)
        elif self.model_name == "logistic":
            return LogisticRegression(max_iter=1000, C=1.0, random_state=42)
        else:
            return RandomForestClassifier(n_estimators=100, max_depth=6, min_samples_leaf=10,
                                           random_state=42, n_jobs=-1)

    def train(self, df: pd.DataFrame, trades: List[Dict]) -> Dict[str, Any]:
        """Entraîne le modèle sur l'historique des trades."""
        if not self.enabled:
            return {"status": "disabled"}
        if not HAS_SKLEARN:
            return {"status": "sklearn_missing"}
        if len(trades) < self.min_samp:
            return {"status": f"not_enough_data ({len(trades)}/{self.min_samp})"}

        from app.core.indicators import build_features
        feat_df = build_features(df, self.window)
        if feat_df.empty:
            return {"status": "feature_error"}

        # Aligne les trades sur les features
        X_rows, y_rows = [], []
        for t in trades:
            bar = t.get("bar")
            if bar is None or bar >= len(feat_df): continue
            row = feat_df.iloc[bar] if bar < len(feat_df) else None
            if row is None or row.isnull().any(): continue
            X_rows.append(row.values)
            y_rows.append(1 if (t.get("pnl") or 0) > 0 else 0)

        if len(X_rows) < 50:
            return {"status": f"not_enough_labeled ({len(X_rows)})"}

        X = np.array(X_rows)
        y = np.array(y_rows)
        self._feature_cols = list(feat_df.columns)

        self._scaler = StandardScaler()
        X_scaled     = self._scaler.fit_transform(X)
        self._model  = self._make_model()
        self._model.fit(X_scaled, y)
        self._trained = True

        # Cross-validation score
        cv_scores = cross_val_score(self._make_model(), X_scaled, y, cv=5, scoring="accuracy")
        logger.info(f"[ML] Modèle {self.model_name} entraîné — {len(X)} exemples | CV acc={np.mean(cv_scores):.3f}")

        return {
            "status":      "ok",
            "model":       self.model_name,
            "n_samples":   len(X),
            "cv_accuracy": round(float(np.mean(cv_scores)), 3),
            "cv_std":      round(float(np.std(cv_scores)), 3),
            "classes":     list(self._model.classes_),
        }

    def predict_proba(self, df: pd.DataFrame, regime: dict = None) -> Optional[float]:
        """
        Retourne la probabilité que le signal actuel soit gagnant [0-1].
        Pondère selon le régime de marché.
        """
        if not self._trained or self._model is None:
            return None
        try:
            from app.core.indicators import build_features
            feat = build_features(df, self.window)
            if feat.empty: return None
            row  = feat.iloc[-1]
            if row.isnull().any(): return None
            x    = self._scaler.transform(row.values.reshape(1, -1))
            prob = float(self._model.predict_proba(x)[0][1])

            # Pondération par régime
            if regime:
                weight = self._regime_weights.get(regime.get("regime",""), 1.0)
                prob   = prob * weight + (1 - weight) * 0.5  # régression vers 0.5 si régime faible

            return round(prob, 4)
        except Exception as e:
            logger.debug(f"[ML] predict_proba: {e}")
            return None

    def amplify_signal(self, base_score: float, df: pd.DataFrame, regime: dict = None) -> float:
        """Amplifie ou réduit le score de signal selon la prédiction ML."""
        if not self._trained:
            return base_score
        prob = self.predict_proba(df, regime)
        if prob is None:
            return base_score
        # Pondération : 70% score base + 30% signal ML
        amplified = base_score * 0.7 + prob * 0.3
        return round(min(1.0, amplified), 4)

    def update_regime_weights(self, results_by_regime: Dict[str, float]):
        """Met à jour les poids par régime basé sur la performance historique."""
        for regime, win_rate in results_by_regime.items():
            # Normalise le win_rate en poids [0.5, 1.5]
            self._regime_weights[regime] = round(max(0.3, min(1.5, win_rate / 50)), 3)
        logger.info(f"[ML] Poids régime mis à jour : {self._regime_weights}")

    @property
    def is_ready(self) -> bool:
        return self._trained and self._model is not None

    def feature_importance(self) -> Dict[str, float]:
        if not self._trained: return {}
        try:
            if hasattr(self._model, "feature_importances_"):
                imp = self._model.feature_importances_
                return dict(sorted(zip(self._feature_cols, imp.tolist()), key=lambda x: -x[1]))
        except Exception:
            pass
        return {}
