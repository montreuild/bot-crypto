"""S2-07 : Versioning des modèles ML par hash features.

L'audit V2 notait que les `models/*.lgb` n'étaient pas versionnés : un
changement de features rendait un modèle silencieusement incompatible (le
`FeatureStore` a un champ `version`, les `.lgb` non). Ce module fournit :

1. `compute_features_hash(features: list[str]) -> str`
   Calcule un hash SHA-256 déterministe de la liste ordonnée des features.
   Deux modèles avec le même hash peuvent échanger leurs données ; deux
   modèles avec un hash différent NE le peuvent PAS.

2. `validate_model_compatibility(meta_path, expected_features) -> tuple`
   Charge `meta.json`, compare le hash features attendu vs celui stocké,
   retourne `(ok, reason, stored_hash, expected_hash)`.

3. `decorate_meta_with_hash(features, payload) -> dict`
   Ajoute `features_hash` + `features_count` + `versioning_schema_version`
   à un payload meta.json avant sauvegarde.

Le contrôle au chargement se fait dans `app/ml/backend/persistence.py`
(les fonctions `load_model`, `load_lgb_with_scaler`, `load_amp_dir_bundle`
appellent `validate_model_compatibility`).

Référence :
    MLC-03 (audité dans docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md
    §Sprint 2 §S2-07).
"""
import hashlib
import json
import logging
import os
from typing import Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Version du schéma de versioning — à incrémenter si le format du hash change.
# Cela force la re-validation de tous les modèles existants.
VERSIONING_SCHEMA_VERSION = 1


def compute_features_hash(features: Sequence[str]) -> str:
    """Calcule un hash SHA-256 déterministe de la liste des features.

    Le hash est **insensible à l'ordre** (tri alphabétique avant hash) pour
    que deux modèles entraînés avec les mêmes features dans un ordre
    différent (ex. après renommage) restent compatibles.

    Parameters
    ----------
    features : list of str
        Liste des noms de features (ex. ["rsi_14", "ema_20", "adx_14"]).

    Returns
    -------
    str
        Hash hex 16 chars (suffisant pour collision-resistance sur ~1e18 modèles).
    """
    if not features:
        return "0000000000000000"
    # Tri alphabétique pour insensibilité à l'ordre
    sorted_features = sorted(str(f) for f in features)
    # Concaténation avec séparateur pour éviter les collisions
    # (ex. ["a_b", "c"] vs ["a", "b_c"] — sans séparateur, "a_b|c" = "a|b_c")
    payload = "|".join(sorted_features)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def decorate_meta_with_hash(features: Sequence[str],
                              payload: dict) -> dict:
    """Ajoute les champs de versioning à un payload meta.json.

    À appeler AVANT `json.dump(payload, ...)` dans les fonctions de
    sauvegarde (`save_model`, `save_amp_dir_bundle`, etc.).

    Parameters
    ----------
    features : list of str
        Liste des features utilisées pour l'entraînement.
    payload : dict
        Payload meta.json à décorer (modifié en place).

    Returns
    -------
    dict
        Le payload modifié avec :
        - `features_hash` : hash SHA-256 (16 chars)
        - `features_count` : nombre de features
        - `versioning_schema_version` : version du schéma
    """
    payload["features_hash"] = compute_features_hash(features)
    payload["features_count"] = len(features)
    payload["versioning_schema_version"] = VERSIONING_SCHEMA_VERSION
    return payload


def validate_model_compatibility(
        meta_path: str,
        expected_features: Optional[Sequence[str]] = None,
        allow_missing_hash: bool = True,
) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """Vérifie la compatibilité entre un modèle persisté et les features attendues.

    Parameters
    ----------
    meta_path : str
        Chemin vers le fichier `.meta.json`.
    expected_features : Optional[list of str]
        Features attendues par l'appelant. Si None, on lit simplement le hash
        stocké sans comparaison.
    allow_missing_hash : bool
        Si True (défaut), accepte les anciens meta.json sans `features_hash`
        (compatibilité rétroactive — warning mais ok).
        Si False, refuse les modèles sans hash (stricte).

    Returns
    -------
    (ok: bool, reason: str, stored_hash: Optional[str], expected_hash: Optional[str])
        - ok=True si le modèle est compatible.
        - reason : explication lisible (pour logs/notifications).
        - stored_hash : hash trouvé dans meta.json (None si absent).
        - expected_hash : hash calculé depuis expected_features (None si
          expected_features est None).
    """
    if not os.path.exists(meta_path):
        return False, f"meta.json introuvable : {meta_path}", None, None

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return False, f"meta.json illisible : {e}", None, None

    stored_hash = payload.get("features_hash")
    schema_version = payload.get("versioning_schema_version", 0)

    # Vérifier la version du schéma
    if schema_version > VERSIONING_SCHEMA_VERSION:
        return (
            False,
            f"Schéma meta.json v{schema_version} > version supportée v{VERSIONING_SCHEMA_VERSION} "
            f"— re-entraîner ou migrer le chargeur",
            stored_hash,
            None,
        )

    if expected_features is None:
        # Pas de comparaison demandée — on retourne juste le hash stocké
        return True, "hash stocké lu sans comparaison", stored_hash, None

    expected_hash = compute_features_hash(expected_features)

    if stored_hash is None:
        # Ancien modèle sans hash
        if allow_missing_hash:
            logger.warning(
                f"[S2-07] meta.json sans features_hash ({meta_path}) — "
                f"modèle ancien accepté en compat rétroactive. "
                f"Forcer une re-entraînement pour ajouter le hash."
            )
            return True, "modèle ancien sans hash (compat rétroactive)", None, expected_hash
        return (
            False,
            "meta.json sans features_hash — refusé en mode strict. "
            "Re-entraîner pour ajouter le hash.",
            None,
            expected_hash,
        )

    if stored_hash != expected_hash:
        return (
            False,
            f"Hash features incompatible : stored={stored_hash} vs expected={expected_hash} "
            f"— les features ont changé depuis l'entraînement. "
            f"Re-entraîner le modèle pour éviter une prédiction silencieusement fausse.",
            stored_hash,
            expected_hash,
        )

    return True, "hash features compatible", stored_hash, expected_hash


def migration_check(models_dir: str = "models") -> dict:
    """Audit tous les meta.json d'un dossier et signale ceux sans hash.

    Utile pour identifier les modèles à re-entraîner après l'activation du
    versioning. À appeler depuis un script d'audit ou un endpoint admin.

    Returns
    -------
    dict
        {
            'total': int,
            'with_hash': int,
            'without_hash': list[str],
            'incompatible': list[str],
        }
    """
    if not os.path.isdir(models_dir):
        return {'total': 0, 'with_hash': 0, 'without_hash': [], 'incompatible': []}

    metas = []
    for root, _, files in os.walk(models_dir):
        for f in files:
            if f.endswith(".meta.json"):
                metas.append(os.path.join(root, f))

    with_hash = 0
    without_hash = []
    incompatible = []

    for meta_path in metas:
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if payload.get("features_hash"):
                with_hash += 1
            else:
                without_hash.append(meta_path)
            # Vérifier aussi la version du schéma
            sv = payload.get("versioning_schema_version", 0)
            if sv > VERSIONING_SCHEMA_VERSION:
                incompatible.append(f"{meta_path} (schema v{sv} > {VERSIONING_SCHEMA_VERSION})")
        except Exception as e:
            incompatible.append(f"{meta_path} (illisible: {e})")

    return {
        'total': len(metas),
        'with_hash': with_hash,
        'without_hash': without_hash,
        'incompatible': incompatible,
    }
