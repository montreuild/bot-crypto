"""
Moteur multi-stratégies avec scoring.
Chaque stratégie retourne un dict {"score": float, "side": str, "name": str}.
Le moteur sélectionne le meilleur signal.
"""
import logging
from typing import List, Dict, Any, Optional

import polars as pl

logger = logging.getLogger(__name__)


class BaseStrategy:
    """Interface que toutes les stratégies doivent respecter."""
    name: str = "base"

    def min_bars_required(self, params: dict = None) -> int:
        """
        Retourne le nombre minimum de bougies requis pour que la stratégie
        puisse calculer ses indicateurs de manière fiable.
        Chaque stratégie surcharge cette méthode selon ses propres besoins.
        """
        return 50

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        """
        df     : OHLCV du timeframe principal
        df_htf : OHLCV du timeframe supérieur — peut être None
        symbol : nom de la paire (ex: "BTC/USDC") — utilisé pour le cooldown
        Analyse le DataFrame OHLCV et retourne un signal.
        Retourne : {"score": float [0-1], "side": "long"|"short"|"none", "name": str}
        """
        raise NotImplementedError


class Engine:
    def __init__(self):
        self.strategies: List[BaseStrategy] = []

    def register(self, strategy: BaseStrategy, silent: bool = False):
        # Garde contre les doublons (même nom)
        if any(s.name == strategy.name for s in self.strategies):
            logger.warning(f"[Engine] Doublon ignoré : {strategy.name}")
            return
        if not silent:
            logger.info(f"[Engine] Stratégie enregistrée : {strategy.name}")
        self.strategies.append(strategy)

    def best_signal(self, df: pl.DataFrame, params: dict = None,
                    df_htf=None, symbol: str = "") -> Dict[str, Any]:
        """
        Retourne le signal avec le meilleur score parmi toutes les stratégies.
        Si aucun signal, retourne {"score": 0, "side": "none", "name": ""}.
        """
        if df is None or len(df) < 2:
            return {"score": 0, "side": "none", "name": ""}

        best = {"score": 0.0, "side": "none", "name": ""}
        for strat in self.strategies:
            try:
                result = strat.score(df, params, df_htf=df_htf, symbol=symbol)
                if not isinstance(result, dict):
                    continue
                if result.get("score", 0) > best["score"]:
                    best = result
            except Exception as e:
                logger.error(f"[Engine] Erreur dans stratégie {strat.name} : {e}")
        return best
