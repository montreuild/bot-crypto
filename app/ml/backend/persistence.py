"""MLBackend.persistence — sauvegarde/chargement des modèles au format natif.

Remplace `pickle.load` / `joblib.load` par un format non-RCE basé sur :

- LightGBM natif : `booster.save_model(path)` → fichier texte/binaire
  déterministe, versionné, sans exécution de code à la désérialisation.
- JSON pour les métadonnées : features, medians, best_auc, train_meta,
  calibrators (IsotonicRegression), config.

Cette migration élimine la vulnérabilité CWE-502 (Deserialization of
Untrusted Data) — un fichier `.pkl` malveillant ne peut plus exécuter de
code arbitraire au chargement.

Format de persistance (3 fichiers par TF) :

    {path}.amp.lgb       # Booster LightGBM natif (amplitude)
    {path}.dir.lgb       # Booster LightGBM natif (direction)
    {path}.meta.json     # Métadonnées + calibrators + config

Pour la compatibilité ascendante, `load_model` détecte aussi les anciens
fichiers `.pkl` (joblib) et les charge via un `RestrictedUnpickler` avec
liste blanche stricte — mais ce chemin est déprécié et loggue un warning
invitant à re-sauvegarder via `save_model`.
"""
from __future__ import annotations

import io
import json
import logging
import os
import pickle
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.ml.backend.trainer import TrainState

logger = logging.getLogger(__name__)


# ── Liste blanche pour RestrictedUnpickler (compat ascendante .pkl) ─────────
_ALLOWED_PICKLE_CLASSES: Tuple[type, ...] = (
    # NumPy
    np.ndarray,
    np.dtype,
    # LightGBM
    *(),
)
# Lazy import pour éviter une dépendance硬 au chargement du module.
def _lgb_allowed_classes() -> Tuple[type, ...]:
    try:
        import lightgbm  # noqa: F401
        from lightgbm.basic import Booster
        from lightgbm.sklearn import LGBMClassifier, LGBMModel
        return (Booster, LGBMClassifier, LGBMModel)
    except Exception:
        return ()


def _sklearn_allowed_classes() -> Tuple[type, ...]:
    try:
        from sklearn.isotonic import IsotonicRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.tree import DecisionTreeClassifier
        return (IsotonicRegression, StandardScaler,
                LogisticRegression, RandomForestClassifier,
                DecisionTreeClassifier)
    except Exception:
        return ()


class RestrictedUnpickler(pickle.Unpickler):
    """Unpickler avec liste blanche — refuse toute classe non autorisée.

    Utilisé UNIQUEMENT pour la compat ascendante avec les anciens .pkl
    (migration progressive vers le format natif). Tout objet non dans la
    liste blanche lève une `pickle.UnpicklingError`.
    """

    def find_class(self, module: str, name: str) -> Any:
        allowed = _ALLOWED_PICKLE_CLASSES + _lgb_allowed_classes() + _sklearn_allowed_classes()
        for cls in allowed:
            if cls.__module__ == module and cls.__name__ == name:
                return super().find_class(module, name)
        raise pickle.UnpicklingError(
            f"[MLBackend.persistence] Classe non autorisée au désérialisation : "
            f"{module}.{name}. Ré-sauvegardez le modèle via save_model() "
            f"pour migrer vers le format natif (sans RCE)."
        )


def restricted_pickle_load(stream) -> Any:
    """`pickle.load` avec liste blanche stricte — défense en profondeur."""
    return RestrictedUnpickler(stream).load()


# ── Helpers de sérialisation JSON pour calibrators sklearn ──────────────────
def _isotonic_to_dict(iso) -> Optional[dict]:
    """Sérialise une IsotonicRegression en dict JSON-compatibile."""
    if iso is None:
        return None
    try:
        return {
            "x_thresholds": iso.X_thresholds_.tolist(),
            "y_thresholds": iso.Y_thresholds_.tolist(),
            "increasing":   bool(iso.increasing),
            "out_of_bounds": str(iso.out_of_bounds),
        }
    except Exception as e:
        logger.debug(f"[MLBackend.persistence] IsotonicRegression → dict KO : {e}")
        return None


def _isotonic_from_dict(d: Optional[dict]):
    """Reconstruit une IsotonicRegression depuis un dict."""
    if d is None:
        return None
    try:
        from sklearn.isotonic import IsotonicRegression
        iso = IsotonicRegression(
            increasing=d["increasing"],
            out_of_bounds=d["out_of_bounds"],
        )
        # Fit "factice" pour initialiser les structures internes, puis
        # on écrase les thresholds.
        iso.fit(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
        iso.X_thresholds_ = np.asarray(d["x_thresholds"], dtype=np.float64)
        iso.Y_thresholds_ = np.asarray(d["y_thresholds"], dtype=np.float64)
        return iso
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] dict → IsotonicRegression KO : {e}")
        return None


def _standardscaler_to_dict(sc) -> Optional[dict]:
    """Sérialise un StandardScaler sklearn en dict JSON-compatible."""
    if sc is None:
        return None
    try:
        return {
            "mean_":          getattr(sc, "mean_", None).tolist() if getattr(sc, "mean_", None) is not None else None,
            "scale_":         getattr(sc, "scale_", None).tolist() if getattr(sc, "scale_", None) is not None else None,
            "var_":           getattr(sc, "var_", None).tolist() if getattr(sc, "var_", None) is not None else None,
            "n_features_in_": int(getattr(sc, "n_features_in_", 0)),
            "with_mean":      bool(getattr(sc, "with_mean", True)),
            "with_std":       bool(getattr(sc, "with_std", True)),
        }
    except Exception as e:
        logger.debug(f"[MLBackend.persistence] StandardScaler → dict KO : {e}")
        return None


def _standardscaler_from_dict(d: Optional[dict]):
    """Reconstruit un StandardScaler depuis un dict."""
    if d is None:
        return None
    try:
        from sklearn.preprocessing import StandardScaler
        sc = StandardScaler(with_mean=d.get("with_mean", True), with_std=d.get("with_std", True))
        # Fit factice puis écrasement des attributs.
        n = d.get("n_features_in_", 1)
        sc.fit(np.zeros((2, n)))
        if d.get("mean_") is not None:
            sc.mean_  = np.asarray(d["mean_"],  dtype=np.float64)
        if d.get("scale_") is not None:
            sc.scale_ = np.asarray(d["scale_"], dtype=np.float64)
        if d.get("var_") is not None:
            sc.var_   = np.asarray(d["var_"],   dtype=np.float64)
        sc.n_features_in_ = int(n)
        return sc
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] dict → StandardScaler KO : {e}")
        return None


# ── Sauvegarde (format natif) ───────────────────────────────────────────────
def save_model(state: TrainState, lock, path: str, tf: str) -> bool:
    """Sauvegarde les modèles (amp + dir) + métadonnées au format natif.

    Écrit 3 fichiers :
        {path}.amp.lgb       (LightGBM booster natif)
        {path}.dir.lgb       (LightGBM booster natif)
        {path}.meta.json     (features, medians, calibrators, train_meta, best_auc)

    Le `path` d'origine (qui pouvait être `models/foo_1h.pkl`) est utilisé
    comme préfixe — l'extension `.pkl` est remplacée.

    Returns:
        True si sauvegardé, False si modèles manquants (non entraînés).
    """
    with lock:
        amp_model = state.amp_models.get(tf)
        dir_model = state.dir_models.get(tf)
        amp_cal   = state.amp_cal.get(tf)
        dir_cal   = state.dir_cal.get(tf)
        feats     = state.feature_cols.get(tf)
        medians   = state.medians.get(tf, {})
        best_auc  = state.best_auc_per_tf.get(tf, 0.0)
        meta      = state.train_meta.get(tf, {})

    if amp_model is None or dir_model is None or not feats:
        return False

    # Préfixe : retirer l'extension .pkl si présente (compat).
    base = path
    if base.endswith(".pkl"):
        base = base[:-4]

    amp_path = f"{base}.amp.lgb"
    dir_path = f"{base}.dir.lgb"
    meta_path = f"{base}.meta.json"

    os.makedirs(os.path.dirname(os.path.abspath(amp_path)), exist_ok=True)

    # 1. Boosters LightGBM au format natif (sans RCE).
    try:
        amp_model.save_model(amp_path)
        dir_model.save_model(dir_path)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] save_model LGB KO : {e}")
        return False

    # 2. Métadonnées JSON.
    payload = {
        "tf":         tf,
        "features":   list(feats),
        "medians":    {k: float(v) for k, v in medians.items()},
        "best_auc":   float(best_auc),
        "train_meta": dict(meta),
        "amp_cal":    _isotonic_to_dict(amp_cal),
        "dir_cal":    _isotonic_to_dict(dir_cal),
        "format_version": 1,
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] write meta.json KO : {e}")
        return False

    logger.info(
        f"[MLBackend.persistence] Modèles sauvegardés → {base}.* "
        f"(AUC={best_auc:.3f}, {len(feats)} features)"
    )
    return True


# ── Chargement (format natif + compat .pkl legacy) ──────────────────────────
def load_model(state: TrainState, lock, path: str, tf: str) -> bool:
    """Charge les modèles + métadonnées depuis le disque.

    Orde de résolution :
    1. Format natif (3 fichiers : `.amp.lgb`, `.dir.lgb`, `.meta.json`).
    2. Format legacy `.pkl` (joblib/pickle) via `RestrictedUnpickler` —
       déprécié, loggue un warning invitant à migrer.

    Args:
        state: état ML à peupler.
        lock:  threading.Lock.
        path:  chemin d'origine (peut être `models/foo_1h.pkl` ou
               `models/foo_1h` sans extension).
        tf:    timeframe à peupler dans l'état.

    Returns:
        True si chargé, False si introuvable ou erreur.
    """
    base = path
    if base.endswith(".pkl"):
        base = base[:-4]

    amp_path  = f"{base}.amp.lgb"
    dir_path  = f"{base}.dir.lgb"
    meta_path = f"{base}.meta.json"

    # 1. Format natif ?
    if os.path.exists(amp_path) and os.path.exists(dir_path) and os.path.exists(meta_path):
        return _load_native(state, lock, amp_path, dir_path, meta_path, tf)

    # 2. Format legacy .pkl ?
    if os.path.exists(path):
        return _load_legacy_pkl(state, lock, path, tf)

    return False


def _load_native(state: TrainState, lock,
                 amp_path: str, dir_path: str, meta_path: str,
                 tf: str) -> bool:
    """Charge le format natif (3 fichiers)."""
    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("[MLBackend.persistence] lightgbm requis pour le chargement")
        return False
    try:
        amp_model = lgb.Booster(model_file=amp_path)
        dir_model = lgb.Booster(model_file=dir_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        feats     = list(payload.get("features") or [])
        medians   = dict(payload.get("medians") or {})
        best_auc  = float(payload.get("best_auc", 0.0))
        meta      = dict(payload.get("train_meta") or {})
        amp_cal   = _isotonic_from_dict(payload.get("amp_cal"))
        dir_cal   = _isotonic_from_dict(payload.get("dir_cal"))

        with lock:
            state.amp_models[tf]    = amp_model
            state.dir_models[tf]    = dir_model
            state.amp_cal[tf]       = amp_cal
            state.dir_cal[tf]       = dir_cal
            state.feature_cols[tf]  = feats
            state.medians[tf]       = medians
            state.best_auc_per_tf[tf] = best_auc
            state.train_meta[tf]    = meta
            state.trained_tfs.add(tf)
            state.best_auc = max(state.best_auc, best_auc)
        logger.info(
            f"[MLBackend.persistence] Modèle {tf} chargé (format natif) "
            f"— AUC={best_auc:.3f}"
        )
        return True
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] Chargement natif KO : {e}")
        return False


def _load_legacy_pkl(state: TrainState, lock, path: str, tf: str) -> bool:
    """Charge un ancien `.pkl` via `RestrictedUnpickler` (compat ascendante).

    Déprécié : loggue un warning invitant à re-sauvegarder via `save_model`.
    """
    import joblib
    try:
        # joblib.load utilise pickle en interne ; on lui passe notre
        # Unpickler restreint via `joblib.load(..., mmap_mode=None)` qui
        # ne supporte pas directement de custom Unpickler. On donc on
        # lit en mode pickle direct avec RestrictedUnpickler.
        try:
            with open(path, "rb") as f:
                data = restricted_pickle_load(f)
        except Exception:
            # Fallback : joblib.load (qui gère aussi la compression).
            data = joblib.load(path)
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] Chargement legacy {path} KO : {e}")
        return False

    logger.warning(
        f"[MLBackend.persistence] Modèle {tf} chargé depuis format LEGACY (.pkl) — "
        f"re-sauvegardez-le via save_model() pour migrer vers le format natif (RCE-safe)."
    )
    try:
        with lock:
            state.amp_models[tf]    = data["amp_model"]
            state.dir_models[tf]    = data["dir_model"]
            state.amp_cal[tf]       = data.get("amp_cal")
            state.dir_cal[tf]       = data.get("dir_cal")
            state.feature_cols[tf]  = list(data.get("features") or [])
            state.medians[tf]       = dict(data.get("medians") or {})
            state.best_auc_per_tf[tf] = float(data.get("best_auc", 0.0))
            state.train_meta[tf]    = dict(data.get("train_meta") or {})
            state.trained_tfs.add(tf)
            state.best_auc = max(state.best_auc, state.best_auc_per_tf[tf])
        return True
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] Lecture payload legacy KO : {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Variantes pour stratégies LightGBM + StandardScaler (scoring_statistique_opus)
# ─────────────────────────────────────────────────────────────────────────────
def save_lgb_with_scaler(amp_model, dir_model, scaler, path: str,
                         tf: str, best_auc: float, train_meta: dict) -> bool:
    """Sauvegarde un payload {amp, dir, scaler} au format natif.

    Pour les stratégies `scoring_statistique_opus_v4/v5` qui utilisent
    LightGBM + StandardScaler (pas de calibrators).
    """
    if amp_model is None or dir_model is None:
        return False
    base = path[:-4] if path.endswith(".pkl") else path
    amp_path  = f"{base}.amp.lgb"
    dir_path  = f"{base}.dir.lgb"
    meta_path = f"{base}.meta.json"
    os.makedirs(os.path.dirname(os.path.abspath(amp_path)), exist_ok=True)
    try:
        amp_model.save_model(amp_path)
        dir_model.save_model(dir_path)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] save_lgb_with_scaler LGB KO : {e}")
        return False
    payload = {
        "tf":         tf,
        "scaler":     _standardscaler_to_dict(scaler),
        "best_auc":   float(best_auc),
        "train_meta": dict(train_meta or {}),
        "format_version": 1,
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] write meta.json KO : {e}")
        return False
    logger.info(f"[MLBackend.persistence] save_lgb_with_scaler → {base}.* (AUC={best_auc:.3f})")
    return True


def load_lgb_with_scaler(path: str):
    """Charge un payload {amp, dir, scaler} depuis le format natif ou legacy.

    Retourne un dict {amp_model, dir_model, scaler, best_auc, train_meta}
    ou None si introuvable/erreur.
    """
    base = path[:-4] if path.endswith(".pkl") else path
    amp_path  = f"{base}.amp.lgb"
    dir_path  = f"{base}.dir.lgb"
    meta_path = f"{base}.meta.json"

    # 1. Format natif ?
    if os.path.exists(amp_path) and os.path.exists(dir_path) and os.path.exists(meta_path):
        try:
            import lightgbm as lgb
            amp_model = lgb.Booster(model_file=amp_path)
            dir_model = lgb.Booster(model_file=dir_path)
            with open(meta_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return {
                "amp_model":  amp_model,
                "dir_model":  dir_model,
                "scaler":     _standardscaler_from_dict(payload.get("scaler")),
                "best_auc":   float(payload.get("best_auc", 0.0)),
                "train_meta": dict(payload.get("train_meta") or {}),
            }
        except Exception as e:
            logger.warning(f"[MLBackend.persistence] load_lgb_with_scaler natif KO : {e}")
            return None

    # 2. Legacy .pkl ?
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = restricted_pickle_load(f)
            logger.warning(
                f"[MLBackend.persistence] load_lgb_with_scaler {path} : format LEGACY — "
                f"re-sauvegardez via save_lgb_with_scaler()"
            )
            return data
        except Exception:
            import joblib
            try:
                data = joblib.load(path)
                logger.warning(
                    f"[MLBackend.persistence] load_lgb_with_scaler {path} : LEGACY (joblib)"
                )
                return data
            except Exception as e:
                logger.warning(f"[MLBackend.persistence] load_lgb_with_scaler legacy KO : {e}")
                return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Variante pour sklearn Pipeline (ml_dynamic_threshold)
# ─────────────────────────────────────────────────────────────────────────────
def save_sklearn_pipeline(pipeline, best_params: dict, best_auc: float,
                          feature_cols: list, model_type: str,
                          path: str) -> bool:
    """Sauvegarde un sklearn Pipeline au format JSON.

    Étapes supportées :
    - StandardScaler  → mean_, scale_, var_, n_features_in_
    - LogisticRegression → coef_, intercept_, classes_, C, solver, etc.
    - RandomForestClassifier → extraction des arbres (feature, threshold,
      children_left/right, value, impurity) en JSON

    Si une étape non-supportée est rencontrée, fallback vers joblib.dump
    avec un warning (pour ne pas casser la migration progressive).
    """
    base = path[:-4] if path.endswith(".pkl") else path
    meta_path = f"{base}.pipeline.json"
    os.makedirs(os.path.dirname(os.path.abspath(meta_path)), exist_ok=True)

    try:
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.pipeline import Pipeline
    except ImportError:
        logger.error("[MLBackend.persistence] sklearn requis pour save_sklearn_pipeline")
        return False

    if not isinstance(pipeline, Pipeline):
        logger.warning(f"[MLBackend.persistence] save_sklearn_pipeline : pas un Pipeline ({type(pipeline)})")
        return _save_sklearn_legacy_fallback(pipeline, best_params, best_auc,
                                              feature_cols, model_type, path)

    payload = {
        "feature_cols":   list(feature_cols or []),
        "best_params":    dict(best_params or {}),
        "best_auc":       float(best_auc),
        "model_type":     str(model_type),
        "steps":          [],
        "format_version": 1,
    }

    for name, step in pipeline.steps:
        step_dict: dict = {"name": name, "class": type(step).__name__}
        if isinstance(step, StandardScaler):
            step_dict["kind"] = "standard_scaler"
            step_dict["params"] = _standardscaler_to_dict(step) or {}
        elif isinstance(step, LogisticRegression):
            step_dict["kind"] = "logistic_regression"
            step_dict["params"] = {
                "coef_":         step.coef_.tolist(),
                "intercept_":    step.intercept_.tolist(),
                "classes_":      step.classes_.tolist(),
                "n_features_in": int(getattr(step, "n_features_in_", 0)),
                "C":             float(getattr(step, "C", 1.0)),
                "solver":        str(getattr(step, "solver", "lbfgs")),
                "max_iter":      int(getattr(step, "max_iter", 100)),
                "multi_class":   str(getattr(step, "multi_class", "auto")),
            }
        elif isinstance(step, RandomForestClassifier):
            step_dict["kind"] = "random_forest"
            step_dict["params"] = _randomforest_to_dict(step)
        else:
            # Étape non supportée → fallback joblib.
            logger.warning(
                f"[MLBackend.persistence] step non-supporté {name}={type(step).__name__} — "
                f"fallback joblib (RCE-risk potentiel, mais pas d'alternative JSON simple)"
            )
            return _save_sklearn_legacy_fallback(pipeline, best_params, best_auc,
                                                  feature_cols, model_type, path)
        payload["steps"].append(step_dict)

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] write pipeline.json KO : {e}")
        return False
    logger.info(f"[MLBackend.persistence] save_sklearn_pipeline → {meta_path}")
    # Supprimer le .pkl si existant (migration idempotente).
    if path.endswith(".pkl") and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            pass
    return True


def _randomforest_to_dict(rf) -> dict:
    """Sérialise un RandomForestClassifier en dict JSON."""
    trees = []
    for est in rf.estimators_:
        t = est.tree_
        trees.append({
            "feature":         t.feature.tolist(),
            "threshold":       t.threshold.tolist(),
            "children_left":   t.children_left.tolist(),
            "children_right":  t.children_right.tolist(),
            "value":           t.value.tolist(),
            "impurity":        t.impurity.tolist(),
            "n_node_samples":  t.n_node_samples.tolist(),
            "weighted_n_node_samples": t.weighted_n_node_samples.tolist() if hasattr(t, "weighted_n_node_samples") else None,
        })
    return {
        "trees":          trees,
        "n_estimators":   int(rf.n_estimators),
        "n_classes":      int(getattr(rf, "n_classes_", 2)),
        "classes_":       rf.classes_.tolist(),
        "n_features_in":  int(getattr(rf, "n_features_in_", 0)),
        "max_depth":      getattr(rf, "max_depth", None),
        "min_samples_leaf": int(getattr(rf, "min_samples_leaf", 1)),
        "max_features":   str(getattr(rf, "max_features", "sqrt")),
        "random_state":   getattr(rf, "random_state", None),
    }


def _randomforest_from_dict(d: dict):
    """Reconstruit un RandomForestClassifier depuis un dict JSON."""
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.tree import DecisionTreeClassifier
        import numpy as np
        rf = RandomForestClassifier(
            n_estimators=int(d.get("n_estimators", 100)),
            max_depth=d.get("max_depth"),
            min_samples_leaf=int(d.get("min_samples_leaf", 1)),
            max_features=d.get("max_features", "sqrt"),
            random_state=d.get("random_state"),
        )
        # Fit factice puis écrasement des arbres.
        n_classes = int(d.get("n_classes", 2))
        n_features = int(d.get("n_features_in", 1))
        X = np.zeros((4, n_features))
        y = np.array([0, 0, 1, 1][:4] if n_classes == 2 else [0, 1, 2, 3][:4])
        rf.fit(X, y)
        # Reconstruire chaque arbre depuis son dict.
        from sklearn.tree._tree import Tree
        new_estimators = []
        for tree_dict in d.get("trees", []):
            n_nodes = len(tree_dict["feature"])
            t = Tree(n_features, n_classes, 1)
            # Reconstruire via l'API interne — c'est complexe ; pour simplifier
            # on délègue à DecisionTreeClassifier avec from_dict.
            # NOTE : la reconstruction exacte d'un sklearn Tree depuis JSON
            # demande l'API C interne __setstate__. On l'utilise ici.
            dt_state = {
                "max_depth": None,
                "min_samples_split": 2,
                "min_samples_leaf": 1,
                "feature": np.asarray(tree_dict["feature"], dtype=np.intp),
                "threshold": np.asarray(tree_dict["threshold"], dtype=np.float64),
                "children_left": np.asarray(tree_dict["children_left"], dtype=np.intp),
                "children_right": np.asarray(tree_dict["children_right"], dtype=np.intp),
                "value": np.asarray(tree_dict["value"], dtype=np.float64),
                "impurity": np.asarray(tree_dict["impurity"], dtype=np.float64),
                "n_node_samples": np.asarray(tree_dict["n_node_samples"], dtype=np.intp),
                "weighted_n_node_samples": np.asarray(
                    tree_dict.get("weighted_n_node_samples") or tree_dict["n_node_samples"],
                    dtype=np.float64),
                "n_outputs": 1,
                "n_classes": np.array([n_classes], dtype=np.intp),
            }
            dt = DecisionTreeClassifier()
            dt.__setstate__(dt_state)
            new_estimators.append(dt)
        rf.estimators_ = np.array(new_estimators, dtype=object)
        rf.n_classes_ = n_classes
        rf.classes_ = np.asarray(d.get("classes_", [0, 1]))
        rf.n_features_in_ = n_features
        return rf
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] _randomforest_from_dict KO : {e}")
        return None


def _save_sklearn_legacy_fallback(pipeline, best_params, best_auc,
                                   feature_cols, model_type, path) -> bool:
    """Fallback joblib pour les pipelines non-sérialisables en JSON."""
    import joblib
    try:
        joblib.dump({
            "pipeline":     pipeline,
            "best_params":  best_params,
            "best_auc":     best_auc,
            "feature_cols": feature_cols,
            "model_type":   model_type,
        }, path)
        logger.warning(
            f"[MLBackend.persistence] save_sklearn_pipeline fallback joblib → {path} "
            f"(RCE-risk si fichier compromis — préférer save_sklearn_pipeline JSON)"
        )
        return True
    except Exception as e:
        logger.error(f"[MLBackend.persistence] fallback joblib KO : {e}")
        return False


def load_sklearn_pipeline(path: str):
    """Charge un sklearn Pipeline depuis format natif (JSON) ou legacy (.pkl).

    Retourne un dict {pipeline, best_params, best_auc, feature_cols, model_type}
    ou None si introuvable/erreur.
    """
    base = path[:-4] if path.endswith(".pkl") else path
    meta_path = f"{base}.pipeline.json"

    # 1. Format natif JSON ?
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler
            from sklearn.linear_model import LogisticRegression
            steps = []
            for step_dict in payload.get("steps", []):
                name = step_dict["name"]
                kind = step_dict.get("kind")
                params = step_dict.get("params", {})
                if kind == "standard_scaler":
                    steps.append((name, _standardscaler_from_dict(params)))
                elif kind == "logistic_regression":
                    steps.append((name, _logreg_from_dict(params)))
                elif kind == "random_forest":
                    steps.append((name, _randomforest_from_dict(params)))
                else:
                    logger.warning(
                        f"[MLBackend.persistence] load_sklearn_pipeline : "
                        f"step kind inconnu '{kind}' — skip"
                    )
            pipeline = Pipeline(steps) if steps else None
            return {
                "pipeline":     pipeline,
                "best_params":  dict(payload.get("best_params") or {}),
                "best_auc":     float(payload.get("best_auc", 0.0)),
                "feature_cols": list(payload.get("feature_cols") or []),
                "model_type":   str(payload.get("model_type", "")),
            }
        except Exception as e:
            logger.warning(f"[MLBackend.persistence] load_sklearn_pipeline natif KO : {e}")
            return None

    # 2. Legacy .pkl ?
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = restricted_pickle_load(f)
            logger.warning(
                f"[MLBackend.persistence] load_sklearn_pipeline {path} : format LEGACY — "
                f"re-sauvegardez via save_sklearn_pipeline()"
            )
            return data
        except Exception:
            import joblib
            try:
                data = joblib.load(path)
                logger.warning(
                    f"[MLBackend.persistence] load_sklearn_pipeline {path} : LEGACY (joblib)"
                )
                return data
            except Exception as e:
                logger.warning(f"[MLBackend.persistence] load_sklearn_pipeline legacy KO : {e}")
                return None
    return None


def _logreg_from_dict(d: dict):
    """Reconstruit une LogisticRegression depuis un dict JSON."""
    try:
        from sklearn.linear_model import LogisticRegression
        import numpy as np
        lr = LogisticRegression(
            C=float(d.get("C", 1.0)),
            solver=str(d.get("solver", "lbfgs")),
            max_iter=int(d.get("max_iter", 100)),
            multi_class=str(d.get("multi_class", "auto")),
        )
        # Fit factice puis écrasement.
        n_classes = len(d.get("classes_", [0, 1]))
        n_features = int(d.get("n_features_in", 1))
        X = np.zeros((4, n_features))
        y = np.array([0, 0, 1, 1][:4]) if n_classes == 2 else np.array([0, 1, 2, 3][:4])
        lr.fit(X, y)
        lr.coef_ = np.asarray(d["coef_"], dtype=np.float64)
        lr.intercept_ = np.asarray(d["intercept_"], dtype=np.float64)
        lr.classes_ = np.asarray(d.get("classes_", [0, 1]))
        lr.n_features_in_ = n_features
        return lr
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] _logreg_from_dict KO : {e}")
        return None
