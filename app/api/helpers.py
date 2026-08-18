"""Helpers partagés de l'API : auth, découverte stratégies, OHLCV."""
import glob
import hmac
import logging
import os
import re
import time

from fastapi import HTTPException, Request

from app.api import state
from app.core.ohlcv_gaps import detect_ohlcv_gaps  # noqa: F401 — re-export D-03
from app.core.sanitize import CleanJSONResponse  # noqa: F401 — re-export
from app.core.sanitize import clean_for_json as _clean  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


# ── Auth ───────────────────────────────────────────────────────────────────

def _trusted_proxies() -> set:
    """IP(s) des reverse-proxies de confiance (env ``TRUSTED_PROXIES``, séparées
    par des virgules). Vide par défaut → ``X-Forwarded-For`` n'est jamais honoré."""
    raw = os.environ.get("TRUSTED_PROXIES", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def _normalize_ip(raw: str) -> str:
    """Ramène une adresse IPv4-mappée-en-IPv6 à sa forme IPv4.

    Sur une pile double, un appelant pourtant local se présente souvent comme
    ``::ffff:127.0.0.1`` — c'est bien du loopback (``ipaddress.is_loopback``
    vaut True), mais la comparaison littérale à ``127.0.0.1``/``::1`` le
    manquait : le filtre « localhost only » refusait des requêtes locales avec
    le message « requête non-locale ». Observé en dev : le proxy serveur de
    Next.js appelle l'API sur 127.0.0.1 et se voyait répondre 403.

    Normalisation seulement : l'allowlist et sa sémantique sont inchangées, ce
    qui n'élargit l'accès à aucune adresse non-loopback.
    """
    if not raw:
        return raw
    try:
        import ipaddress
        # `ipv4_mapped` n'existe que sur IPv6Address — d'où le getattr plutôt
        # qu'un accès direct, qui lèverait sur une IPv4 déjà normalisée.
        mapped = getattr(ipaddress.ip_address(raw), "ipv4_mapped", None)
        if mapped is not None:
            return str(mapped)
    except ValueError:
        pass        # "localhost", "unknown"… : laissés tels quels
    return raw


def _extract_client_ip(request: Request) -> str:
    """IP cliente réelle, robuste au spoofing de ``X-Forwarded-For``.

    ``X-Forwarded-For`` est trivialement falsifiable par le client ; s'y fier
    sans condition permettait de se faire passer pour ``127.0.0.1`` et de
    contourner le contrôle « localhost only » quand aucune clé API n'est
    configurée (bypass d'auth). On n'honore donc le header **que** si la
    connexion provient directement d'un proxy explicitement déclaré de confiance
    (``TRUSTED_PROXIES``) ; sinon on utilise l'IP réelle du pair TCP.

    Les deux chemins passent par :func:`_normalize_ip` : un proxy déclaré en
    ``127.0.0.1`` doit être reconnu même quand la pile le présente en
    ``::ffff:127.0.0.1``.
    """
    peer = _normalize_ip((getattr(request.client, "host", "") if request.client else "") or "")
    if peer in _trusted_proxies():
        forwarded_for = request.headers.get("x-forwarded-for", "").strip()
        if forwarded_for:
            real_ip = _normalize_ip(forwarded_for.split(",")[0].strip())
            if real_ip:
                return real_ip
    return peer or "unknown"


async def verify_api_key(request: Request):
    key = (state.cfg or {}).get("web", {}).get("api_key", "")
    if not key:
        # When no API key is configured, only allow requests from localhost.
        # Also honour X-Forwarded-For so reverse-proxy deployments are handled correctly.
        client_host = _extract_client_ip(request)
        if client_host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                f"[Auth] Accès refusé depuis {client_host} — "
                f"aucune clé API configurée et requête non-locale"
            )
            raise HTTPException(
                status_code=403,
                detail="API key required for remote access. Set web.api_key in config.yaml."
            )
        return
    token = request.headers.get("X-API-Key") or request.cookies.get("api_key") or ""
    if len(token) > 256:
        raise HTTPException(status_code=403, detail="Clé API invalide")
    if not hmac.compare_digest(token, key):
        client_host = _extract_client_ip(request)
        logger.warning(
            f"[Auth] Clé API invalide depuis {client_host} — "
            f"{request.method} {request.url.path}"
        )
        raise HTTPException(status_code=403, detail="Clé API invalide")


# ── Exchange backtest (singleton partagé) ──────────────────────────────────

def _get_bt_exchange(cfg: dict):
    with state._bt_exchange_lock:
        if state._bt_exchange is None:
            from app.core.exchange import create_exchange
            state._bt_exchange = create_exchange(cfg)
        return state._bt_exchange


# ── Découverte des stratégies ──────────────────────────────────────────────

#: Un module d'``app/strategies/`` n'est une stratégie que s'il expose une
#: classe ``Strategy`` — c'est le contrat qu'appliquent tous les chargeurs
#: (``mod.Strategy()`` dans ``auto_opt_mixin``, ``auto_optimizer``,
#: ``forward_test``, ``opt_workers``…).
_STRATEGY_CLASS_RE = re.compile(r"^class\s+Strategy\b", re.MULTILINE)


def _module_defines_strategy(path: str) -> bool:
    """True si le fichier définit ``class Strategy``.

    Le test se fait sur le TEXTE, pas par import : importer les ~45 modules
    de stratégies à chaque expiration de cache coûterait plusieurs secondes
    (LightGBM, polars…) et exécuterait leur code au niveau module pour une
    simple question de nommage.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return bool(_STRATEGY_CLASS_RE.search(fh.read()))
    except OSError as e:
        logger.warning(f"[Strategies] Lecture impossible de {path} : {e}")
        return False


def _discover_strategies() -> frozenset:
    """Retourne les noms de stratégies valides sur disque (cache 60 s).

    Filtre sur la présence d'une classe ``Strategy`` : le glob seul remontait
    aussi les modules d'appoint d'``app/strategies/`` — ``smart_money_params``,
    ``smart_money_aux``, ``smart_money_plans`` et ``smart_money_signals``,
    extraits de ``smart_money.py`` lors du découpage ARCH-05. Ils étaient
    proposés à la sélection dans le Laboratoire et l'optimiseur alors qu'ils
    n'ont pas de ``Strategy`` : les choisir faisait échouer le chargement.
    """
    now = time.monotonic()
    if (state._strategies_cache is not None
            and (now - state._strategies_cache_ts) < state._STRATEGIES_CACHE_TTL):
        return state._strategies_cache
    strat_dir = os.path.join(os.path.dirname(__file__), "..", "strategies")
    result = frozenset(
        os.path.splitext(os.path.basename(f))[0]
        for f in glob.glob(os.path.join(strat_dir, "*.py"))
        if not os.path.basename(f).startswith("__") and _module_defines_strategy(f)
    )
    state._strategies_cache    = result
    state._strategies_cache_ts = now
    return result


# ── Helpers OHLCV ──────────────────────────────────────────────────────────
# detect_ohlcv_gaps : app.core.ohlcv_gaps (calendrier-aware, D-03).


