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
``load_model``, ``reset_model``) + le format de persistance universel à 3
fichiers (``app.ml.backend.persistence`` — toutes les stratégies ML du dépôt
y écrivent, cf. ``save_model``/``save_amp_dir_bundle``). Aucun sklearn/scipy
(cf. phase6-sklearn-removal) : l'AUC est calculée par rang (Mann-Whitney),
implémentation numpy pure.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

import app.ml.model_registry as registry
from app.ml.backend.persistence import load_amp_dir_bundle
from app.ml.backend.predictor import predict_batch_raw

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
#  Score d'un artefact sur un holdout (features + labels calculés
#  UNIQUEMENT sur le holdout — aucun look-ahead au-delà)
# ─────────────────────────────────────────────────────────────────────────────
def score_holdout(path_prefix: str, holdout_df, *,
                  label_horizons: Optional[List[int]] = None,
                  amp_top_pct: float = 0.30) -> Dict[str, Any]:
    """Charge l'artefact à ``path_prefix`` (format universel 3-fichiers) et
    calcule son AUC réalisée (amp + dir) sur ``holdout_df``.

    Features et labels sont dérivés de ``holdout_df`` seul : le label d'un
    horizon ``h`` d'une barre proche de la fin du holdout nécessite ``h``
    barres *après elle mais toujours dans le holdout* — jamais au-delà (les
    fonctions de labellisation tronquent silencieusement les dernières
    ``max(horizons)`` barres, non labellisables). Retourne ``{}`` si
    l'artefact est illisible ou le holdout trop court.
    """
    from app.ml.backend.features import (
        build_features,
        multi_horizon_labels,
        single_horizon_labels,
    )

    bundle = load_amp_dir_bundle(path_prefix)
    if bundle is None or bundle.get("amp_model") is None:
        return {}

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
    dir_meds  = bundle.get("dir_medians") if bundle.get("dir_medians") is not None else bundle["medians"]
    dir_scores = predict_batch_raw(bundle.get("dir_model"), bundle.get("dir_cal"),
                                   dir_feats, dir_meds, scored_feats)
    if dir_scores is not None:
        out["auc_dir"] = rank_auc(y_dir, dir_scores)
    return out


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
        p = p or {}
        wb = p.get("window_bars")
        return cls(
            holdout_bars=int(p.get("gate_holdout_bars", 1500)),
            window_bars=int(wb) if wb else None,
            min_window_bars=int(p.get("gate_min_window_bars", 2000)),
            auc_floor=float(p.get("gate_auc_floor", 0.55)),
            epsilon=float(p.get("gate_epsilon", 0.01)),
            metric=str(p.get("gate_metric", "auc_amp")),
            label_horizons=list(p.get("label_horizons", [1, 3, 6])),
            amp_top_pct=float(p.get("amp_top_pct", 0.30)),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Orchestration — appelée par le live trainer et le backtest simulated_live
# ─────────────────────────────────────────────────────────────────────────────
def maybe_refresh(strategy: Any, symbol: str, tf: str, df, *,
                  params: Dict[str, Any],
                  recipe: Optional[str] = None,
                  gate_cfg: Optional[GateConfig] = None,
                  source: str = "live",
                  base_dir: str = registry.DEFAULT_BASE_DIR) -> Dict[str, Any]:
    """Entraîne un candidat sur ``df`` (fenêtre causale disponible "as of
    now"), le compare au sortant publié via un holdout partagé, publie le
    résultat dans le registre, et s'assure que ``strategy`` termine avec le
    modèle GAGNANT chargé en mémoire (jamais un candidat rejeté).

    ``df`` doit être strictement antérieur à l'instant de décision (aucune
    barre "future" par rapport à ce que l'appelant sait déjà) — c'est
    l'appelant (live trainer, Backtester) qui garantit cette causalité en ne
    passant que les données disponibles à cet instant.

    Retourne un résumé JSON-sérialisable (decision, métriques, versions) —
    jamais d'exception pour un échec d'entraînement (retourne
    ``decision="failed"``) ; seules les erreurs de programmation (mauvais
    type d'argument, etc.) remontent.
    """
    recipe = recipe or getattr(strategy, "name", "strategy")
    gc_ = gate_cfg or GateConfig.from_params(params)

    n = len(df) if df is not None else 0
    required = gc_.holdout_bars + gc_.min_window_bars
    if n < required:
        return {"decision": "skipped", "reason": "insufficient_data",
                "n_bars": n, "required_bars": required,
                "recipe": recipe, "symbol": symbol, "tf": tf}

    holdout_df = df.tail(gc_.holdout_bars + 210)
    pre_holdout = df.slice(0, n - gc_.holdout_bars)
    train_df = pre_holdout.tail(gc_.window_bars) if gc_.window_bars else pre_holdout

    incumbent = registry.latest_promoted(symbol, tf, recipe, base_dir=base_dir)
    incumbent_metrics = None
    if incumbent is not None:
        incumbent_metrics = score_holdout(
            incumbent.path_prefix, holdout_df,
            label_horizons=gc_.label_horizons, amp_top_pct=gc_.amp_top_pct,
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
            logger.error(f"[MLPolicy] {symbol}/{tf}/{recipe} : fit() candidat KO : {e}")
            return {"decision": "failed", "reason": f"fit KO : {e}",
                    "recipe": recipe, "symbol": symbol, "tf": tf,
                    "incumbent_version": incumbent.version_id if incumbent else None}
        if not getattr(strategy, "is_trained", False):
            return {"decision": "failed", "reason": "fit n'a produit aucun modèle exploitable",
                    "recipe": recipe, "symbol": symbol, "tf": tf,
                    "incumbent_version": incumbent.version_id if incumbent else None}

        strategy.save_model(tmp_prefix)
        candidate_metrics = score_holdout(
            tmp_prefix, holdout_df,
            label_horizons=gc_.label_horizons, amp_top_pct=gc_.amp_top_pct,
        )
        gate = decide_gate(candidate_metrics, incumbent_metrics,
                           auc_floor=gc_.auc_floor, epsilon=gc_.epsilon, metric=gc_.metric)

        bounds = registry.train_window_bounds(train_df)
        recipe_cfg = {k: params.get(k) for k in _RECIPE_PARAM_KEYS if k in params}
        recipe_cfg.update({"holdout_bars": gc_.holdout_bars, "window_bars": gc_.window_bars,
                           "auc_floor": gc_.auc_floor, "epsilon": gc_.epsilon})

        published = registry.publish(
            symbol, tf, recipe, tmp_prefix,
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
        f"[MLPolicy] {symbol}/{tf}/{recipe} : {gate.decision} — {gate.reason}"
    )
    return {
        "decision": gate.decision, "reason": gate.reason,
        "candidate": candidate_metrics, "incumbent": incumbent_metrics,
        "published_version": published.version_id if published else None,
        "incumbent_version": incumbent.version_id if incumbent else None,
        "recipe": recipe, "symbol": symbol, "tf": tf,
    }


def freshness_warning(artifact: Optional["registry.ArtifactRef"], *,
                      current_bars_ago: Optional[int] = None,
                      cadence_bars: Optional[int] = None,
                      stale_factor: float = 2.0) -> Optional[str]:
    """Message d'alerte si ``artifact`` est trop vieux par rapport à la
    cadence de rafraîchissement attendue — ``None`` si tout va bien ou si la
    fraîcheur n'est pas mesurable (pas de provenance, ex. legacy)."""
    if artifact is None:
        return "aucun modèle chargé"
    if artifact.legacy or not artifact.train_end:
        return f"modèle sans provenance datée (legacy, version={artifact.version_id}) — fraîcheur non mesurable"
    if current_bars_ago is None or cadence_bars is None or cadence_bars <= 0:
        return None
    if current_bars_ago > stale_factor * cadence_bars:
        return (f"modèle {artifact.version_id} vieux de {current_bars_ago} barres "
               f"(> {stale_factor:.0f}× la cadence {cadence_bars}) — ré-entraînement en retard ?")
    return None
