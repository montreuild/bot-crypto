"""Convertit le pkl V4 pré-entraîné (8,8 Mo, 8 entrées) vers le format natif
LightGBM + JSON — élimine le risque RCE et accélère le chargement.

Format de sortie (par entrée `(tf, target, config)`) :
    v4_{tf}_{target}_{config}.lgb     # Booster LightGBM natif (lgb.Booster)
    v4_{tf}_{target}_{config}.json    # Métadonnées (features, split_idx, classes, etc.)

Le fichier `v4_medians.json` reste inchangé (déjà JSON).
Le fichier `v4_models.pkl` est supprimé après conversion réussie.

Usage :
    python scripts/convert_v4_pretrained.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("convert_v4")


def convert_v4(dry_run: bool = False) -> int:
    data_dir = REPO / "app/strategies/opus_stat_pretrained_v4_data"
    pkl_path = data_dir / "v4_models.pkl"

    if not pkl_path.exists():
        logger.error(f"pkl introuvable : {pkl_path}")
        return 1

    # Charger le pkl (avec suppression du warning sklearn 1.8 → 1.5).
    try:
        from sklearn.exceptions import InconsistentVersionWarning
        warnings.simplefilter("ignore", InconsistentVersionWarning)
    except ImportError:
        pass

    logger.info(f"Chargement {pkl_path} ({pkl_path.stat().st_size / 1e6:.1f} Mo)...")
    with open(pkl_path, "rb") as f:
        models: Dict[Tuple[str, str, str], Dict[str, Any]] = pickle.load(f)

    logger.info(f"  {len(models)} entrées trouvées : {list(models.keys())}")

    if dry_run:
        logger.info("[DRY-RUN] Les fichiers suivants seraient créés :")

    n_ok, n_fail = 0, 0
    for key, entry in models.items():
        tf, target, config = key
        base_name = f"v4_{tf}_{target}_{config}"
        lgb_path  = data_dir / f"{base_name}.lgb"
        meta_path = data_dir / f"{base_name}.json"

        clf = entry.get("model")
        if clf is None:
            logger.warning(f"  ⚠️  {key} : model=None — skip")
            n_fail += 1
            continue

        # Extraire le booster LightGBM natif.
        if not hasattr(clf, "booster_"):
            logger.warning(f"  ⚠️  {key} : pas de booster_ — skip")
            n_fail += 1
            continue

        if dry_run:
            logger.info(f"  [DRY-RUN] écrirait {lgb_path.name} + {meta_path.name}")
            n_ok += 1
            continue

        try:
            # 1. Sauvegarder le booster au format natif LightGBM (RCE-safe).
            booster = clf.booster_
            booster.save_model(str(lgb_path))

            # 2. Sauvegarder les métadonnées sklearn dans un JSON.
            meta = {
                "tf":           tf,
                "target":       target,
                "config":       config,
                "features":     list(entry.get("features") or []),
                "split_idx":    int(entry.get("split_idx", 0)),
                "n_features_in": int(getattr(clf, "n_features_in_", 0)),
                "n_classes":    int(getattr(clf, "n_classes_", 2)),
                "classes_":     [int(c) for c in getattr(clf, "classes_", [0, 1])],
                "n_estimators": int(getattr(clf, "n_estimators_", 0)),
                "objective":    str(getattr(clf, "objective_", "binary")),
                "format_version": 1,
                "source":       "v4_models.pkl (converté via scripts/convert_v4_pretrained.py)",
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            logger.info(f"  ✅ {base_name} → {lgb_path.name} + {meta_path.name}")
            n_ok += 1
        except Exception as e:
            logger.error(f"  ❌ {key} KO : {e}")
            n_fail += 1

    if dry_run:
        logger.info(f"\n[DRY-RUN] Synthèse : {n_ok} OK, {n_fail} échec(s)")
        return 0 if n_fail == 0 else 1

    # Supprimer le pkl original si toutes les conversions ont réussi.
    if n_fail == 0:
        try:
            pkl_path.unlink()
            logger.info(f"\n🗑️  pkl original supprimé : {pkl_path}")
        except Exception as e:
            logger.warning(f"Suppression pkl KO : {e}")
    else:
        logger.warning(f"\n⚠️  pkl conservé ({n_fail} échecs)")

    logger.info(f"\n=== Synthèse : {n_ok} OK, {n_fail} échec(s) ===")
    logger.info(f"Fichiers générés dans : {data_dir}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Ne pas écrire les fichiers, juste lister")
    args = parser.parse_args()
    sys.exit(convert_v4(dry_run=args.dry_run))
