"""
Moteur multi-stratégies avec scoring.
Chaque stratégie retourne un dict {"score": float, "side": str, "name": str}.
Le moteur sélectionne le meilleur signal.
"""
import logging
from typing import List, Dict, Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class BaseStrategy:
    """Interface que toutes les stratégies doivent respecter."""
    name: str = "base"

    def score(self, df: pd.DataFrame, params: dict = None) -> Dict[str, Any]:
        """
        Analyse le DataFrame OHLCV et retourne un signal.
        Retourne : {"score": float [0-1], "side": "long"|"short"|"none", "name": str}
        """
        raise NotImplementedError


class Engine:
    def __init__(self):
        self.strategies: List[BaseStrategy] = []

    def register(self, strategy: BaseStrategy):
        logger.info(f"[Engine] Stratégie enregistrée : {strategy.name}")
        self.strategies.append(strategy)

    def best_signal(self, df: pd.DataFrame, params: dict = None) -> Dict[str, Any]:
        """
        Retourne le signal avec le meilleur score parmi toutes les stratégies.
        Si aucun signal, retourne {"score": 0, "side": "none", "name": ""}.
        """
        if df is None or len(df) < 2:
            return {"score": 0, "side": "none", "name": ""}

        best = {"score": 0.0, "side": "none", "name": ""}
        for strat in self.strategies:
            try:
                result = strat.score(df, params)
                if not isinstance(result, dict):
                    continue
                if result.get("score", 0) > best["score"]:
                    best = result
            except Exception as e:
                logger.error(f"[Engine] Erreur dans stratégie {strat.name} : {e}")
        return best

    def all_signals(self, df: pd.DataFrame, params: dict = None) -> List[Dict[str, Any]]:
        """Retourne les signaux de toutes les stratégies (pour debug/backtest)."""
        signals = []
        for strat in self.strategies:
            try:
                signals.append(strat.score(df, params))
            except Exception as e:
                logger.error(f"[Engine] Erreur {strat.name} : {e}")
                signals.append({"score": 0, "side": "none", "name": strat.name, "error": str(e)})
        return signals
