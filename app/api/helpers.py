"""Helpers partagés de l'API : sanitisation JSON, auth, découverte stratégies, OHLCV."""
import glob
import hmac
import json
import logging
import math
import os
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.api import state

logger = logging.getLogger(__name__)


# ── Sanitisation JSON ──────────────────────────────────────────────────────

def _clean(obj: Any) -> Any:
    """Sanitise récursivement pour JSON : NaN→None, ±Inf→±1e308, clés privées ignorées."""
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if math.isinf(obj):
            return 1e308 if obj > 0 else -1e308
        return obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, (int, str, bool, type(None))):
        return obj
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return None


class CleanJSONResponse(JSONResponse):
    """JSONResponse qui neutralise les float NaN/Inf sur toutes les réponses."""
    def render(self, content) -> bytes:
        return json.dumps(
            _clean(content),
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


# ── Auth ───────────────────────────────────────────────────────────────────

async def verify_api_key(request: Request):
    key = state.cfg["web"].get("api_key", "") if state.cfg else ""
    if not key:
        return
    token = request.headers.get("X-API-Key") or request.query_params.get("api_key") or ""
    if not hmac.compare_digest(token, key):
        client_host = getattr(request.client, "host", "unknown") if request.client else "unknown"
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


