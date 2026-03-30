"""Routes ML — entraînement et informations sur les modèles."""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from app.api import state
from app.api.helpers import verify_api_key
from app.core.candle_store import get_store
from app.core.exchange import create_exchange

logger = logging.getLogger(__name__)
router = APIRouter()

_MODELS_DIR = "models"


@router.post("/api/ml/train", dependencies=[Depends(verify_api_key)])
def train_ml(symbol: str = "BTC/USDC", limit: int = 2000, timeframe: str = "",
             strategy: str = "ml_manual"):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    # Validate strategy name with whitelist (prevent path traversal)
    import re as _re
    if not strategy or not _re.match(r'^[a-zA-Z0-9_-]+$', strategy):
        raise HTTPException(400, "Nom de stratégie invalide (caractères autorisés : lettres, chiffres, _ et -)")
    try:
        from app.ml.model import MLPredictor
        import datetime
        exchange = create_exchange(state.cfg)
        ml_tf    = timeframe.strip() if timeframe.strip() else state.cfg["trading"].get("timeframe", "1h")
        df       = get_store().fetch(exchange, symbol, ml_tf, total=limit)
        if df is None or len(df) == 0:
            raise HTTPException(400, f"Aucune donnée disponible pour {symbol}/{ml_tf}")
        ml = MLPredictor(state.cfg)
        metrics = ml.train(df)
        if "error" in metrics:
            raise HTTPException(400, f"Entraînement échoué : {metrics['error']}")

        # Save model to models/{strategy}_{timeframe}.pkl
        os.makedirs(_MODELS_DIR, exist_ok=True)
        ml.model_path = os.path.join(_MODELS_DIR, f"{strategy}_{ml_tf}.pkl")
        ml.save()
        logger.info(f"[ML] Modèle sauvegardé : {ml.model_path}")

        # Persist results to optimizer_results for Audit OOS
        cv_auc  = metrics.get("cv_auc", 0.0)
        cv_std  = metrics.get("cv_std", 0.0)
        samples = metrics.get("samples", len(df))
        model_name = metrics.get("model", ml.model_name)
        oos_score = round(cv_auc - 0.5, 4)   # AUC relative to random baseline

        run_date = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        if state.cfg is not None:
            state.cfg.setdefault("optimizer_results", {}).setdefault(strategy, {})[ml_tf] = {
                "run_date":  run_date,
                "oos_score": oos_score,
                "params": {
                    "model":    model_name,
                    "cv_auc":   cv_auc,
                    "cv_std":   cv_std,
                    "samples":  samples,
                    "symbol":   symbol,
                },
            }
            # Save to YAML
            try:
                from app.api.routes.config import _save_yaml
                def _upd(d):
                    d.setdefault("optimizer_results", {}).setdefault(strategy, {})[ml_tf] = {
                        "run_date":  run_date,
                        "oos_score": oos_score,
                        "params": {
                            "model":   model_name,
                            "cv_auc":  cv_auc,
                            "cv_std":  cv_std,
                            "samples": samples,
                            "symbol":  symbol,
                        },
                    }
                _save_yaml(_upd)
            except Exception as e:
                logger.warning(f"[ML] Sauvegarde YAML optimizer_results KO : {e}")

        return {
            "status":     "trained",
            "strategy":   strategy,
            "timeframe":  ml_tf,
            "model_path": ml.model_path,
            "samples":    samples,
            "cv_auc":     cv_auc,
            "cv_std":     cv_std,
            "oos_score":  oos_score,
            "model":      model_name,
        }
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
