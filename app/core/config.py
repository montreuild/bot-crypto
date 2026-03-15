"""
Chargement et validation stricte de la configuration au démarrage.
Lève une ValueError claire si des champs obligatoires sont manquants.

Les valeurs de la forme ${VAR_NAME} ou $VAR_NAME dans le YAML sont
automatiquement substituées par les variables d'environnement correspondantes.
Exemple dans config.yaml :
    api_key: "${BINANCE_API_KEY}"
    api_secret: "${BINANCE_API_SECRET}"
"""
import logging
import os
import re
from typing import Any

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
        "latency_ms": 50,
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


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Fichier de configuration introuvable : {path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if not isinstance(cfg, dict):
        raise ValueError("Le fichier config.yaml est vide ou invalide.")

    # Substitution des variables d'environnement (ex: ${BINANCE_API_KEY})
    cfg = _expand_env(cfg)

    # Applique les défauts
    for section, defaults in DEFAULTS.items():
        if section not in cfg:
            cfg[section] = {}
        for k, v in defaults.items():
            cfg[section].setdefault(k, v)

    # Validation des champs requis
    errors = []
    for section, field in REQUIRED_FIELDS:
        val = cfg.get(section, {}).get(field)
        if val is None:
            errors.append(f"  [{section}].{field} manquant")
    if errors:
        raise ValueError("Configuration invalide :\n" + "\n".join(errors))

    # Avertissements sécurité
    api_key = cfg.get("exchange", {}).get("api_key", "")
    if api_key in ("", "YOUR_KEY"):
        logger.warning("⚠ Clés API exchange non configurées — mode backtest uniquement.")
    if not cfg["trading"].get("paper_mode"):
        logger.warning("🔴 LIVE TRADING ACTIVÉ — vérifiez bien vos paramètres !")

    # Compatibilité multi-TF : injecter strategies.enabled depuis timeframes si absent
    if "timeframes" in cfg and cfg["timeframes"]:
        all_strats = []
        for tf_cfg in cfg["timeframes"].values():
            all_strats.extend(tf_cfg.get("strategies", []))
        if "strategies" not in cfg:
            cfg["strategies"] = {}
        cfg["strategies"].setdefault("enabled", list(dict.fromkeys(all_strats)))
        # TF de référence = premier TF listé (rétrocompat)
        cfg["trading"].setdefault("timeframe", next(iter(cfg["timeframes"])))
    else:
        # Mode mono-TF classique : injecter une entrée timeframes minimale
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
