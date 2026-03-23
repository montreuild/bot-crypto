"""
Routes de configuration — gestion de config.yaml via l'API.

Endpoints :
  GET  /api/config
  POST /api/config/strategies
  POST /api/config/timeframes
  POST /api/config/auto-optimizer
  POST /api/config/trading
  POST /api/config/risk
  POST /api/config/strategy-params
  GET  /api/backtest/settings
  GET  /api/config/changelog
  GET  /api/config/notifications
  POST /api/config/notifications
  POST /api/config/notifications/test
  POST /api/config/margin
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key, _discover_strategies

logger = logging.getLogger(__name__)
router = APIRouter()


def _save_yaml(updates_fn):
    """Applique updates_fn(disk_cfg) puis réécrit config.yaml de façon thread-safe."""
    import yaml as _yaml
    with state._config_write_lock:
        with open("config.yaml", "r", encoding="utf-8") as f:
            disk_cfg = _yaml.safe_load(f) or {}
        updates_fn(disk_cfg)
        with open("config.yaml", "w", encoding="utf-8") as f:
            _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── GET /api/config ────────────────────────────────────────────────────────

@router.get("/api/config")
def get_config():
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    all_strats = sorted(_discover_strategies())
    safe = {k: v for k, v in state.cfg.items() if k not in ("exchange", "notifications")}
    safe["exchange"]           = {"name": state.cfg["exchange"]["name"]}
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
    try:
        _save_yaml(lambda d: d.setdefault("strategies", {}).__setitem__("enabled", strat_list))
        result["saved_to_disk"] = True
    except Exception as e:
        result["save_error"] = str(e)
    return result


# ── POST /api/config/timeframes ───────────────────────────────────────────

@router.post("/api/config/timeframes", dependencies=[Depends(verify_api_key)])
def update_timeframes(timeframes: str = "1h"):
    """Met à jour les timeframes actifs. timeframes = CSV ex: '5m,1h,4h'"""
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
    except Exception:
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
    except Exception:
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
            # Apply à chaud sur le RiskManager
            if state.trader and hasattr(state.trader.risk, f"_{key}"):
                setattr(state.trader.risk, f"_{key}", val)

    try:
        _save_yaml(lambda d: d.setdefault("risk", {}).update(changed))
        saved = True
    except Exception:
        saved = False
    return {"changed": changed, "saved_to_disk": saved,
            "trader_updated": state.trader is not None}


# ── POST /api/config/strategy-params ─────────────────────────────────────

@router.post("/api/config/strategy-params", dependencies=[Depends(verify_api_key)])
def update_strategy_params(strategy: str, params: dict):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")
    state.cfg.setdefault("strategy_params", {})[strategy] = params
    if state.trader:
        state.trader.strat_params = state.cfg["strategy_params"]
    try:
        def _upd(d):
            d.setdefault("strategy_params", {})[strategy] = params
        _save_yaml(_upd)
        return {"saved": True, "strategy": strategy, "params": params}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── GET /api/backtest/settings ────────────────────────────────────────────

@router.get("/api/backtest/settings")
def backtest_settings():
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    all_strats = sorted(_discover_strategies())
    return {
        "timeframe":            state.cfg["trading"].get("timeframe", "1h"),
        "timeframes":           state.cfg["trading"].get("timeframes", ["1h"]),
        "available_timeframes": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"],
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
        with open(changelog_path, "r") as f:
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
    except Exception:
        saved = False
    return {"changed": list(changed.keys()), "saved_to_disk": saved}


# ── POST /api/config/notifications/test ──────────────────────────────────

@router.post("/api/config/notifications/test", dependencies=[Depends(verify_api_key)])
def test_notification():
    if not state.trader:
        raise HTTPException(503, "Trader non initialisé")
    state.trader.notif.send("🔔 Test de notification depuis le bot V11", async_=False)
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
    except Exception:
        saved = False
    return {"saved_to_disk": saved}
