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
    "optimizer": {"enabled": False, "method": "bayesian", "n_trials": 50, "out_of_sample_ratio": 0.3},
    "logging":   {"level": "INFO", "debug": False, "max_bytes": 10_485_760, "backup_count": 5,
                  "log_file": "logs/bot.log"},
    "web":       {"host": "127.0.0.1", "port": 8000, "refresh_interval": 5, "api_key": ""},
    "scanner":   {"symbols": ["BTC/USDC","ETH/USDC","SOL/USDC"], "dynamic_scan": False, "top_n": 20},
    # Dérivés (funding/OI/long-short/taker) accumulés au fil de l'eau dans
    # data/derivatives/*.parquet, comme l'OHLCV. Opt-in (enabled: false par défaut
    # → comportement inchangé). Enrichit le df de scoring en colonnes funding_z/
    # oi_change_pct/lsr_z/taker_z, consommées par la stratégie funding_flow.
    "derivatives": {"enabled": False, "period": "1h", "refresh_interval": 300, "z_window": 90},
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


def _load_strategy_configs(strategies_dir: str) -> Tuple[dict, dict, list]:
    """
    Charge tous les *.yaml dans strategies_dir et retourne :
      (strategy_params, optimizer_results, enabled_strategies)

    Chaque fichier a la structure :
      enabled: true          # optionnel — true par défaut ; mettre false pour désactiver
      params: {...}
      optimizer_results: {tf: {run_date, oos_score, params}}

    Une stratégie est active si et seulement si son fichier existe
    et que `enabled` n'est pas explicitement à false.
    """
    strategy_params:    dict = {}
    optimizer_results:  dict = {}
    enabled_strategies: list = []
    sdir = Path(strategies_dir)
    if not sdir.is_dir():
        return strategy_params, optimizer_results, enabled_strategies

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
            # Activée par défaut ; `enabled: false` pour désactiver sans supprimer le fichier
            if data.get("enabled", True):
                enabled_strategies.append(name)
        except Exception as e:
            logger.warning(f"[Config] Erreur lecture {f} : {e}")

    logger.debug(
        f"[Config] strategies/ : {len(enabled_strategies)} actives / "
        f"{len(strategy_params)} chargées "
        f"({', '.join(enabled_strategies)})"
    )
    return strategy_params, optimizer_results, enabled_strategies


def strategy_file_path(strategy_name: str, config_path: str = "config.yaml") -> str:
    """Retourne le chemin du fichier YAML d'une stratégie (strategies/{name}.yaml)."""
    if not strategy_name or "/" in strategy_name or "\\" in strategy_name or ".." in strategy_name:
        raise ValueError(f"Nom de stratégie invalide : {strategy_name}")
    config_dir = os.path.dirname(os.path.abspath(config_path))
    return os.path.join(config_dir, "strategies", f"{strategy_name}.yaml")


def _bootstrap_strategy_files(strategies_dir: str) -> None:
    """
    Crée un fichier YAML minimal pour chaque stratégie Python sans fichier YAML existant.
    Appelé avant _load_strategy_configs pour garantir la cohérence .py ↔ .yaml.

    Une stratégie est éligible si elle expose une classe Strategy avec param_space.
    Le fichier créé contient uniquement optimizer_results: {} — la stratégie est
    active par défaut et sera enrichie par l'optimiseur lors de sa première exécution.
    """
    try:
        import pkgutil
        import importlib
        import app.strategies as _strat_pkg
    except ImportError:
        return

    sdir = Path(strategies_dir)
    sdir.mkdir(parents=True, exist_ok=True)

    for _, module_name, _ in pkgutil.iter_modules(_strat_pkg.__path__):
        yaml_path = sdir / f"{module_name}.yaml"
        if yaml_path.exists():
            continue
        try:
            mod = importlib.import_module(f"app.strategies.{module_name}")
            cls = getattr(mod, "Strategy", None)
            if cls is None or not getattr(cls, "param_space", None):
                continue
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    {"optimizer_results": {}},
                    f, allow_unicode=True, sort_keys=False,
                )
            logger.info(f"[Config] YAML créé automatiquement : strategies/{module_name}.yaml")
        except Exception as exc:
            # warning : sans YAML bootstrappé, la stratégie n'apparaît ni dans
            # strategies.enabled ni dans la page Configuration.
            logger.warning(f"[Config] Bootstrap strategies/{module_name}.yaml KO : {exc}")


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
    _bootstrap_strategy_files(strategies_dir)
    strat_params, opt_results, enabled_strategies = _load_strategy_configs(strategies_dir)

    # Liste des stratégies activées : dérivée des fichiers strategies/
    # (plus de strategies.enabled dans config.yaml)
    cfg.setdefault("strategies", {})["enabled"] = enabled_strategies

    # Merge strategy_params : le fichier stratégie est prioritaire sur config.yaml
    merged_params = dict(cfg.get("strategy_params", {}))
    for name, params in strat_params.items():
        if name in merged_params:
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

    # Validation numérique des paramètres critiques
    t = cfg["trading"]
    try:
        t["capital"]        = float(t["capital"])
        t["risk_per_trade"] = float(t["risk_per_trade"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"Valeur numérique invalide dans [trading] : {e}")
    if t["capital"] <= 0:
        raise ValueError(f"[trading].capital doit être > 0 (valeur : {t['capital']})")
    if not (0 < t["risk_per_trade"] <= 1):
        raise ValueError(f"[trading].risk_per_trade doit être entre 0 et 1 (valeur : {t['risk_per_trade']})")

    api_key = cfg.get("exchange", {}).get("api_key", "")
    if api_key in ("", "YOUR_KEY"):
        logger.warning("⚠ Clés API exchange non configurées — mode backtest uniquement.")
    if not cfg["trading"].get("paper_mode"):
        logger.warning("🔴 LIVE TRADING ACTIVÉ — vérifiez bien vos paramètres !")

    # OKX (et Kucoin/Coinbase) exigent une passphrase API en plus de la clé/secret.
    exch_name = str(cfg.get("exchange", {}).get("name", "")).lower()
    if (exch_name in ("okx", "kucoin", "coinbase", "coinbasepro")
            and api_key not in ("", "YOUR_KEY")
            and not (cfg["exchange"].get("api_password")
                     or cfg["exchange"].get("api_passphrase")
                     or cfg["exchange"].get("password"))):
        logger.warning(
            f"⚠ [Config] {exch_name} requiert une passphrase API "
            f"(exchange.api_password / ${{OKX_API_PASSWORD}}) — absente : "
            f"les appels authentifiés échoueront en live."
        )

    # ── Cohérence margin / levier / paper (garde-fous production) ────────────
    margin_on = bool(cfg.get("exchange", {}).get("margin")
                     or cfg["trading"].get("margin_mode"))
    if margin_on and float(cfg["trading"].get("max_leverage", 1)) <= 1:
        logger.warning(
            "⚠ [Config] exchange.margin actif mais trading.max_leverage <= 1 : "
            "le levier ne sera jamais utilisé (l'emprunt margin reste actif — "
            "tdMode margin sur OKX). Pour du spot "
            "pur : margin: false ET margin_mode: null ; pour du margin réel : "
            "max_leverage > 1."
        )
    if margin_on and cfg["trading"].get("paper_mode"):
        logger.warning(
            "⚠ [Config] paper_mode + margin simultanés : les coûts d'emprunt sont "
            "simulés mais aucun emprunt réel n'a lieu — les PnL paper et live "
            "divergeront. Désactivez margin pour un paper trading représentatif."
        )

    # ── Sécurité API web (OPS-02 : BLOQUANT) ─────────────────────────────────
    # Sans web.api_key, l'auth retombe sur un filtre « localhost only » basé
    # sur l'IP client — contournable derrière un reverse proxy mal configuré
    # (X-Forwarded-For). Exposer 0.0.0.0 sans clé = API de trading OUVERTE
    # (start/stop du bot, écriture de config). Le démarrage est donc REFUSÉ,
    # sauf override explicite et assumé via ALLOW_INSECURE_WEB=1 (dev local).
    web_cfg = cfg.get("web", {})
    if not web_cfg.get("api_key") and str(web_cfg.get("host", "")) in ("0.0.0.0", "::"):
        if os.environ.get("ALLOW_INSECURE_WEB") == "1":
            logger.warning(
                "🔓 [Config] web.host=%s SANS web.api_key (override "
                "ALLOW_INSECURE_WEB=1) : l'API n'est protégée que par le filtre "
                "localhost — à réserver au développement.", web_cfg.get("host"),
            )
        else:
            raise ValueError(
                f"web.host={web_cfg.get('host')} SANS web.api_key : l'API de "
                "trading serait exposée à tout le réseau. Définissez web.api_key "
                "(ex. : python -c \"import secrets; print(secrets.token_urlsafe(32))\") "
                "ou, pour du développement local uniquement, lancez avec "
                "ALLOW_INSECURE_WEB=1."
            )

    # ── Notifications en réel (OPS-04) ───────────────────────────────────────
    # paper_mode=false sans AUCUN canal externe : un HALT (margin critique,
    # kill-switch, mismatch de réconciliation) resterait invisible hors du
    # dashboard. Alerte CRITICAL — à transformer en blocage si souhaité.
    if not cfg["trading"].get("paper_mode", True):
        notif = cfg.get("notifications", {}) or {}
        channels_on = any(bool(notif.get(k)) for k in
                          ("telegram_enabled", "whatsapp_enabled", "email_enabled"))
        if not channels_on:
            logger.critical(
                "🚨 [Config] paper_mode=false SANS canal de notification externe "
                "(telegram/whatsapp/email) : un HALT ou une erreur critique en "
                "trading RÉEL ne préviendrait personne. Activez au moins un canal "
                "dans notifications: avant de trader en réel."
            )

    # Compatibilité multi-TF
    _VALID_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}
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
        if tf not in _VALID_TIMEFRAMES:
            logger.warning(f"[Config] Timeframe '{tf}' non standard — valides : {sorted(_VALID_TIMEFRAMES)}")
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
