"""Routes ML et candles — entraînement et stats du cache Parquet."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key
from app.core.candle_store import get_store
from app.core.exchange import create_exchange

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/ml/train", dependencies=[Depends(verify_api_key)])
def train_ml(symbol: str = "BTC/USDC", limit: int = 2000, timeframe: str = ""):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        from app.ml.model import MLPredictor
        exchange = create_exchange(state.cfg)
        ml_tf    = timeframe.strip() if timeframe.strip() else state.cfg["trading"].get("timeframe", "1h")
        df       = get_store().fetch(exchange, symbol, ml_tf, total=limit)
        if df is None or len(df) == 0:
            raise HTTPException(400, f"Aucune donnée disponible pour {symbol}/{ml_tf}")
        ml = MLPredictor(state.cfg)
        ml.train(df)
        ml.save()
        return {"status": "trained", "samples": len(df), "timeframe": ml_tf}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/ml/strategy-info", dependencies=[Depends(verify_api_key)])
def ml_strategy_info():
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    enabled = state.cfg.get("ml", {}).get("enabled", False)
    ready   = state.trader and state.trader.ml and state.trader.ml.is_ready

    strategies_info: dict = {}
    if state.trader:
        from app.engine.engine import BaseStrategyML
        ml_trainer = getattr(state.trader, "_ml_trainer", None)
        loaded     = getattr(state.trader, "_loaded_strategies", {})
        for name, strat in loaded.items():
            if not isinstance(strat, BaseStrategyML):
                continue
            next_retrain = None
            if ml_trainer:
                ts = ml_trainer._retrain_at.get(name)
                if ts is not None:
                    next_retrain = int(ts)
            strategies_info[name] = {
                "is_trained":      strat.is_trained,
                "best_auc":        round(float(getattr(strat, "_best_auc", 0.0)), 4),
                "next_retrain_at": next_retrain,
            }

    return {"enabled": enabled, "ready": ready,
            "config": state.cfg.get("ml", {}),
            "strategies": strategies_info}


@router.get("/api/candles/stats", dependencies=[Depends(verify_api_key)])
def candles_stats():
    """Retourne les statistiques du cache Parquet local (toutes paires/TFs stockés)."""
    return {"store": get_store().all_stats()}
