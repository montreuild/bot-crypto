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

#: Réglages LightGBM par défaut — ceux de ``app.ml.backend.trainer.train``,
#: pour qu'une recette omnibus qui n'en déclare aucun produise EXACTEMENT le
#: même modèle qu'aujourd'hui. Noter l'absence de ``seed`` : MLBackend n'en
#: passe pas, et le fixer produirait des arbres différents des siens.
_LGB_DEFAULTS: Dict[str, Any] = dict(
    objective="binary", metric="auc",
    num_leaves=31, learning_rate=0.05,
    min_child_samples=20, subsample=0.8, subsample_freq=5,
    colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
    max_bin=63, force_col_wise=True, verbosity=-1, n_jobs=1,
)

#: Clés du bloc ``hp:`` d'une recette transmises telles quelles à LightGBM.
#: Coder ``max_bin``/``force_col_wise`` en dur ici reproduirait le défaut même
#: qu'on corrige : une recette doit pouvoir décrire son modèle en entier,
#: y compris pour reproduire un modèle historique aux réglages différents.
_LGB_KEYS: tuple = tuple(_LGB_DEFAULTS) + (
    "min_data_in_leaf", "feature_fraction", "bagging_fraction", "bagging_freq",
    "lambda_l1", "lambda_l2", "min_sum_hessian_in_leaf", "max_depth", "seed",
)


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

    #: Têtes que le format bundle sait écrire. Toute autre tête entraînée est
    #: PERDUE à la sauvegarde — d'où l'avertissement dans ``_save_bundle``.
    _TETES_BUNDLE = ("amp", "dir")

    def _save_bundle(self, path_prefix: str) -> bool:
        from app.ml.backend.persistence import save_amp_dir_bundle
        amp, dir_ = self.boosters.get("amp"), self.boosters.get("dir")

        # Une tête entraînée que le format ne sait pas écrire disparaît sans
        # rien casser : l'entraînement réussit, l'artefact est valide, et la
        # tête n'existe simplement pas à l'inférence. C'est le pire mode de
        # défaillance — celui qu'aucun test ne voit et qui se découvre en
        # cherchant pourquoi une stratégie n'exploite pas un signal. On le dit.
        perdues = [t for t in sorted(self.boosters) if t not in self._TETES_BUNDLE]
        if perdues:
            logger.warning(
                f"[RecipeTrainer] {self.recipe} : tête(s) {perdues} entraînée(s) "
                f"mais NON ÉCRITE(S) — le format {self.recipe!r} "
                f"(lgbm_amp_dir_bundle) ne stocke que {list(self._TETES_BUNDLE)}. "
                f"Leur AUC reste dans train_meta pour la mesure, mais elles ne "
                f"seront pas servies à l'inférence : servir ces têtes demande un "
                f"format de persistance multi-têtes."
            )

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
          features: Optional[Any] = None) -> Optional[TrainedRecipe]:
    """Entraîne la recette ``recipe_name`` sur ``df``. Aucune stratégie importée.

    ``params`` surcharge les valeurs de la recette (hyperparamètres, paramètres
    de features/labels) — même précédence que partout ailleurs : la recette
    fixe les défauts, l'appelant a le dernier mot.

    Retourne ``None`` si l'historique est insuffisant ou les labels dégénérés ;
    ces cas sont journalisés avec leur cause, jamais silencieux.
    """
    from app.ml import features_catalog, labelling
    from app.ml.recipe import load_recipe

    why = supports(recipe_name)
    if why:
        logger.error(f"[RecipeTrainer] {recipe_name} non pris en charge : {why}")
        return None

    r = load_recipe(recipe_name)
    # ``hp_for_tf`` en DERNIER — cf. ``Recipe.hp_for_tf`` (ML-11).
    p: Dict[str, Any] = {**r.train_params(), **(params or {}), **r.hp_for_tf(tf)}

    # ``features`` fourni : l'appelant possède déjà la matrice (cache de
    # backtest de scoring_statistique_opus_v4/v5, semé par
    # prepare_for_backtest pour chaque adx_threshold du param_space). La
    # reconstruire ici transformerait un correctif en régression de perf sur
    # la boucle walk-forward.
    fs = features if features is not None else features_catalog.build(
        r.features_catalog, df, p)
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
    # Embargo : le labelleur annonce ses horizons, le module de découpage en
    # déduit combien de lignes d'entraînement portent une information tirée de
    # la validation (cf. app/ml/splitting.py).
    from app.ml.splitting import chrono_split
    plan = chrono_split(lab.n, lab.stats.get("horizons"))
    if plan is None:
        logger.warning(f"[RecipeTrainer] {recipe_name}/{tf} : split impossible (n={lab.n})")
        return None

    stats = dict(lab.stats)
    stats["embargo"] = int(plan.embargo)
    return _fit_heads(recipe_name, r, p, tf, names, X_full,
                      {h: lab.y[h][:lab.n] for h in heads},
                      plan.split, lab.n, heads, stats, train_end=plan.train)


def _fit_heads(recipe_name: str, r, p: Dict[str, Any], tf: str,
               names: List[str], X_full, y_by_head: Dict[str, Any],
               split: int, n: int, heads: List[str],
               lab_stats: Dict[str, Any],
               train_end: Optional[int] = None) -> Optional["TrainedRecipe"]:
    """Cœur d'entraînement, partagé par ``train`` (un symbole) et
    ``train_multi`` (plusieurs). Reçoit une matrice et des cibles DÉJÀ
    alignées et découpées : la façon dont le découpage a été décidé —
    par index sur un symbole, par coupure temporelle commune sur
    plusieurs — appartient à l'appelant."""
    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("[RecipeTrainer] lightgbm requis : pip install lightgbm")
        return None

    from app.ml.backend.features import impute_inplace
    # ``train_end`` < ``split`` quand un embargo s'applique ; l'entraînement
    # s'arrête avant, la validation démarre au même endroit qu'avant.
    train_end = int(split if train_end is None else train_end)
    medians: Dict[str, float] = {}
    for j, col in enumerate(names):
        col_train = X_full[:train_end, j]
        mask = np.isfinite(col_train)
        medians[col] = float(np.median(col_train[mask])) if mask.any() else 0.0

    X_train, X_valid = X_full[:train_end].copy(), X_full[split:n].copy()
    impute_inplace(X_train, names, medians)
    impute_inplace(X_valid, names, medians)

    common = {**_LGB_DEFAULTS,
              **{k: p[k] for k in _LGB_KEYS if k in p and p[k] is not None}}
    # Une recette qui fixe explicitement ``n_jobs`` garde le dernier mot (elle
    # reproduit peut-être un modèle historique) ; sinon le nombre de threads
    # dépend du contexte d'appel, pas d'un littéral (cf. app/ml/threads.py).
    if p.get("n_jobs") is None:
        from app.ml.threads import lgb_threads
        common["n_jobs"] = lgb_threads()
    # ``force_col_wise: false`` doit pouvoir DÉSACTIVER le drapeau, pas
    # seulement l'omettre : LightGBM refuse force_col_wise et force_row_wise
    # simultanément, et une recette qui reproduit un modèle historique a besoin
    # de le retirer.
    if p.get("force_col_wise") is False:
        common.pop("force_col_wise", None)
    n_estimators = int(p.get("n_estimators", 300))
    early_stopping = int(p.get("early_stopping_rounds", 20))
    calibrate = bool(p.get("calibrate", False))

    boosters: Dict[str, Any] = {}
    calibrators: Dict[str, Any] = {}
    cal_err: Dict[str, float] = {}
    importances: Dict[str, List[tuple]] = {}
    meta: Dict[str, Any] = {
        "n_train": int(train_end), "n_valid": int(n - split),
        "embargo": int(split - train_end),
        "n_features": len(names), "horizons": lab_stats.get("horizons"),
        "label_stats": lab_stats, "recipe": recipe_name,
        "label_scheme": r.label_scheme, "features_catalog": r.features_catalog,
        "source": "recipe_trainer",
    }
    top_n = int(p.get("importance_top_n", 20))

    for head in heads:
        y = y_by_head[head][:n]
        y_tr, y_va = y[:train_end], y[split:n]
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
            callbacks=[lgb.early_stopping(early_stopping, verbose=False),
                       lgb.log_evaluation(-1)],
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
        f"[RecipeTrainer] {recipe_name}/{tf} : {split} train / {n - split} val | "
        f"{len(names)} feats (gardées {len(meta['kept_features'])}) | calib="
        f"{meta['calibrated']} | "
        + " ".join(f"AUC {h}={meta.get(f'auc_{h}', 0):.3f}" for h in heads)
    )
    return TrainedRecipe(recipe=recipe_name, tf=tf, boosters=boosters,
                         feature_names=names, medians=medians, train_meta=meta,
                         calibrators=calibrators)


def train_multi(recipe_name: str, frames: Dict[str, pl.DataFrame], tf: str, *,
                params: Optional[Dict[str, Any]] = None) -> Optional[TrainedRecipe]:
    """Entraîne UNE recette sur PLUSIEURS symboles mis en commun (pooling).

    Motivation mesurée (G2). Les recettes du dépôt demandent ``min_bars``
    1500–2000 plus un holdout de 1500 ; sur Euronext (~256 séances/an), un titre
    seul ne fournit ces 3 500 barres journalières qu'après **~13,7 ans**. Le
    pooling n'est donc pas un raffinement pour les actions : c'est la condition
    d'existence du modèle. Sur crypto il reste optionnel — la matrice de
    transfert (``scripts/measure_symbol_transfer.py``) montre qu'un modèle par
    symbole n'y apporte rien de mesurable.

    **Trois pièges, traités explicitement.**

    1. *Ne jamais concaténer les OHLCV.* Les features sont construites PAR
       symbole : une fenêtre glissante qui traverserait la jointure entre deux
       titres produirait des valeurs qui ne correspondent à aucun marché. Ce
       sont les matrices X/y qui sont mises bout à bout, jamais les bougies.
    2. *Le découpage doit être TEMPOREL, pas indiciel.* ``train`` coupe à 80 %
       des lignes ; appliqué à un empilement, cela mettrait le premier symbole
       entièrement en entraînement et le dernier en validation. On coupe donc à
       une DATE commune, et chaque ligne rejoint l'un ou l'autre côté selon son
       horodatage — même protocole que ``measure_symbol_transfer``, qui avait
       mesuré cinq mois de fuite temporelle sur une version indexée.
    3. *Les niveaux de prix ne sont pas comparables entre titres.* Ce n'est pas
       un problème ici : les labels sont des RENDEMENTS et les features des
       grandeurs normalisées ou stationnaires. Aucun prix absolu n'entre dans
       la matrice.

    ``frames`` : ``{symbole: DataFrame}``. Un symbole dont l'historique est trop
    court est ignoré avec un avertissement plutôt que de faire échouer le tout.
    """
    why = supports(recipe_name)
    if why:
        logger.error(f"[RecipeTrainer] {recipe_name} non pris en charge : {why}")
        return None
    if not frames:
        logger.error("[RecipeTrainer] train_multi : aucun symbole fourni")
        return None

    from app.ml import features_catalog, labelling
    from app.ml.recipe import load_recipe

    r = load_recipe(recipe_name)
    p: Dict[str, Any] = {**r.train_params(), **(params or {}), **r.hp_for_tf(tf)}

    names: Optional[List[str]] = None
    parts: List[tuple] = []          # (symbole, times, X, {tête: y})
    skipped: Dict[str, str] = {}
    for symbol, df in frames.items():
        fs = features_catalog.build(r.features_catalog, df, p)
        if fs is None:
            skipped[symbol] = "features non constructibles"
            continue
        lab = labelling.build(r.label_scheme, fs.frame, p)
        # Même plancher que ``train`` sur un symbole unique : sous 200 barres
        # labellisables, un titre n'apporte que du bruit au jeu commun.
        if lab is None or lab.n < 200:
            skipped[symbol] = f"trop peu de barres labellisables ({0 if lab is None else lab.n})"
            continue
        if names is None:
            names = list(fs.names)
        elif list(fs.names) != names:
            # Deux catalogues divergents sous une même recette empileraient des
            # colonnes qui ne désignent pas la même chose.
            skipped[symbol] = "colonnes de features incompatibles"
            continue
        if "time" not in fs.frame.columns:
            skipped[symbol] = "pas de colonne 'time' — découpage temporel impossible"
            continue
        # ``.dt.epoch("s")`` et non ``.to_numpy()`` : sur une colonne Datetime,
        # polars 1.0.0 (version épinglée du dépôt) SEGFAULTE — pas d'exception,
        # le process meurt. Les secondes epoch suffisent au découpage temporel.
        times = fs.frame["time"].dt.epoch("s").to_numpy()[:lab.n]
        parts.append((symbol, times,
                      fs.frame.select(names).to_numpy().astype(np.float32)[:lab.n],
                      {h: lab.y[h][:lab.n] for h in r.heads if h in lab.y}))

    if skipped:
        logger.warning(f"[RecipeTrainer] {recipe_name}/{tf} : {len(skipped)} symbole(s) "
                       f"écarté(s) — {skipped}")
    if not parts or names is None:
        logger.error(f"[RecipeTrainer] {recipe_name}/{tf} : aucun symbole exploitable")
        return None

    heads = [h for h in r.heads if all(h in ys for _, _, _, ys in parts)]
    if not heads:
        logger.error(f"[RecipeTrainer] {recipe_name} : têtes {r.heads} absentes "
                     f"des labels produits")
        return None

    # Coupure TEMPORELLE commune (secondes epoch) : le 80e centile des
    # horodatages mis en commun. Couper à 80 % des LIGNES mettrait le premier
    # symbole en entraînement et le dernier en validation.
    all_times = np.concatenate([t for _, t, _, _ in parts])
    cut_ts = np.int64(np.quantile(all_times, 0.8))

    tr_X, va_X = [], []
    tr_y: Dict[str, list] = {h: [] for h in heads}
    va_y: Dict[str, list] = {h: [] for h in heads}
    per_symbol: Dict[str, int] = {}
    # Embargo appliqué PAR SYMBOLE, avant concaténation : les blocs
    # d'entraînement se suivent dans ``X_tr``, donc « retirer les dernières
    # lignes » après coup n'en retirerait qu'au dernier symbole, alors que
    # chacun a sa propre frontière avec la validation.
    from app.ml.splitting import label_embargo
    embargo = label_embargo(r.label_horizons)
    for symbol, times, X, ys in parts:
        m_tr, m_va = times < cut_ts, times >= cut_ts
        n_tr = int(m_tr.sum())
        garde = max(0, n_tr - embargo)
        tr_X.append(X[m_tr][:garde])
        va_X.append(X[m_va])
        for h in heads:
            tr_y[h].append(ys[h][m_tr][:garde])
            va_y[h].append(ys[h][m_va])
        per_symbol[symbol] = int(len(X))

    X_tr = np.concatenate(tr_X)
    X_va = np.concatenate(va_X)
    split, n = len(X_tr), len(X_tr) + len(X_va)
    if split < 100 or n - split < 50:
        logger.warning(f"[RecipeTrainer] {recipe_name}/{tf} : découpage temporel "
                       f"impraticable ({split} train / {n - split} val)")
        return None

    X_full = np.concatenate([X_tr, X_va])
    y_by_head = {h: np.concatenate([np.concatenate(tr_y[h]), np.concatenate(va_y[h])])
                 for h in heads}
    stats = {"horizons": r.label_horizons, "n_labels": n,
             "pooled_symbols": sorted(per_symbol), "bars_per_symbol": per_symbol,
             "split_at": str(np.array(cut_ts).astype("datetime64[s]")),
             "embargo": int(embargo), "skipped_symbols": skipped}

    out = _fit_heads(recipe_name, r, p, tf, names, X_full, y_by_head,
                     split, n, heads, stats)
    if out is not None:
        out.train_meta["pooled_symbols"] = sorted(per_symbol)
        out.train_meta["n_symbols"] = len(per_symbol)
        logger.info(f"[RecipeTrainer] {recipe_name}/{tf} : pooling de "
                    f"{len(per_symbol)} symboles, coupure au {stats['split_at']}")
    return out
