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
from datetime import datetime, timezone
from typing import Any, Dict, List

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
    """Charge un fichier stratégie YAML ; retourne {} si absent ou vide.

    Round-trip (ruamel) : préserve les commentaires existants pour qu'ils
    survivent à la réécriture par :func:`_write_strategy_file`.
    """
    from app.core.yaml_io import load_yaml
    data = load_yaml(strat_path, default={})
    return data if isinstance(data, dict) else {}


def _write_strategy_file(strat_path: str, data: dict) -> None:
    """Écrit un fichier stratégie YAML en préservant les commentaires des clés
    non modifiées (si ``data`` a été chargé via :func:`_load_strategy_file`)."""
    from app.core.yaml_io import dump_yaml
    dump_yaml(strat_path, data)


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
                "run_date":  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
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
                      timeframe: str | None = None,
                      oos_score: float = 0.0,
                      symbol: str | None = None) -> bool:
    """
    Applique le paramétrage optimisé **uniquement** dans ``optimizer_results``
    de strategies/{strategy_name}.yaml, sans jamais toucher au bloc ``params``
    (= configuration par défaut réglée à la main, qui doit rester intacte).
    Le store ``optimizer_results[tf][symbol]`` a précédence dans
    ``resolve_strategy_params``, donc écrire ici suffit à activer le paramétrage.
    Préserve les autres timeframes/symboles. Thread-safe via _config_write_lock.

    ``symbol`` : si fourni, écrit sous ``optimizer_results[tf][symbol]`` (une
    entrée héritée existante est migrée vers ``[tf][DEFAULT_CONFIG_SYMBOL]`` pour
    ne pas être perdue). Sinon, comportement hérité (``optimizer_results[tf]``).

    Un timeframe est requis : sans lui, il n'existe aucun emplacement
    ``optimizer_results[tf]`` où activer le paramétrage.
    """
    if not timeframe:
        logger.warning(
            f"[Optimizer] apply_best_params({strategy_name}) sans timeframe : "
            "rien à appliquer (optimizer_results est indexé par TF)."
        )
        return False

    from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL, _is_legacy_tf_entry
    strat_path = _strategy_file_path(strategy_name, config_path)
    old_params_snapshot = {}
    entry = {
        "run_date":  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "oos_score": round(float(oos_score), 6),
        "params":    deepcopy(params),
    }
    try:
        with _config_write_lock:
            data = _load_strategy_file(strat_path)
            opt = data.setdefault("optimizer_results", {})
            tf_entry = opt.get(timeframe)

            if symbol:
                # Schéma par symbole : migre une éventuelle entrée héritée (= BTC)
                # avant d'écrire, pour que les deux configs coexistent.
                if isinstance(tf_entry, dict) and _is_legacy_tf_entry(tf_entry):
                    tf_entry = {DEFAULT_CONFIG_SYMBOL: tf_entry}
                elif not isinstance(tf_entry, dict):
                    tf_entry = {}
                old_params_snapshot = deepcopy(
                    (tf_entry.get(symbol) or {}).get("params", {}))
                tf_entry[symbol] = entry
                opt[timeframe] = tf_entry
            else:
                old_params_snapshot = deepcopy(
                    (tf_entry or {}).get("params", {})
                    if isinstance(tf_entry, dict) else {})
                opt[timeframe] = entry

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
    entry: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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

def _config_symbols(cfg: dict) -> List[str]:
    """Symboles à activer (scanner.symbols) ; défaut BTC/USDC si absent."""
    from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
    syms = ((cfg.get("scanner") or {}).get("symbols")
            or cfg.get("trading", {}).get("symbols") or [])
    return list(syms) if syms else [DEFAULT_CONFIG_SYMBOL]


def get_active_strategies_per_tf(cfg: dict) -> Dict[str, List[dict]]:
    """
    Retourne les stratégies actives par TF **et par symbole** depuis
    optimizer_results (schéma ``[strat][tf][symbol]``, rétro-compatible : une
    entrée héritée = config BTC/USDC).

    Format : ``{ "1h": [{"name","params","score","tf","symbol"}, ...] }`` — un
    élément par couple (stratégie, symbole) actif sur ce TF.

    Fallback : si AUCUN résultat d'optimisation n'existe (jamais optimisé),
    utilise strategies.enabled avec strategy_params, pour chaque symbole configuré.
    """
    from app.core.param_resolution import _select_symbol_entry
    timeframes   = cfg["trading"].get("timeframes", [cfg["trading"].get("timeframe", "1h")])
    top_n        = cfg["trading"].get("top_strategies_per_tf", 2)
    opt_results  = cfg.get("optimizer_results") or {}
    strat_params = cfg.get("strategy_params", {})
    symbols      = _config_symbols(cfg)
    has_any_opt  = any(isinstance(m, dict) and m for m in opt_results.values())
    result: Dict[str, List[dict]] = {}
    MIN_VIABLE_SCORE = -0.05

    for tf in timeframes:
        active_tf: List[dict] = []
        for symbol in symbols:
            candidates = []
            for strat_name, tf_map in opt_results.items():
                if not isinstance(tf_map, dict) or tf not in tf_map:
                    continue
                tf_entry = tf_map[tf]
                if not isinstance(tf_entry, dict):
                    continue
                entry = _select_symbol_entry(tf_entry, symbol)
                if not isinstance(entry, dict):
                    continue
                score  = entry.get("oos_score") if entry.get("oos_score") is not None else -999
                params = entry.get("params", strat_params.get(strat_name, {}))
                candidates.append({
                    "name": strat_name, "params": {strat_name: params},
                    "score": score, "tf": tf, "symbol": symbol,
                })

            candidates.sort(key=lambda x: x["score"], reverse=True)
            viable   = [c for c in candidates if c["score"] >= MIN_VIABLE_SCORE]
            rejected = [c for c in candidates
                        if c["score"] < MIN_VIABLE_SCORE and c["score"] > -999]
            for r in rejected:
                logger.warning(
                    f"[Optimizer] {r['name']}@{tf}/{symbol} exclu du live : "
                    f"score OOS {r['score']:.4f} < seuil {MIN_VIABLE_SCORE}.")

            if viable[:top_n]:
                active_tf.extend(viable[:top_n])
            elif not has_any_opt:
                # Jamais optimisé : fallback stratégies activées, params de base.
                enabled = cfg["strategies"].get("enabled", [])
                active_tf.extend(
                    {"name": n, "params": {n: strat_params.get(n, {})},
                     "score": 0.0, "tf": tf, "symbol": symbol}
                    for n in enabled)

        result[tf] = active_tf
        if not active_tf and has_any_opt:
            logger.warning(
                f"[Optimizer] {tf} : aucune stratégie active (scores OOS sous "
                f"seuil pour tous les symboles).")

    return result
