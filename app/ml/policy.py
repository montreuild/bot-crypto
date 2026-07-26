"""Politique de rafraîchissement des modèles ML — gate de promotion (ML-02 §3.3/§3.4).

Une seule implémentation, appelée par trois exécutants (conception §3.3) :
le live (``app.ml.trainer.MLStrategyTrainer``), le backtest ``simulated_live``
(``app.engine.backtest``) et, à terme, le runner CLI committé. Le principe :

    candidat entraîné sur ``[…, T-h]``  vs  sortant déjà publié
                    │
                    ▼
     score des DEUX sur le MÊME holdout ``]T-h, T]`` (aveugle pour les deux —
     le sortant a train_end ≤ T-cadence ≤ T-h par construction)
                    │
                    ▼
        promotion ssi le candidat ne régresse pas (AUC ≥ floor, et
        AUC ≥ AUC(sortant) - epsilon si un sortant comparable existe)

Le gate ne dépend d'AUCUNE spécificité de backend ML : il s'appuie uniquement
sur le contrat ``BaseStrategyML`` (``fit``, ``save_model``, ``is_trained``,
``load_model``, ``reset_model``, et pour le scoring ``gate_spec`` /
``score_holdout``). Le SCORING lui-même n'est pas universel et n'essaie plus
de l'être : il est porté par la recette (cf. ``app.ml.scoring``), avec le
bundle amplitude+direction V4 comme implémentation par défaut. Aucun
sklearn/scipy (cf. phase6-sklearn-removal) : l'AUC est calculée par rang
(Mann-Whitney), implémentation numpy pure.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import app.ml.model_registry as registry
from app.ml.scoring import (
    resolve_gate_spec,
    resolve_recipe_name,
    resolve_scorer,
    resolve_train_meta,
    score_amp_dir_bundle,
)

logger = logging.getLogger(__name__)

# Clés de params reprises dans le hash de recette (best-effort — les
# stratégies bespoke n'exposent pas toutes exactement les mêmes clés que
# MLBackend ; un sous-ensemble commun suffit à distinguer les recettes dans
# l'usage courant, ce n'est pas une frontière de sécurité).
_RECIPE_PARAM_KEYS = (
    "amp_top_pct", "n_estimators", "num_leaves", "learning_rate",
    "label_horizons", "calibrate", "prune_features", "warmup_bars",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Score d'un artefact sur un holdout — DISPATCH vers la recette
# ─────────────────────────────────────────────────────────────────────────────
def score_holdout(path_prefix: str, holdout_df, *,
                  strategy: Any = None,
                  gate_cfg: Any = None,
                  params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Score l'artefact à ``path_prefix`` sur ``holdout_df``.

    Le scoring appartient à la RECETTE, pas au gate (cf. ``app.ml.scoring``) :
    si ``strategy`` est fourni et surcharge ``score_holdout``, on l'appelle ;
    sinon on retombe sur le scorer par défaut (bundle amplitude+direction V4).
    ``strategy`` accepte une instance, une classe ou un nom de recette.

    Sans ``gate_cfg``, les défauts de ``GateConfig`` s'appliquent. Les anciens
    arguments ``label_horizons``/``amp_top_pct`` en direct ont été retirés :
    ils doublonnaient ``gate_cfg`` et permettaient de scorer avec une
    convention de labels différente de celle déclarée par la recette — la
    cause exacte du décalage 0.732 vs 0.702 qui a motivé ``gate_spec``.
    """
    if gate_cfg is None:
        gate_cfg = GateConfig()

    if strategy is not None:
        scorer = resolve_scorer(strategy)
        if scorer is not None:
            try:
                return scorer(path_prefix, holdout_df, gate_cfg=gate_cfg, params=params or {})
            except Exception as e:
                logger.warning(
                    f"[MLPolicy] scorer dédié de {getattr(strategy, 'name', strategy)!r} "
                    f"KO sur {path_prefix} : {e} — repli sur le scorer par défaut"
                )

    return score_amp_dir_bundle(
        path_prefix, holdout_df,
        label_horizons=gate_cfg.label_horizons, amp_top_pct=gate_cfg.amp_top_pct,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Gate de promotion
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GateResult:
    decision: str            # "promote" | "keep" | "initial" | "failed" | "skipped"
    reason: str
    candidate_metrics: Dict[str, Any] = field(default_factory=dict)
    incumbent_metrics: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"decision": self.decision, "reason": self.reason,
                "candidate_metrics": self.candidate_metrics,
                "incumbent_metrics": self.incumbent_metrics}


def decide_gate(candidate_metrics: Optional[Dict[str, Any]],
                incumbent_metrics: Optional[Dict[str, Any]], *,
                auc_floor: float = 0.55, epsilon: float = 0.01,
                metric: str = "auc_amp") -> GateResult:
    """Décide promote/keep. ``incumbent_metrics is None`` = aucun sortant
    (cold start) ; ``incumbent_metrics == {}`` = sortant présent mais
    non scorable (holdout dégénéré) — deux cas distincts, cf. docstring
    module. Le plancher ``auc_floor`` s'applique dans tous les cas ;
    ``epsilon`` ne joue que si un sortant comparable existe."""
    if (candidate_metrics or {}).get("unsupported_format"):
        return GateResult(
            "keep",
            "candidat : format de persistance non reconnu par le scorer générique "
            "(pas de bundle amplitude+direction V4, cf. score_holdout) — cette recette "
            "n'a pas encore de scoring de gate dédié, comparaison manuelle requise",
            candidate_metrics or {}, incumbent_metrics)
    cand_auc = (candidate_metrics or {}).get(metric)
    if cand_auc is None:
        return GateResult("keep",
                          f"candidat : {metric} indisponible (labels mono-classe / holdout dégénéré)",
                          candidate_metrics or {}, incumbent_metrics)
    if cand_auc < auc_floor:
        return GateResult("keep", f"{metric} candidat={cand_auc:.3f} < plancher={auc_floor:.3f}",
                          candidate_metrics, incumbent_metrics)

    inc_auc = (incumbent_metrics or {}).get(metric)
    if inc_auc is None:
        if incumbent_metrics is None:
            reason = f"{metric} candidat={cand_auc:.3f} >= plancher={auc_floor:.3f} ; aucun sortant"
            return GateResult("initial", reason, candidate_metrics, incumbent_metrics)
        reason = (f"{metric} candidat={cand_auc:.3f} >= plancher={auc_floor:.3f} ; "
                 f"sortant non mesurable sur ce holdout, promu par défaut")
        return GateResult("promote", reason, candidate_metrics, incumbent_metrics)

    if cand_auc >= inc_auc - epsilon:
        reason = f"{metric} candidat={cand_auc:.3f} >= sortant({inc_auc:.3f}) - eps({epsilon:.3f})"
        return GateResult("promote", reason, candidate_metrics, incumbent_metrics)
    reason = f"{metric} candidat={cand_auc:.3f} < sortant({inc_auc:.3f}) - eps({epsilon:.3f}) : régression"
    return GateResult("keep", reason, candidate_metrics, incumbent_metrics)


# ─────────────────────────────────────────────────────────────────────────────
#  Configuration de la politique (lue depuis les params résolus de la stratégie)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GateConfig:
    """Hypothèses par défaut (conception §8) — à calibrer au banc (window
    sweep), pas des réglages de référence. Surchageable par le bloc ``model:``
    du YAML de stratégie (clés préfixées ``gate_``/``window_bars``)."""
    holdout_bars: int = 1500
    window_bars: Optional[int] = None       # None = tout ce qui est fourni (borné par l'appelant)
    min_window_bars: int = 2000
    auc_floor: float = 0.55
    epsilon: float = 0.01
    metric: str = "auc_amp"
    label_horizons: List[int] = field(default_factory=lambda: [1, 3, 6])
    amp_top_pct: float = 0.30

    @classmethod
    def from_params(cls, p: Dict[str, Any]) -> "GateConfig":
        """``p`` = ``{**gate_spec_de_la_recette, **params_résolus}``.

        Deux origines se rencontrent ici, avec des préséances différentes :
        la RECETTE déclare ses conventions (``metric``, ``label_horizons``,
        ``amp_top_pct`` — cf. ``gate_spec``), l'EXPLOITATION règle les seuils
        (clés préfixées ``gate_``, lues du YAML). ``gate_metric`` gagne donc
        sur le ``metric`` de la recette : un opérateur doit pouvoir arbitrer
        sur une autre métrique sans toucher au code de la stratégie.
        """
        p = p or {}
        wb = p.get("window_bars")
        return cls(
            holdout_bars=int(p.get("gate_holdout_bars", 1500)),
            window_bars=int(wb) if wb else None,
            min_window_bars=int(p.get("gate_min_window_bars", 2000)),
            auc_floor=float(p.get("gate_auc_floor", 0.55)),
            epsilon=float(p.get("gate_epsilon", 0.01)),
            metric=str(p.get("gate_metric", p.get("metric", "auc_amp"))),
            label_horizons=list(p.get("label_horizons", [1, 3, 6])),
            amp_top_pct=float(p.get("amp_top_pct", 0.30)),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Orchestration — appelée par le live trainer et le backtest simulated_live
# ─────────────────────────────────────────────────────────────────────────────
def maybe_refresh(strategy: Any, train_symbol: str, tf: str, df, *,
                  params: Dict[str, Any],
                  recipe: Optional[str] = None,
                  gate_cfg: Optional[GateConfig] = None,
                  source: str = "live",
                  base_dir: str = registry.DEFAULT_BASE_DIR) -> Dict[str, Any]:
    """Entraîne un candidat sur ``df`` (fenêtre causale disponible "as of
    now"), le compare au sortant publié via un holdout partagé, publie le
    résultat dans le registre, et s'assure que ``strategy`` termine avec le
    modèle GAGNANT chargé en mémoire (jamais un candidat rejeté).

    ``train_symbol`` est le symbole d'où vient ``df``. Il n'entre PAS dans la
    clé du registre (qui est ``(tf, recette)``) : il est enregistré en
    provenance de l'artefact publié, parce que l'artefact servira ensuite tous
    les symboles tradés — cf. la docstring de ``app.ml.model_registry``.

    ``df`` doit être strictement antérieur à l'instant de décision (aucune
    barre "future" par rapport à ce que l'appelant sait déjà) — c'est
    l'appelant (live trainer, Backtester) qui garantit cette causalité en ne
    passant que les données disponibles à cet instant.

    Retourne un résumé JSON-sérialisable (decision, métriques, versions) —
    jamais d'exception pour un échec d'entraînement (retourne
    ``decision="failed"``) ; seules les erreurs de programmation (mauvais
    type d'argument, etc.) remontent.
    """
    recipe = recipe or resolve_recipe_name(strategy, params)
    gc_ = gate_cfg or GateConfig.from_params({**resolve_gate_spec(strategy), **(params or {})})

    n = len(df) if df is not None else 0
    required = gc_.holdout_bars + gc_.min_window_bars
    if n < required:
        return {"decision": "skipped", "reason": "insufficient_data",
                "n_bars": n, "required_bars": required,
                "recipe": recipe, "train_symbol": train_symbol, "tf": tf}

    holdout_df = df.tail(gc_.holdout_bars + 210)
    pre_holdout = df.slice(0, n - gc_.holdout_bars)
    train_df = pre_holdout.tail(gc_.window_bars) if gc_.window_bars else pre_holdout

    incumbent = registry.latest_promoted(tf, recipe, base_dir=base_dir)
    incumbent_metrics = None
    if incumbent is not None:
        incumbent_metrics = score_holdout(
            incumbent.path_prefix, holdout_df,
            strategy=strategy, gate_cfg=gc_, params=params,
        )

    tmp_dir = tempfile.mkdtemp(prefix="ml_gate_")
    try:
        # Le nom DOIT se terminer par "_{tf}" : save_model/load_model dérivent
        # le TF du nom de fichier (``_tf_from_path``), pas d'un paramètre
        # explicite — un suffixe différent (ex. "_candidate") ferait déduire
        # un TF erroné et produirait un save_model() silencieusement no-op.
        tmp_prefix = os.path.join(tmp_dir, f"{recipe}_{tf}")
        try:
            strategy.fit(train_df, params={getattr(strategy, "name", recipe): params})
        except Exception as e:
            logger.error(f"[MLPolicy] {tf}/{recipe} : fit() candidat KO : {e}")
            return {"decision": "failed", "reason": f"fit KO : {e}",
                    "recipe": recipe, "train_symbol": train_symbol, "tf": tf,
                    "incumbent_version": incumbent.version_id if incumbent else None}
        if not getattr(strategy, "is_trained", False):
            return {"decision": "failed", "reason": "fit n'a produit aucun modèle exploitable",
                    "recipe": recipe, "train_symbol": train_symbol, "tf": tf,
                    "incumbent_version": incumbent.version_id if incumbent else None}

        strategy.save_model(tmp_prefix)
        # Vérifier ICI que l'artefact existe. Sans ce contrôle, un save_model()
        # no-op (magasin de modèles indexé autrement que par TF) se propageait
        # en deux symptômes trompeurs : un score_holdout sur un préfixe vide,
        # rapporté comme « auc indisponible (labels mono-classe / holdout
        # dégénéré) », puis un « artefacts absents » du registre — deux
        # messages qui accusaient les données alors que rien n'avait été écrit.
        missing = registry.missing_artifacts(recipe, tmp_prefix)
        if missing:
            logger.error(
                f"[MLPolicy] {tf}/{recipe} : save_model() n'a produit aucun artefact "
                f"exploitable (manquants : {missing})"
            )
            return {"decision": "failed",
                    "reason": f"save_model n'a écrit aucun artefact (manquants : {missing})",
                    "recipe": recipe, "train_symbol": train_symbol, "tf": tf,
                    "incumbent_version": incumbent.version_id if incumbent else None}

        candidate_metrics = score_holdout(
            tmp_prefix, holdout_df,
            strategy=strategy, gate_cfg=gc_, params=params,
        )
        gate = decide_gate(candidate_metrics, incumbent_metrics,
                           auc_floor=gc_.auc_floor, epsilon=gc_.epsilon, metric=gc_.metric)
        # Capturé ICI, avant le reset_model() de la branche "keep" plus bas :
        # ce sont les diagnostics du CANDIDAT, et c'est justement quand il est
        # rejeté qu'on veut voir pourquoi. Après restauration du sortant, ils
        # ne seraient plus les siens.
        candidate_train_meta = resolve_train_meta(strategy, tf)

        bounds = registry.train_window_bounds(train_df)
        recipe_cfg = {k: params.get(k) for k in _RECIPE_PARAM_KEYS if k in params}
        recipe_cfg.update({"holdout_bars": gc_.holdout_bars, "window_bars": gc_.window_bars,
                           "auc_floor": gc_.auc_floor, "epsilon": gc_.epsilon})

        published = registry.publish(
            tf, recipe, tmp_prefix, train_symbol=train_symbol,
            train_start=bounds["train_start"], train_end=bounds["train_end"],
            n_bars=bounds["n_bars"], recipe_cfg=recipe_cfg, source=source,
            decision=gate.decision,
            decision_metrics={
                "candidate": candidate_metrics, "incumbent": incumbent_metrics,
                "reason": gate.reason,
                "incumbent_version": incumbent.version_id if incumbent else None,
            },
            base_dir=base_dir,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if gate.decision == "keep":
        # Le candidat régresse (ou est sous le plancher) : la stratégie vient
        # d'être mutée vers lui par fit() — il faut restaurer le sortant en
        # mémoire (ou repasser non-entraînée s'il n'y en a pas), jamais
        # laisser un modèle rejeté silencieusement en place.
        strategy.reset_model()
        if incumbent is not None:
            strategy.load_model(incumbent.path_prefix)

    logger.info(
        f"[MLPolicy] {tf}/{recipe} : {gate.decision} — {gate.reason}"
    )
    return {
        "decision": gate.decision, "reason": gate.reason,
        "candidate": candidate_metrics, "incumbent": incumbent_metrics,
        "published_version": published.version_id if published else None,
        "incumbent_version": incumbent.version_id if incumbent else None,
        "recipe": recipe, "train_symbol": train_symbol, "tf": tf,
        "train_meta": candidate_train_meta,
    }


def freshness_warning(artifact: Optional["registry.ArtifactRef"], *,
                      current_bars_ago: Optional[int] = None,
                      cadence_bars: Optional[int] = None,
                      stale_factor: float = 2.0) -> Optional[str]:
    """Message d'alerte si ``artifact`` est trop vieux par rapport à la
    cadence de rafraîchissement attendue — ``None`` si tout va bien ou si la
    fraîcheur n'est pas mesurable (artefact sans provenance datée)."""
    if artifact is None:
        return "aucun modèle chargé"
    if not artifact.train_end:
        return f"modèle sans provenance datée (version={artifact.version_id}) — fraîcheur non mesurable"
    if current_bars_ago is None or cadence_bars is None or cadence_bars <= 0:
        return None
    if current_bars_ago > stale_factor * cadence_bars:
        return (f"modèle {artifact.version_id} vieux de {current_bars_ago} barres "
               f"(> {stale_factor:.0f}× la cadence {cadence_bars}) — ré-entraînement en retard ?")
    return None
