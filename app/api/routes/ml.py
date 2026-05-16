"""Routes ML — informations sur les modèles BaseStrategyML chargés."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key
from app.core.candle_store import get_store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/ml/strategy-info", dependencies=[Depends(verify_api_key)])
def ml_strategy_info():
    """Retourne l'état d'entraînement des stratégies BaseStrategyML chargées."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")

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
                # Le trainer stocke un timer par paire (name@tf) ; on prend le plus proche.
                ts_list = [ts for key, ts in ml_trainer._retrain_at.items()
                           if key.startswith(f"{name}@")]
                if ts_list:
                    next_retrain = int(min(ts_list))
            strategies_info[name] = {
                "is_trained":      strat.is_trained,
                "best_auc":        round(float(getattr(strat, "_best_auc", 0.0)), 4),
                "next_retrain_at": next_retrain,
            }

    return {"strategies": strategies_info}


@router.get("/api/candles/stats", dependencies=[Depends(verify_api_key)])
def candles_stats():
    """Retourne les statistiques du cache Parquet local (toutes paires/TFs stockés)."""
    return {"store": get_store().all_stats()}
