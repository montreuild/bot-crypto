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

    if len(df) == 0:
        return None

    # Colonnes ``_pre_*`` partagées : le cache Parquet ne stocke que l'OHLCV
    # brut, alors que les stratégies « bespoke » (scoring_statistique_opus_v4/
    # v5…) les attendent en entrée de ``fit()`` — le chemin LIVE les reçoit de
    # ``scanner.fetch_ohlcv``, pas ce chemin offline. Sans elles, entraîner
    # depuis l'UI échouait avec « _build_features=None (colonnes _pre_*
    # manquantes) » là où le live entraînait la même recette sans broncher.
    # ``precompute_df`` est idempotent et mémoïsé : sans effet sur les recettes
    # qui construisent leurs features depuis le seul OHLCV.
    from app.core.indicators import precompute_df
    try:
        df = precompute_df(df)
    except Exception as e:
        logger.warning(f"[train_runner] precompute_df KO ({e}) — df brut conservé")
    return df


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
        {**policy.resolve_gate_spec(strategy_name), **p})
    return policy.maybe_refresh(strat, symbol, tf, df, params=p,
                                gate_cfg=gc_, source=source, base_dir=base_dir)


def _train_candidate_and_score(strategy_name: str, tf: str, train_df: pl.DataFrame,
                               holdout_df: pl.DataFrame, params: Dict[str, Any],
                               gate_cfg, recipe_name: str) -> tuple:
    """Entraîne un candidat sur ``train_df``, le score sur ``holdout_df``.

    Retourne ``(strat_ou_None, metrics_dict)`` — ``strat`` est ``None`` si
    l'entraînement a échoué (``metrics_dict`` contient alors ``"skipped"``).
    L'appelant décide quoi faire de l'instance entraînée (publier ou jeter).
    """
    import importlib

    import app.ml.model_registry as registry
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
        # Un save_model() no-op donnerait un score_holdout sur un préfixe vide,
        # rapporté comme un holdout dégénéré : dire ce qui s'est réellement
        # passé plutôt que de laisser accuser les données.
        missing = registry.missing_artifacts(recipe_name, tmp_prefix)
        if missing:
            return None, {"skipped": f"save_model n'a écrit aucun artefact "
                                     f"(manquants : {missing})"}
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
        {**policy.resolve_gate_spec(strategy_name), **params})
    # La clé du registre est la RECETTE, pas le nom de stratégie (fracture b).
    recipe_name = policy.resolve_recipe_name(strategy_name, params)
    n = len(df)
    if n < gc_.holdout_bars + gc_.min_window_bars:
        return {"decision": "skipped", "reason": "insufficient_data", "n_bars": n,
                "required_bars": gc_.holdout_bars + gc_.min_window_bars}

    holdout_df = df.tail(gc_.holdout_bars + 210)
    train_df   = df.slice(0, n - gc_.holdout_bars)

    strat, candidate_metrics = _train_candidate_and_score(
        strategy_name, tf, train_df, holdout_df, params, gc_, recipe_name)
    if strat is None:
        return {"decision": "failed", "reason": candidate_metrics.get("skipped"), "n_bars": n}

    incumbent = registry.latest_promoted(tf, recipe_name, base_dir=base_dir)
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
        # Diagnostics du candidat (top features, calibration, AUC par régime).
        # Un dry-run n'écrit RIEN au registre : sans ce champ, les seuls
        # diagnostics de l'expérience mourraient avec l'instance — alors que
        # c'est le mode fait pour expérimenter.
        "train_meta": policy.resolve_train_meta(strat, tf),
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
        {**policy.resolve_gate_spec(strategy_name), **p})
    recipe_name = policy.resolve_recipe_name(strategy_name, p)
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
            strategy_name, tf, train_df, holdout_df, p, gc_, recipe_name)
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
    # Diagnostics de la MEILLEURE fenêtre seulement : comparer des top-features
    # entre fenêtres est l'usage même du sweep, mais n'en renvoyer qu'un garde
    # la réponse bornée — les autres instances restent dans ``trained``.
    if best_w in trained:
        result["train_meta"] = policy.resolve_train_meta(trained[best_w], tf)

    if not publish_best:
        result["note"] = "comparaison seule (publish_best=False) — rien n'a été écrit au registre."
        return result

    best_strat = trained[best_w]
    incumbent = registry.latest_promoted(tf, recipe_name, base_dir=base_dir)
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
            tf, recipe_name, tmp_prefix, train_symbol=symbol,
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


# ─────────────────────────────────────────────────────────────────────────────
#  Entraînement piloté par la RECETTE (étape C — sans classe Strategy)
# ─────────────────────────────────────────────────────────────────────────────
def train_recipe_and_publish(recipe_name: str, symbol: str, tf: str, *,
                             as_of: Optional[str] = None,
                             window_bars: Optional[int] = None,
                             params: Optional[Dict[str, Any]] = None,
                             publish: bool = False,
                             base_dir: str = "models",
                             source: str = "runner_recipe") -> Dict[str, Any]:
    """Même contrat que ``train_and_publish``, mais la clé d'entrée est la
    RECETTE — aucune stratégie n'est importée ni instanciée.

    C'est ce qui permet à la page « Modèles », indexée par recette, de cesser
    de demander une stratégie : le formulaire parlait un vocabulaire que le
    tableau juste au-dessus n'utilisait pas.

    Le gate est identique au chemin stratégie (mêmes seuils, même
    ``decide_gate``, même registre) : seule change la façon dont le candidat
    est produit.
    """
    import app.ml.model_registry as registry
    import app.ml.policy as policy
    from app.ml import recipe_trainer
    from app.ml.recipe import load_recipe

    why = recipe_trainer.supports(recipe_name)
    if why:
        return {"decision": "failed", "reason": why, "recipe": recipe_name, "tf": tf}

    df = load_offline_ohlcv(symbol, tf, as_of=as_of, window_bars=window_bars)
    if df is None:
        return {"decision": "failed", "recipe": recipe_name, "tf": tf,
                "reason": f"aucune donnée en cache pour {symbol}/{tf}"}

    r = load_recipe(recipe_name)
    # ML-11 : mêmes surcharges par TF que ``recipe_trainer`` — le gate doit
    # lire les paramètres RÉELLEMENT utilisés, pas ceux d'avant surcharge.
    p = {**r.train_params(), **(params or {}), **r.hp_for_tf(tf)}
    gc_ = policy.GateConfig.from_params({**r.gate_spec(), **r.gate_params(), **p})

    n = len(df)
    if n < gc_.holdout_bars + gc_.min_window_bars:
        return {"decision": "skipped", "reason": "insufficient_data", "n_bars": n,
                "required_bars": gc_.holdout_bars + gc_.min_window_bars,
                "recipe": recipe_name, "tf": tf}

    holdout_df = df.tail(gc_.holdout_bars + 210)
    train_df = df.slice(0, n - gc_.holdout_bars)
    if gc_.window_bars:
        train_df = train_df.tail(gc_.window_bars)

    trained = recipe_trainer.train(recipe_name, train_df, tf, params=p)
    if trained is None:
        return {"decision": "failed", "recipe": recipe_name, "tf": tf,
                "reason": "l'entraînement n'a produit aucun modèle exploitable"}

    incumbent = registry.latest_promoted(tf, recipe_name, base_dir=base_dir)
    incumbent_metrics = None
    if incumbent is not None:
        incumbent_metrics = policy.score_holdout(incumbent.path_prefix, holdout_df,
                                                 strategy=recipe_name, gate_cfg=gc_,
                                                 params=p)

    tmp_dir = tempfile.mkdtemp(prefix="ml_recipe_")
    try:
        # Suffixe "_{tf}" : contrat partagé avec save_model/load_model, qui
        # déduisent le TF du nom de fichier.
        tmp_prefix = os.path.join(tmp_dir, f"{recipe_name}_{tf}")
        if not trained.save(tmp_prefix):
            return {"decision": "failed", "recipe": recipe_name, "tf": tf,
                    "reason": "l'écriture de l'artefact a échoué"}
        candidate_metrics = policy.score_holdout(tmp_prefix, holdout_df,
                                                 strategy=recipe_name, gate_cfg=gc_,
                                                 params=p)
        gate = policy.decide_gate(candidate_metrics, incumbent_metrics,
                                  auc_floor=gc_.auc_floor, epsilon=gc_.epsilon,
                                  metric=gc_.metric)
        out: Dict[str, Any] = {
            "recipe": recipe_name, "tf": tf, "train_symbol": symbol,
            "reason": gate.reason, "candidate": candidate_metrics,
            "incumbent": incumbent_metrics, "n_bars": n, "n_train": len(train_df),
            "train_meta": trained.train_meta,
            "incumbent_version": incumbent.version_id if incumbent else None,
        }
        if not publish:
            out["decision"] = f"dry_run_would_{gate.decision}"
            out["note"] = ("dry-run : rien n'a été écrit au registre — relancez "
                           "avec publish=True pour publier réellement.")
            return out

        bounds = registry.train_window_bounds(train_df)
        published = registry.publish(
            tf, recipe_name, tmp_prefix, train_symbol=symbol,
            train_start=bounds["train_start"], train_end=bounds["train_end"],
            n_bars=bounds["n_bars"],
            recipe_cfg={**{k: p.get(k) for k in ("n_estimators", "num_leaves",
                                                 "learning_rate", "amp_top_pct")
                           if k in p},
                        "recipe_hash": r.hash()},
            source=source, decision=gate.decision,
            decision_metrics={"candidate": candidate_metrics,
                              "incumbent": incumbent_metrics, "reason": gate.reason},
            base_dir=base_dir,
        )
        out["decision"] = gate.decision
        out["published_version"] = published.version_id if published else None
        return out
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Entraînement POOLÉ multi-symboles (ML-16)
# ─────────────────────────────────────────────────────────────────────────────
def _pooled_holdout_metrics(prefix: str, holdouts: Dict[str, pl.DataFrame],
                            recipe_name: str, gc_, p: Dict[str, Any]) -> Dict[str, Any]:
    """Score un artefact poolé sur le holdout de CHAQUE symbole, puis agrège.

    Pourquoi pas un holdout unique concaténé : ``score_holdout`` reconstruit
    des labels à partir de rendements sur fenêtre glissante. Coller bout à bout
    les holdouts de deux titres fabriquerait, à la jointure, des rendements
    qui n'ont existé sur aucun marché — le piège n°1 de ``train_multi``, qui
    ne concatène jamais d'OHLCV. On score donc séparément.

    L'agrégation est une moyenne NON PONDÉRÉE des métriques numériques. C'est
    un choix, et il est discutable : pondérer par le nombre de barres
    laisserait le titre au plus long historique décider seul de la promotion,
    ce qui est exactement ce que le pooling cherche à éviter. Le détail par
    symbole est conservé dans ``per_symbol`` — un modèle bon en moyenne mais
    mauvais sur la moitié des titres reste lisible.
    """
    import app.ml.policy as policy

    per_symbol: Dict[str, Any] = {}
    for symbol, hdf in holdouts.items():
        try:
            per_symbol[symbol] = policy.score_holdout(
                prefix, hdf, strategy=recipe_name, gate_cfg=gc_, params=p)
        except Exception as e:                                  # pragma: no cover
            logger.warning(f"[train_runner] holdout {symbol} KO : {e}")
            per_symbol[symbol] = {"skipped": str(e)}

    scored = [m for m in per_symbol.values() if isinstance(m, dict) and "skipped" not in m]
    if not scored:
        return {"skipped": "aucun symbole n'a pu être scoré", "per_symbol": per_symbol}

    keys = {k for m in scored for k, v in m.items() if isinstance(v, (int, float))
            and not isinstance(v, bool)}
    agg: Dict[str, Any] = {}
    for k in keys:
        vals = [float(m[k]) for m in scored if isinstance(m.get(k), (int, float))
                and not isinstance(m.get(k), bool)]
        if vals:
            agg[k] = round(sum(vals) / len(vals), 6)
    agg["n_scored_symbols"] = len(scored)
    agg["per_symbol"] = per_symbol
    return agg


def _solo_vs_pooled(recipe_name: str, tf: str, gc_, p: Dict[str, Any],
                    train_frames: Dict[str, pl.DataFrame],
                    holdouts: Dict[str, pl.DataFrame],
                    pooled_per_symbol: Dict[str, Any],
                    limit: int) -> List[Dict[str, Any]]:
    """Pour chaque titre, l'écart entre un modèle POOLÉ et un modèle DÉDIÉ.

    C'est la question que se pose l'exploitant devant un pool : « ce titre
    gagne-t-il à être servi par le modèle commun, ou méritait-il le sien ? ».
    La moyenne agrégée que le gate arbitre ne peut pas y répondre — elle peut
    recouvrir douze titres homogènes comme six bons et six mauvais.

    Coûteux par construction : un entraînement complet par titre. D'où
    ``limit``, et le caractère opt-in côté API. Les titres dont l'historique
    ne permet pas un modèle solo sont rapportés comme tels plutôt qu'omis —
    « le pooling est ta seule option ici » est une réponse, pas un trou.
    """
    from app.ml import recipe_trainer

    out: List[Dict[str, Any]] = []
    for symbol in list(train_frames)[:max(0, int(limit))]:
        pooled_auc = (pooled_per_symbol.get(symbol) or {}).get(gc_.metric)
        row: Dict[str, Any] = {"symbol": symbol, "pooled": pooled_auc,
                               "n_bars": len(train_frames[symbol])}
        if len(train_frames[symbol]) < gc_.min_window_bars:
            row["solo"] = None
            row["note"] = (f"historique insuffisant pour un modèle dédié "
                           f"({len(train_frames[symbol])} < {gc_.min_window_bars})")
            out.append(row)
            continue
        solo = recipe_trainer.train(recipe_name, train_frames[symbol], tf, params=p)
        if solo is None:
            row["solo"] = None
            row["note"] = "entraînement dédié sans résultat exploitable"
            out.append(row)
            continue
        tmp = tempfile.mkdtemp(prefix="ml_solo_")
        try:
            prefix = os.path.join(tmp, f"{recipe_name}_{tf}")
            if not solo.save(prefix):
                row["solo"] = None
                row["note"] = "écriture de l'artefact dédié impossible"
                out.append(row)
                continue
            import app.ml.policy as policy
            m = policy.score_holdout(prefix, holdouts[symbol], strategy=recipe_name,
                                     gate_cfg=gc_, params=p)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        row["solo"] = m.get(gc_.metric)
        if row["solo"] is not None and pooled_auc is not None:
            row["delta"] = round(float(pooled_auc) - float(row["solo"]), 4)
        out.append(row)
    out.sort(key=lambda r: (r.get("delta") is None, -(r.get("delta") or 0)))
    return out


def train_multi_and_publish(recipe_name: str, symbols: List[str], tf: str, *,
                            as_of: Optional[str] = None,
                            window_bars: Optional[int] = None,
                            params: Optional[Dict[str, Any]] = None,
                            publish: bool = False,
                            base_dir: str = "models",
                            compare_solo: int = 0,
                            source: str = "runner_pooled") -> Dict[str, Any]:
    """Entraîne UNE recette sur PLUSIEURS symboles mis en commun, puis gate.

    ML-16 — ``recipe_trainer.train_multi`` existait, testé, et n'était appelé
    par personne : ni l'API, ni la page « Modèles ». C'est le chaînon qui
    manquait pour que G2 puisse produire son premier modèle actions, où le
    pooling n'est pas un raffinement mais la condition d'existence du modèle
    (~13,7 ans d'historique nécessaires pour un titre Euronext seul).

    Le holdout est prélevé PAR SYMBOLE avant l'entraînement, jamais après :
    laisser ``train_multi`` voir les dernières barres puis les réutiliser pour
    juger le modèle produirait le score de complaisance habituel.
    """
    import app.ml.model_registry as registry
    import app.ml.policy as policy_mod
    from app.ml import recipe_trainer
    from app.ml.recipe import load_recipe

    why = recipe_trainer.supports(recipe_name)
    if why:
        return {"decision": "failed", "reason": why, "recipe": recipe_name, "tf": tf}

    wanted = [s for s in dict.fromkeys(symbols or []) if s]
    if not wanted:
        return {"decision": "failed", "recipe": recipe_name, "tf": tf,
                "reason": "aucun symbole fourni"}

    r = load_recipe(recipe_name)
    p = {**r.train_params(), **(params or {}), **r.hp_for_tf(tf)}
    gc_ = policy_mod.GateConfig.from_params({**r.gate_spec(), **r.gate_params(), **p})

    train_frames: Dict[str, pl.DataFrame] = {}
    holdouts: Dict[str, pl.DataFrame] = {}
    skipped: Dict[str, str] = {}
    for symbol in wanted:
        df = load_offline_ohlcv(symbol, tf, as_of=as_of, window_bars=window_bars)
        if df is None:
            skipped[symbol] = "aucune donnée en cache"
            continue
        n = len(df)
        # Un symbole trop court est ÉCARTÉ, pas fatal : c'est tout l'intérêt
        # du pooling de tolérer des historiques inégaux. Le refuser en bloc
        # rendrait l'univers actions inutilisable, puisque les introductions
        # récentes y côtoient des titres cotés depuis vingt ans.
        if n < gc_.holdout_bars + 250:
            skipped[symbol] = (f"historique trop court ({n} barres < "
                               f"{gc_.holdout_bars + 250})")
            continue
        holdouts[symbol] = df.tail(gc_.holdout_bars + 210)
        train_frames[symbol] = df.slice(0, n - gc_.holdout_bars)

    if not train_frames:
        return {"decision": "failed", "recipe": recipe_name, "tf": tf,
                "reason": "aucun symbole exploitable", "skipped_symbols": skipped}

    trained = recipe_trainer.train_multi(recipe_name, train_frames, tf, params=p)
    if trained is None:
        return {"decision": "failed", "recipe": recipe_name, "tf": tf,
                "reason": "l'entraînement poolé n'a produit aucun modèle exploitable",
                "skipped_symbols": skipped}

    incumbent = registry.latest_promoted(tf, recipe_name, base_dir=base_dir)

    tmp_dir = tempfile.mkdtemp(prefix="ml_recipe_pooled_")
    try:
        tmp_prefix = os.path.join(tmp_dir, f"{recipe_name}_{tf}")
        if not trained.save(tmp_prefix):
            return {"decision": "failed", "recipe": recipe_name, "tf": tf,
                    "reason": "l'écriture de l'artefact a échoué"}

        candidate_metrics = _pooled_holdout_metrics(tmp_prefix, holdouts,
                                                    recipe_name, gc_, p)
        incumbent_metrics = None
        if incumbent is not None:
            # Le sortant est scoré sur LE MÊME protocole — mêmes holdouts,
            # même agrégation. Comparer une moyenne multi-symboles à un score
            # mono-symbole trancherait sur une différence de mesure.
            incumbent_metrics = _pooled_holdout_metrics(
                incumbent.path_prefix, holdouts, recipe_name, gc_, p)

        gate = policy_mod.decide_gate(candidate_metrics, incumbent_metrics,
                                      auc_floor=gc_.auc_floor, epsilon=gc_.epsilon,
                                      metric=gc_.metric)
        pooled = trained.train_meta.get("pooled_symbols", sorted(train_frames))
        out: Dict[str, Any] = {
            "recipe": recipe_name, "tf": tf, "pooled_symbols": pooled,
            "n_symbols": len(pooled), "skipped_symbols": skipped,
            "reason": gate.reason, "candidate": candidate_metrics,
            "incumbent": incumbent_metrics, "train_meta": trained.train_meta,
            "incumbent_version": incumbent.version_id if incumbent else None,
        }
        if compare_solo:
            out["solo_vs_pooled"] = _solo_vs_pooled(
                recipe_name, tf, gc_, p, train_frames, holdouts,
                (candidate_metrics or {}).get("per_symbol") or {}, compare_solo)
            out["metric"] = gc_.metric
        if not publish:
            out["decision"] = f"dry_run_would_{gate.decision}"
            out["note"] = ("dry-run : rien n'a été écrit au registre — relancez "
                           "avec publish=True pour publier réellement.")
            return out

        # Bornes temporelles de l'UNION : le premier début et la dernière fin
        # de toutes les fenêtres poolées. Prendre celles d'un symbole
        # arbitraire mentirait sur la couverture réelle du modèle.
        bounds_all = [registry.train_window_bounds(f) for f in train_frames.values()]
        starts = [b["train_start"] for b in bounds_all if b["train_start"]]
        ends = [b["train_end"] for b in bounds_all if b["train_end"]]
        published = registry.publish(
            tf, recipe_name, tmp_prefix,
            # ``train_symbol`` est un champ d'affichage : la liste complète vit
            # dans ``recipe_cfg`` et dans ``train_meta``, tous deux archivés.
            train_symbol=f"pooled:{len(pooled)}",
            train_start=min(starts) if starts else None,
            train_end=max(ends) if ends else None,
            n_bars=sum(b["n_bars"] for b in bounds_all),
            recipe_cfg={**{k: p.get(k) for k in ("n_estimators", "num_leaves",
                                                 "learning_rate", "amp_top_pct")
                           if k in p},
                        "recipe_hash": r.hash(), "pooled_symbols": pooled},
            source=source, decision=gate.decision,
            decision_metrics={"candidate": candidate_metrics,
                              "incumbent": incumbent_metrics, "reason": gate.reason},
            base_dir=base_dir,
        )
        out["decision"] = gate.decision
        out["published_version"] = published.version_id if published else None
        return out
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
