"""Runner d'entraînement ML committé et reproductible (ML-02 §3.3, tâche E3).

Répond au constat de l'investigation d'origine : le pkl V4 a été généré par
un script **hors dépôt**, impossible à régénérer à l'identique. Ce module
est LE script versionné qui produit des artefacts pour le registre ML, avec
fenêtre / hyperparamètres / seed explicites, et provenance capturée par le
registre (git commit, hash de recette, dates d'entraînement).

Trois usages, du plus exploratoire au plus définitif :

1. **Dry-run** (``publish=False``, défaut) : entraîne un candidat, le score
   sur un holdout face au sortant déjà publié, affiche ce qui SE PASSERAIT
   — n'écrit rien. Pour tester une recette avant de s'engager.
2. **Publication gatée** (``publish=True``) : passe par
   ``app.ml.policy.maybe_refresh`` — même gate que le live et le backtest
   ``simulated_live``, le candidat n'est promu que s'il ne régresse pas.
3. **Window sweep** (``window_sweep``) : compare plusieurs tailles de
   fenêtre d'entraînement sur un HOLDOUT COMMUN (comparaison légitime,
   cf. conception §5.3), ne publie (optionnellement) que le meilleur —
   lui-même gaté contre le sortant courant, jamais publié aveuglément.

Source des données : cache Parquet local (``app.core.candle_store``, ZÉRO
appel exchange) — reproductible par construction (mêmes fichiers →
même résultat). Si l'historique local est insuffisant pour la fenêtre
demandée, ce module ne fetch PAS depuis l'exchange lui-même : faites
tourner le live ou le scanner au préalable pour peupler le cache.

CLI : ``scripts/train_model.py`` (wrapper argparse autour de ce module).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

import polars as pl

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Données
# ─────────────────────────────────────────────────────────────────────────────
def load_offline_ohlcv(symbol: str, tf: str, *, as_of: Optional[str] = None,
                       window_bars: Optional[int] = None) -> Optional[pl.DataFrame]:
    """Charge l'historique (symbol, tf) depuis le cache Parquet local — aucun
    appel exchange, reproductible.

    ``as_of`` : ne garde que les barres jusqu'à cette date incluse (ISO,
    ``YYYY-MM-DDTHH:MM:SS``). ``window_bars`` : ne garde que les dernières
    ``window_bars`` barres APRÈS filtrage ``as_of``. Retourne ``None`` si
    rien en cache ou fenêtre vide.
    """
    from app.core.candle_store import get_store
    df = get_store().load_cached(symbol, tf)
    if df is None or len(df) == 0:
        logger.warning(f"[train_runner] aucun historique en cache pour {symbol}/{tf}")
        return None

    if as_of:
        import datetime as _dt
        try:
            cutoff = _dt.datetime.fromisoformat(as_of[:19])
        except ValueError:
            logger.warning(f"[train_runner] as_of={as_of!r} illisible (attendu ISO) — ignoré")
        else:
            if "time" in df.columns:
                df = df.filter(pl.col("time") <= cutoff)

    if window_bars:
        df = df.tail(int(window_bars))

    return df if len(df) > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
#  Entraînement simple (dry-run ou publication gatée)
# ─────────────────────────────────────────────────────────────────────────────
def train_and_publish(strategy_name: str, symbol: str, tf: str, *,
                      as_of: Optional[str] = None,
                      window_bars: Optional[int] = None,
                      params: Optional[Dict[str, Any]] = None,
                      gate_cfg: Optional[Any] = None,
                      publish: bool = False,
                      base_dir: str = "models",
                      source: str = "runner") -> Dict[str, Any]:
    """Entraîne ``strategy_name`` sur (symbol, tf) et publie ou non le résultat.

    ``publish=False`` (défaut) : dry-run — entraîne, score sur un holdout
    contre le sortant publié, ne modifie RIEN sur disque. ``publish=True`` :
    passe par ``app.ml.policy.maybe_refresh`` (gate + publication réelle,
    même chemin que le live/backtest simulated_live).
    """
    df = load_offline_ohlcv(symbol, tf, as_of=as_of, window_bars=window_bars)
    if df is None:
        return {"decision": "failed", "reason": f"aucune donnée en cache pour {symbol}/{tf}"}

    p = dict(params or {})
    if not publish:
        return _dry_run(strategy_name, symbol, tf, df, p, gate_cfg, base_dir)

    import importlib

    import app.ml.policy as policy
    mod = importlib.import_module(f"app.strategies.{strategy_name}")
    strat = mod.Strategy()
    gc_ = gate_cfg or policy.GateConfig.from_params(
        {**policy.recipe_gate_defaults(strategy_name), **p})
    return policy.maybe_refresh(strat, symbol, tf, df, params=p, recipe=strategy_name,
                                gate_cfg=gc_, source=source, base_dir=base_dir)


def _train_candidate_and_score(strategy_name: str, tf: str, train_df: pl.DataFrame,
                               holdout_df: pl.DataFrame, params: Dict[str, Any],
                               gate_cfg) -> tuple:
    """Entraîne un candidat sur ``train_df``, le score sur ``holdout_df``.

    Retourne ``(strat_ou_None, metrics_dict)`` — ``strat`` est ``None`` si
    l'entraînement a échoué (``metrics_dict`` contient alors ``"skipped"``).
    L'appelant décide quoi faire de l'instance entraînée (publier ou jeter).
    """
    import importlib

    import app.ml.policy as policy
    mod = importlib.import_module(f"app.strategies.{strategy_name}")
    strat = mod.Strategy()
    try:
        strat.fit(train_df, params={strategy_name: params})
    except Exception as e:
        return None, {"skipped": f"fit KO : {e}"}
    if not getattr(strat, "is_trained", False):
        return None, {"skipped": "fit() n'a produit aucun modèle exploitable"}

    tmp_dir = tempfile.mkdtemp(prefix="ml_train_runner_")
    try:
        tmp_prefix = os.path.join(tmp_dir, f"{strategy_name}_{tf}")
        strat.save_model(tmp_prefix)
        metrics = policy.score_holdout(tmp_prefix, holdout_df, strategy=strat,
                                       gate_cfg=gate_cfg, params=params)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return strat, metrics


def _dry_run(strategy_name: str, symbol: str, tf: str, df: pl.DataFrame,
            params: Dict[str, Any], gate_cfg, base_dir: str) -> Dict[str, Any]:
    import app.ml.model_registry as registry
    import app.ml.policy as policy

    gc_ = gate_cfg or policy.GateConfig.from_params(
        {**policy.recipe_gate_defaults(strategy_name), **params})
    n = len(df)
    if n < gc_.holdout_bars + gc_.min_window_bars:
        return {"decision": "skipped", "reason": "insufficient_data", "n_bars": n,
                "required_bars": gc_.holdout_bars + gc_.min_window_bars}

    holdout_df = df.tail(gc_.holdout_bars + 210)
    train_df   = df.slice(0, n - gc_.holdout_bars)

    strat, candidate_metrics = _train_candidate_and_score(
        strategy_name, tf, train_df, holdout_df, params, gc_)
    if strat is None:
        return {"decision": "failed", "reason": candidate_metrics.get("skipped"), "n_bars": n}

    incumbent = registry.latest_promoted(symbol, tf, strategy_name, base_dir=base_dir)
    incumbent_metrics = None
    if incumbent is not None:
        incumbent_metrics = policy.score_holdout(incumbent.path_prefix, holdout_df,
                                                 strategy=strategy_name,
                                                 gate_cfg=gc_, params=params)
    gate = policy.decide_gate(candidate_metrics, incumbent_metrics,
                              auc_floor=gc_.auc_floor, epsilon=gc_.epsilon, metric=gc_.metric)
    return {
        "decision": f"dry_run_would_{gate.decision}", "reason": gate.reason,
        "candidate": candidate_metrics, "incumbent": incumbent_metrics,
        "incumbent_version": incumbent.version_id if incumbent else None,
        "n_bars": n, "n_train": len(train_df),
        "note": "dry-run : rien n'a été écrit au registre — relancez avec "
                "publish=True (--publish en CLI) pour publier réellement.",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Window sweep — comparaison sur holdout commun
# ─────────────────────────────────────────────────────────────────────────────
def window_sweep(strategy_name: str, symbol: str, tf: str, windows: List[int], *,
                 as_of: Optional[str] = None,
                 params: Optional[Dict[str, Any]] = None,
                 gate_cfg: Optional[Any] = None,
                 publish_best: bool = False,
                 base_dir: str = "models",
                 source: str = "runner_sweep") -> Dict[str, Any]:
    """Compare plusieurs tailles de fenêtre d'entraînement sur un holdout
    COMMUN (même recette, même hold-out — comparaison légitime). N'entraîne
    qu'un candidat par taille ; ``publish_best=True`` publie uniquement le
    meilleur, lui-même gaté contre le sortant courant (jamais aveuglément —
    un window sweep n'est pas un raccourci pour contourner le gate)."""
    import app.ml.model_registry as registry
    import app.ml.policy as policy

    df = load_offline_ohlcv(symbol, tf, as_of=as_of)
    if df is None:
        return {"error": f"aucune donnée en cache pour {symbol}/{tf}"}

    p = dict(params or {})
    gc_ = gate_cfg or policy.GateConfig.from_params(
        {**policy.recipe_gate_defaults(strategy_name), **p})
    n = len(df)
    smallest = min(windows) if windows else 0
    if n < gc_.holdout_bars + smallest:
        return {"error": f"historique insuffisant ({n} barres) pour la plus petite "
                         f"fenêtre ({smallest}) + holdout ({gc_.holdout_bars})"}

    holdout_df  = df.tail(gc_.holdout_bars + 210)
    pre_holdout = df.slice(0, n - gc_.holdout_bars)

    trained: Dict[int, Any] = {}
    candidates: List[Dict[str, Any]] = []
    for w in sorted(set(int(x) for x in windows)):
        train_df = pre_holdout.tail(w)
        if len(train_df) < gc_.min_window_bars:
            candidates.append({"window_bars": w, "n_train": len(train_df),
                               "skipped": "historique insuffisant pour ce window"})
            continue
        strat, metrics = _train_candidate_and_score(
            strategy_name, tf, train_df, holdout_df, p, gc_)
        if strat is None:
            candidates.append({"window_bars": w, "n_train": len(train_df), **metrics})
            continue
        trained[w] = strat
        candidates.append({"window_bars": w, "n_train": len(train_df), **metrics})

    scored = [c for c in candidates if c.get(gc_.metric) is not None]
    result: Dict[str, Any] = {"candidates": candidates, "metric": gc_.metric}
    if not scored:
        result["best_window_bars"] = None
        return result

    best = max(scored, key=lambda c: c[gc_.metric])
    best_w = best["window_bars"]
    result["best_window_bars"] = best_w
    result["best_metrics"] = {k: v for k, v in best.items() if k not in ("window_bars", "n_train")}

    if not publish_best:
        result["note"] = "comparaison seule (publish_best=False) — rien n'a été écrit au registre."
        return result

    best_strat = trained[best_w]
    incumbent = registry.latest_promoted(symbol, tf, strategy_name, base_dir=base_dir)
    incumbent_metrics = None
    if incumbent is not None:
        incumbent_metrics = policy.score_holdout(incumbent.path_prefix, holdout_df,
                                                 strategy=strategy_name,
                                                 gate_cfg=gc_, params=p)
    candidate_metrics = {k: v for k, v in best.items() if k not in ("window_bars", "n_train")}
    gate = policy.decide_gate(candidate_metrics, incumbent_metrics,
                              auc_floor=gc_.auc_floor, epsilon=gc_.epsilon, metric=gc_.metric)

    tmp_dir = tempfile.mkdtemp(prefix="ml_sweep_publish_")
    try:
        tmp_prefix = os.path.join(tmp_dir, f"{strategy_name}_{tf}")
        best_strat.save_model(tmp_prefix)
        bounds = registry.train_window_bounds(pre_holdout.tail(best_w))
        published = registry.publish(
            symbol, tf, strategy_name, tmp_prefix,
            train_start=bounds["train_start"], train_end=bounds["train_end"],
            n_bars=bounds["n_bars"], recipe_cfg={**p, "window_bars": best_w},
            source=source, decision=gate.decision,
            decision_metrics={"candidate": candidate_metrics, "incumbent": incumbent_metrics,
                              "reason": gate.reason,
                              "incumbent_version": incumbent.version_id if incumbent else None},
            base_dir=base_dir,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    result["gate_decision"] = gate.decision
    result["gate_reason"] = gate.reason
    result["published_version"] = published.version_id if published else None
    return result
