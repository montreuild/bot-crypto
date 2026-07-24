#!/usr/bin/env python3
"""Migre les modèles V4 pré-entraînés vers le registre ML (ML-02).

Contexte : les modèles V4 (introduits par un seul commit, `683aee1`, sans
script de génération dans le dépôt — irréproductibles à l'identique
aujourd'hui) vivent dans ``app/strategies/opus_stat_pretrained_v4_data/``,
un répertoire à part de tous les autres modèles (``models/``). Cinq
stratégies les partagent déjà de fait (``opus_stat_pretrained_v4``,
``opus_omnibus_v7_pretrained/v8/v9/v10``) via la fonction singleton
``_load_pretrained()``.

Ce script :
  1. Lit les 6 fichiers réellement utilisés à l'inférence — config
     ``"single"`` uniquement : ``_predict()`` dans ``opus_stat_pretrained_v4.py``
     lit toujours ``key = (tf, target, "single")`` ; les fichiers ``*_multi.*``
     existent sur disque mais ne sont jamais chargés en mémoire pour la
     prédiction — poids morts, non migrés (conservés dans l'ancien répertoire,
     rien n'est supprimé par CE script).
  2. Convertit chaque (tf, amp, dir) en un bundle standard 3-fichiers
     (``model.amp.lgb``/``model.dir.lgb``/``model.meta.json``) via
     ``save_amp_dir_bundle`` — amp et dir gardent CHACUN leurs propres
     features/médianes (``dir_features``/``dir_medians``, cf. persistence.py)
     car le format V4 original ne les partage PAS (vérifié : 33/40 features
     différentes entre amp et dir sur 15m).
  3. Importe chaque bundle dans le registre (``models/BTC_USDC/{tf}/
     opus_stat_pretrained_v4/legacy/``) via ``registry.import_legacy`` —
     idempotent, ne supprime jamais l'original (copie).

``n_bars``/dates d'entraînement restent NON DÉTERMINABLES avec certitude
(script de génération perdu) — seuls les ``split_idx`` par cible, présents
dans les JSON originaux, sont reportés tels quels en provenance, marqués
``inferred`` et PAS comme des dates vérifiées.

Usage ::

    python -m scripts.migrate_v4_to_registry [--base-dir models] [--symbol BTC/USDC]

Idempotent : peut être relancé sans effet si la version "legacy" existe déjà.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate_v4")

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "strategies", "opus_stat_pretrained_v4_data",
)
_TFS = ("15m", "30m", "1h")


def _load_medians() -> dict:
    with open(os.path.join(_DATA_DIR, "v4_medians.json"), "r", encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for k, v in raw.items():
        k_clean = k.strip("()").replace("'", "").replace(" ", "")
        parts = k_clean.split(",")
        if len(parts) == 2:
            out[(parts[0], parts[1])] = v
    return out


def _load_target(tf: str, target: str) -> dict:
    """Lit ``v4_{tf}_{target}_single.{json,lgb}`` — seule config utilisée à
    l'inférence (cf. docstring module)."""
    import lightgbm as lgb

    meta_path = os.path.join(_DATA_DIR, f"v4_{tf}_{target}_single.json")
    lgb_path  = os.path.join(_DATA_DIR, f"v4_{tf}_{target}_single.lgb")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    booster = lgb.Booster(model_file=lgb_path)
    return {
        "booster": booster,
        "features": list(meta.get("features") or []),
        "split_idx": int(meta.get("split_idx", 0)),
    }


def migrate(base_dir: str = "models", symbol: str = "BTC/USDC") -> None:
    import app.ml.model_registry as registry
    from app.ml.backend.persistence import save_amp_dir_bundle

    medians = _load_medians()
    tmp_dir = tempfile.mkdtemp(prefix="v4_migrate_")
    try:
        for tf in _TFS:
            amp = _load_target(tf, "amp")
            dir_ = _load_target(tf, "dir")
            amp_medians = medians.get((tf, "amp"), {})
            dir_medians = medians.get((tf, "dir"), {})

            tmp_prefix = os.path.join(tmp_dir, f"opus_stat_pretrained_v4_{tf}")
            ok = save_amp_dir_bundle(
                tmp_prefix, tf, amp["booster"], dir_["booster"],
                features=amp["features"], medians=amp_medians,
                best_auc=0.0,  # AUC de validation non disponible (script perdu)
                train_meta={
                    "amp_split_idx": amp["split_idx"],
                    "dir_split_idx": dir_["split_idx"],
                    "note": "split_idx reporté tel quel depuis les JSON V4 "
                            "originaux — approximation du nombre de barres "
                            "d'entraînement, PAS une valeur vérifiée (script "
                            "de génération perdu, cf. commit 683aee1).",
                },
                dir_features=dir_["features"], dir_medians=dir_medians,
            )
            if not ok:
                logger.error(f"[migrate_v4] {tf} : échec save_amp_dir_bundle, skip")
                continue

            art = registry.import_legacy(
                symbol, tf, "opus_stat_pretrained_v4", tmp_prefix,
                base_dir=base_dir, version_id="legacy",
            )
            if art is None:
                logger.error(f"[migrate_v4] {tf} : échec import_legacy")
                continue
            logger.info(
                f"[migrate_v4] {symbol}/{tf}/opus_stat_pretrained_v4 -> "
                f"{art.path_prefix} (amp_split_idx={amp['split_idx']}, "
                f"dir_split_idx={dir_['split_idx']})"
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default="models")
    parser.add_argument("--symbol", default="BTC/USDC")
    args = parser.parse_args()
    migrate(base_dir=args.base_dir, symbol=args.symbol)


if __name__ == "__main__":
    main()
