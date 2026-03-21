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

    # ── Métadonnées d'optimisation (surchargées par chaque stratégie) ──────
    # timeframes   : TFs recommandés pour l'optimisation (ex: ["1h", "1d"])
    # param_space  : espace de recherche {param: [valeurs]} pour l'optimiseur
    # fixed_params : paramètres fixes (non optimisables), ex: {"ema_trend": 200}
    timeframes:   List[str] = []
    param_space:  Dict[str, List] = {}
    fixed_params: Dict[str, Any]  = {}

    def __init__(self) -> None:
        # Cooldown par symbole : dernière barre où un signal a été émis
        self._last_signal: Dict[str, int] = {}
        # Compteur d'appels par symbole (position dans la série temporelle)
        self._call_count:  Dict[str, int] = {}

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

    def _none(self, reason: str = "") -> Dict[str, Any]:
        """Signal nul standardisé (aucune position à prendre)."""
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}


class BaseStrategyML(BaseStrategy):
    """
    Classe de base pour les stratégies basées sur un modèle ML.

    Distingue les stratégies ML des stratégies classiques dans tout le bot :
    - auto_optimizer : exclusion automatique (gèrent leur propre optimisation interne)
    - live_trader    : entraînement périodique en arrière-plan + persistance du modèle
    - backtest       : reset_model() disponible pour le walk-forward

    Contrat à implémenter :
      fit(df, params)          → entraîne le modèle sur les données historiques
      predict(df, params)      → génère un signal sans ré-entraîner
      save_model(path)         → persiste le modèle (joblib)
      load_model(path) → bool  → charge un modèle depuis le disque
      reset_model()            → réinitialise l'état (nouveau walk-forward fold)
      is_trained (property)    → True si un modèle est disponible

    Paramètre de classe :
      retrain_interval_h : fréquence d'entraînement périodique en live (défaut : 6h)
      model_dir          : répertoire de persistance des modèles (défaut : "models")

    Flag d'instance :
      managed_externally : si True, le _signal() interne ne réentraîne pas inline ;
                           c'est le LiveTrader qui planifie les réentraînements.
                           Mis à True automatiquement au démarrage du LiveTrader.
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
