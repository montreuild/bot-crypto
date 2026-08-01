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
from dotenv import load_dotenv

from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL

logger = logging.getLogger(__name__)

# Racine du dépôt : app/core/config.py → app/core → app → <racine>
_REPO_ROOT = Path(__file__).resolve().parents[2]
_dotenv_loaded = False


def _ensure_dotenv() -> None:
    """Charge `.env` dans l'environnement, une seule fois par process.

    `setup.sh` génère un `.env` contenant une `WEB_API_KEY` aléatoire et les
    clés exchange, mais rien ne le lisait : `${WEB_API_KEY}` restait vide, et
    `web.host: 0.0.0.0` sans `api_key` faisait *refuser le démarrage* (garde
    OPS-02). Le fichier était donc écrit puis ignoré.

    `override=False` : une variable déjà exportée dans le shell (ou injectée
    par systemd/Docker) reste prioritaire sur le fichier — le `.env` est un
    filet pour le dev local, pas une source d'autorité en production.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True

    env_path = _REPO_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
        logger.debug(f"[Config] .env chargé depuis {env_path}")

REQUIRED_FIELDS = [
    ("exchange", "name"),
    ("trading", "capital"),
    ("trading", "risk_per_trade"),
    ("database", "url"),
    # ("trading", "timeframe")  -- optionnel en mode multi-TF
    # ("strategies", "enabled") -- optionnel en mode multi-TF
]

# Frais par défaut (ARCH-10) — SOURCE UNIQUE : tout repli `cfg.get("taker_fee", …)`
# ou défaut de fonction doit importer ces constantes, jamais recopier le littéral
# (sinon un changement du défaut canonique laisse des sites incohérents).
DEFAULT_TAKER_FEE = 0.001
DEFAULT_MAKER_FEE = 0.0004

# Racine des données persistées hors BDD (ARCH-13) — les stores en dérivent
# leurs répertoires par défaut (data/ohlcv, data/features, data/derivatives).
DATA_ROOT = "data"

DEFAULTS = {
    "trading": {
        "paper_mode": True, "max_positions": 5, "max_longs": 3, "max_shorts": 3,
        "scan_interval": 60, "score_threshold": 0.55, "daily_drawdown_limit": 0.05,
        "max_trades_per_minute": 3, "min_volume_usdc_24h": 5_000_000,
        # Alias générique (S2-03, multi-actifs) — même défaut ; scanner.py lit
        # min_volume_quote_24h en priorité (repli automatique sur l'ancienne
        # clé si l'utilisateur ne l'a personnalisée qu'elle).
        "min_volume_quote_24h": 5_000_000,
        "taker_fee": DEFAULT_TAKER_FEE, "maker_fee": DEFAULT_MAKER_FEE,
        "borrow_rate_daily": 0.0002,
        "max_leverage": 1, "max_drawdown_global": 0.20, "spread_pct": 0.0005,
        "latency_ms": 50, "paper_slippage": 0.001,
        # FIN-07 : "static" (défaut, comportement inchangé) vs "size" (ajoute
        # un coût d'impact ~ notional/volume_moyen_20b, même formule que
        # backtest.slippage_model — app.core.execution.size_impact_cost).
        "paper_slippage_model": "static", "slippage_k": 1.0,
    },
    "backtest": {
        "spread_pct": 0.0005, "latency_ms": 50, "partial_fill_pct": 0.95,
        "monte_carlo_runs": 200, "walk_forward_folds": 5,
        # Plafond de notionnel PAR TRADE (fraction du capital) — valeur UNIQUE
        # partagée backtest/live (BT-03 : le backtest utilisait un repli 0.50
        # quand le RiskManager live plafonnait à 0.20 → tailles ×2,5 invalidant
        # la reproductibilité des backtests en réel).
        "max_notional_pct": 0.20,
    },
    "optimizer": {"enabled": False, "method": "bayesian", "n_trials": 50, "out_of_sample_ratio": 0.3,
                  # S4-03 : "full" (IS+OOS, historique) vs "is_only" — cf.
                  # docstring de _save_ml_model_post_opt (auto_optimizer.py).
                  "ml_final_train_mode": "full"},
    # OBS-02 : "format" pilote le seul handler FICHIER — "json" (défaut) ou
    # "text" pour revenir à l'ancien format ligne.
    "logging":   {"level": "INFO", "debug": False, "max_bytes": 10_485_760, "backup_count": 5,
                  "log_file": "logs/bot.log", "format": "json"},
    "web":       {"host": "127.0.0.1", "port": 8000, "refresh_interval": 5, "api_key": ""},
    "scanner":   {"symbols": [DEFAULT_CONFIG_SYMBOL,"ETH/USDC","SOL/USDC"], "dynamic_scan": False, "top_n": 20},
    # Dérivés (funding/OI/long-short/taker) accumulés au fil de l'eau dans
    # data/derivatives/*.parquet, comme l'OHLCV. Opt-in (enabled: false par défaut
    # → comportement inchangé). Enrichit le df de scoring en colonnes funding_z/
    # oi_change_pct/lsr_z/taker_z, consommées par la stratégie funding_flow.
    "derivatives": {"enabled": False, "period": "1h", "refresh_interval": 300, "z_window": 90},
    # PERF-01 : taille du cache LRU process-wide de indicators_precompute.py
    # (avant : 16 fixe, non configurable).
    "perf": {"precompute_cache_size": 128},
}

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}|\$([A-Z_][A-Z0-9_]*)")


def _expand_env(value: Any, missing: set | None = None) -> Any:
    """Substitue récursivement les variables d'environnement dans les chaînes.

    Les noms de variables référencées (``${VAR}``) mais absentes ou vides
    dans l'environnement sont collectés dans ``missing`` si fourni — permet
    à ``load_config`` de lever une erreur explicite en mode live plutôt que
    de démarrer avec des identifiants vides (échecs d'authentification
    silencieux, cf. OPS/14.1).
    """
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var = m.group(1) or m.group(2)
            env_val = os.environ.get(var, "")
            if env_val:
                logger.debug(f"[Config] Variable d'env résolue : ${var}")
            elif missing is not None:
                missing.add(var)
            return env_val
        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, missing) for v in value]
    return value


def _load_strategy_configs(strategies_dir: str) -> Tuple[dict, dict, list]:
    """
    Charge tous les *.yaml dans strategies_dir et retourne :
      (strategy_params, optimizer_results, enabled_strategies)

    Chaque fichier a la structure :
      enabled: true          # optionnel — true par défaut ; mettre false pour désactiver
      params: {...}
      optimizer_results: {tf: {...}}   # schéma détaillé ci-dessous

    Schéma EXACT de ``optimizer_results`` une fois assemblé dans la config
    globale (clé racine = nom de stratégie, ajoutée par cette fonction) :

      optimizer_results[strategy][tf][symbol] = {
          "run_date":  str,     # date ISO de l'optimisation
          "oos_score": float,   # score out-of-sample (seuil d'exclusion live : -0.05)
          "params":    dict,    # overrides de strategy_params[strategy] pour ce (tf, symbol)
      }

      Exemple :
        smart_money:
          4h:
            BTC/USDC: {run_date: "2026-07-01", oos_score: 0.42, params: {adx_min: 22}}
            ETH/USDC: {run_date: "2026-07-02", oos_score: 0.18, params: {adx_min: 25}}

    Rétro-compatibilité (schéma hérité, pré-refonte par symbole) : une entrée
    ``optimizer_results[strategy][tf]`` qui contient DIRECTEMENT les clés
    ``run_date``/``oos_score``/``params`` (au lieu d'un mapping ``{symbol: ...}``)
    est réputée calibrée pour ``DEFAULT_CONFIG_SYMBOL`` (BTC/USDC) — elle ne
    s'applique PAS aux autres symboles. Cette règle et sa résolution exacte
    vivent dans ``app/core/param_resolution.py`` (``_select_symbol_entry``,
    ``_is_legacy_tf_entry``, ``resolve_strategy_params`` — utilisé à la fois
    par le backtest et le live pour garantir une résolution identique).
    Cf. aussi la section « Live Trading Loop » d'ARCHITECTURE.md.

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


def _bootstrap_strategy_files(strategies_dir: str) -> None:
    """
    Crée un fichier YAML minimal pour chaque stratégie Python sans fichier YAML existant.
    Appelé avant _load_strategy_configs pour garantir la cohérence .py ↔ .yaml.

    Une stratégie est éligible si elle expose une classe Strategy avec param_space.
    Le fichier créé contient uniquement optimizer_results: {} — la stratégie est
    active par défaut et sera enrichie par l'optimiseur lors de sa première exécution.
    """
    try:
        import importlib
        import pkgutil

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


def active_timeframes(cfg: dict) -> list:
    """Timeframes réellement scannés par le bot — ``trading.timeframes``.

    Source unique : c'est cette liste que lit ``get_active_strategies_per_tf``.
    ``trading.timeframe`` (singulier) n'est qu'un repli mono-TF.
    """
    t = cfg.get("trading", {}) or {}
    tfs = t.get("timeframes")
    if tfs:
        return list(tfs)
    return [t.get("timeframe", "1h")]


def _validate_venues(cfg: dict) -> None:
    """Cohérence du modèle de venue (S11) — la venue est la source de vérité.

    Trois garde-fous, du plus dur au plus souple :

    1. **``venues.default`` obligatoire** dès que ``venues.defs`` est renseigné.
       Sans lui, la résolution retombait sur ``default_venue_from_cfg``, qui
       fabriquait une venue à partir des globales ``exchange.margin`` /
       ``trading.margin_mode`` / ``trading.max_leverage`` — parfois **homonyme**
       d'une entrée de ``defs`` mais avec d'autres valeurs (levier notamment).
       Deux sources de vérité concurrentes et silencieuses : refusé au
       démarrage.
    2. **Toute venue référencée doit exister** dans ``defs`` (``assign`` et
       ``default``) — une faute de frappe ne doit pas router un bot vers les
       globales sans que personne ne le voie.
    3. **Les globales margin doivent s'accorder** avec la venue par défaut,
       sinon WARNING : elles ne pilotent plus rien dès que ``venues.default``
       existe, et les laisser mentir induit en erreur le prochain lecteur.
    """
    venues = cfg.get("venues") or {}
    defs = venues.get("defs") or {}
    default = venues.get("default")
    assign = venues.get("assign") or {}

    if defs and not default:
        raise ValueError(
            "venues.defs est renseigné mais venues.default est vide : la venue "
            "des symboles non assignés serait dérivée des globales "
            "(exchange.margin / trading.margin_mode / trading.max_leverage), "
            "en concurrence silencieuse avec venues.defs. Déclarez "
            f"venues.default parmi : {sorted(defs)}."
        )

    unknown = sorted({v for v in ([default] if default else []) + list(assign.values())
                      if v and v not in defs})
    if unknown:
        raise ValueError(
            f"venues : référence(s) inconnue(s) {unknown} — absentes de "
            f"venues.defs ({sorted(defs)}). Corrigez venues.default/assign."
        )

    if not default:
        return

    d = defs.get(default) or {}
    market = d.get("market_type", "spot")
    borrows = market in ("margin", "perp")
    t = cfg["trading"]
    globals_margin = bool(cfg.get("exchange", {}).get("margin") or t.get("margin_mode"))

    if globals_margin != borrows:
        logger.warning(
            "⚠ [Config] les globales margin (exchange.margin=%s, "
            "trading.margin_mode=%s) contredisent la venue par défaut '%s' "
            "(market_type=%s). Depuis S11 ce sont les venues qui font foi — "
            "alignez les globales pour ne pas induire en erreur.",
            cfg.get("exchange", {}).get("margin"), t.get("margin_mode"),
            default, market,
        )

    lev = float(d.get("max_leverage", 1) or 1)
    if borrows and lev <= 1:
        logger.warning(
            f"⚠ [Config] venue par défaut '{default}' en {market} avec "
            f"max_leverage={lev:g} : l'emprunt est facturé mais le levier ne "
            f"sera jamais utilisé. Pour du spot pur, déclarez "
            f"market_type: spot ; pour du margin réel, max_leverage > 1."
        )

    if borrows and t.get("paper_mode"):
        logger.warning(
            f"⚠ [Config] paper_mode + venue par défaut '{default}' en {market} : "
            f"les coûts d'emprunt sont simulés au taux "
            f"trading.borrow_rate_daily={t.get('borrow_rate_daily')} mais aucun "
            f"emprunt réel n'a lieu — les PnL paper et live divergeront. Pour un "
            f"paper représentatif du comptant, basculez venues.default sur une "
            f"venue market_type: spot (mettre exchange.margin: false ne suffit "
            f"PAS : le taux d'emprunt est porté par la venue)."
        )


def load_config(path: str = "config.yaml") -> dict:
    # Avant toute expansion `${VAR}` : sans ça, les valeurs du `.env` généré
    # par setup.sh ne sont jamais visibles (cf. `_ensure_dotenv`).
    _ensure_dotenv()

    if not os.path.exists(path):
        raise FileNotFoundError(f"Fichier de configuration introuvable : {path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if not isinstance(cfg, dict):
        raise ValueError("Le fichier config.yaml est vide ou invalide.")

    # S2-03 : alias générique min_volume_quote_24h — propage une valeur
    # personnalisée de l'ancienne clé min_volume_usdc_24h AVANT le merge des
    # défauts (sinon la nouvelle clé retomberait sur le défaut générique et
    # ignorerait le réglage existant de l'utilisateur).
    _t_raw = cfg.get("trading") or {}
    if isinstance(_t_raw, dict) and "min_volume_quote_24h" not in _t_raw \
            and "min_volume_usdc_24h" in _t_raw:
        cfg.setdefault("trading", {})["min_volume_quote_24h"] = _t_raw["min_volume_usdc_24h"]

    _missing_env: set = set()
    cfg = _expand_env(cfg, _missing_env)

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

    # Variables d'env référencées mais absentes (OPS/14.1) : bloquant en live
    # (sinon échecs d'authentification silencieux avec des clés vides),
    # WARNING seulement en paper mode. Opt-out explicite via config.strict_env.
    if _missing_env:
        paper_mode = bool(cfg["trading"].get("paper_mode", True))
        strict_env = cfg.get("config", {}).get("strict_env")
        strict_env = (not paper_mode) if strict_env is None else bool(strict_env)
        missing_list = ", ".join(f"${{{v}}}" for v in sorted(_missing_env))
        if strict_env:
            raise ValueError(
                f"Variable(s) d'environnement référencée(s) dans config.yaml mais "
                f"absente(s)/vide(s) : {missing_list} — le mode live refuse de "
                f"démarrer avec des identifiants vides (échec d'authentification "
                f"silencieux sinon). Définissez ces variables, ou passez "
                f"trading.paper_mode: true / config.strict_env: false pour ignorer."
            )
        logger.warning(
            f"[Config] Variable(s) d'environnement absente(s)/vide(s) (mode paper, "
            f"non bloquant) : {missing_list}"
        )

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

    _validate_venues(cfg)

    # ── Sécurité API web (OPS-02 : BLOQUANT) ─────────────────────────────────
    # Sans web.api_key, l'auth retombe sur un filtre « localhost only » basé
    # sur l'IP client — contournable derrière un reverse proxy mal configuré
    # (X-Forwarded-For). Exposer 0.0.0.0 sans clé = API de trading OUVERTE
    # (start/stop du bot, écriture de config). Le démarrage est donc REFUSÉ,
    # sauf override explicite et assumé via ALLOW_INSECURE_WEB=1 (dev local).
    web_cfg = cfg.get("web", {})
    if not web_cfg.get("api_key") and str(web_cfg.get("host", "")) in ("0.0.0.0", "::"):
        allow_insecure = (bool(web_cfg.get("allow_insecure"))
                          or os.environ.get("ALLOW_INSECURE_WEB") == "1")
        if allow_insecure:
            logger.warning(
                "🔓 [Config] web.host=%s SANS web.api_key (override "
                "web.allow_insecure) : l'API n'est protégée que par le filtre "
                "localhost — à réserver au développement local.", web_cfg.get("host"),
            )
        else:
            raise ValueError(
                f"web.host={web_cfg.get('host')} SANS web.api_key : l'API de "
                "trading serait exposée à tout le réseau. Définissez web.api_key "
                "(ex. : python -c \"import secrets; print(secrets.token_urlsafe(32))\") "
                "ou, pour du développement local uniquement, mettez "
                "web.allow_insecure: true dans config.yaml."
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

    # ── Compatibilité multi-TF ───────────────────────────────────────────────
    # Schéma HÉRITÉ : une clé racine `timeframes:` mappant chaque TF vers sa
    # liste de stratégies. Encore acceptée en lecture, plus jamais fabriquée :
    # elle l'était systématiquement en repli, à partir du SEUL
    # `trading.timeframe`, alors que le bot tourne sur `trading.timeframes`
    # (5 TF dans la config livrée). Personne ne la lisait sauf le log de
    # démarrage, qui annonçait donc « TF=['1h'] » pendant que le bot scannait
    # 15m/30m/1h/4h/1d.
    _VALID_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}
    if cfg.get("timeframes"):
        all_strats = []
        for tf_cfg in cfg["timeframes"].values():
            all_strats.extend((tf_cfg or {}).get("strategies", []))
        if "strategies" not in cfg:
            cfg["strategies"] = {}
        cfg["strategies"].setdefault("enabled", list(dict.fromkeys(all_strats)))
        cfg["trading"].setdefault("timeframe", next(iter(cfg["timeframes"])))

    tfs = active_timeframes(cfg)
    for tf in tfs:
        if tf not in _VALID_TIMEFRAMES:
            logger.warning(f"[Config] Timeframe '{tf}' non standard — "
                           f"valides : {sorted(_VALID_TIMEFRAMES)}")

    logger.info(f"Config chargée : {path} | Capital={cfg['trading']['capital']} "
                f"| TF={tfs} | Paper={cfg['trading']['paper_mode']}")
    return cfg
