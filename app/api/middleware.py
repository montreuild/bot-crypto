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


# ── OBS-02 : identifiant de corrélation ───────────────────────────────────
#: En-tête accepté en entrée et renvoyé en sortie. Un reverse-proxy ou un
#: client qui en pose déjà un voit sa valeur RÉUTILISÉE : la trace traverse
#: alors nginx et le bot sous un seul identifiant.
CORRELATION_HEADER = "X-Request-ID"

#: Longueur maximale acceptée d'un identifiant fourni par le client. La valeur
#: arrive dans chaque ligne de log : sans borne, un client hostile écrit ce
#: qu'il veut, aussi long qu'il veut, dans le fichier de log du serveur.
_MAX_CID = 64


def _incoming_correlation_id(request: Request) -> str:
    """Identifiant fourni par l'appelant, assaini — sinon un neuf.

    Ne garde que ``[A-Za-z0-9._-]`` : la valeur est écrite telle quelle dans
    les logs, et un identifiant porteur de sauts de ligne permettrait d'y
    injecter des entrées entières (log forging).
    """
    from app.core.log_context import new_correlation_id
    raw = (request.headers.get(CORRELATION_HEADER) or "")[:_MAX_CID]
    clean = "".join(c for c in raw if c.isalnum() or c in "._-")
    return clean or new_correlation_id()


async def correlation_middleware(request: Request, call_next):
    """Rattache toutes les lignes de log d'une requête à un même identifiant."""
    from app.core.log_context import correlation_scope
    with correlation_scope(_incoming_correlation_id(request)) as cid:
        # Exposé sur ``request.state`` pour que les routes puissent le
        # transmettre aux jobs de fond qu'elles démarrent (cf. ml_jobs).
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = cid
        return response


# ── OBS-01 : métriques HTTP ───────────────────────────────────────────────
def _route_template(request: Request) -> str:
    """Template de route (``/api/ml/registry/versions``), jamais l'URL concrète.

    Libeller une métrique par ``request.url.path`` ferait une série temporelle
    par valeur de paramètre — un id de job, un symbole, un version_id. C'est le
    mode de panne le plus courant d'une instrumentation Prometheus : la
    cardinalité explose côté serveur, longtemps après la mise en production.

    ``scope["route"]`` n'est posé qu'APRÈS le routage, donc cette fonction ne
    peut être appelée qu'une fois ``call_next`` revenu. Sur un 404, aucune
    route ne correspond : on renvoie un libellé constant plutôt que le chemin
    demandé, qui est justement de la donnée non bornée — et contrôlée par
    l'appelant.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    return "__unmatched__"


async def metrics_middleware(request: Request, call_next):
    """Compte et chronomètre chaque requête servie.

    L'erreur est enregistrée puis RE-LEVÉE : le handler global reste
    responsable de la réponse 500. Une exception avalée ici produirait une
    métrique juste et une réponse cassée.
    """
    import time

    from app.core.metrics import observe_http
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        observe_http(request.method, _route_template(request), 500,
                     time.perf_counter() - t0)
        raise
    observe_http(request.method, _route_template(request),
                 response.status_code, time.perf_counter() - t0)
    return response


def _compute_allowed_origins() -> list:
    """Whitelist localhost par défaut (dev) ; ``ALLOWED_ORIGINS`` en production.

    ``ALLOWED_ORIGINS`` est une liste séparée par des virgules — ex.
    ``ALLOWED_ORIGINS=https://bot.mondomaine.com`` pour restreindre au(x)
    domaine(s) réel(s) en prod.

    ⚠ S6-09 — la liste par défaut datait de l'ère Jinja2, où l'UI était servie
    par FastAPI lui-même (ports 8000/8001) : elle n'était donc pas cross-origin.
    Depuis la migration, le frontend Next.js tourne sur **3000** et n'y figurait
    pas — le préflight `OPTIONS /api/*` était rejeté en 400 et *aucun* appel API
    n'aboutissait, backend allumé ou non. On ajoute 3000 (et 3001, port de repli
    de `next dev` quand 3000 est pris) ainsi que ``FRONTEND_URL`` s'il est défini.
    """
    explicit = [
        o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
    ]
    if explicit:
        return explicit

    defaults = [
        "http://localhost",      "http://127.0.0.1",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:8001", "http://127.0.0.1:8001",
    ]
    frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")
    if frontend and frontend not in defaults:
        defaults.append(frontend)
    return defaults


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

    # OBS-01 — métriques HTTP. Ajouté après CORS/GZip donc plus externe
    # qu'eux : la latence mesurée inclut la compression, qui fait partie du
    # temps que le client attend réellement.
    app.middleware("http")(metrics_middleware)

    # OBS-02 — corrélation. Ajouté APRÈS les métriques, donc plus externe
    # qu'elles : l'identifiant doit être posé avant tout le reste, sans quoi
    # les logs du middleware de métriques et ceux du handler d'exceptions
    # tomberaient hors de la trace — or ce sont les plus utiles quand quelque
    # chose se passe mal.
    app.middleware("http")(correlation_middleware)

    # HTTPS redirect — dernier ajouté, donc middleware le plus externe
    # (appelé en premier sur chaque requête entrante).
    app.middleware("http")(https_redirect)
