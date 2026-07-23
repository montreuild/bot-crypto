"""Middlewares FastAPI — extraction ARCH-014 depuis ``main.py``.

Centralise l'enregistrement de tous les middlewares et exception handlers
globaux sur l'app FastAPI, via :func:`setup_middleware` à appeler depuis
``main.py`` juste après ``app.state.limiter = state.limiter`` (SlowAPI lit
``app.state.limiter`` à l'enregistrement du middleware).

Inclut :
- le handler d'exceptions global (filet de sécurité 500 propre) ;
- le handler SlowAPI pour ``RateLimitExceeded`` ;
- le rate-limiting ``SlowAPIMiddleware`` ;
- la compression ``GZipMiddleware`` ;
- le ``CORSMiddleware`` (whitelist localhost / ``ALLOWED_ORIGINS``) ;
- la redirection HTTPS (production, si ``FORCE_HTTPS=1``).

Ordre d'enregistrement préservé depuis ``main.py`` : l'ordre d'ajout des
middlewares compte en FastAPI — le dernier ajouté devient le plus externe
(donc appelé en premier sur chaque requête entrante).
"""
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

logger = logging.getLogger(__name__)


# ── Filet de sécurité : capture toutes les exceptions non gérées ──────────
# Sans ce handler, une exception dans une route (ex. ``TypeError`` à cause
# d'un ``oos_score=None``) remonte à travers la middleware Starlette et
# se transforme en ``ExceptionGroup: unhandled errors in a TaskGroup``
# moche dans les logs serveur, sans message HTTP propre côté client.
async def _global_exception_handler(request: Request, exc: Exception):
    """Loggue l'exception puis retourne un JSON 500 propre (sans stacktrace)."""
    logger.error(
        f"[API] Exception non gérée {request.method} {request.url.path} : "
        f"{type(exc).__name__}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Erreur interne : {type(exc).__name__}",
            "path":   request.url.path,
        },
    )


# ── HTTPS redirect (si configuré en production) ───────────────────────────
async def https_redirect(request: Request, call_next):
    """Redirect HTTP to HTTPS in production if FORCE_HTTPS env var is set."""
    if os.environ.get("FORCE_HTTPS", "").lower() in ("1", "true", "yes"):
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "http":
            url = request.url.replace(scheme="https")
            from starlette.responses import RedirectResponse
            return RedirectResponse(url=str(url), status_code=301)
    return await call_next(request)


def _compute_allowed_origins() -> list:
    """Whitelist localhost par défaut (dev) ; ``ALLOWED_ORIGINS`` en production.

    ``ALLOWED_ORIGINS`` est une liste séparée par des virgules — ex.
    ``ALLOWED_ORIGINS=https://bot.mondomaine.com`` pour restreindre au(x)
    domaine(s) réel(s) en prod.
    """
    return [
        o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
    ] or [
        "http://localhost",      "http://127.0.0.1",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:8001", "http://127.0.0.1:8001",
    ]


def setup_middleware(app: FastAPI) -> None:
    """Enregistre tous les middlewares et exception handlers sur l'app.

    À appeler APRÈS ``app.state.limiter = state.limiter`` : ``SlowAPIMiddleware``
    lit ``app.state.limiter`` à l'enregistrement, donc la config du limiter
    doit être posée avant.
    """
    # Handler SlowAPI pour ``RateLimitExceeded`` : convertit l'exception
    # du limiter en réponse 429 standard.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Filet de sécurité global (avant les middlewares, ordre sans importance
    # pour ``add_exception_handler`` — lookup par type d'exception).
    app.add_exception_handler(Exception, _global_exception_handler)

    # Rate-limiting effectif : sans ce middleware, ``default_limits`` du
    # Limiter n'était jamais appliqué (les limites étaient configurées
    # mais inertes).
    app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(GZipMiddleware, minimum_size=500)

    _allowed_origins = _compute_allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "PUT"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    # HTTPS redirect — dernier ajouté, donc middleware le plus externe
    # (appelé en premier sur chaque requête entrante).
    app.middleware("http")(https_redirect)
