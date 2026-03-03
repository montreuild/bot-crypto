"""
Module 5 — Modèles ML prédictifs :
  - Logistic Regression
  - Random Forest
  - XGBoost (optionnel, import conditionnel)
Pondération des signaux ML selon régime de marché.
"""
import logging
import pickle
import os
from typing import Optional, Tuple, Dict

import numpy as np
import pandas as pd
from sklearn.linear_model    import LogisticRegression
from sklearn.ensemble        import RandomForestClassifier
from sklearn.preprocessing   import StandardScaler
from sklearn.model_selection import cross_val_score
from sklearn.pipeline        import Pipeline

from app.ml.features import extract_features, build_labels

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False
    logger.debug("[ML] XGBoost non disponible — utiliser random_forest ou logistic.")


class MLPredictor:
    """Prédicteur ML adaptable selon le régime de marché."""

    def __init__(self, cfg: dict):
        ml = cfg.get("ml", {})
        self.model_name   = ml.get("model", "random_forest")
        self.blend_weight = ml.get("blend_weight", 0.3)
        self.min_samples  = ml.get("min_samples", 200)
        self.window       = ml.get("feature_window", 50)
        self.trained      = False
        self.model_path   = "logs/ml_model.pkl"
        self._pipeline: Optional[Pipeline] = None
        self._regime_models: Dict[str, Pipeline] = {}

    def build_pipeline(self, regime: str = "all") -> Pipeline:
        """Construit le pipeline sklearn selon le modèle choisi et le régime."""
        if self.model_name == "logistic":
            clf = LogisticRegression(C=1.0, max_iter=500, random_state=42)
        elif self.model_name == "xgboost" and _HAS_XGB:
            clf = xgb.XGBClassifier(n_estimators=100, max_depth=4,
                                    learning_rate=0.05, use_label_encoder=False,
                                    eval_metric="logloss", random_state=42)
        else:
            clf = RandomForestClassifier(n_estimators=200, max_depth=6,
                                        min_samples_leaf=5, random_state=42, n_jobs=-1)
        return Pipeline([("scaler", StandardScaler()), ("clf", clf)])

    def train(self, df: pd.DataFrame, regime: str = "all") -> Dict:
        """Entraîne le modèle sur les données historiques."""
        features = extract_features(df, self.window)
        labels   = build_labels(df["close"].loc[features.index])
        labels   = labels.loc[features.index].dropna()
        features = features.loc[labels.index]

        if len(features) < self.min_samples:
            logger.warning(f"[ML] Pas assez de samples ({len(features)}/{self.min_samples})")
            return {"error": "insufficient_data"}

        X, y = features.values, labels.values
        pipeline = self.build_pipeline(regime)
        cv_scores = cross_val_score(pipeline, X, y, cv=5, scoring="roc_auc")
        pipeline.fit(X, y)

        self._regime_models[regime] = pipeline
        if regime == "all":
            self._pipeline = pipeline
        self.trained = True

        metrics = {
            "regime":     regime,
            "samples":    len(X),
            "cv_auc":     round(float(cv_scores.mean()), 3),
            "cv_std":     round(float(cv_scores.std()),  3),
            "features":   list(features.columns),
            "model":      self.model_name,
        }
        logger.info(f"[ML] Entraîné — {self.model_name} | AUC={metrics['cv_auc']:.3f}±{metrics['cv_std']:.3f} | n={len(X)}")
        return metrics

    def predict_proba(self, df: pd.DataFrame, regime: str = "all") -> float:
        """Retourne la probabilité d'un mouvement haussier (0-1)."""
        pipeline = self._regime_models.get(regime) or self._pipeline
        if pipeline is None or not self.trained:
            return 0.5
        features = extract_features(df, self.window)
        if features.empty:
            return 0.5
        try:
            prob = pipeline.predict_proba(features.values[-1:])
            return float(prob[0, 1])
        except Exception as e:
            logger.error(f"[ML] predict_proba : {e}")
            return 0.5

    def blend_signal(self, rule_score: float, rule_side: str,
                     df: pd.DataFrame, regime: str = "all") -> Tuple[float, str]:
        """
        Mélange le signal règle + ML selon blend_weight.
        Si le ML contredit fortement, réduit le score.
        """
        if not self.trained:
            return rule_score, rule_side
        prob = self.predict_proba(df, regime)
        ml_score = prob if rule_side == "long" else (1 - prob)
        blended  = (1 - self.blend_weight) * rule_score + self.blend_weight * ml_score
        # Si ML très contraire (prob < 0.3 pour long ou > 0.7 pour short), veto
        veto = (rule_side == "long"  and prob < 0.3) or \
               (rule_side == "short" and prob > 0.7)
        if veto:
            blended *= 0.5
            logger.debug(f"[ML] Veto ML — prob={prob:.2f}, score réduit à {blended:.2f}")
        return round(blended, 3), rule_side

    def save(self):
        os.makedirs("logs", exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump({"pipeline": self._pipeline, "regime_models": self._regime_models,
                         "trained": self.trained}, f)

    def load(self) -> bool:
        if not os.path.exists(self.model_path):
            return False
        with open(self.model_path, "rb") as f:
            data = pickle.load(f)
        self._pipeline       = data["pipeline"]
        self._regime_models  = data["regime_models"]
        self.trained         = data["trained"]
        logger.info("[ML] Modèle chargé depuis le disque.")
        return True
