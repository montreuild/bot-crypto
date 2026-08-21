"""État partagé de l'API — variables initialisées par init_app(), accédées via `state.cfg`."""
import os
import threading
from typing import Any, Dict, Optional

from slowapi import Limiter

# ── Runtime state ──────────────────────────────────────────────────────────
# Annotés : sans cela mypy infère `None` et tout `state.cfg.get(...)` en aval
# devient une erreur — la vingtaine de faux positifs de app/api venait de là.
cfg:          Optional[Dict[str, Any]] = None   # config chargée depuis config.yaml
trader:       Any = None                        # LiveTrader, ou None si arrêté
SessionLocal: Any = None                        # factory SQLAlchemy session

# ── Rate limiter (SEC-04) ────────────────────────────────────────────────────
# Défini ici (et non dans main.py) : main.py importe les modules de routes
# AVANT toute définition de module-level — un `from app.api.main import
# limiter` dans un fichier de route lèverait une ImportError circulaire.
# `state` est déjà importé en premier par chaque route (`from app.api import
# state`), donc `state.limiter` y est toujours disponible pour les décorateurs
# `@state.limiter.limit(...)` par endpoint, en plus de `app.state.limiter`
# (SlowAPIMiddleware) réglé sur le MÊME objet dans main.py.
# Clé = même règle que helpers._extract_client_ip (S-02 / A-06) : X-Forwarded-For
# honoré seulement si le pair TCP figure dans TRUSTED_PROXIES (vide par défaut
# → IP du pair, non spoofable). Derrière nginx, sans ça, les 300 req/min
# formaient un seau global. Import paresseux : helpers importe state.
# Limite globale généreuse (surchargeable via RATE_LIMIT) ; les endpoints
# sensibles/coûteux ont des limites `@state.limiter.limit(...)` plus strictes.
_RATE_LIMIT = os.environ.get("RATE_LIMIT", "300/minute")


def _rate_limit_key(request) -> str:
    from app.api.helpers import _extract_client_ip
    return _extract_client_ip(request) or "unknown"


limiter = Limiter(key_func=_rate_limit_key, default_limits=[_RATE_LIMIT])

# ── Sémaphores & verrous ───────────────────────────────────────────────────
_bt_exchange       = None
_bt_exchange_lock  = threading.Lock()
_bt_semaphore      = threading.Semaphore(1)   # un seul backtest à la fois
_opt_semaphore     = threading.Semaphore(1)   # un seul démarrage optimizer à la fois
_config_write_lock = threading.Lock()         # écritures concurrentes sur config.yaml
_bt_cancel_event   = threading.Event()        # signal d'arrêt pour le backtest en cours
_rp_semaphore      = threading.Semaphore(1)   # un seul replay à la fois
_rp_cancel_event   = threading.Event()        # signal d'arrêt pour le replay en cours
# Smart replay/graph SMC : borne la concurrence des backtests synchrones lancés
# depuis les endpoints (protège le thread API), + cache court TTL du payload.
_smc_semaphore     = threading.Semaphore(2)   # ≤ 2 rejeux SMC concurrents
_smc_replay_cache: dict = {}                  # (symbol, tf, limit) → (ts, payload)
_smc_replay_lock   = threading.Lock()
_SMC_REPLAY_TTL: float = 30.0                 # secondes

# ── Cache découverte stratégies ────────────────────────────────────────────
_strategies_cache:    frozenset | None = None
_strategies_cache_ts: float = 0.0
_STRATEGIES_CACHE_TTL: float = 60.0  # secondes
