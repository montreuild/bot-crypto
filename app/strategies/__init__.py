"""Package strategies — stratégies de trading et utilitaires communs.

Classes et modules exportés :
  StrategyBase      : classe de base pour les stratégies classiques (non-ML)
                      (init cooldown + méthode _none commune)
  fft_direction()   : analyse spectrale FFT partagée entre fft_spectral
                      et composite_score
"""
from app.strategies.base import StrategyBase
from app.strategies.utils import fft_direction

__all__ = ["StrategyBase", "fft_direction"]
