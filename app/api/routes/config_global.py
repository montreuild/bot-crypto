"""Routes config GLOBALE — split ARCH-013 de config.py (684 lignes → 4 routers).

Endpoints : GET /api/config, POST /api/config/{trading,margin},
GET /api/backtest/settings, GET /api/config/changelog.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api import state
from app.api.helpers import _discover_strategies, verify_api_key
from app.api.routes._config_helpers import _save_yaml
from app.api.schemas import MarginConfigBody, TradingParamsBody
from app.core.config import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.core.risk_envelope import trade_risk_pct as _trade_risk_pct
from app.core.risk_gate import _default_venue_capital

logger = logging.getLogger(__name__)
router = APIRouter()


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
    from app.engine.optimizer_search import RECOMMENDED_LIMIT, STRATEGY_TIMEFRAMES
    safe["strategy_timeframes"] = STRATEGY_TIMEFRAMES
    safe["recommended_limits"]  = RECOMMENDED_LIMIT
    if state.trader:
        safe["_auto_opt_enabled"]    = state.trader._auto_opt_enabled
        safe["_auto_opt_interval_h"] = state.trader._auto_opt_interval // 3600
        safe["_auto_opt_next_run"]   = state.trader._auto_opt_next_run
        safe["_active_per_tf"] = {tf: [s["name"] for s in v]
                                  for tf, v in state.trader._active_per_tf.items()}
    return safe


@router.post("/api/config/trading", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_trading_params(request: Request, body: TradingParamsBody):
    """SEC-03 — corps JSON validé par ``TradingParamsBody``."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    changed = body.model_dump(exclude_none=True)
    for key, val in changed.items():
        state.cfg["trading"][key] = val
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


@router.post("/api/config/margin", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def update_margin_config(request: Request, body: MarginConfigBody):
    """SEC-03 — corps JSON validé par ``MarginConfigBody``."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    margin = body.margin
    margin_mode = body.margin_mode
    max_leverage = body.max_leverage
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


@router.get("/api/backtest/settings", dependencies=[Depends(verify_api_key)])
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
        "taker_fee":            state.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE),
        "maker_fee":            state.cfg["trading"].get("maker_fee", DEFAULT_MAKER_FEE),
        # S12 : le capital appartient à la venue par défaut, le taux de risque
        # au profil — `trading.capital` / `trading.risk_per_trade` n'existent
        # plus. Les enveloppes détaillées sont servies par /api/risk.
        "capital":              _default_venue_capital(state.cfg),
        "risk_per_trade":       _trade_risk_pct(state.cfg),
        "spread_pct":           state.cfg.get("backtest", {}).get("spread_pct", 0.0005),
        "partial_fill_pct":     state.cfg.get("backtest", {}).get("partial_fill_pct", 0.95),
    }


@router.get("/api/config/changelog", dependencies=[Depends(verify_api_key)])
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
