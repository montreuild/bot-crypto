"""Scoring d'artefacts ML sur un holdout — briques réutilisables du gate (ML-02).

Ce module contient ce qui est **commun à toutes les recettes** (calcul d'AUC
par rang, scorer par défaut au format bundle amplitude+direction V4) ; la
décision de promotion elle-même vit dans ``app.ml.policy``.

Pourquoi un module à part ? Parce que le scoring n'est PAS universel, et le
prétendre a produit des cas particuliers empilés dans le gate :

  - ``opus_stat_retrained_v4`` labellise en single-horizon ``t+1``, pas en
    multi-horizon ``[1,3,6]`` (défaut hérité de V11) → AUC mesurée sur une
    cible que le modèle n'a jamais apprise.
  - ``ml_dynamic_threshold`` persiste UN booster (``{path}.lgb``) avec ses
    propres features et un label à seuil de volatilité adaptatif → le scorer
    "bundle amp+dir" ne trouvait rien et rapportait un faux "holdout dégénéré".

La sortie de ce constat : **c'est la recette qui sait se scorer**, pas le
gate. ``BaseStrategyML`` expose donc deux points d'extension (cf.
``app.engine.engine``) :

  - ``gate_spec`` (déclaratif) — ``{"label_horizons": [...], "metric": "..."}``
    pour les recettes qui utilisent le scorer par défaut mais avec d'autres
    conventions de labels/métrique ;
  - ``score_holdout()`` (classmethod surchargeable) — pour les recettes dont
    le format de persistance, les features ou les labels diffèrent
    complètement.

Le défaut (``score_amp_dir_bundle``) reste le comportement historique : la
grande majorité des recettes du dépôt écrivent le bundle 3-fichiers V4 et
n'ont donc rien à surcharger.

Aucun sklearn/scipy (cf. phase6-sklearn-removal) : l'AUC est calculée par
rang (Mann-Whitney), implémentation numpy pure.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  AUC par rang (Mann-Whitney), sans sklearn/scipy
# ─────────────────────────────────────────────────────────────────────────────
def _rankdata_average(a: np.ndarray) -> np.ndarray:
    """Rangs moyens (méthode "average", équivalent ``scipy.stats.rankdata``) —
    dépôt sans scipy (phase6-sklearn-removal), implémentation numpy pure via
    un double argsort + moyenne par groupe de valeurs égales."""
    n = len(a)
    sorter = np.argsort(a, kind="mergesort")
    a_sorted = a[sorter]
    ordinal = np.arange(1, n + 1, dtype=np.float64)
    is_new_group = np.empty(n, dtype=bool)
    is_new_group[0] = True
    if n > 1:
        is_new_group[1:] = a_sorted[1:] != a_sorted[:-1]
    group_id = np.cumsum(is_new_group) - 1
    sum_per_group = np.bincount(group_id, weights=ordinal)
    cnt_per_group = np.bincount(group_id)
    avg_rank_sorted = (sum_per_group / cnt_per_group)[group_id]
    ranks = np.empty(n, dtype=np.float64)
    ranks[sorter] = avg_rank_sorted
    return ranks


def rank_auc(y_true: np.ndarray, scores: np.ndarray) -> Optional[float]:
    """AUC ROC via la statistique de Mann-Whitney U (équivalente à l'AUC,
    Wilcoxon rank-sum) — ``None`` si une seule classe est présente (AUC non
    définie, PAS 0.5 : ne pas confondre "pas de signal" et "pas mesurable")."""
    y = np.asarray(y_true)
    s = np.asarray(scores, dtype=np.float64)
    if len(y) == 0 or len(y) != len(s):
        return None
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    finite = np.isfinite(s)
    if not finite.all():
        y, s = y[finite], s[finite]
        n_pos, n_neg = int((y == 1).sum()), int((y == 0).sum())
        if n_pos == 0 or n_neg == 0:
            return None
    ranks = _rankdata_average(s)
    sum_ranks_pos = float(ranks[y == 1].sum())
    u = sum_ranks_pos - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


# ─────────────────────────────────────────────────────────────────────────────
#  Scorer par défaut : bundle amplitude + direction au format V4
# ─────────────────────────────────────────────────────────────────────────────
def score_amp_dir_bundle(path_prefix: str, holdout_df, *,
                         label_horizons: Optional[List[int]] = None,
                         amp_top_pct: float = 0.30) -> Dict[str, Any]:
    """Scorer par défaut — artefact au format bundle 3-fichiers
    (``{path}.amp.lgb`` + ``{path}.dir.lgb`` + ``{path}.meta.json``) et
    features V4. Calcule l'AUC réalisée (amp + dir) sur ``holdout_df``.

    Features et labels sont dérivés de ``holdout_df`` seul : le label d'un
    horizon ``h`` d'une barre proche de la fin du holdout nécessite ``h``
    barres *après elle mais toujours dans le holdout* — jamais au-delà (les
    fonctions de labellisation tronquent silencieusement les dernières
    ``max(horizons)`` barres, non labellisables).

    Retourne ``{}`` si l'artefact est illisible ou le holdout trop court, et
    ``{"unsupported_format": True}`` si des fichiers modèle existent au
    préfixe donné mais dans un format que CE scorer ne lit pas — filet de
    sécurité pour une recette qui n'a ni déclaré ``gate_spec`` ni surchargé
    ``score_holdout`` (cf. docstring module).
    """
    from app.ml.backend.features import (
        build_features,
        multi_horizon_labels,
        single_horizon_labels,
    )
    from app.ml.backend.persistence import load_amp_dir_bundle
    from app.ml.backend.predictor import predict_batch_raw

    bundle = load_amp_dir_bundle(path_prefix)
    if bundle is None or bundle.get("amp_model") is None:
        if os.path.exists(f"{path_prefix}.lgb") and not os.path.exists(f"{path_prefix}.amp.lgb"):
            return {"unsupported_format": True}
        return {}

    # Boosters présents mais meta.json sans liste de features : c'est le
    # format `save_lgb_with_scaler` (format_version 1, cf. persistence.py —
    # utilisé par scoring_statistique_opus_v4/v5), qui ne sérialise ni
    # features ni médianes. Impossible de reconstruire la matrice d'entrée :
    # on le dit, au lieu de laisser predict_batch_raw renvoyer None et le
    # gate conclure à un « holdout dégénéré ».
    if not bundle.get("features"):
        return {"unsupported_format": True}

    feats = build_features(holdout_df)
    if feats is None or len(feats) < 50:
        return {}

    close = feats["close"].to_numpy().astype(np.float64)
    hs = list(label_horizons or [1, 3, 6])
    if len(hs) > 1:
        y_amp, y_dir, n, _, _ = multi_horizon_labels(close, hs, amp_top_pct)
    else:
        y_amp, y_dir, n, _ = single_horizon_labels(close, amp_top_pct)
    if n < 30:
        return {}

    scored_feats = feats.head(n)
    out: Dict[str, Any] = {"n": int(n)}
    amp_scores = predict_batch_raw(bundle["amp_model"], bundle.get("amp_cal"),
                                   bundle["features"], bundle["medians"], scored_feats)
    if amp_scores is not None:
        out["auc_amp"] = rank_auc(y_amp, amp_scores)
    # V4 legacy : amp/dir peuvent avoir des features/médianes distinctes
    # (cf. persistence.save_amp_dir_bundle) — None = partagées (cas courant).
    dir_feats = bundle.get("dir_features") if bundle.get("dir_features") is not None else bundle["features"]
    dir_meds = bundle.get("dir_medians") if bundle.get("dir_medians") is not None else bundle["medians"]
    dir_scores = predict_batch_raw(bundle.get("dir_model"), bundle.get("dir_cal"),
                                   dir_feats, dir_meds, scored_feats)
    if dir_scores is not None:
        out["auc_dir"] = rank_auc(y_dir, dir_scores)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Spécification de gate déclarée par une recette
# ─────────────────────────────────────────────────────────────────────────────
#  Clés reconnues dans ``BaseStrategyML.gate_spec`` — volontairement
#  restreintes : tout le reste (fenêtre, plancher, epsilon) est une décision
#  d'EXPLOITATION qui appartient à la config, pas à la recette.
GATE_SPEC_KEYS = ("label_horizons", "amp_top_pct", "metric")


def _as_class(strategy_or_name: Any) -> Any:
    """Classe de stratégie depuis une instance, une classe ou un nom de module.
    ``None`` si irrésoluble — jamais d'exception."""
    if isinstance(strategy_or_name, str):
        try:
            import importlib
            return importlib.import_module(f"app.strategies.{strategy_or_name}").Strategy
        except Exception:
            return None
    if isinstance(strategy_or_name, type):
        return strategy_or_name
    return type(strategy_or_name)


def resolve_recipe_name(strategy_or_name: Any,
                        params: Optional[Dict[str, Any]] = None) -> str:
    """Nom de la recette consommée par une stratégie — clé du registre.

    C'est la correction de la fracture (b) : la clé était le NOM DE STRATÉGIE,
    codé en dur sur quatre sites. Conséquence, « même recette ⇒ mêmes
    artefacts » (ML-02 §5.5) était inapplicable : cinq consommateurs d'un même
    modèle publiaient cinq lignées, et ``v12`` réentraînait la recette de
    ``v11`` sous un autre nom.

    Lève si la stratégie ne déclare aucune recette : une stratégie ML sans
    liaison est une erreur de configuration. Retomber sur son nom
    ressusciterait exactement le défaut qu'on vient de corriger.
    """
    from app.ml.recipe import primary_recipe
    cls = _as_class(strategy_or_name)
    name = primary_recipe(cls, params) if cls is not None else None
    if not name:
        label = getattr(cls, "name", None) or strategy_or_name
        raise ValueError(
            f"{label!r} ne déclare aucune recette : ajouter un attribut "
            f"`models = {{'signal': '<recette>'}}` à la classe, ou un bloc "
            f"`models:` dans son YAML (cf. app.ml.recipe)."
        )
    return name


def resolve_gate_spec(strategy_or_name: Any,
                      params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Conventions de scoring de la recette consommée par ``strategy_or_name``.

    Source de vérité : le fichier ``recipes/{nom}.yaml``. L'attribut de classe
    ``gate_spec`` reste lu en dernier recours pour une stratégie sans liaison
    (aucune aujourd'hui) — mais il ne doit pas doubler la recette : deux
    déclarations de la même convention finiraient par diverger, ce qui est
    précisément l'origine du décalage 0.732 vs 0.702.

    Best-effort : jamais d'exception, retourne ``{}`` si rien n'est résoluble.
    """
    cls = _as_class(strategy_or_name)
    if cls is None:
        return {}
    try:
        from app.ml.recipe import load_recipe, primary_recipe
        name = primary_recipe(cls, params)
        if name:
            return {k: v for k, v in load_recipe(name).gate_spec().items()
                    if k in GATE_SPEC_KEYS}
    except Exception as e:
        logger.warning(f"[scoring] gate_spec depuis la recette KO pour {cls} : {e}")

    spec = getattr(cls, "gate_spec", None) or {}
    if not isinstance(spec, dict):
        return {}
    return {k: v for k, v in spec.items() if k in GATE_SPEC_KEYS}


def resolve_scorer(strategy_or_name: Any):
    """Retourne le ``score_holdout`` de la recette s'il est surchargé, sinon
    ``None`` (l'appelant utilise alors ``score_amp_dir_bundle``).

    Comparaison à l'implémentation de ``BaseStrategyML`` : une recette qui
    n'a pas surchargé la méthode ne "compte" pas comme un scorer dédié, ce
    qui évite un aller-retour inutile par la classe de base.
    """
    cls: Any = None
    if isinstance(strategy_or_name, str):
        try:
            import importlib
            cls = importlib.import_module(f"app.strategies.{strategy_or_name}").Strategy
        except Exception:
            return None
    elif isinstance(strategy_or_name, type):
        cls = strategy_or_name
    else:
        cls = type(strategy_or_name)

    fn = getattr(cls, "score_holdout", None)
    if fn is None:
        return None
    try:
        from app.engine.engine import BaseStrategyML
        base_fn = getattr(BaseStrategyML, "score_holdout", None)
        # __func__ : comparer les fonctions sous-jacentes des classmethods liées.
        if base_fn is not None and getattr(fn, "__func__", fn) is getattr(base_fn, "__func__", base_fn):
            return None
    except Exception:
        return None
    return fn


# ─────────────────────────────────────────────────────────────────────────────
#  Diagnostics d'entraînement d'une instance fraîchement entraînée
# ─────────────────────────────────────────────────────────────────────────────
def resolve_train_meta(strategy: Any, tf: str) -> Dict[str, Any]:
    """Diagnostics du modèle que ``strategy`` vient d'entraîner pour ``tf``.

    C'est ce que la page « Modèles » affiche (top features + gain, erreur de
    calibration, AUC par régime) : produit par ``app.ml.backend.trainer`` et
    déposé dans ``_train_meta``, dict indexé PAR TF.

    Le repli de clé est le même que celui de ``save_model`` et pour la même
    raison : l'indexation dépend de l'appelant — ``fit()`` écrit sous
    ``"default"`` faute de TF, ``score()`` sous le symbole. Lire la seule clé
    ``tf`` ne rendrait rien sur le chemin d'entraînement offline, et un
    panneau vide se lit comme « ce modèle n'a pas de diagnostics » alors qu'il
    en a.

    Best-effort : ``{}`` si la stratégie n'expose rien d'exploitable — toutes
    les recettes n'instrumentent pas leur entraînement (cf. docstring module).
    """
    meta = getattr(strategy, "_train_meta", None)
    if not isinstance(meta, dict) or not meta:
        return {}
    for key in (tf, "default"):
        entry = meta.get(key)
        if isinstance(entry, dict) and entry:
            return dict(entry)
    entries = [v for v in meta.values() if isinstance(v, dict) and v]
    return dict(entries[0]) if len(entries) == 1 else {}
