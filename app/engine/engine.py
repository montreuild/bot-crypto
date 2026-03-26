"""Moteur multi-stratégies avec scoring — chaque stratégie retourne un dict signal."""
import logging
from typing import List, Dict, Any, Optional

import polars as pl

logger = logging.getLogger(__name__)


class BaseStrategy:
    """Interface que toutes les stratégies doivent respecter."""
    name: str = "base"

    # Métadonnées d'optimisation (surchargées par chaque stratégie)
    timeframes:   List[str] = []
    param_space:  Dict[str, List] = {}
    fixed_params: Dict[str, Any]  = {}

    def min_bars_required(self, params: dict = None) -> int:
        """Nombre minimum de bougies requis pour calculer les indicateurs."""
        return 50

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        """Retourne {"score": float [0-1], "side": "long"|"short"|"none", "name": str}."""
        raise NotImplementedError


class BaseStrategyML(BaseStrategy):
    """
    Classe de base pour les stratégies ML.
    Distingue les stratégies ML (entraînement périodique, persistance modèle)
    des stratégies classiques. managed_externally=True désactive le réentraînement inline.
    """
    retrain_interval_h: int = 6
    model_dir: str = "models"

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        """Entraîne le modèle sur df avec les paramètres fournis."""
        raise NotImplementedError

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        """Retourne un signal en utilisant le modèle déjà entraîné."""
        raise NotImplementedError

    def save_model(self, path: str) -> None:
        """Persiste le modèle entraîné sur disque (implémentation optionnelle)."""

    def load_model(self, path: str) -> bool:
        """Charge un modèle depuis le disque. Retourne True si réussi."""
        return False

    def reset_model(self) -> None:
        """Réinitialise l'état du modèle (utilisé par le walk-forward backtest)."""

    @property
    def is_trained(self) -> bool:
        return False


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
