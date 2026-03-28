"""Modèles ML prédictifs : Logistic Regression, Random Forest, XGBoost (optionnel)."""
import logging
import pickle
import os
from typing import Optional, Tuple, Dict

import numpy as np
import polars as pl
from sklearn.linear_model    import LogisticRegression
from sklearn.ensemble        import RandomForestClassifier
from sklearn.preprocessing   import StandardScaler
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
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
        self.confidence_threshold = ml.get("confidence_threshold", 0.55)
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
                                        min_samples_leaf=5, random_state=42, n_jobs=-1,
                                        class_weight="balanced")
        return Pipeline([("scaler", StandardScaler()), ("clf", clf)])

    def train(self, df: pl.DataFrame, regime: str = "all") -> Dict:
        """Entraîne le modèle sur les données historiques."""
        features = extract_features(df, self.window)
        if len(features) == 0:
            logger.warning(f"[ML] Pas assez de samples (0/{self.min_samples})")
            return {"error": "insufficient_data"}

        # extract_features supprime exactement `window` lignes du début (warmup).
        # L'offset est donc exact — garantit un alignement temporel correct
        # entre features[i] et le prix df["close"][window + i].
        offset = self.window
        close_aligned = df["close"][offset:]
        labels_raw    = build_labels(close_aligned)

        # Supprimer les NaN de fin (lookahead shift)
        n_valid  = len(labels_raw.drop_nulls())
        features = features[:n_valid]
        labels   = labels_raw[:n_valid]

        if len(features) < self.min_samples:
            logger.warning(f"[ML] Pas assez de samples ({len(features)}/{self.min_samples})")
            return {"error": "insufficient_data"}

        X = features.to_numpy()
        y = labels.to_numpy()

        # Vérification de l'équilibre des classes
        unique, counts = np.unique(y, return_counts=True)
        if len(unique) < 2:
            logger.warning(f"[ML] Données mono-classe ({dict(zip(unique, counts))}) — entraînement ignoré")
            return {"error": "single_class", "class_distribution": dict(zip(unique.tolist(), counts.tolist()))}
        minority_ratio = counts.min() / counts.sum()
        if minority_ratio < 0.05:
            logger.warning(f"[ML] Déséquilibre sévère : minorité {minority_ratio:.1%} — résultats potentiellement biaisés")

        pipeline  = self.build_pipeline(regime)
        # TimeSeriesSplit prevents temporal leakage (future data in train folds)
        tscv = TimeSeriesSplit(n_splits=5)
        cv_scores = cross_val_score(pipeline, X, y, cv=tscv, scoring="roc_auc")
        pipeline.fit(X, y)

        self._regime_models[regime] = pipeline
        if regime == "all":
            self._pipeline = pipeline
            self._last_feature_cols = features.columns
        self.trained = True

        metrics = {
            "regime":   regime,
            "samples":  len(X),
            "cv_auc":   round(float(cv_scores.mean()), 3),
            "cv_std":   round(float(cv_scores.std()),  3),
            "features": features.columns,
            "model":    self.model_name,
        }
        logger.info(f"[ML] Entraîné — {self.model_name} | AUC={metrics['cv_auc']:.3f}±{metrics['cv_std']:.3f} | n={len(X)}")
        return metrics

    def predict_proba(self, df: pl.DataFrame, regime: str = "all") -> float:
        """Retourne la probabilité d'un mouvement haussier (0-1)."""
        pipeline = self._regime_models.get(regime) or self._pipeline
        if pipeline is None or not self.trained:
            return 0.5
        features = extract_features(df, self.window)
        if len(features) == 0:
            return 0.5
        try:
            prob = pipeline.predict_proba(features.to_numpy()[-1:])
            return float(prob[0, 1])
        except Exception as e:
            logger.error(f"[ML] predict_proba : {e}")
            return 0.5

    def _effective_blend_weight(self, ml_confidence: float) -> float:
        """Compute the effective blend weight adjusted for ML confidence.

        ml_confidence is normalised to [0, 1] (0 = 50/50 prediction, 1 = certain).
        Below confidence_threshold the weight is scaled down proportionally.
        A small floor (0.01) prevents division-by-zero when threshold == 0.5.
        """
        threshold_margin = max(self.confidence_threshold - 0.5, 0.01)
        return self.blend_weight * min(1.0, ml_confidence / threshold_margin)

    def blend_signal(self, rule_score: float, rule_side: str,
                     df: pl.DataFrame, regime: str = "all") -> Tuple[float, str]:
        """
        Mélange le signal règle + ML selon blend_weight.
        Si le ML contredit fortement, réduit le score.
        Prefers regime-specific model when available.
        """
        if not self.trained:
            return rule_score, rule_side
        # Use regime-specific model if available, fallback to general
        prob = self.predict_proba(df, regime)
        ml_score = prob if rule_side == "long" else (1 - prob)
        # Confidence check: if ML is not confident enough, reduce its weight
        ml_confidence = abs(prob - 0.5) * 2  # 0.0 = no confidence, 1.0 = max confidence
        effective_weight = self._effective_blend_weight(ml_confidence)
        blended  = (1 - effective_weight) * rule_score + effective_weight * ml_score
        veto = (rule_side == "long"  and prob < 0.3) or \
               (rule_side == "short" and prob > 0.7)
        if veto:
            blended *= 0.5
            logger.debug(f"[ML] Veto ML — prob={prob:.2f}, score réduit à {blended:.2f}")
        return round(blended, 3), rule_side

    def amplify_signal(self, base_score: float, df: pl.DataFrame,
                       regime: str = "all") -> float:
        """
        Amplifie ou réduit le score de signal selon la prédiction ML.
        Pondération : 70% score règle + 30% signal ML.
        """
        if not self.trained:
            return base_score
        prob = self.predict_proba(df, regime)
        return round(min(1.0, base_score * 0.7 + prob * 0.3), 4)

    def update_regime_weights(self, results_by_regime: dict):
        """
        Met à jour les poids des modèles par régime selon la performance historique.
        results_by_regime : {"trending": win_rate_pct, "ranging": …, "volatile": …}
        """
        for regime, win_rate in results_by_regime.items():
            weight = round(max(0.3, min(1.5, win_rate / 50)), 3)
            if regime not in self._regime_models and self._pipeline:
                self._regime_models[regime] = self._pipeline
            logger.info(f"[ML] Poids régime '{regime}' mis à jour : {weight}")

    @property
    def is_ready(self) -> bool:
        """True si le modèle est entraîné et prêt à prédire."""
        return self.trained and self._pipeline is not None

    def feature_importance(self) -> dict:
        """Retourne l'importance des features (si le modèle le supporte)."""
        if not self.trained or self._pipeline is None:
            return {}
        try:
            clf = self._pipeline.named_steps.get("clf")
            if clf is None:
                return {}
            if hasattr(clf, "feature_importances_"):
                imp  = clf.feature_importances_
                cols = getattr(self, "_last_feature_cols", [f"f{i}" for i in range(len(imp))])
                return dict(sorted(zip(cols, imp.tolist()), key=lambda x: -x[1]))
        except Exception as e:
            logger.debug(f"[MLPredictor] feature_importances : {e}")
        return {}

    def save(self):
        import hashlib, hmac as _hmac
        os.makedirs("logs", exist_ok=True)
        payload = pickle.dumps({"pipeline": self._pipeline, "regime_models": self._regime_models,
                                "trained": self.trained})
        sig = _hmac.new(self._hmac_key(), payload, hashlib.sha256).digest()
        with open(self.model_path, "wb") as f:
            f.write(sig)
            f.write(payload)

    def load(self) -> bool:
        import hashlib, hmac as _hmac
        if not os.path.exists(self.model_path):
            return False
        with open(self.model_path, "rb") as f:
            raw = f.read()
        if len(raw) < 32:
            logger.error("[ML] Fichier modèle corrompu ou trop court — ignoré.")
            return False
        sig_stored = raw[:32]
        payload    = raw[32:]
        expected   = _hmac.new(self._hmac_key(), payload, hashlib.sha256).digest()
        if not _hmac.compare_digest(sig_stored, expected):
            logger.error("[ML] Signature HMAC invalide — fichier modèle potentiellement altéré. "
                         "Supprimez logs/ml_model.pkl et réentraînez.")
            return False
        data = pickle.loads(payload)    # noqa: S301 — payload vérifié par HMAC ci-dessus
        self._pipeline       = data["pipeline"]
        self._regime_models  = data["regime_models"]
        self.trained         = data["trained"]
        logger.info("[ML] Modèle chargé et vérifié depuis le disque.")
        return True

    @staticmethod
    def _hmac_key() -> bytes:
        """Clé HMAC dérivée du nom de machine — locale, non partagée."""
        import socket, hashlib
        host = socket.gethostname().encode()
        return hashlib.sha256(b"crypto_bot_ml_v1:" + host).digest()
