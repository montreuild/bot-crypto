"""Motif de sortie d'une position de backtest (DETTE-04c)."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RaisonDeSortie:
    """Résultat de ``_evaluer_sorties`` — clôture totale, pas une jambe."""
    reason: str
    exec_price: float
    ref_price: Optional[float]
    maker: bool
