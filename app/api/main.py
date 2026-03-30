"""API FastAPI du Crypto Bot — point d'entrée, middlewares, pages web et status."""
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.api import state
from app.api.helpers import CleanJSONResponse
from app.api.routes import config, trades, backtest, scanner, optimizer, bot, ml, replay
from app.core.database import init_db

logger = logging.getLogger(__name__)

# ── Rate limiter ───────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

# ── Application ────────────────────────────────────────────────────────────
app = FastAPI(
    title="Crypto Bot",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    description="API de trading algorithmique multi-stratégies. Tous les endpoints protégés exigent `X-API-Key`.",
    default_response_class=CleanJSONResponse,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",      "http://127.0.0.1",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:8001", "http://127.0.0.1:8001",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ── HTTPS redirect (if configured) ────────────────────────────────────────
@app.middleware("http")
async def https_redirect(request: Request, call_next):
    """Redirect HTTP to HTTPS in production if FORCE_HTTPS env var is set."""
    if os.environ.get("FORCE_HTTPS", "").lower() in ("1", "true", "yes"):
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "http":
            url = request.url.replace(scheme="https")
            from starlette.responses import RedirectResponse
            return RedirectResponse(url=str(url), status_code=301)
    return await call_next(request)

# ── Templates ──────────────────────────────────────────────────────────────
try:
    _tpl_path = os.path.join(os.path.dirname(__file__), "..", "web", "templates")
    templates = Jinja2Templates(directory=_tpl_path)
except Exception as e:
    logger.warning(f"[API] Chargement templates KO : {e}")
    templates = None


# ── Initialisation ─────────────────────────────────────────────────────────
def init_app(config: dict, live_trader=None):
    """Injecte la config et le trader dans l'état partagé au démarrage."""
    state.cfg    = config
    state.trader = live_trader
    _, state.SessionLocal = init_db(config["database"]["url"])


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
    api_key = state.cfg["web"].get("api_key", "") if state.cfg else ""
    ctx = {"request": request, "api_key": api_key, **(extra or {})}
    if templates:
        return templates.TemplateResponse(name, ctx)
    return HTMLResponse(f"<h1>{name}</h1>")


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

@app.get("/strategies", response_class=HTMLResponse)
def strategies_page(request: Request):
    return _tpl("strategies.html", request, {"active_page": "strategies"})

@app.get("/slots", response_class=HTMLResponse)
def slots_page(request: Request):
    return _tpl("slots.html", request, {"active_page": "slots"})

@app.get("/replay", response_class=HTMLResponse)
def replay_page(request: Request):
    return _tpl("replay.html", request, {"active_page": "replay"})

@app.get("/ml", response_class=HTMLResponse)
def ml_page(request: Request):
    return _tpl("ml.html", request, {"active_page": "ml"})


# ── Status (accès direct à state.cfg, hors router) ────────────────────────
@app.get("/api/status")
def get_status(request: Request):
    if not state.cfg:
        return {"status": "not_started"}
    api_key_cfg  = state.cfg["web"].get("api_key", "")
    token        = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    authenticated = not api_key_cfg or token == api_key_cfg
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
app.include_router(config.router)
app.include_router(trades.router)
app.include_router(backtest.router)
app.include_router(scanner.router)
app.include_router(optimizer.router)
app.include_router(bot.router)
app.include_router(ml.router)
app.include_router(replay.router)
