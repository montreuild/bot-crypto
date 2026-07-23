"""API FastAPI du Crypto Bot — point d'entrée, pages web et status.

Les middlewares (CORS, GZIP, SlowAPI, HTTPS redirect) et les exception
handlers globaux sont désormais dans :mod:`app.api.middleware` et enregistrés
via :func:`setup_middleware` (refactoring ARCH-014).
"""
import hmac
import logging
import os

# phase6-sklearn-removal : plus de warning sklearn à filtrer (sklearn supprimé
# du repo). Le bloc try/except qui suivait est retiré.
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import state
from app.api.helpers import CleanJSONResponse
from app.api.middleware import setup_middleware
from app.api.routes import (
    audit_log,
    backtest,
    bot,
    config_global,
    config_notifications,
    config_risk,
    config_strategies,
    data,
    derivatives,
    ml,
    optimizer,
    portfolio,
    replay,
    scanner,
    trades,
    ws,
)
from app.core.database import init_db
from app.core.events import event_hub

logger = logging.getLogger(__name__)

# ── Application ────────────────────────────────────────────────────────────
@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Lifespan : lie la loop async au hub d'événements au démarrage."""
    import asyncio
    event_hub.set_loop(asyncio.get_running_loop())
    logger.info("[API] Event hub loop liée — WebSocket prêt")
    yield

app = FastAPI(
    title="Crypto Bot",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    description=(
        "API de trading algorithmique multi-stratégies. Tous les endpoints protégés "
        "exigent `X-API-Key`. Endpoint WebSocket temps réel sur `/ws`."
    ),
    default_response_class=CleanJSONResponse,
    lifespan=_lifespan,
)

# ``app.state.limiter`` doit être posé AVANT ``setup_middleware`` car
# ``SlowAPIMiddleware`` le lit à l'enregistrement.
app.state.limiter = state.limiter
setup_middleware(app)

# ── Templates ──────────────────────────────────────────────────────────────
try:
    _tpl_path = os.path.join(os.path.dirname(__file__), "..", "web", "templates")
    templates = Jinja2Templates(directory=_tpl_path)
except Exception as e:
    logger.warning(f"[API] Chargement templates KO : {e}")
    templates = None

# ── Static (JS/CSS partagés entre templates — UI-05) ──────────────────────
try:
    _static_path = os.path.join(os.path.dirname(__file__), "..", "web", "static")
    app.mount("/static", StaticFiles(directory=_static_path), name="static")
except Exception as e:
    logger.warning(f"[API] Montage /static KO : {e}")


# ── Initialisation ─────────────────────────────────────────────────────────
def init_app(config: dict, live_trader=None):
    """Injecte la config et le trader dans l'état partagé au démarrage."""
    state.cfg    = config
    state.trader = live_trader
    _engine, state.SessionLocal = init_db(config["database"]["url"])
    # Initialise la table d'audit (crée la table si absente)
    from app.core.audit_log import _init_audit_db
    _init_audit_db(_engine)


# ── Health check (sans auth) ───────────────────────────────────────────────
@app.get("/health")
def health_check():
    """Santé du service (pas d'auth requise)."""
    db_ok       = state.SessionLocal is not None
    exchange_ok = state.cfg is not None
    return {
        "status":   "ok" if (db_ok and exchange_ok) else "degraded",
        "db":       db_ok,
        "exchange": exchange_ok,
        "trader":   state.trader is not None and getattr(state.trader, "running", False),
    }


# ── Pages web ──────────────────────────────────────────────────────────────
def _tpl(name: str, request: Request, extra: dict = None):
    ctx = {"request": request, **(extra or {})}
    if templates:
        resp = templates.TemplateResponse(name, ctx)
    else:
        resp = HTMLResponse(f"<h1>{name}</h1>")
    api_key = state.cfg["web"].get("api_key", "") if state.cfg else ""
    if api_key:
        # honour X-Forwarded-Proto for reverse-proxy deployments
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        resp.set_cookie(
            key="api_key",
            value=api_key,
            httponly=True,
            samesite="strict",
            secure=proto == "https",
        )
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return _tpl("dashboard.html", request, {"active_page": "dashboard"})

@app.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request):
    return _tpl("backtest.html", request, {"active_page": "backtest"})

@app.get("/optimizer", response_class=HTMLResponse)
def optimizer_page(request: Request):
    return _tpl("optimizer.html", request, {"active_page": "optimizer"})

@app.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    return _tpl("config.html", request, {"active_page": "config"})

@app.get("/scanner", response_class=HTMLResponse)
def scanner_page(request: Request):
    return _tpl("scanner.html", request, {"active_page": "scanner"})

@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    return _tpl("audit.html", request, {"active_page": "audit"})

@app.get("/trades", response_class=HTMLResponse)
def trades_page(request: Request):
    return _tpl("trades.html", request, {"active_page": "trades"})

@app.get("/slots")
def slots_page():
    # Fusionnée dans « Mes Bots » : on redirige les anciens liens/favoris.
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/bots", status_code=307)

@app.get("/replay", response_class=HTMLResponse)
def replay_page(request: Request):
    return _tpl("replay.html", request, {"active_page": "replay"})

@app.get("/ml", response_class=HTMLResponse)
def ml_page(request: Request):
    return _tpl("ml.html", request, {"active_page": "ml"})

@app.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    return _tpl("compare.html", request, {"active_page": "compare"})

@app.get("/derivatives", response_class=HTMLResponse)
def derivatives_page(request: Request):
    return _tpl("derivatives.html", request, {"active_page": "derivatives"})

# ── Pages Phase 4 (portefeuille de bots autonomes) ────────────────────────
@app.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request):
    return _tpl("portfolio.html", request, {"active_page": "portfolio"})

@app.get("/bots", response_class=HTMLResponse)
def bots_page(request: Request):
    return _tpl("bots.html", request, {"active_page": "bots"})

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _tpl("settings.html", request, {"active_page": "settings"})

@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request):
    return _tpl("data.html", request, {"active_page": "data"})

@app.get("/smartgraph", response_class=HTMLResponse)
def smartgraph_page(request: Request):
    return _tpl("smartgraph.html", request, {"active_page": "smartgraph"})

@app.get("/smartreplay", response_class=HTMLResponse)
def smartreplay_page(request: Request):
    return _tpl("smartreplay.html", request, {"active_page": "smartreplay"})


# ── Status (accès direct à state.cfg, hors router) ────────────────────────
@app.get("/api/status")
def get_status(request: Request):
    if not state.cfg:
        return {"status": "not_started"}
    api_key_cfg  = state.cfg["web"].get("api_key", "")
    token        = request.headers.get("X-API-Key") or request.cookies.get("api_key") or ""
    authenticated = not api_key_cfg or hmac.compare_digest(token, api_key_cfg)
    base = {
        "status":     "running" if (state.trader and state.trader.running) else "idle",
        "paper_mode": state.cfg["trading"].get("paper_mode", True),
        "timeframe":  state.cfg["trading"].get("timeframe", "1h"),
        "timeframes": state.cfg["trading"].get(
            "timeframes", [state.cfg["trading"].get("timeframe", "1h")]
        ),
        "strategies": state.cfg["strategies"]["enabled"],
    }
    if authenticated:
        base["capital"] = (state.trader.capital_display
                           if state.trader else state.cfg["trading"]["capital"])
        if state.trader:
            base.update(state.trader.status)
        else:
            base.update({
                "total_pnl":              0.0,
                "total_pnl_pct":          0.0,
                "total_trades":           0,
                "win_rate":               0.0,
                "profit_factor":          0.0,
                "total_fees":             0.0,
                "best_trade":             0.0,
                "positions":              [],
                "by_strategy":            {},
                "signal_log":             [],
                "active_per_tf":          {},
                "circuit_breaker_active": False,
                "circuit_breaker_reason": "",
                "daily_pnl_pct":          0.0,
                "global_dd_pct":          0.0,
                "current_risk":  round(
                    state.cfg["trading"].get("risk_per_trade", 0.01) * 100, 2
                ),
                "daily_dd_limit":    state.cfg["trading"].get("daily_drawdown_limit", 0.05),
                "global_dd_limit":   state.cfg["trading"].get("max_drawdown_global", 0.20),
                "capital_allocation": [],
                "circuit_breakers":   [],
                "slot_states":        [],
                "volatility_brake":   False,
            })
    return base


# ── Inclusion des routers ──────────────────────────────────────────────────
app.include_router(config_global.router)
app.include_router(config_strategies.router)
app.include_router(config_risk.router)
app.include_router(config_notifications.router)
app.include_router(trades.router)
app.include_router(backtest.router)
app.include_router(scanner.router)
app.include_router(optimizer.router)
app.include_router(bot.router)
app.include_router(ml.router)
app.include_router(replay.router)
app.include_router(derivatives.router)
app.include_router(portfolio.router)
app.include_router(data.router)
app.include_router(ws.router)  # WebSocket temps réel + /api/ws/status
app.include_router(audit_log.router)  # Journal d'audit /api/audit/log
