"""Conversion one-shot des fichiers `.pkl` ML legacy vers le format natif
(LightGBM booster + JSON métadonnées), RCE-safe.

Usage :
    python scripts/convert_pkl_to_native.py [--dry-run] [--models-dir models]

Pour chaque fichier `models/{name}_{tf}.pkl` trouvé :
1. Charge via `RestrictedUnpickler` (liste blanche stricte).
2. Sauvegarde au format natif : `{name}_{tf}.amp.lgb`, `{name}_{tf}.dir.lgb`,
   `{name}_{tf}.meta.json`.
3. (Sans --dry-run) Supprime le `.pkl` original après confirmation.

Ce script est idempotent : si le format natif existe déjà, le `.pkl` est
simplement supprimé (sans re-conversion).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

# Ajouter la racine du repo au path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("convert_pkl")


def convert_one(pkl_path: Path, dry_run: bool = False) -> bool:
    """Convertit un fichier .pkl vers le format natif. Retourne True si OK."""
    from app.ml.backend.persistence import (
        _isotonic_to_dict,
        restricted_pickle_load,
    )

    base = pkl_path.with_suffix("")  # retire .pkl
    amp_path  = Path(f"{base}.amp.lgb")
    dir_path  = Path(f"{base}.dir.lgb")
    meta_path = Path(f"{base}.meta.json")

    # Déjà converti ?
    if amp_path.exists() and dir_path.exists() and meta_path.exists():
        logger.info(f"  ✅ {pkl_path.name} : déjà converti (format natif présent)")
        if not dry_run:
            try:
                pkl_path.unlink()
                logger.info(f"     .pkl original supprimé")
            except Exception as e:
                logger.warning(f"     suppression .pkl KO : {e}")
        return True

    logger.info(f"  ⏳ Conversion {pkl_path.name}...")
    try:
        with open(pkl_path, "rb") as f:
            data = restricted_pickle_load(f)
    except Exception as e:
        logger.error(f"     ❌ chargement échoué : {e}")
        return False

    # Structure attendue (V11/retrained/scoring) :
    #   {amp_model, dir_model, amp_cal, dir_cal, features, medians, best_auc, train_meta, tf}
    # Pour ml_dynamic_threshold : {pipeline, best_params, best_auc, feature_cols, model_type}
    # → on ne convertit que les payloads LightGBM (booster natif).
    if "amp_model" not in data or "dir_model" not in data:
        logger.warning(
            f"     ⚠️  payload non-LightGBM (clés: {list(data.keys())[:5]}...) — "
            f"skip (utiliser skops.io ou conversion manuelle)"
        )
        return False

    amp_model = data["amp_model"]
    dir_model = data["dir_model"]
    if amp_model is None or dir_model is None:
        logger.warning(f"     ⚠️  modèles None — skip")
        return False

    # 1. Sauvegarder les boosters au format natif LightGBM.
    try:
        if dry_run:
            logger.info(f"     [DRY-RUN] écrirait {amp_path.name}, {dir_path.name}, {meta_path.name}")
            return True
        amp_model.save_model(str(amp_path))
        dir_model.save_model(str(dir_path))
    except Exception as e:
        logger.error(f"     ❌ save_model LGB KO : {e}")
        return False

    # 2. Métadonnées JSON.
    import json
    payload = {
        "tf":         data.get("tf", base.name.rsplit("_", 1)[-1]),
        "features":   list(data.get("features") or []),
        "medians":    {k: float(v) for k, v in (data.get("medians") or {}).items()},
        "best_auc":   float(data.get("best_auc", 0.0)),
        "train_meta": dict(data.get("train_meta") or {}),
        "amp_cal":    _isotonic_to_dict(data.get("amp_cal")),
        "dir_cal":    _isotonic_to_dict(data.get("dir_cal")),
        "format_version": 1,
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"     ❌ write meta.json KO : {e}")
        return False

    logger.info(f"     ✅ converti → {amp_path.name}, {dir_path.name}, {meta_path.name}")

    # 3. Supprimer le .pkl original.
    try:
        pkl_path.unlink()
        logger.info(f"     .pkl original supprimé")
    except Exception as e:
        logger.warning(f"     suppression .pkl KO : {e}")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Ne pas écrire les fichiers, juste lister ce qui serait converti")
    parser.add_argument("--models-dir", default="models",
                        help="Répertoire contenant les .pkl (défaut: models)")
    parser.add_argument("--include-pretrained", action="store_true",
                        help="Convertir aussi v4_models.pkl (8,8 Mo) — expérimental")
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    if not models_dir.is_absolute():
        models_dir = REPO / models_dir

    if not models_dir.is_dir():
        logger.error(f"Répertoire introuvable : {models_dir}")
        return 1

    pkl_files = sorted(models_dir.glob("*.pkl"))
    if not pkl_files:
        logger.info(f"Aucun .pkl trouvé dans {models_dir}")
        return 0

    logger.info(f"Trouvé {len(pkl_files)} fichier(s) .pkl dans {models_dir}")
    n_ok, n_fail = 0, 0
    for pkl in pkl_files:
        if convert_one(pkl, dry_run=args.dry_run):
            n_ok += 1
        else:
            n_fail += 1

    # Conversion optionnelle du pkl V4 pré-entraîné (8,8 Mo, multi-entrées).
    if args.include_pretrained:
        v4_pkl = REPO / "app/strategies/opus_stat_pretrained_v4_data/v4_models.pkl"
        if v4_pkl.exists():
            logger.info(f"\nConversion v4_models.pkl (8,8 Mo, multi-entrées)...")
            logger.warning("  ⚠️  conversion expérimentale — format multi-entrées non standard")
            # Le pkl V4 contient 8 entrées (tf, target, config) → dict de dicts.
            # On convertit chaque entrée en 3 fichiers séparés.
            try:
                from app.ml.backend.persistence import restricted_pickle_load
                with open(v4_pkl, "rb") as f:
                    v4_data = restricted_pickle_load(f)
                v4_dir = v4_pkl.parent
                if not args.dry_run:
                    v4_dir.mkdir(parents=True, exist_ok=True)
                for key, entry in v4_data.items():
                    tf, target, config = key
                    base_name = f"v4_{tf}_{target}_{config}"
                    lgb_path  = v4_dir / f"{base_name}.lgb"
                    meta_path = v4_dir / f"{base_name}.json"
                    if entry.get("model") is None:
                        continue
                    if not args.dry_run:
                        # Le modèle V4 est un LGBMClassifier sklearn → extraire le booster.
                        clf = entry["model"]
                        if hasattr(clf, "booster_"):
                            clf.booster_.save_model(str(lgb_path))
                        else:
                            logger.warning(f"     pas de booster_ pour {key}")
                            continue
                        import json
                        meta = {
                            "tf": tf, "target": target, "config": config,
                            "features": list(entry.get("features") or []),
                            "split_idx": int(entry.get("split_idx", 0)),
                            "n_features_in": int(getattr(clf, "n_features_in_", 0)),
                            "n_classes": int(getattr(clf, "n_classes_", 2)),
                            "classes_": [int(c) for c in getattr(clf, "classes_", [0, 1])],
                            "format_version": 1,
                        }
                        with open(meta_path, "w", encoding="utf-8") as f:
                            json.dump(meta, f, ensure_ascii=False)
                    logger.info(f"  ✅ v4 {key} → {base_name}.lgb + .json")
                logger.info("  Note : v4_medians.json reste inchangé (déjà JSON)")
            except Exception as e:
                logger.error(f"  ❌ conversion v4_models.pkl KO : {e}")
                n_fail += 1
        else:
            logger.info(f"\nv4_models.pkl non trouvé (recherche : {v4_pkl})")

    logger.info(f"\n=== Synthèse : {n_ok} OK, {n_fail} échec(s) ===")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
