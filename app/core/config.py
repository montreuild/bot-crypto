"""
Chargement et validation de la configuration.
Les valeurs ${VAR} dans le YAML sont substituées par les variables d'environnement.

Structure des fichiers :
  config.yaml         — configuration système (exchange, trading, backtest…)
  strategies/*.yaml   — paramètres et résultats d'optimisation par stratégie
"""
import logging
import os
import re
from pathlib import Path
from typing import Any, Tuple

import yaml

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = [
    ("exchange", "name"),
    ("trading", "capital"),
    ("trading", "risk_per_trade"),
    ("database", "url"),
    # ("trading", "timeframe")  -- optionnel en mode multi-TF
    # ("strategies", "enabled") -- optionnel en mode multi-TF
]

DEFAULTS = {
    "trading": {
        "paper_mode": True, "max_positions": 5, "max_longs": 3, "max_shorts": 3,
        "scan_interval": 60, "score_threshold": 0.55, "daily_drawdown_limit": 0.05,
        "max_trades_per_minute": 3, "min_volume_usdc_24h": 5_000_000,
        "taker_fee": 0.001, "maker_fee": 0.0004, "borrow_rate_daily": 0.0002,
        "max_leverage": 1, "max_drawdown_global": 0.20, "spread_pct": 0.0005,
        "latency_ms": 50, "paper_slippage": 0.001,
    },
    "backtest": {
        "spread_pct": 0.0005, "latency_ms": 50, "partial_fill_pct": 0.95,
        "monte_carlo_runs": 200, "walk_forward_folds": 5,
    },
    "ml":        {"enabled": False, "model": "random_forest", "blend_weight": 0.3,
                  "min_samples": 200, "feature_window": 50},
    "optimizer": {"enabled": False, "method": "bayesian", "n_trials": 50, "out_of_sample_ratio": 0.3},
    "logging":   {"level": "INFO", "debug": False, "max_bytes": 10_485_760, "backup_count": 5,
                  "log_file": "logs/bot.log"},
    "web":       {"host": "127.0.0.1", "port": 8000, "refresh_interval": 5, "api_key": ""},
    "scanner":   {"symbols": ["BTC/USDC","ETH/USDC","SOL/USDC"], "dynamic_scan": False, "top_n": 20},
}

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}|\$([A-Z_][A-Z0-9_]*)")


def _expand_env(value: Any) -> Any:
    """Substitue récursivement les variables d'environnement dans les chaînes."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var = m.group(1) or m.group(2)
            env_val = os.environ.get(var, "")
            if env_val:
                logger.debug(f"[Config] Variable d'env résolue : ${var}")
            return env_val
        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _load_strategy_configs(strategies_dir: str) -> Tuple[dict, dict]:
    """
    Charge tous les *.yaml dans strategies_dir et retourne :
      (strategy_params, optimizer_results)
    Chaque fichier doit avoir la structure :
      params: {...}
      optimizer_results: {tf: {run_date, oos_score, params}}
    """
    strategy_params:    dict = {}
    optimizer_results:  dict = {}
    sdir = Path(strategies_dir)
    if not sdir.is_dir():
        return strategy_params, optimizer_results

    for f in sorted(sdir.glob("*.yaml")):
        name = f.stem
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = yaml.safe_load(fp) or {}
            if not isinstance(data, dict):
                logger.warning(f"[Config] {f} invalide — ignoré")
                continue
            if "params" in data and isinstance(data["params"], dict):
                strategy_params[name] = data["params"]
            if "optimizer_results" in data and isinstance(data["optimizer_results"], dict):
                optimizer_results[name] = data["optimizer_results"]
        except Exception as e:
            logger.warning(f"[Config] Erreur lecture {f} : {e}")

    logger.debug(
        f"[Config] strategies/ : {len(strategy_params)} stratégies chargées "
        f"({', '.join(sorted(strategy_params))})"
    )
    return strategy_params, optimizer_results


def strategy_file_path(strategy_name: str, config_path: str = "config.yaml") -> str:
    """Retourne le chemin du fichier YAML d'une stratégie (strategies/{name}.yaml)."""
    config_dir = os.path.dirname(os.path.abspath(config_path))
    return os.path.join(config_dir, "strategies", f"{strategy_name}.yaml")


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Fichier de configuration introuvable : {path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if not isinstance(cfg, dict):
        raise ValueError("Le fichier config.yaml est vide ou invalide.")

    cfg = _expand_env(cfg)

    # ── Chargement des configs de stratégies (strategies/*.yaml) ─────────────
    strategies_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "strategies")
    strat_params, opt_results = _load_strategy_configs(strategies_dir)

    # Merge strategy_params : le fichier stratégie est prioritaire sur config.yaml
    merged_params = dict(cfg.get("strategy_params", {}))
    for name, params in strat_params.items():
        if name in merged_params:
            # Fusion : les valeurs du fichier stratégie écrasent config.yaml
            merged_params[name] = {**merged_params[name], **params}
        else:
            merged_params[name] = dict(params)
    cfg["strategy_params"] = merged_params

    # Merge optimizer_results : idem, fichier stratégie prioritaire
    merged_opt = dict(cfg.get("optimizer_results", {}))
    for name, results in opt_results.items():
        if name in merged_opt:
            merged_opt[name] = {**merged_opt[name], **results}
        else:
            merged_opt[name] = dict(results)
    cfg["optimizer_results"] = merged_opt

    for section, defaults in DEFAULTS.items():
        if section not in cfg:
            cfg[section] = {}
        for k, v in defaults.items():
            cfg[section].setdefault(k, v)

    errors = []
    for section, field in REQUIRED_FIELDS:
        val = cfg.get(section, {}).get(field)
        if val is None:
            errors.append(f"  [{section}].{field} manquant")
    if errors:
        raise ValueError("Configuration invalide :\n" + "\n".join(errors))

    api_key = cfg.get("exchange", {}).get("api_key", "")
    if api_key in ("", "YOUR_KEY"):
        logger.warning("⚠ Clés API exchange non configurées — mode backtest uniquement.")
    if not cfg["trading"].get("paper_mode"):
        logger.warning("🔴 LIVE TRADING ACTIVÉ — vérifiez bien vos paramètres !")

    # Compatibilité multi-TF
    if "timeframes" in cfg and cfg["timeframes"]:
        all_strats = []
        for tf_cfg in cfg["timeframes"].values():
            all_strats.extend(tf_cfg.get("strategies", []))
        if "strategies" not in cfg:
            cfg["strategies"] = {}
        cfg["strategies"].setdefault("enabled", list(dict.fromkeys(all_strats)))
        cfg["trading"].setdefault("timeframe", next(iter(cfg["timeframes"])))
    else:
        tf = cfg["trading"].get("timeframe", "1h")
        strats = cfg.get("strategies", {}).get("enabled", [])
        cfg.setdefault("timeframes", {tf: {
            "strategies": strats,
            "limit": 1500,
            "scan_interval": cfg["trading"].get("scan_interval", 60),
        }})

    tfs = list(cfg.get("timeframes", {}).keys())
    logger.info(f"Config chargée : {path} | Capital={cfg['trading']['capital']} "
                f"| TF={tfs} | Paper={cfg['trading']['paper_mode']}")
    return cfg
