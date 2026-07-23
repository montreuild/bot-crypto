"""MLBackend.persistence — sauvegarde/chargement des modèles au format natif.

Remplace `pickle.load` / `joblib.load` par un format non-RCE basé sur :

- LightGBM natif : `booster.save_model(path)` → fichier texte/binaire
  déterministe, versionné, sans exécution de code à la désérialisation.
- JSON pour les métadonnées : features, medians, best_auc, train_meta,
  calibrators (IsotonicRegression native), config.

Cette migration élimine la vulnérabilité CWE-502 (Deserialization of
Untrusted Data) — un fichier `.pkl` malveillant ne peut plus exécuter de
code arbitraire au chargement.

Format de persistance (3 fichiers par TF) :

    {path}.amp.lgb       # Booster LightGBM natif (amplitude)
    {path}.dir.lgb       # Booster LightGBM natif (direction)
    {path}.meta.json     # Métadonnées + calibrators + config

phase6-sklearn-removal : tous les helpers sklearn (StandardScaler, Pipeline,
RandomForest, LogisticRegression) ont été supprimés. La calibration isotone
utilise désormais une implémentation native (``app.ml.backend.isotonic``).
Plus aucun import sklearn dans ce module.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.ml.backend.trainer import TrainState

logger = logging.getLogger(__name__)


# ── Liste blanche pour RestrictedUnpickler (compat ascendante .pkl) ─────────
# phase6 : seuls NumPy et LightGBM sont autorisés — plus de sklearn.
_ALLOWED_PICKLE_CLASSES: Tuple[type, ...] = (
    # NumPy
    np.ndarray,
    np.dtype,
)


def _lgb_allowed_classes() -> Tuple[type, ...]:
    """Lazy import pour éviter une dépendance hard au chargement du module."""
    try:
        from lightgbm.basic import Booster
        from lightgbm.sklearn import LGBMClassifier, LGBMModel
        return (Booster, LGBMClassifier, LGBMModel)
    except Exception:
        return ()


class RestrictedUnpickler(pickle.Unpickler):
    """Unpickler avec liste blanche — refuse toute classe non autorisée.

    Utilisé UNIQUEMENT pour la compat ascendante avec les anciens .pkl
    (migration progressive vers le format natif). Tout objet non dans la
    liste blanche lève une `pickle.UnpicklingError`.
    """

    def find_class(self, module: str, name: str) -> Any:
        allowed = _ALLOWED_PICKLE_CLASSES + _lgb_allowed_classes()
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


# ── Helpers de sérialisation JSON pour IsotonicRegression native ────────────
def _isotonic_to_dict(iso) -> Optional[dict]:
    """Sérialise une IsotonicRegression (native) en dict JSON-compatible."""
    if iso is None:
        return None
    try:
        return {
            "x_thresholds":  iso.X_thresholds_.tolist() if iso.X_thresholds_ is not None else [],
            "y_thresholds":  iso.Y_thresholds_.tolist() if iso.Y_thresholds_ is not None else [],
            "increasing":    bool(iso.increasing),
            "out_of_bounds": str(iso.out_of_bounds),
            "y_min":         iso.y_min,
            "y_max":         iso.y_max,
        }
    except Exception as e:
        logger.debug(f"[MLBackend.persistence] IsotonicRegression → dict KO : {e}")
        return None


def _isotonic_from_dict(d: Optional[dict]):
    """Reconstruit une IsotonicRegression native depuis un dict."""
    if d is None:
        return None
    try:
        from app.ml.backend.isotonic import IsotonicRegression
        iso = IsotonicRegression(
            y_min=d.get("y_min"),
            y_max=d.get("y_max"),
            increasing=d.get("increasing", True),
            out_of_bounds=d.get("out_of_bounds", "clip"),
        )
        # Fit factice pour initialiser les structures, puis on écrase les thresholds.
        iso.fit(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
        if d.get("x_thresholds") is not None:
            iso.X_thresholds_ = np.asarray(d["x_thresholds"], dtype=np.float64)
        if d.get("y_thresholds") is not None:
            iso.Y_thresholds_ = np.asarray(d["y_thresholds"], dtype=np.float64)
        return iso
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] dict → IsotonicRegression KO : {e}")
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


# ── Chargement (format natif) ───────────────────────────────────────────────
def load_model(state: TrainState, lock, path: str, tf: str) -> bool:
    """Charge les modèles + métadonnées depuis le disque (format natif).

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

    if os.path.exists(amp_path) and os.path.exists(dir_path) and os.path.exists(meta_path):
        return _load_native(state, lock, amp_path, dir_path, meta_path, tf)

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


# ─────────────────────────────────────────────────────────────────────────────
#  Variante pour stratégies LightGBM sans calibrators (scoring_statistique_opus)
# ─────────────────────────────────────────────────────────────────────────────
# phase6 : le paramètre `scaler` est conservé pour compat API mais ignoré
# (StandardScaler supprimé — LightGBM est invariant aux transformations
# monotones des features). On ne sérialise plus que amp + dir + meta.
def save_lgb_with_scaler(amp_model, dir_model, scaler, path: str,
                         tf: str, best_auc: float, train_meta: dict) -> bool:
    """Sauvegarde un payload {amp, dir} au format natif (scaler ignoré).

    Pour les stratégies `scoring_statistique_opus_v4/v5` qui utilisaient
    LightGBM + StandardScaler. Depuis phase6, le scaler est supprimé
    (LightGBM n'en a pas besoin) — le paramètre `scaler` est gardé pour
    compat API mais ignoré.
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
    """Charge un payload {amp, dir} depuis le format natif.

    Retourne un dict {amp_model, dir_model, scaler=None, best_auc, train_meta}
    ou None si introuvable/erreur. Le `scaler` est toujours None depuis
    phase6 (plus de StandardScaler).
    """
    base = path[:-4] if path.endswith(".pkl") else path
    amp_path  = f"{base}.amp.lgb"
    dir_path  = f"{base}.dir.lgb"
    meta_path = f"{base}.meta.json"

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
                "scaler":     None,  # phase6 : plus de StandardScaler
                "best_auc":   float(payload.get("best_auc", 0.0)),
                "train_meta": dict(payload.get("train_meta") or {}),
            }
        except Exception as e:
            logger.warning(f"[MLBackend.persistence] load_lgb_with_scaler natif KO : {e}")
            return None

    return None
