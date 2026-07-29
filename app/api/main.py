"""API FastAPI du Crypto Bot — point d'entrée, pages web et status.

Les middlewares (CORS, GZIP, SlowAPI, HTTPS redirect) et les exception
handlers globaux sont désormais dans :mod:`app.api.middleware` et enregistrés
via :func:`setup_middleware` (refactoring ARCH-014).

⚠ S6-09 (29/07/2026) — FIN JINJA2 :
Le frontend Next.js (`frontend/`) est désormais le frontend officiel unique.
Les templates Jinja2 (`app/web/templates/`) ont été SUPPRIMÉS physiquement
(cf. `docs/FIN_JINJA2.md`). Les anciennes routes HTML (`GET /dashboard`,
`GET /backtest`, etc.) renvoient maintenant un **redirect 308 permanent**
vers le frontend Next.js (port 3000 ou proxy nginx).

L'API REST (`/api/*`) est INTACTE — aucune cassure pour les consommateurs
API. Seules les routes HTML ont été remplacées par des redirects.

Configuration du frontend cible via env var `FRONTEND_URL` (défaut
`http://localhost:3000`).
"""
import hmac
import logging
import os

# phase6-sklearn-removal : plus de warning sklearn à filtrer (sklearn supprimé
# du repo). Le bloc try/except qui suivait est retiré.
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

# S6-09 : Jinja2Templates et StaticFiles SUPPRIMÉS — fin Jinja2.
# Le frontend Next.js (frontend/) sert maintenant tout le HTML/CSS/JS.

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

# S6-09 : URL du frontend Next.js (configurable via env).
# En prod : proxy nginx sert le build Next.js statique et proxie /api/*.
# En dev : Next.js tourne sur :3000, FastAPI sur :8000.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")


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
        "exigent `X-API-Key`. Endpoint WebSocket temps réel sur `/ws`. "
        "⚠ Frontend Next.js sur " + FRONTEND_URL + " (Jinja2 supprimé S6-09)."
    ),
    default_response_class=CleanJSONResponse,
    lifespan=_lifespan,
)

# ``app.state.limiter`` doit être posé AVANT ``setup_middleware`` car
# ``SlowAPIMiddleware`` le lit à l'enregistrement.
app.state.limiter = state.limiter
setup_middleware(app)


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


# ── Métriques Prometheus (OBS-01, sans auth) ──────────────────────────────
@app.get("/metrics")
def prometheus_metrics():
    """Exposition Prometheus au format texte.

    Sans authentification, comme ``/health`` : un scrapeur Prometheus ne sait
    pas porter d'en-tête ``X-API-Key`` sans configuration supplémentaire, et
    l'endpoint ne divulgue aucun secret. Il divulgue en revanche l'activité de
    trading (capital, positions, PnL) — **à restreindre au réseau
    d'administration côté nginx**, comme les autres endpoints d'exploitation.
    """
    from starlette.responses import Response

    from app.core.metrics import render
    payload, content_type = render()
    if payload is None:
        return CleanJSONResponse(
            status_code=503,
            content={"detail": "prometheus-client absent — "
                               "pip install prometheus-client"},
        )
    return Response(content=payload, media_type=content_type)


# ── Redirects HTML vers le frontend Next.js (S6-09 — fin Jinja2) ───────────
# Toutes les anciennes routes HTML renvoient un 308 (permanent) vers la
# page Next.js correspondante. Les bookmarks sont préservés, et les
# moteurs de recherche ignorent les anciennes URL.
#
# En production, nginx peut servir le build Next.js statiquement et
# proxier /api/* vers FastAPI. En dev, Next.js tourne sur :3000 (npm run dev)
# et FastAPI sur :8000 (python cli.py).

# Cookie api_key (HttpOnly) — posé par le frontend Next.js via /api/auth/login
# (cf. frontend/src/lib/api.ts). Plus de cookie posé par l'API sur les pages
# HTML (puisque l'API ne sert plus de HTML).
def _redirect_to_nextjs(path: str = "/"):
    """Helper pour rediriger vers le frontend Next.js avec 308 permanent."""
    target = f"{FRONTEND_URL}{path}"
    return RedirectResponse(url=target, status_code=308)


# Anciennes routes HTML → redirects 308 vers Next.js (mêmes chemins)
# La liste est exhaustive : 18 routes (la 19e, /slots, redirigeait déjà vers /bots)
HTML_ROUTES_TO_REDIRECT = [
    "/", "/backtest", "/optimizer", "/config", "/scanner", "/audit",
    "/audit-log", "/trades", "/replay", "/ml", "/models", "/compare",
    "/derivatives", "/portfolio", "/bots", "/settings", "/data",
    "/smartgraph", "/smartreplay",
]
for _route in HTML_ROUTES_TO_REDIRECT:
    # Capture _route via default arg pour éviter le piège du closure tardif
    def _make_redirect(r):
        def _redirect(request: Request, _r=r):
            return _redirect_to_nextjs(_r)
        return _redirect
    app.add_api_route(_route, _make_redirect(_route), methods=["GET"])


# Rétro-compat : /slots redirigeait vers /bots (déjà en 307, gardé en 308)
@app.get("/slots")
def slots_legacy():
    return _redirect_to_nextjs("/bots")


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
