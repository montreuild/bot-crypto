"""Classe de base commune à toutes les stratégies non-ML.

Factorisation de l'initialisation et des helpers identiques dans
breakout, composite_score, fear_momentum, fft_spectral, multi_tf_sr,
pullback_trend, supertrend_macd, trend.

Utilisation :

    from app.strategies.base import StrategyBase

    class Strategy(StrategyBase):
        name = "ma_strategie"
        ...
"""
from typing import Dict, Any

from app.engine.engine import BaseStrategy


class StrategyBase(BaseStrategy):
    """Base commune pour les stratégies classiques (non-ML).

    Fournit :
      - __init__ standard : _last_signal et _call_count (cooldown par symbole)
      - _none()           : retourne un signal nul formaté uniformément
    """

    def __init__(self) -> None:
        self._last_signal: Dict[str, int] = {}
        self._call_count:  Dict[str, int] = {}

    def _none(self, reason: str = "") -> Dict[str, Any]:
        """Signal nul standardisé (aucune position à prendre)."""
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
