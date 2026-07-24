"""MLBackend.persistence — sauvegarde/chargement des modèles au format natif.

Format 100 % LightGBM natif, sans pickle :

- LightGBM natif : `booster.save_model(path)` → fichier texte/binaire
  déterministe, versionné, sans exécution de code à la désérialisation.
- JSON pour les métadonnées : features, medians, best_auc, train_meta,
  calibrators (IsotonicRegression native, sérialisée en JSON), config.

Format de persistance (3 fichiers par modèle) :

    {path}.amp.lgb       # Booster LightGBM natif (amplitude)
    {path}.dir.lgb       # Booster LightGBM natif (direction)
    {path}.meta.json     # Métadonnées + calibrators + config

Il n'y a plus aucun `.pkl` : les anciens modèles picklés (v4_models.pkl,
runtime joblib) ont tous été migrés. Aucun import ``pickle`` / ``joblib`` /
``sklearn`` dans ce module — uniquement ``lightgbm``, ``json`` et ``numpy``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import numpy as np

from app.ml.backend.trainer import TrainState

logger = logging.getLogger(__name__)


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


# ── Provenance (ML-02 / meta.json v2) ───────────────────────────────────────
# Bloc optionnel, absent des anciens meta.json (lu comme {} par les
# lecteurs — aucune rupture de compatibilité). Rempli par le registre
# (app/ml/model_registry.py) et la politique de gate (app/ml/policy.py) ;
# les appelants qui n'utilisent pas encore le registre (tests, scripts de
# recherche) peuvent laisser `extra_meta=None` — comportement inchangé.
def _merge_provenance(payload: dict, extra_meta: Optional[dict]) -> None:
    if extra_meta:
        payload["provenance"] = dict(extra_meta.get("provenance") or {})
        gate = extra_meta.get("gate")
        if gate:
            payload["gate"] = dict(gate)


# ── Sauvegarde (format natif) ───────────────────────────────────────────────
def save_model(state: TrainState, lock, path: str, tf: str,
               extra_meta: Optional[dict] = None) -> bool:
    """Sauvegarde les modèles (amp + dir) + métadonnées au format natif.

    Écrit 3 fichiers :
        {path}.amp.lgb       (LightGBM booster natif)
        {path}.dir.lgb       (LightGBM booster natif)
        {path}.meta.json     (features, medians, calibrators, train_meta, best_auc)

    Le `path` sert de préfixe (ex. `models/foo_1h` → `models/foo_1h.amp.lgb`).

    Args:
        extra_meta: bloc de provenance optionnel (ML-02) — voir
            ``_merge_provenance``. ``{"provenance": {...}, "gate": {...}}``.

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

    amp_path = f"{path}.amp.lgb"
    dir_path = f"{path}.dir.lgb"
    meta_path = f"{path}.meta.json"

    os.makedirs(os.path.dirname(os.path.abspath(amp_path)), exist_ok=True)

    # 1. Boosters LightGBM au format natif.
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
        "format_version": 2,
    }
    _merge_provenance(payload, extra_meta)
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] write meta.json KO : {e}")
        return False

    logger.info(
        f"[MLBackend.persistence] Modèles sauvegardés → {path}.* "
        f"(AUC={best_auc:.3f}, {len(feats)} features)"
    )
    return True


# ── Chargement (format natif) ───────────────────────────────────────────────
def load_model(state: TrainState, lock, path: str, tf: str) -> bool:
    """Charge les modèles + métadonnées depuis le disque (format natif).

    Args:
        state: état ML à peupler.
        lock:  threading.Lock.
        path:  préfixe des fichiers modèle (ex. `models/foo_1h`).
        tf:    timeframe à peupler dans l'état.

    Returns:
        True si chargé, False si introuvable ou erreur.
    """
    base = path
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
        # ML-02 : provenance/gate optionnels (absents des anciens meta.json) —
        # rattachés à train_meta plutôt qu'un nouveau slot TrainState, pour ne
        # pas complexifier l'état pour un besoin de lecture/diagnostic seul.
        if payload.get("provenance"):
            meta["provenance"] = dict(payload["provenance"])
        if payload.get("gate"):
            meta["gate"] = dict(payload["gate"])
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
    base = path
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
    base = path
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


# ─────────────────────────────────────────────────────────────────────────────
#  Variante pour stratégies « retrained » standalone (amp + dir + features +
#  medians, sans TrainState ni calibrators) — opus_omnibus_v7 / v10_retrained /
#  v11_followsetup / opus_stat_retrained_v4.
# ─────────────────────────────────────────────────────────────────────────────
# Ces stratégies entraînent leurs propres boosters LightGBM. Elles utilisent le
# même format natif LGB + JSON que le reste du backend.

def save_amp_dir_bundle(path: str, tf: str, amp_model, dir_model,
                        features, medians, best_auc: float,
                        train_meta: dict, amp_cal=None, dir_cal=None,
                        extra_meta: Optional[dict] = None,
                        dir_features=None, dir_medians=None) -> bool:
    """Sauvegarde ``{amp, dir, features, medians, best_auc, train_meta}`` (+
    calibrators isotoniques optionnels) au format natif LGB + JSON.

    Écrit ``{path}.amp.lgb`` + ``{path}.dir.lgb`` + ``{path}.meta.json``. Les
    calibrators ``amp_cal``/``dir_cal`` (``IsotonicRegression`` native) sont
    sérialisés en JSON via ``_isotonic_to_dict`` — ``None`` accepté (stratégies
    sans calibration). ``extra_meta`` : bloc de provenance ML-02 optionnel
    (cf. ``_merge_provenance``).

    ``dir_features``/``dir_medians`` : optionnels — certains modèles legacy
    (V4) entraînent amplitude et direction sur des listes de features et des
    médianes DIFFÉRENTES (contrairement à ``MLBackend``/``trainer.py`` qui
    partagent une liste unique entre les deux cibles). ``None`` (défaut)
    préserve le comportement historique : ``features``/``medians`` servent
    aux deux cibles — c'est le cas de tous les appelants actuels
    (``opus_omnibus_v7/v10_retrained/v11_followsetup``, ``opus_stat_retrained_v4``).

    Retourne ``False`` si un modèle manque.
    """
    if amp_model is None or dir_model is None:
        return False
    base = path
    amp_path  = f"{base}.amp.lgb"
    dir_path  = f"{base}.dir.lgb"
    meta_path = f"{base}.meta.json"
    os.makedirs(os.path.dirname(os.path.abspath(amp_path)), exist_ok=True)
    try:
        amp_model.save_model(amp_path)
        dir_model.save_model(dir_path)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] save_amp_dir_bundle LGB KO : {e}")
        return False
    payload = {
        "tf":         tf,
        "features":   list(features or []),
        "medians":    {k: float(v) for k, v in (medians or {}).items()},
        "best_auc":   float(best_auc),
        "train_meta": dict(train_meta or {}),
        "amp_cal":    _isotonic_to_dict(amp_cal),
        "dir_cal":    _isotonic_to_dict(dir_cal),
        "format_version": 2,
    }
    if dir_features is not None:
        payload["dir_features"] = list(dir_features)
    if dir_medians is not None:
        payload["dir_medians"] = {k: float(v) for k, v in dir_medians.items()}
    _merge_provenance(payload, extra_meta)
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[MLBackend.persistence] save_amp_dir_bundle meta.json KO : {e}")
        return False
    return True


def load_amp_dir_bundle(path: str) -> Optional[dict]:
    """Charge ``{amp_model, dir_model, features, medians, best_auc, train_meta,
    tf, amp_cal, dir_cal}`` depuis le format natif LGB + JSON. Retourne ``None``
    si introuvable ou illisible (l'appelant doit alors ré-entraîner)."""
    amp_path  = f"{path}.amp.lgb"
    dir_path  = f"{path}.dir.lgb"
    meta_path = f"{path}.meta.json"

    if not (os.path.exists(amp_path) and os.path.exists(dir_path)
            and os.path.exists(meta_path)):
        return None
    try:
        import lightgbm as lgb
        amp_model = lgb.Booster(model_file=amp_path)
        dir_model = lgb.Booster(model_file=dir_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        features = list(payload.get("features") or [])
        medians  = dict(payload.get("medians") or {})
        return {
            "amp_model":  amp_model,
            "dir_model":  dir_model,
            "features":   features,
            "medians":    medians,
            # None = pas de split amp/dir -> l'appelant retombe sur features/medians
            # (comportement historique, cf. docstring save_amp_dir_bundle).
            "dir_features": (list(payload["dir_features"])
                             if payload.get("dir_features") is not None else None),
            "dir_medians":  (dict(payload["dir_medians"])
                             if payload.get("dir_medians") is not None else None),
            "best_auc":   float(payload.get("best_auc", 0.0)),
            "train_meta": dict(payload.get("train_meta") or {}),
            "amp_cal":    _isotonic_from_dict(payload.get("amp_cal")),
            "dir_cal":    _isotonic_from_dict(payload.get("dir_cal")),
            "tf":         payload.get("tf"),
            "provenance": dict(payload.get("provenance") or {}),
            "gate":       dict(payload.get("gate") or {}),
        }
    except Exception as e:
        logger.warning(f"[MLBackend.persistence] load_amp_dir_bundle KO : {e}")
        return None
