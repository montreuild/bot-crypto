"""P2-2 : Métriques étendues extraites de ``backtest.py::BacktestResult``.

Ce module contient la logique de calcul des métriques étendues
(Sortino, Calmar, CAGR, alpha vs B&H) qui était inline dans
``BacktestResult._compute_extended_metrics()``.

À terme, ``BacktestResult`` devrait déléguer à ce module plutôt que
de porter la logique lui-même.
"""
from typing import Optional


def compute_extended_metrics(
    trades: list,
    equity_curve: list,
    initial_capital: float,
    prices: Optional[list] = None,
    years: Optional[float] = None,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> dict:
    """Calcule Sortino, Calmar, CAGR, alpha vs Buy & Hold.

    Extrait de ``BacktestResult._compute_extended_metrics()`` pour
    permettre la réutilisation et les tests unitaires isolés.
    """
    from app.core.performance_metrics import compute_extended_metrics as _cem
    return _cem(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=initial_capital,
        prices=prices,
        years=years,
        periods_per_year=periods_per_year,
        risk_free_rate=risk_free_rate,
    )
