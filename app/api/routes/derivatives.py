"""Routes dérivés — funding, open interest, long/short ratio, taker ratio.

Sert la page /derivatives : séries temporelles depuis le cache Parquet
data/derivatives/ (alimenté au fil de l'eau par OHLCVCache quand
derivatives.enabled, ou par research/accumulate_derivatives.py), avec
rafraîchissement réseau à la demande.
"""
import logging
import uuid
from typing import Dict, Optional

import polars as pl
from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key, _clean

logger = logging.getLogger(__name__)
router = APIRouter()

_METRICS = {
    "funding": "funding_rate",
    "oi":      "open_interest",
    "lsratio": "long_short_ratio",
    "taker":   "taker_buy_sell_ratio",
}


def _store():
    from app.core.derivatives import DerivativesStore
    return DerivativesStore()


def _series_payload(df: Optional[pl.DataFrame], limit: int) -> Optional[Dict]:
    if df is None or len(df) == 0:
        return None
    df = df.sort("time").tail(limit)
    return {
        "time":   [int(t.timestamp()) for t in df["time"]],
        "value":  [float(v) if v is not None else None for v in df["value"]],
        "count":  len(df),
        "first":  str(df["time"][0]),
        "last":   str(df["time"][-1]),
    }


@router.get("/api/derivatives/data", dependencies=[Depends(verify_api_key)])
def derivatives_data(symbol: str = "BTC/USDC", period: str = "1h",
                     limit: int = 1000, refresh: bool = False):
    """Séries dérivées + close OHLCV pour superposition graphique.

    ``refresh=true`` force un fetch réseau incrémental avant lecture
    (funding/OI via ccxt, long-short/taker via REST Binance public).
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    limit = max(10, min(int(limit), 5000))
    try:
        store = _store()
        if refresh:
            exchange = None
            try:
                from app.core.exchange import create_exchange
                exchange = create_exchange(state.cfg)
            except Exception as e:
                logger.debug(f"[API] derivatives refresh sans exchange : {e}")
            store.refresh(exchange, symbol, period, min_interval=0.0)

        out = {"symbol": symbol, "period": period, "metrics": {}}
        for metric, label in _METRICS.items():
            df = store._load(store._path(symbol, metric))
            out["metrics"][label] = _series_payload(df, limit)

        # Close OHLCV (cache Parquet local) pour superposer le prix
        try:
            from app.core.candle_store import get_store
            df_px = get_store()._load(get_store()._path(symbol, period))
            if df_px is not None and len(df_px) > 0:
                df_px = df_px.sort("time").tail(limit)
                out["price"] = {
                    "time":  [int(t.timestamp()) for t in df_px["time"]],
                    "close": [float(c) for c in df_px["close"]],
                }
        except Exception as e:
            logger.debug(f"[API] derivatives close OHLCV KO : {e}")

        return _clean(out)
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} derivatives/data : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/derivatives/status", dependencies=[Depends(verify_api_key)])
def derivatives_status(symbol: str = "BTC/USDC"):
    """État du cache dérivés : config, nb de points et bornes par métrique."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        store = _store()
        dcfg = state.cfg.get("derivatives", {}) or {}
        metrics = {}
        for metric, label in _METRICS.items():
            path = store._path(symbol, metric)
            df = store._load(path)
            metrics[label] = {
                "points": len(df),
                "first":  str(df["time"][0]) if len(df) else None,
                "last":   str(df["time"][-1]) if len(df) else None,
                "file":   str(path),
            }
        return _clean({
            "symbol":  symbol,
            "enabled": bool(dcfg.get("enabled", False)),
            "period":  dcfg.get("period", "1h"),
            "refresh_interval": dcfg.get("refresh_interval", 300),
            "base_dir": str(store._base),
            "metrics": metrics,
        })
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} derivatives/status : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
