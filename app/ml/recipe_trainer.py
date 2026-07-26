"""``train(recipe, df, tf)`` — entraîner sans passer par une classe Strategy.

C'est l'étape C de ``docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md``, dont le
diagnostic tient en une phrase (§2) :

    **La classe de stratégie est propriétaire de son modèle.**

ML-02 avait extrait l'artefact (registre) et la décision de promotion (gate) ;
l'étape B a extrait la recette et le prédicteur. Il restait une asymétrie :

    lecture   ``build_predictor(persistence, path)``   → piloté par la recette
    écriture  ``mod.Strategy().fit(df, …)``            → piloté par la stratégie

Conséquence directement visible dans l'UI : la page « Modèles » est indexée par
RECETTE, mais son formulaire d'entraînement doit demander une STRATÉGIE, parce
que seule une classe ``Strategy`` sait construire des features et appeler
LightGBM. Ce module supprime cette obligation — il n'importe aucune stratégie
pour entraîner : la recette déclare son catalogue de features
(``app.ml.features_catalog``), son schéma de labels (``app.ml.labelling``), ses
hyperparamètres et son format de persistance, et cela suffit.

**Équivalence avec MLBackend, recherchée et mesurée.** Pour une recette
``v4_polars@1`` + ``amp_dir_quantile`` — c'est-à-dire la famille omnibus, celle
qui tourne en production — ce module reproduit ``app.ml.backend.trainer.train``
au réglage près : mêmes hyperparamètres LightGBM, même ``scale_pos_weight``,
même early stopping, même calibration isotone sur le set de validation. Ce
n'est pas de la cosmétique : sans cette identité, basculer une recette
changerait le modèle produit, et la conception exige que toute divergence soit
énumérée AVANT la bascule. ``scripts/check_recipe_trainer_equivalence.py``
mesure l'écart.

**Élagage de features.** ``MLBackend`` garde en mémoire les features à gain
non nul d'un cycle pour restreindre le suivant. Ici l'état n'existe pas : la
liste est publiée dans ``train_meta["kept_features"]`` et peut être réinjectée
par ``params["kept_features"]``. Le comportement est le même, la mémoire passe
par l'artefact au lieu d'un attribut de processus — ce qui la rend d'ailleurs
inspectable.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

#: Formats de persistance que ce module sait écrire, par nombre de têtes.
_BUNDLE_PERSISTENCE = ("lgbm_amp_dir_bundle", "lgbm_scaler")
_SINGLE_PERSISTENCE = ("lgbm_single",)


@dataclass
class TrainedRecipe:
    """Résultat d'un entraînement piloté par la recette."""

    recipe: str
    tf: str
    boosters: Dict[str, Any]
    feature_names: List[str]
    medians: Dict[str, float]
    train_meta: Dict[str, Any] = field(default_factory=dict)
    #: Calibrateurs isotones par tête — sérialisés dans le meta.json, sans
    #: quoi un modèle calibré à l'entraînement serait servi non calibré.
    calibrators: Dict[str, Any] = field(default_factory=dict)

    @property
    def best_auc(self) -> float:
        aucs = [v for k, v in self.train_meta.items()
                if k.startswith("auc_") and isinstance(v, (int, float))]
        return float(max(aucs)) if aucs else 0.0

    def save(self, path_prefix: str) -> bool:
        """Écrit l'artefact au format déclaré par la recette.

        Le ``meta.json`` porte TOUJOURS la liste des features et les médianes.
        C'est ce qui manquait au format ``save_lgb_with_scaler`` des recettes
        ``stat48_*`` et qui rendait leurs artefacts illisibles par le scorer
        générique (``unsupported_format``) : sans noms de colonnes, la matrice
        d'entrée du holdout n'est pas reconstructible.
        """
        from app.ml.model_registry import model_suffixes
        suffixes = model_suffixes(self.recipe)
        os.makedirs(os.path.dirname(os.path.abspath(path_prefix)) or ".", exist_ok=True)

        if suffixes == (".lgb",):
            return self._save_single(path_prefix)
        return self._save_bundle(path_prefix)

    def _save_bundle(self, path_prefix: str) -> bool:
        from app.ml.backend.persistence import save_amp_dir_bundle
        amp, dir_ = self.boosters.get("amp"), self.boosters.get("dir")
        if amp is None or dir_ is None:
            logger.error(
                f"[RecipeTrainer] {self.recipe} : format bundle demandé mais têtes "
                f"disponibles = {sorted(self.boosters)} — rien n'est écrit."
            )
            return False
        return save_amp_dir_bundle(
            path_prefix, self.tf, amp, dir_, features=self.feature_names,
            medians=self.medians, best_auc=self.best_auc, train_meta=self.train_meta,
            amp_cal=self.calibrators.get("amp"), dir_cal=self.calibrators.get("dir"),
        )

    def _save_single(self, path_prefix: str) -> bool:
        import json
        booster = self.boosters.get("dir") or next(iter(self.boosters.values()), None)
        if booster is None:
            return False
        booster.save_model(f"{path_prefix}.lgb")
        payload = {
            "tf": self.tf,
            "features": list(self.feature_names),
            "medians": {k: float(v) for k, v in self.medians.items()},
            "best_auc": float(self.best_auc),
            "train_meta": dict(self.train_meta),
            "model_type": "lightgbm",
            "format_version": 2,
            "source": "recipe_trainer",
        }
        with open(f"{path_prefix}.meta.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return True


def supports(recipe_name: str) -> Optional[str]:
    """``None`` si ce module sait entraîner ``recipe_name``, sinon la raison.

    Répondre « non » explicitement vaut mieux que produire un modèle qui n'est
    pas celui que la recette décrit : c'est le garde-fou qui rend la bascule
    sûre recette par recette, au lieu de tout basculer d'un coup.
    """
    from app.ml.features_catalog import available_catalogs
    from app.ml.labelling import available_schemes
    from app.ml.recipe import load_recipe

    try:
        r = load_recipe(recipe_name)
    except Exception as e:
        return f"recette illisible : {e}"
    if r.persistence == "proxy":
        return "recette sans artefact (persistence: proxy) — rien à entraîner"
    if r.features_catalog not in available_catalogs():
        return (f"catalogue {r.features_catalog!r} non enregistré "
                f"(connus : {available_catalogs()})")
    if r.label_scheme not in available_schemes():
        return f"schéma de labels {r.label_scheme!r} non enregistré"
    if r.persistence not in _BUNDLE_PERSISTENCE + _SINGLE_PERSISTENCE:
        return f"persistance {r.persistence!r} non gérée par ce module"
    return None


def train(recipe_name: str, df: pl.DataFrame, tf: str, *,
          params: Optional[Dict[str, Any]] = None,
          seed: int = 42) -> Optional[TrainedRecipe]:
    """Entraîne la recette ``recipe_name`` sur ``df``. Aucune stratégie importée.

    ``params`` surcharge les valeurs de la recette (hyperparamètres, paramètres
    de features/labels) — même précédence que partout ailleurs : la recette
    fixe les défauts, l'appelant a le dernier mot.

    Retourne ``None`` si l'historique est insuffisant ou les labels dégénérés ;
    ces cas sont journalisés avec leur cause, jamais silencieux.
    """
    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("[RecipeTrainer] lightgbm requis : pip install lightgbm")
        return None

    from app.ml import features_catalog, labelling
    from app.ml.recipe import load_recipe

    why = supports(recipe_name)
    if why:
        logger.error(f"[RecipeTrainer] {recipe_name} non pris en charge : {why}")
        return None

    r = load_recipe(recipe_name)
    p: Dict[str, Any] = {**r.train_params(), **(params or {})}

    fs = features_catalog.build(r.features_catalog, df, p)
    if fs is None or len(fs) < r.min_bars:
        logger.warning(
            f"[RecipeTrainer] {recipe_name}/{tf} : features insuffisantes "
            f"({0 if fs is None else len(fs)} < min_bars={r.min_bars})")
        return None

    lab = labelling.build(r.label_scheme, fs.frame, p)
    if lab is None or lab.n < 200:
        logger.warning(f"[RecipeTrainer] {recipe_name}/{tf} : pas assez de barres "
                       f"labellisables (n={0 if lab is None else lab.n})")
        return None

    heads = [h for h in r.heads if h in lab.y]
    if not heads:
        logger.error(f"[RecipeTrainer] {recipe_name} : têtes déclarées {r.heads} "
                     f"absentes des labels produits {lab.heads}")
        return None

    # Élagage : restreindre aux features retenues d'un cycle précédent. La
    # liste vient de ``params`` (donc d'un artefact), pas d'un état de process.
    # Le seuil de 50 reproduit MLBackend : sous ce nombre, l'élagage a
    # probablement mordu trop loin et on repart du catalogue complet.
    names = list(fs.names)
    if p.get("prune_features") and p.get("kept_features"):
        kept = [c for c in names if c in set(p["kept_features"])]
        if len(kept) >= 50:
            names = kept

    X_full = fs.frame.select(names).to_numpy().astype(np.float32)[:lab.n]
    split = max(int(lab.n * 0.8), 100)
    split = min(split, lab.n - 50)
    if split < 100 or lab.n - split < 50:
        logger.warning(f"[RecipeTrainer] {recipe_name}/{tf} : split impossible (n={lab.n})")
        return None

    from app.ml.backend.features import impute_inplace
    medians: Dict[str, float] = {}
    for j, col in enumerate(names):
        col_train = X_full[:split, j]
        mask = np.isfinite(col_train)
        medians[col] = float(np.median(col_train[mask])) if mask.any() else 0.0

    X_train, X_valid = X_full[:split].copy(), X_full[split:lab.n].copy()
    impute_inplace(X_train, names, medians)
    impute_inplace(X_valid, names, medians)

    # Réglages repris À L'IDENTIQUE de app.ml.backend.trainer.train : c'est ce
    # qui rend la bascule d'une recette omnibus sans effet sur le modèle.
    # Ne pas passer `seed` ici — MLBackend ne le fait pas, et le fixer
    # produirait des arbres différents des siens.
    common = dict(
        objective="binary", metric="auc",
        num_leaves=int(p.get("num_leaves", 31)),
        learning_rate=float(p.get("learning_rate", 0.05)),
        min_child_samples=20, subsample=0.8, subsample_freq=5,
        colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
        max_bin=63, force_col_wise=True, verbosity=-1, n_jobs=1,
    )
    n_estimators = int(p.get("n_estimators", 300))
    calibrate = bool(p.get("calibrate", False))

    boosters: Dict[str, Any] = {}
    calibrators: Dict[str, Any] = {}
    cal_err: Dict[str, float] = {}
    importances: Dict[str, List[tuple]] = {}
    meta: Dict[str, Any] = {
        "n_train": int(split), "n_valid": int(lab.n - split),
        "n_features": len(names), "horizons": lab.stats.get("horizons"),
        "label_stats": lab.stats, "recipe": recipe_name,
        "label_scheme": r.label_scheme, "features_catalog": r.features_catalog,
        "source": "recipe_trainer",
    }
    top_n = int(p.get("importance_top_n", 20))

    for head in heads:
        y = lab.y[head][:lab.n]
        y_tr, y_va = y[:split], y[split:lab.n]
        if len(np.unique(y_tr)) < 2:
            logger.warning(f"[RecipeTrainer] {recipe_name}/{tf} : labels mono-classe "
                           f"pour la tête {head!r}, entraînement ignoré")
            return None
        spw = (y_tr == 0).sum() / max((y_tr == 1).sum(), 1)
        ds_tr = lgb.Dataset(X_train, label=y_tr, feature_name=names, free_raw_data=False)
        ds_va = lgb.Dataset(X_valid, label=y_va, reference=ds_tr,
                            feature_name=names, free_raw_data=False)
        booster = lgb.train(
            {**common, "scale_pos_weight": spw}, ds_tr,
            num_boost_round=n_estimators, valid_sets=[ds_va],
            callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)],
        )
        boosters[head] = booster
        meta[f"auc_{head}"] = round(
            float(booster.best_score.get("valid_0", {}).get("auc", 0.0)), 4)

        raw_va = booster.predict(X_valid)
        if calibrate and len(np.unique(y_va)) >= 2:
            try:
                from app.ml.backend.isotonic import IsotonicRegression
                iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                iso.fit(raw_va, y_va)
                calibrators[head] = iso
                cal_err[head] = round(float(np.mean(np.abs(iso.predict(raw_va) - y_va))), 4)
            except Exception as e:                        # pragma: no cover
                logger.debug(f"[RecipeTrainer] calibration {head} KO : {e}")

        gains = booster.feature_importance(importance_type="gain")
        pairs = sorted(zip(names, gains), key=lambda kv: -kv[1])
        importances[head] = [(c, float(g)) for c, g in pairs]
        meta[f"feature_importance_{head}"] = [
            {"feature": c, "gain": round(float(g), 2)} for c, g in pairs[:top_n]]

    # Features à gain non nul, toutes têtes confondues — mémoire d'élagage du
    # cycle suivant, publiée dans l'artefact plutôt que gardée en mémoire.
    kept_set = {c for pairs in importances.values() for c, g in pairs if g > 0}
    meta["kept_features"] = [c for c in names if c in kept_set]
    meta["n_kept_features"] = len(meta["kept_features"])
    meta["calibrated"] = bool(calibrators)
    meta["cal_err"] = cal_err

    logger.info(
        f"[RecipeTrainer] {recipe_name}/{tf} : {split} train / {lab.n - split} val | "
        f"{len(names)} feats (gardées {len(meta['kept_features'])}) | calib="
        f"{meta['calibrated']} | "
        + " ".join(f"AUC {h}={meta.get(f'auc_{h}', 0):.3f}" for h in heads)
    )
    return TrainedRecipe(recipe=recipe_name, tf=tf, boosters=boosters,
                         feature_names=names, medians=medians, train_meta=meta,
                         calibrators=calibrators)
