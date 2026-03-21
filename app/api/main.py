"""
API FastAPI — V11 (Multi-Timeframe + CandleStore)

Nouveautés V11 :
  - CandleStore : données OHLCV persistées en Parquet par paire/TF
  - /api/candles/stats GET : statistiques du cache local
  - Backtest, optimizer et ML training utilisent le cache local
    → moins de requêtes exchange, historique accumulé automatiquement

Structure des routes :
  api/routes/config.py    — /api/config, /api/config/*
  api/routes/trades.py    — /api/trades, /api/stats, /api/risk
  api/routes/backtest.py  — /api/backtest
  api/routes/scanner.py   — /api/scanner, /api/scanner/*
  api/routes/optimizer.py — /api/optimize/*
  api/routes/bot.py       — /api/bot/start, /api/bot/stop
  api/routes/ml.py        — /api/ml/*, /api/candles/stats
"""
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api import state
from app.api.helpers import CleanJSONResponse
from app.api.routes import config, trades, backtest, scanner, optimizer, bot, ml
from app.core.database import init_db

logger = logging.getLogger(__name__)

# ── Application ────────────────────────────────────────────────────────────
app = FastAPI(
    title="Crypto Bot V11",
    version="11.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    description=(
        "API de trading algorithmique multi-stratégies. "
        "Tous les endpoints protégés exigent l'en-tête `X-API-Key`."
    ),
    default_response_class=CleanJSONResponse,
)

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

# ── Templates ──────────────────────────────────────────────────────────────
try:
    _tpl_path = os.path.join(os.path.dirname(__file__), "..", "web", "templates")
    templates = Jinja2Templates(directory=_tpl_path)
except Exception:
    templates = None


# ── Initialisation ─────────────────────────────────────────────────────────
def init_app(config: dict, live_trader=None):
    """Appelé au démarrage pour injecter la config et le trader dans l'état partagé."""
    state.cfg    = config
    state.trader = live_trader
    _, state.SessionLocal = init_db(config["database"]["url"])


# ── Health check (sans auth) ───────────────────────────────────────────────
@app.get("/health")
def health_check():
    """Vérification de l'état du service (pas d'auth requise)."""
    db_ok       = state.SessionLocal is not None
    exchange_ok = state.cfg is not None
    return {
        "status":   "ok" if (db_ok and exchange_ok) else "degraded",
        "version":  "11.0.0",
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


# ── Status (pas dans un router pour garder l'accès à state.cfg direct) ────
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
        "version":    "11.0.0",
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
                "daily_dd_limit":  state.cfg["trading"].get("daily_drawdown_limit", 0.05),
                "global_dd_limit": state.cfg["trading"].get("max_drawdown_global", 0.20),
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
