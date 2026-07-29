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


# ── Routes HTML → redirect 308 vers Next.js OU page d'aide ─────────────────
# S6-09 (29/07/2026) — Fin Jinja2 : le frontend Next.js sert maintenant
# tout le HTML. Les anciennes routes HTML (`/dashboard`, `/backtest`, etc.)
# doivent rediriger vers le frontend Next.js.
#
# ⚠ Mais si le frontend Next.js n'est PAS démarré (cas fréquent en dev :
# l'utilisateur a lancé `python cli.py` mais pas `cd frontend && npm run dev`),
# un 308 vers `localhost:3000` qui ne répond pas provoque un "site inaccessible"
# + warnings uvicorn "Invalid HTTP request received".
#
# Solution : on ping le frontend au démarrage (et toutes les 60s). S'il
# répond, on 308 redirect. S'il ne répond pas, on sert une **page d'aide
# HTML** qui explique comment le démarrer + liens vers /api/docs.
# Ça donne un retour visible à l'utilisateur au lieu d'un "site inaccessible".

import socket
import time as _time

_frontend_reachable_cache: dict = {"ts": 0.0, "ok": False}
_FRONTEND_CHECK_TTL = 60.0  # cache 60s pour éviter un ping par request


def _is_frontend_reachable() -> bool:
    """Ping TCP rapide du frontend Next.js (cache 60s)."""
    now = _time.monotonic()
    if now - _frontend_reachable_cache["ts"] < _FRONTEND_CHECK_TTL:
        return _frontend_reachable_cache["ok"]

    # Parse host + port depuis FRONTEND_URL
    try:
        from urllib.parse import urlparse
        parsed = urlparse(FRONTEND_URL)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except Exception:
        host, port = "localhost", 3000

    ok = False
    try:
        with socket.create_connection((host, port), timeout=1.0):
            ok = True
    except (OSError, socket.timeout):
        ok = False

    _frontend_reachable_cache["ts"] = now
    _frontend_reachable_cache["ok"] = ok
    return ok


def _route_frontend_or_help(path: str):
    """Redirige 308 vers le frontend si joignable, sinon sert la page d'aide."""
    if _is_frontend_reachable():
        return RedirectResponse(url=f"{FRONTEND_URL}{path}", status_code=308)
    # Frontend non joignable : sert la page d'aide HTML (status 200, pas de redirect)
    from starlette.responses import Response
    return Response(
        content=_frontend_help_page_html(path),
        media_type="text/html",
        status_code=200,
    )


def _frontend_help_page_html(path: str) -> str:
    """Renvoie le HTML de la page d'aide (string)."""
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>Bot-Crypto — Frontend non démarré</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 720px;
         margin: 80px auto; padding: 0 24px; color: #1f2937; line-height: 1.6; }}
  h1 {{ color: #256a8c; margin-bottom: 8px; }}
  .badge {{ display: inline-block; padding: 4px 12px; background: #fef3c7;
           color: #92400e; border-radius: 4px; font-size: 12px; font-weight: 600;
           margin-bottom: 16px; }}
  code, pre {{ background: #f4f5f5; padding: 2px 6px; border-radius: 4px;
              font-family: 'JetBrains Mono', monospace; font-size: 13px; }}
  pre {{ padding: 16px; overflow-x: auto; }}
  a {{ color: #256a8c; }}
  a:hover {{ text-decoration: underline; }}
  .step {{ background: #f9fafb; border-left: 3px solid #256a8c;
          padding: 12px 16px; margin: 12px 0; border-radius: 0 6px 6px 0; }}
  .step strong {{ color: #256a8c; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 4px 0; }}
</style></head><body>
<div class="badge">⚠ Frontend Next.js non démarré</div>
<h1>Backend OK, mais le frontend n'est pas accessible</h1>

<p>Le backend FastAPI tourne correctement sur le port <code>8000</code>,
mais le frontend Next.js (port <code>3000</code>) ne répond pas.</p>

<p><strong>Configuration actuelle :</strong>
<code>FRONTEND_URL = {FRONTEND_URL}</code></p>

<h2>Comment démarrer le frontend</h2>

<div class="step">
  <strong>Étape 1 — Ouvrir un nouveau terminal</strong> (garder le bot en route
  dans le premier).
</div>

<div class="step">
  <strong>Étape 2 — Installer les dépendances frontend</strong> (première fois
  seulement) :
  <pre>cd frontend
npm install</pre>
</div>

<div class="step">
  <strong>Étape 3 — Démarrer le serveur de développement Next.js</strong> :
  <pre>npm run dev</pre>
  Le frontend sera accessible sur <a href="http://localhost:3000{path}">http://localhost:3000{path}</a>.
</div>

<h2>Alternatives</h2>

<ul>
  <li><a href="/api/docs"><strong>API Swagger UI</strong></a> — documentation
      interactive de l'API REST (<code>/api/*</code>) sur le port 8000.</li>
  <li><a href="/health"><strong>Health check</strong></a> — statut du bot.</li>
  <li><a href="/api/status"><strong>Status</strong></a> — capital, positions,
      PnL (peut nécessiter <code>X-API-Key</code>).</li>
  <li><strong>Build de production</strong> :
      <pre>cd frontend
npm run build
npm start</pre>
      Next.js sert le build optimisé sur <code>:3000</code>.</li>
  <li><strong>Production avec nginx</strong> : nginx sert le build statique
      Next.js et proxie <code>/api/*</code> vers FastAPI. Voir
      <code>deploy/nginx.conf</code>.</li>
</ul>

<h2>Configurer FRONTEND_URL</h2>

<p>Si le frontend tourne sur une URL différente (ex. en production), définir
la variable d'environnement <code>FRONTEND_URL</code> :</p>
<pre># .env ou shell
FRONTEND_URL=https://bot.mondomaine.com</pre>

<p style="margin-top: 32px; font-size: 12px; color: #94a3b8;">
  Bot-Crypto V12 — S6-09 fin Jinja2 — Page d'aide générée par FastAPI car
  le frontend Next.js n'est pas joignable sur {FRONTEND_URL}.
</p>
</body></html>"""


# Anciennes routes HTML → redirect 308 vers Next.js si joignable, sinon page d'aide
# La liste est exhaustive : 18 routes (la 19e, /slots, redirigeait déjà vers /bots)
HTML_ROUTES_TO_REDIRECT = [
    "/", "/backtest", "/optimizer", "/config", "/scanner", "/audit",
    "/audit-log", "/trades", "/replay", "/ml", "/models", "/compare",
    "/derivatives", "/portfolio", "/bots", "/settings", "/data",
    "/smartgraph", "/smartreplay",
]
for _route in HTML_ROUTES_TO_REDIRECT:
    # Capture _route via default arg pour éviter le piège du closure tardif
    def _make_handler(r):
        def _handler(request: Request, _r=r):
            return _route_frontend_or_help(_r)
        return _handler
    app.add_api_route(_route, _make_handler(_route), methods=["GET"])


# Rétro-compat : /slots redirigeait vers /bots (déjà en 307, gardé en 308)
@app.get("/slots")
def slots_legacy():
    return _route_frontend_or_help("/bots")


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
