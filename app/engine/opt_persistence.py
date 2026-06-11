"""Persistance des résultats d'optimisation (YAML stratégies, changelog, slots actifs).

Extrait de ``optimizer.py`` (découpage V13 : recherche / scoring / persistance) :
  - lecture/écriture de ``strategies/{nom}.yaml`` (optimizer_results par TF) ;
  - changelog d'audit ``optimizer_changelog.json`` (rotation 200 entrées) ;
  - résolution des stratégies actives par TF pour le LiveTrader.
"""
import logging
import os
import threading
from copy import deepcopy
from datetime import datetime
from typing import Dict, List

import yaml

logger = logging.getLogger(__name__)

# Lock global pour protéger les écritures concurrentes dans les fichiers YAML
_config_write_lock = threading.Lock()
_changelog_lock    = threading.Lock()


# ── Fichiers stratégies ──────────────────────────────────────────────────────

def _resolve_config_path(config_path: str) -> str:
    """Résout le chemin du fichier config principal en chemin absolu."""
    if os.path.isabs(config_path):
        return config_path
    cwd_path = os.path.abspath(config_path)
    if os.path.exists(cwd_path):
        return cwd_path
    return cwd_path


def _strategy_file_path(strategy_name: str, config_path: str = "config.yaml") -> str:
    """Retourne le chemin absolu de strategies/{strategy_name}.yaml."""
    config_dir = os.path.dirname(os.path.abspath(_resolve_config_path(config_path)))
    return os.path.join(config_dir, "strategies", f"{strategy_name}.yaml")


def _load_strategy_file(strat_path: str) -> dict:
    """Charge un fichier stratégie YAML ; retourne {} si absent ou vide."""
    if not os.path.exists(strat_path):
        return {}
    try:
        with open(strat_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[Optimizer] Lecture {strat_path} KO : {e}")
        return {}


def _write_strategy_file(strat_path: str, data: dict) -> None:
    """Écrit un fichier stratégie YAML avec un commentaire d'en-tête."""
    os.makedirs(os.path.dirname(strat_path), exist_ok=True)
    with open(strat_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ── Sauvegarde / application des résultats ──────────────────────────────────

def save_optimizer_results(strategy_name: str, timeframe: str,
                           params: dict, oos_score: float,
                           config_path: str = "config.yaml") -> bool:
    """
    Persiste le résultat d'optimisation dans strategies/{strategy_name}.yaml.
    Préserve les autres timeframes existants. Thread-safe via _config_write_lock.
    """
    strat_path = _strategy_file_path(strategy_name, config_path)
    try:
        with _config_write_lock:
            data = _load_strategy_file(strat_path)
            data.setdefault("optimizer_results", {})[timeframe] = {
                "run_date":  datetime.utcnow().strftime("%Y-%m-%d"),
                "oos_score": round(float(oos_score), 6),
                "params":    deepcopy(params),
            }
            _write_strategy_file(strat_path, data)

        try:
            _append_changelog(
                _resolve_config_path(config_path),
                strategy_name, timeframe, params, oos_score, {}
            )
        except Exception as _cle:
            logger.debug(f"[Optimizer] changelog KO (non bloquant) : {_cle}")

        logger.info(
            f"[Optimizer] Résultat sauvegardé → strategies/{strategy_name}.yaml "
            f"[{timeframe}] score OOS={oos_score:.4f}"
        )
        return True
    except Exception as e:
        logger.error(f"[Optimizer] save_optimizer_results KO : {e}")
        return False


def record_optimizer_audit(strategy_name: str, timeframe: str,
                           params: dict, oos_score: float,
                           config_path: str = "config.yaml") -> bool:
    """
    Trace un résultat d'optimisation **non appliqué** dans le changelog (audit),
    SANS l'écrire dans ``optimizer_results`` — qui est le store actif lu par
    ``resolve_strategy_params``. Sémantique « non appliqué = non utilisé » :
    le backtest/comparatif/live continuent d'utiliser le paramétrage en place
    tant que l'utilisateur (ou le garde-fou en auto-apply) ne l'a pas appliqué.
    """
    try:
        _append_changelog(
            _resolve_config_path(config_path),
            strategy_name, timeframe, params, oos_score, {}
        )
        logger.info(
            f"[Optimizer] Résultat NON appliqué tracé pour audit "
            f"{strategy_name}[{timeframe}] score OOS={oos_score:.4f} "
            "(optimizer_results inchangé)"
        )
        return True
    except Exception as e:
        logger.error(f"[Optimizer] record_optimizer_audit KO : {e}")
        return False


def apply_best_params(strategy_name: str, params: dict,
                      config_path: str = "config.yaml",
                      timeframe: str = None,
                      oos_score: float = 0.0) -> bool:
    """
    Applique le paramétrage optimisé **uniquement** dans ``optimizer_results``
    de strategies/{strategy_name}.yaml, sans jamais toucher au bloc ``params``
    (= configuration par défaut réglée à la main, qui doit rester intacte).
    Le store ``optimizer_results[tf]`` a précédence dans ``resolve_strategy_params``,
    donc écrire ici suffit à activer le paramétrage. Préserve les autres
    timeframes/paramètres. Thread-safe via _config_write_lock.

    Un timeframe est requis : sans lui, il n'existe aucun emplacement
    ``optimizer_results[tf]`` où activer le paramétrage.
    """
    if not timeframe:
        logger.warning(
            f"[Optimizer] apply_best_params({strategy_name}) sans timeframe : "
            "rien à appliquer (optimizer_results est indexé par TF)."
        )
        return False

    strat_path = _strategy_file_path(strategy_name, config_path)
    old_params_snapshot = {}
    try:
        with _config_write_lock:
            data = _load_strategy_file(strat_path)

            # Snapshot pour le changelog : ancien paramétrage actif de ce TF
            # (l'entrée optimizer_results précédente, pas le bloc params: par défaut).
            old_params_snapshot = deepcopy(
                data.get("optimizer_results", {}).get(timeframe, {}).get("params", {})
            )

            # Écriture exclusivement dans optimizer_results[tf] — params: intact.
            data.setdefault("optimizer_results", {})[timeframe] = {
                "run_date":  datetime.utcnow().strftime("%Y-%m-%d"),
                "oos_score": round(float(oos_score), 6),
                "params":    deepcopy(params),
            }

            _write_strategy_file(strat_path, data)

        try:
            _append_changelog(
                _resolve_config_path(config_path),
                strategy_name, timeframe, params, oos_score, old_params_snapshot
            )
        except Exception as _cle:
            logger.debug(f"[Optimizer] changelog KO (non bloquant) : {_cle}")

        logger.info(
            f"[Optimizer] Params appliqués → strategies/{strategy_name}.yaml "
            f"[optimizer_results.{timeframe}] (params: par défaut inchangés)"
        )
        return True
    except Exception as e:
        logger.error(f"[Optimizer] apply_best_params KO : {e}")
        return False


def _append_changelog(config_path: str, strategy: str, timeframe: str,
                      new_params: dict, oos_score: float, old_params: dict) -> None:
    """Ajoute une entrée dans optimizer_changelog.json (max 200 entrées). Thread-safe."""
    import json
    # Utiliser le répertoire du fichier config (résolu en absolu)
    abs_config = config_path if os.path.isabs(config_path) else os.path.abspath(config_path)
    changelog_path = os.path.join(os.path.dirname(abs_config), "optimizer_changelog.json")
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source":    "optimizer",
        "strategy":  strategy,
        "timeframe": timeframe or "",
        "oos_score": round(float(oos_score), 4),
        "params":    deepcopy(new_params),
        "changed":   {},
    }
    for k, v in new_params.items():
        old_v = old_params.get(k) if old_params else None
        if old_v != v:
            entry["changed"][k] = {"before": old_v, "after": v}
    with _changelog_lock:
        try:
            with open(changelog_path, "r", encoding="utf-8") as f:
                log = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            log = []
        log.append(entry)
        log = log[-200:]  # garder les 200 derniers
        with open(changelog_path, "w", encoding="utf-8") as f:
            # Compact (pas d'indent) : divise la taille du fichier par ~1.6 ;
            # le changelog est consommé par l'API/UI, pas lu à la main.
            json.dump(log, f, ensure_ascii=False, separators=(",", ":"))


# ── Stratégies actives par TF (consommé par le LiveTrader) ──────────────────

def get_active_strategies_per_tf(cfg: dict) -> Dict[str, List[dict]]:
    """
    Retourne les stratégies actives par TF depuis optimizer_results.
    Format : { "1h": [{"name": "trend", "params": {...}, "score": 0.82}, ...], ... }

    Fallback : si aucun résultat pour un TF, utilise strategies.enabled avec strategy_params.
    """
    timeframes   = cfg["trading"].get("timeframes", [cfg["trading"].get("timeframe", "1h")])
    top_n        = cfg["trading"].get("top_strategies_per_tf", 2)
    opt_results  = cfg.get("optimizer_results") or {}
    strat_params = cfg.get("strategy_params", {})
    result       = {}

    for tf in timeframes:
        candidates = []
        for strat_name, tf_map in opt_results.items():
            if not isinstance(tf_map, dict):
                continue
            if tf in tf_map:
                entry = tf_map[tf]
                if isinstance(entry, dict):
                    score  = entry.get("oos_score") if entry.get("oos_score") is not None else -999
                    params = entry.get("params", strat_params.get(strat_name, {}))
                    candidates.append({
                        "name":   strat_name,
                        "params": {strat_name: params},
                        "score":  score,
                        "tf":     tf,
                    })

        # Tri par score OOS décroissant, top N
        # MIN_VIABLE_SCORE: exclure les stratégies nettement perdantes en OOS
        # (-0.05 = légèrement négatif acceptable, -0.15 = clairement mauvais)
        MIN_VIABLE_SCORE = -0.05
        candidates.sort(key=lambda x: x["score"], reverse=True)
        viable   = [c for c in candidates if c["score"] >= MIN_VIABLE_SCORE]
        rejected = [c for c in candidates if c["score"] < MIN_VIABLE_SCORE and c["score"] > -999]
        active   = viable[:top_n]

        if rejected:
            for r in rejected:
                logger.warning(
                    f"[Optimizer] {r['name']}@{tf} exclu du live : "
                    f"score OOS {r['score']:.4f} < seuil {MIN_VIABLE_SCORE} — "
                    f"relancez une optimisation pour améliorer."
                )

        if active:
            result[tf] = active
        elif not candidates:
            # Fallback uniquement si aucun résultat d'optimisation n'existe pour ce TF
            # (stratégies jamais optimisées) — pas de fallback si tous les scores sont négatifs
            enabled = cfg["strategies"].get("enabled", [])
            result[tf] = [
                {"name": n, "params": {n: strat_params.get(n, {})}, "score": 0.0, "tf": tf}
                for n in enabled
            ]
            logger.info(f"[Optimizer] {tf} : aucun résultat OOS — "
                        f"fallback sur stratégies activées : {enabled}")
        else:
            # Tous les candidats ont un score OOS sous le seuil → aucune stratégie active sur ce TF
            scores_str = ', '.join(f"{c['name']}={c['score']:.3f}" for c in candidates[:5])
            logger.warning(
                f"[Optimizer] {tf} : tous les scores OOS < {MIN_VIABLE_SCORE} "
                f"({scores_str}) — aucune stratégie active sur ce TF. "
                f"Relancez l'optimiseur pour améliorer les scores."
            )
            result[tf] = []

    return result
