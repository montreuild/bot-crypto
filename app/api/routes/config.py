"""Routes configuration — lecture et mise à jour de config.yaml via l'API."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key, _discover_strategies

logger = logging.getLogger(__name__)
router = APIRouter()


def _save_yaml(updates_fn):
    """Applique updates_fn(disk_cfg) et réécrit config.yaml (thread-safe).

    Round-trip (ruamel) : les commentaires de config.yaml sont préservés."""
    from app.core.yaml_io import load_yaml, dump_yaml
    with state._config_write_lock:
        disk_cfg = load_yaml("config.yaml", default={})
        updates_fn(disk_cfg)
        dump_yaml("config.yaml", disk_cfg)


def _save_strategy_yaml(strategy_name: str, updates_fn):
    """
    Applique updates_fn(data) et réécrit strategies/{strategy_name}.yaml (thread-safe).
    Crée le fichier s'il n'existe pas. Préserve les commentaires (round-trip).
    """
    from app.engine.optimizer import _strategy_file_path, _load_strategy_file
    from app.core.yaml_io import dump_yaml
    strat_path = _strategy_file_path(strategy_name)
    with state._config_write_lock:
        data = _load_strategy_file(strat_path)
        updates_fn(data)
        dump_yaml(strat_path, data)


# ── GET /api/config ────────────────────────────────────────────────────────

@router.get("/api/config", dependencies=[Depends(verify_api_key)])
def get_config():
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    all_strats = sorted(_discover_strategies())
    safe = {k: v for k, v in state.cfg.items() if k not in ("exchange", "notifications")}
    safe["exchange"]           = {"name": state.cfg["exchange"]["name"]}
    # Redaction : la clé API web et d'éventuels credentials dans l'URL de la
    # base ne sortent jamais (le front n'en a pas besoin : le cookie d'auth
    # est posé côté serveur).
    if isinstance(safe.get("web"), dict):
        web = dict(safe["web"])
        if web.get("api_key"):
            web["api_key"] = "****"
        safe["web"] = web
    if isinstance(safe.get("database"), dict):
        db  = dict(safe["database"])
        url = str(db.get("url", ""))
        if "://" in url and "@" in url:
            scheme = url.split("://", 1)[0]
            db["url"] = f"{scheme}://****@{url.split('@', 1)[1]}"
        safe["database"] = db
    safe["all_strategies"]     = all_strats
    from app.engine.optimizer import STRATEGY_TIMEFRAMES, RECOMMENDED_LIMIT
    safe["strategy_timeframes"] = STRATEGY_TIMEFRAMES
    safe["recommended_limits"]  = RECOMMENDED_LIMIT
    if state.trader:
        safe["_auto_opt_enabled"]    = state.trader._auto_opt_enabled
        safe["_auto_opt_interval_h"] = state.trader._auto_opt_interval // 3600
        safe["_auto_opt_next_run"]   = state.trader._auto_opt_next_run
        safe["_active_per_tf"] = {tf: [s["name"] for s in v]
                                  for tf, v in state.trader._active_per_tf.items()}
    return safe


# ── POST /api/config/strategies ───────────────────────────────────────────

@router.post("/api/config/strategies", dependencies=[Depends(verify_api_key)])
def update_strategies(enabled: str = ""):
    """
    Active/désactive des stratégies en écrivant `enabled: true/false`
    dans chaque fichier strategies/{name}.yaml.
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    strat_list = [s.strip() for s in enabled.split(",") if s.strip()]
    if not strat_list:
        raise HTTPException(400, "Aucune stratégie spécifiée")
    allowed = _discover_strategies()
    invalid = [s for s in strat_list if s not in allowed]
    if invalid:
        raise HTTPException(400, f"Stratégie(s) inconnue(s) : {', '.join(invalid)}")

    state.cfg["strategies"]["enabled"] = strat_list
    result = {"config_updated": True, "strategies": strat_list, "trader_updated": False}

    if state.trader:
        reload_result = state.trader.reload_strategies(strat_list)
        result["trader_updated"] = True
        result.update(reload_result)

    # Écriture de enabled: true/false dans chaque fichier strategies/
    errors = []
    for name in allowed:
        try:
            _save_strategy_yaml(name, lambda data, _n=name: data.update(
                {"enabled": _n in strat_list}
            ))
        except Exception as e:
            errors.append(f"{name}: {e}")
    result["saved_to_disk"] = len(errors) == 0
    if errors:
        result["save_errors"] = errors
    return result


# ── POST /api/config/timeframes ───────────────────────────────────────────

@router.post("/api/config/timeframes", dependencies=[Depends(verify_api_key)])
def update_timeframes(timeframes: str = "1h"):
    """Met à jour les timeframes actifs (CSV, ex: '5m,1h,4h')."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    allowed_tfs = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}
    tf_list = [t.strip() for t in timeframes.split(",") if t.strip()]
    invalid = [t for t in tf_list if t not in allowed_tfs]
    if invalid:
        raise HTTPException(400, f"Timeframe(s) invalide(s) : {', '.join(invalid)}")
    if not tf_list:
        raise HTTPException(400, "Aucun timeframe spécifié")

    state.cfg["trading"]["timeframes"] = tf_list
    state.cfg["trading"]["timeframe"]  = tf_list[0]

    result = {"timeframes": tf_list, "trader_updated": False}
    if state.trader:
        state.trader.timeframes = tf_list
        state.trader.tf         = tf_list[0]
        state.trader._build_active_per_tf()
        result["trader_updated"] = True
        result["active_per_tf"]  = {tf: [s["name"] for s in v]
                                    for tf, v in state.trader._active_per_tf.items()}
    try:
        def _upd(d):
            d.setdefault("trading", {})["timeframes"] = tf_list
            d["trading"]["timeframe"] = tf_list[0]
        _save_yaml(_upd)
        result["saved_to_disk"] = True
    except Exception as e:
        result["save_error"] = str(e)
    return result


# ── POST /api/config/auto-optimizer ──────────────────────────────────────

@router.post("/api/config/auto-optimizer", dependencies=[Depends(verify_api_key)])
def update_auto_optimizer(enabled: bool = False, interval_h: int = 24):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    state.cfg.setdefault("optimizer", {})["enabled"]        = enabled
    state.cfg["optimizer"]["auto_interval_h"]               = interval_h
    if state.trader:
        state.trader.set_auto_optimizer(enabled, interval_h)
    try:
        def _upd(d):
            d.setdefault("optimizer", {})["enabled"]        = enabled
            d["optimizer"]["auto_interval_h"]               = interval_h
        _save_yaml(_upd)
        saved = True
    except Exception as e:
        logger.warning(f"[config/auto-optimizer] sauvegarde YAML KO : {e}")
        saved = False
    return {"enabled": enabled, "interval_h": interval_h,
            "trader_updated": state.trader is not None, "saved_to_disk": saved}


# ── POST /api/config/trading ──────────────────────────────────────────────

@router.post("/api/config/trading", dependencies=[Depends(verify_api_key)])
def update_trading_params(
    score_threshold:      float = None,
    risk_per_trade:       float = None,
    max_positions:        int   = None,
    paper_mode:           bool  = None,
    paper_slippage:       float = None,
    daily_drawdown_limit: float = None,
):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    # ── Validation des bornes ──
    if score_threshold is not None and not (0.0 < score_threshold < 1.0):
        raise HTTPException(400, "score_threshold doit être entre 0 et 1 (exclus)")
    if risk_per_trade is not None and not (0.0 < risk_per_trade <= 1.0):
        raise HTTPException(400, "risk_per_trade doit être entre 0 (exclus) et 1.0")
    if max_positions is not None and not (1 <= max_positions <= 50):
        raise HTTPException(400, "max_positions doit être entre 1 et 50")
    if paper_slippage is not None and not (0.0 <= paper_slippage <= 0.05):
        raise HTTPException(400, "paper_slippage doit être entre 0 et 0.05 (5%)")
    if daily_drawdown_limit is not None and not (0.0 < daily_drawdown_limit <= 0.5):
        raise HTTPException(400, "daily_drawdown_limit doit être entre 0 (exclus) et 0.5")
    changed = {}
    mapping = {
        "score_threshold":      score_threshold,
        "risk_per_trade":       risk_per_trade,
        "max_positions":        max_positions,
        "paper_mode":           paper_mode,
        "paper_slippage":       paper_slippage,
        "daily_drawdown_limit": daily_drawdown_limit,
    }
    for key, val in mapping.items():
        if val is not None:
            state.cfg["trading"][key] = val
            changed[key] = val
            if state.trader:
                if hasattr(state.trader, key):
                    setattr(state.trader, key, val)
                if hasattr(state.trader.risk, key):
                    setattr(state.trader.risk, key, val)
                if key == "score_threshold":
                    state.trader.threshold = val
    try:
        _save_yaml(lambda d: d.setdefault("trading", {}).update(changed))
        saved = True
    except Exception as e:
        logger.warning(f"[config/trading] sauvegarde YAML KO : {e}")
        saved = False
    return {"changed": changed, "saved_to_disk": saved,
            "trader_updated": state.trader is not None}


# ── POST /api/config/risk ─────────────────────────────────────────────────

@router.post("/api/config/risk", dependencies=[Depends(verify_api_key)])
def update_risk_config(
    consecutive_loss_limit:  int   = None,
    slot_daily_dd_limit:     float = None,
    win_rate_floor:          float = None,
    volatility_threshold:    float = None,
    consecutive_pause_secs:  int   = None,
):
    """Met à jour la configuration des circuit breakers par slot."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    # ── Validation des bornes ──
    if consecutive_loss_limit is not None and not (1 <= consecutive_loss_limit <= 20):
        raise HTTPException(400, "consecutive_loss_limit doit être entre 1 et 20")
    if slot_daily_dd_limit is not None and not (0.0 < slot_daily_dd_limit <= 0.5):
        raise HTTPException(400, "slot_daily_dd_limit doit être entre 0 (exclus) et 0.5")
    if win_rate_floor is not None and not (0.0 <= win_rate_floor <= 1.0):
        raise HTTPException(400, "win_rate_floor doit être entre 0 et 1")
    if volatility_threshold is not None and not (0.0 < volatility_threshold <= 1.0):
        raise HTTPException(400, "volatility_threshold doit être entre 0 (exclus) et 1.0")
    if consecutive_pause_secs is not None and not (60 <= consecutive_pause_secs <= 86400):
        raise HTTPException(400, "consecutive_pause_secs doit être entre 60 et 86400 (1 min — 24h)")
    changed = {}
    mapping = {
        "consecutive_loss_limit":  consecutive_loss_limit,
        "slot_daily_dd_limit":     slot_daily_dd_limit,
        "win_rate_floor":          win_rate_floor,
        "volatility_threshold":    volatility_threshold,
        "consecutive_pause_secs":  consecutive_pause_secs,
    }
    for key, val in mapping.items():
        if val is not None:
            state.cfg.setdefault("risk", {})[key] = val
            changed[key] = val
            # Applique à chaud sur le RiskManager
            if state.trader and hasattr(state.trader.risk, f"_{key}"):
                setattr(state.trader.risk, f"_{key}", val)

    try:
        _save_yaml(lambda d: d.setdefault("risk", {}).update(changed))
        saved = True
    except Exception as e:
        logger.warning(f"[config/risk] sauvegarde YAML KO : {e}")
        saved = False
    return {"changed": changed, "saved_to_disk": saved,
            "trader_updated": state.trader is not None}


# ── POST /api/config/strategy-params ─────────────────────────────────────

@router.get("/api/config/strategy-overrides", dependencies=[Depends(verify_api_key)])
def strategy_overrides(strategy: str):
    """Liste les overrides ``optimizer_results[tf][symbole]`` d'une stratégie
    (cf. UI-02). Une entrée héritée (sans dimension symbole) est présentée
    comme la config de ``DEFAULT_CONFIG_SYMBOL`` (BTC/USDC). Fournit aussi la
    liste des symboles configurés (scanner.symbols) pour les sélecteurs UI."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")
    from app.live.utils import DEFAULT_CONFIG_SYMBOL, _is_legacy_tf_entry
    tf_map = (state.cfg.get("optimizer_results") or {}).get(strategy) or {}
    overrides = []
    for tf, entry in tf_map.items():
        if not isinstance(entry, dict):
            continue
        if _is_legacy_tf_entry(entry):
            overrides.append({"timeframe": tf, "symbol": DEFAULT_CONFIG_SYMBOL,
                              "legacy": True,
                              "oos_score": entry.get("oos_score"),
                              "run_date": entry.get("run_date"),
                              "params": entry.get("params", {})})
        else:
            for sym, sub in entry.items():
                if isinstance(sub, dict):
                    overrides.append({"timeframe": tf, "symbol": sym,
                                      "legacy": False,
                                      "oos_score": sub.get("oos_score"),
                                      "run_date": sub.get("run_date"),
                                      "params": sub.get("params", {})})
    symbols = list((state.cfg.get("scanner") or {}).get("symbols") or [])
    return {"strategy": strategy, "overrides": overrides, "symbols": symbols,
            "base_params": (state.cfg.get("strategy_params") or {}).get(strategy, {})}


@router.post("/api/config/strategy-params", dependencies=[Depends(verify_api_key)])
def update_strategy_params(strategy: str, params: dict,
                           timeframe: str = None, symbol: str = None):
    """Sauvegarde les paramètres d'une stratégie.

    - Sans ``timeframe``/``symbol`` : comportement historique inchangé — écrit
      le bloc ``params:`` de base de strategies/{strategy}.yaml.
    - Avec ``timeframe`` ET ``symbol`` (cf. UI-02) : écrit un OVERRIDE dans
      ``optimizer_results[tf][symbole]`` via ``apply_best_params`` (schéma
      per-symbole canonique — une entrée héritée est migrée vers BTC/USDC,
      les autres symboles restent intacts). Le ``oos_score`` existant de
      l'entrée est préservé (édition manuelle ≠ nouvelle optimisation).
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")
    if (timeframe is None) != (symbol is None):
        raise HTTPException(400, "timeframe et symbol doivent être fournis ensemble")

    if timeframe and symbol:
        from app.engine.opt_persistence import apply_best_params
        from app.live.utils import _select_symbol_entry
        tf_entry = ((state.cfg.get("optimizer_results") or {})
                    .get(strategy, {}).get(timeframe) or {})
        prev = _select_symbol_entry(tf_entry, symbol) if isinstance(tf_entry, dict) else None
        prev_score = float((prev or {}).get("oos_score") or 0.0)
        ok = apply_best_params(strategy, params, "config.yaml",
                               timeframe=timeframe, oos_score=prev_score,
                               symbol=symbol)
        if not ok:
            raise HTTPException(500, "Erreur écriture override")
        # Mise à jour à chaud de la config en mémoire (même schéma que le YAML).
        opt = state.cfg.setdefault("optimizer_results", {}).setdefault(strategy, {})
        cur = opt.get(timeframe)
        from app.live.utils import DEFAULT_CONFIG_SYMBOL, _is_legacy_tf_entry
        if isinstance(cur, dict) and _is_legacy_tf_entry(cur):
            cur = {DEFAULT_CONFIG_SYMBOL: cur}
        elif not isinstance(cur, dict):
            cur = {}
        cur[symbol] = {"oos_score": prev_score, "params": dict(params)}
        opt[timeframe] = cur
        if state.trader:
            try:
                state.trader.reload_active_strategies()
            except Exception as e:
                logger.warning(f"[config] reload après override KO : {e}")
        return {"saved": True, "strategy": strategy, "timeframe": timeframe,
                "symbol": symbol, "params": params,
                "file": f"strategies/{strategy}.yaml", "scope": "override"}

    state.cfg.setdefault("strategy_params", {})[strategy] = params
    if state.trader:
        state.trader.strat_params = state.cfg["strategy_params"]
    try:
        def _upd(data):
            data["params"] = params
        _save_strategy_yaml(strategy, _upd)
        return {"saved": True, "strategy": strategy, "params": params,
                "file": f"strategies/{strategy}.yaml", "scope": "base"}
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} config/strategy-params : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")

@router.post("/api/config/strategy-timeframe", dependencies=[Depends(verify_api_key)])
def toggle_strategy_timeframe(strategy: str, timeframe: str, enabled: bool = True):
    """
    Active ou désactive une stratégie sur un timeframe spécifique.
    Fonctionne via strategy_params[strategy]["disabled_timeframes"].
    Appliqué à chaud sur le trader si disponible.
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")
    allowed_tfs = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}
    if timeframe not in allowed_tfs:
        raise HTTPException(400, f"Timeframe invalide : {timeframe}")

    sp = state.cfg.setdefault("strategy_params", {}).setdefault(strategy, {})
    disabled: list = list(sp.get("disabled_timeframes", []))

    if enabled:
        disabled = [tf for tf in disabled if tf != timeframe]
    else:
        if timeframe not in disabled:
            disabled.append(timeframe)

    sp["disabled_timeframes"] = disabled

    # Propagation à chaud sur la stratégie chargée
    trader_updated = False
    if state.trader:
        strat_obj = getattr(state.trader, "_loaded_strategies", {}).get(strategy)
        if strat_obj and hasattr(strat_obj, "disabled_timeframes"):
            strat_obj.disabled_timeframes = list(disabled)
            trader_updated = True

    try:
        def _upd(data):
            data.setdefault("params", {})["disabled_timeframes"] = disabled
        _save_strategy_yaml(strategy, _upd)
        saved = True
    except Exception as e:
        saved = False
        logger.warning(f"[config/strategy-timeframe] save KO: {e}")

    return {
        "strategy":           strategy,
        "timeframe":          timeframe,
        "enabled":            enabled,
        "disabled_timeframes": disabled,
        "trader_updated":     trader_updated,
        "saved_to_disk":      saved,
    }


# ── GET /api/backtest/settings ────────────────────────────────────────────

@router.get("/api/backtest/settings")
def backtest_settings():
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    all_strats = sorted(_discover_strategies())
    return {
        "timeframe":            state.cfg["trading"].get("timeframe", "1h"),
        "timeframes":           state.cfg["trading"].get("timeframes", ["1h"]),
        # Harmonisé : les TFs sélectionnables suivent la config à chaud (trading.timeframes).
        "available_timeframes": state.cfg["trading"].get("timeframes", ["1h"]),
        "strategies":           state.cfg["strategies"]["enabled"],
        "all_strategies":       all_strats,
        "strategy_params":      state.cfg.get("strategy_params", {}),
        "score_threshold":      state.cfg["trading"].get("score_threshold", 0.55),
        "taker_fee":            state.cfg["trading"].get("taker_fee", 0.001),
        "maker_fee":            state.cfg["trading"].get("maker_fee", 0.0004),
        "capital":              state.cfg["trading"]["capital"],
        "risk_per_trade":       state.cfg["trading"]["risk_per_trade"],
        "spread_pct":           state.cfg.get("backtest", {}).get("spread_pct", 0.0005),
        "partial_fill_pct":     state.cfg.get("backtest", {}).get("partial_fill_pct", 0.95),
    }


# ── GET /api/config/changelog ─────────────────────────────────────────────

@router.get("/api/config/changelog")
def get_changelog(limit: int = 50):
    """Retourne les N dernières entrées du changelog d'optimisation."""
    import json as _json
    import os as _os
    changelog_path = _os.path.join(
        _os.path.dirname(_os.path.abspath("config.yaml")),
        "optimizer_changelog.json"
    )
    try:
        with open(changelog_path, "r", encoding="utf-8") as f:
            log = _json.load(f)
        log = list(reversed(log))[:max(1, min(limit, 500))]
        return log
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning(f"[changelog] lecture KO : {e}")
        return []


# ── GET /api/config/notifications ─────────────────────────────────────────

@router.get("/api/config/notifications", dependencies=[Depends(verify_api_key)])
def get_notifications_config():
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    notif = dict(state.cfg.get("notifications", {}))

    def _mask(token: str) -> str:
        if not token:
            return ""
        return token[:4] + "****" + token[-3:] if len(token) > 8 else "****"

    sensitive = ["telegram_bot_token", "twilio_auth_token", "twilio_account_sid",
                 "whatsapp_token", "email_password"]
    for k in sensitive:
        if k in notif and notif[k]:
            notif[k] = _mask(str(notif[k]))
    return notif


# ── POST /api/config/notifications ────────────────────────────────────────

@router.post("/api/config/notifications", dependencies=[Depends(verify_api_key)])
def update_notifications_config(
    telegram_enabled:       bool  = None,
    telegram_bot_token:     str   = None,
    telegram_chat_id:       str   = None,
    whatsapp_enabled:       bool  = None,
    whatsapp_number:        str   = None,
    whatsapp_token:         str   = None,
    email_enabled:          bool  = None,
    email_smtp:             str   = None,
    email_port:             int   = None,
    email_user:             str   = None,
    email_password:         str   = None,
    email_to:               str   = None,
    min_pnl_to_notify:      float = None,
    position_loss_warn_pct: float = None,
):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    notif = state.cfg.setdefault("notifications", {})
    mapping = {
        "telegram_enabled":       telegram_enabled,
        "telegram_bot_token":     telegram_bot_token,
        "telegram_chat_id":       telegram_chat_id,
        "whatsapp_enabled":       whatsapp_enabled,
        "whatsapp_number":        whatsapp_number,
        "whatsapp_token":         whatsapp_token,
        "email_enabled":          email_enabled,
        "email_smtp":             email_smtp,
        "email_port":             email_port,
        "email_user":             email_user,
        "email_password":         email_password,
        "email_to":               email_to,
        "min_pnl_to_notify":      min_pnl_to_notify,
        "position_loss_warn_pct": position_loss_warn_pct,
    }
    changed = {k: v for k, v in mapping.items() if v is not None}
    notif.update(changed)

    if state.trader:
        from app.core.notifications import Notifier
        state.trader.notif = Notifier(state.cfg)
        state.trader.risk.attach_notifier(state.trader.notif)

    try:
        def _upd(d):
            n = d.setdefault("notifications", {})
            n.update({k: v for k, v in changed.items()
                      if "password" not in k and "token" not in k})
            for k in ["telegram_bot_token", "whatsapp_token", "email_password"]:
                if k in changed:
                    n[k] = changed[k]
        _save_yaml(_upd)
        saved = True
    except Exception as e:
        logger.warning(f"[config/notifications] sauvegarde YAML KO : {e}")
        saved = False
    return {"changed": list(changed.keys()), "saved_to_disk": saved}


# ── POST /api/config/notifications/test ──────────────────────────────────

@router.post("/api/config/notifications/test", dependencies=[Depends(verify_api_key)])
def test_notification():
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    state.trader.notif.send("🔔 Test de notification depuis le bot", async_=False)
    return {"status": "sent"}


# ── POST /api/config/margin ────────────────────────────────────────────────

@router.post("/api/config/margin", dependencies=[Depends(verify_api_key)])
def update_margin_config(
    margin:       bool = None,
    margin_mode:  str  = None,
    max_leverage: int  = None,
):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    if margin_mode is not None and margin_mode not in ("isolated", "cross"):
        raise HTTPException(400, "margin_mode doit être 'isolated' ou 'cross'")
    if max_leverage is not None and not (1 <= max_leverage <= 125):
        raise HTTPException(400, "max_leverage doit être entre 1 et 125")
    if margin is not None:
        state.cfg["exchange"]["margin"]      = margin
    if margin_mode is not None:
        state.cfg["trading"]["margin_mode"]  = margin_mode
    if max_leverage is not None:
        state.cfg["trading"]["max_leverage"] = max_leverage
    try:
        def _upd(d):
            if margin is not None:
                d.setdefault("exchange", {})["margin"]      = margin
            if margin_mode is not None:
                d.setdefault("trading", {})["margin_mode"]  = margin_mode
            if max_leverage is not None:
                d.setdefault("trading", {})["max_leverage"] = max_leverage
        _save_yaml(_upd)
        saved = True
    except Exception as e:
        logger.warning(f"[config/margin] sauvegarde YAML KO : {e}")
        saved = False
    return {"saved_to_disk": saved}


# ── POST /api/config/capital-allocator ─────────────────────────────────────

@router.post("/api/config/capital-allocator", dependencies=[Depends(verify_api_key)])
def update_capital_allocator_config(
    mode: str = None,
    rebalance_interval: str = None,
    max_slot_pct: float = None,
):
    """
    Met à jour la configuration du capital allocator.
    mode: 'equal', 'manual' ou 'performance'
    rebalance_interval: 'daily', 'weekly' ou 'never'
    max_slot_pct: fraction max par slot (0.0-1.0)
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    valid_modes = ("equal", "manual", "performance")
    if mode is not None and mode not in valid_modes:
        raise HTTPException(400, f"mode doit être parmi : {valid_modes}")
    valid_intervals = ("daily", "weekly", "never")
    if rebalance_interval is not None and rebalance_interval not in valid_intervals:
        raise HTTPException(400, f"rebalance_interval doit être parmi : {valid_intervals}")
    if max_slot_pct is not None and not (0.0 < max_slot_pct <= 1.0):
        raise HTTPException(400, "max_slot_pct doit être entre 0.01 et 1.0")

    state.cfg.setdefault("capital_allocator", {})
    if mode is not None:
        state.cfg["capital_allocator"]["mode"] = mode
        if state.trader and state.trader.allocator:
            state.trader.allocator.set_mode(mode)
    if rebalance_interval is not None:
        state.cfg["capital_allocator"]["rebalance_interval"] = rebalance_interval
        if state.trader and state.trader.allocator:
            state.trader.allocator.set_rebalance_interval(rebalance_interval)
    if max_slot_pct is not None:
        state.cfg["capital_allocator"]["max_slot_pct"] = max_slot_pct
        if state.trader and state.trader.allocator:
            state.trader.allocator.set_max_slot_pct(max_slot_pct)

    try:
        def _upd(d):
            d.setdefault("capital_allocator", {})
            if mode is not None:
                d["capital_allocator"]["mode"] = mode
            if rebalance_interval is not None:
                d["capital_allocator"]["rebalance_interval"] = rebalance_interval
            if max_slot_pct is not None:
                d["capital_allocator"]["max_slot_pct"] = max_slot_pct
        _save_yaml(_upd)
        saved = True
    except Exception as e:
        logger.warning(f"[config/capital-allocator] sauvegarde YAML KO : {e}")
        saved = False
    return {"saved_to_disk": saved, "capital_allocator": state.cfg["capital_allocator"]}
