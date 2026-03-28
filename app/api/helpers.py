"""Helpers partagés de l'API : auth, découverte stratégies, OHLCV."""
import glob
import hmac
import logging
import os
import time

from fastapi import HTTPException, Request

from app.api import state
from app.core.sanitize import (                          # noqa: F401 — re-export
    clean_for_json as _clean,
    CleanJSONResponse,
)

logger = logging.getLogger(__name__)


# ── Auth ───────────────────────────────────────────────────────────────────

def _extract_client_ip(request: Request) -> str:
    """Extrait l'IP client depuis X-Forwarded-For ou request.client."""
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        real_ip = forwarded_for.split(",")[0].strip()
        if real_ip:
            return real_ip
    return getattr(request.client, "host", "unknown") if request.client else "unknown"


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
    token = request.headers.get("X-API-Key") or request.query_params.get("api_key") or ""
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

def _discover_strategies() -> frozenset:
    """Retourne les noms de stratégies valides sur disque (cache 60 s)."""
    now = time.monotonic()
    if (state._strategies_cache is not None
            and (now - state._strategies_cache_ts) < state._STRATEGIES_CACHE_TTL):
        return state._strategies_cache
    strat_dir = os.path.join(os.path.dirname(__file__), "..", "strategies")
    result = frozenset(
        os.path.splitext(os.path.basename(f))[0]
        for f in glob.glob(os.path.join(strat_dir, "*.py"))
        if not os.path.basename(f).startswith("__")
    )
    state._strategies_cache    = result
    state._strategies_cache_ts = now
    return result


# ── Helpers OHLCV ──────────────────────────────────────────────────────────

def detect_ohlcv_gaps(df, timeframe: str) -> list:
    tf_mins = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
               "1h": 60, "4h": 240, "1d": 1440}
    expected_mins  = tf_mins.get(timeframe, 60)
    from datetime import timedelta as _timedelta
    expected_delta = _timedelta(minutes=expected_mins)
    gaps  = []
    times = df["time"]
    for i in range(1, len(times)):
        delta = times[i] - times[i - 1]
        if delta > expected_delta * 1.5:
            gap_bars = round(delta.total_seconds() / 60 / expected_mins) - 1
            gaps.append({
                "index":        int(i),
                "time_before":  str(times[i - 1])[:16],
                "time_after":   str(times[i])[:16],
                "gap_bars":     gap_bars,
                "gap_duration": str(delta),
            })
    return gaps


