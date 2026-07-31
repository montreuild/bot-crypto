"""S3-07 : Métriques de performance étendues.

Ajoute aux métriques existantes (Sharpe, Max DD, Profit Factor, Win Rate) :
    - **Sortino Ratio** : Sharpe ne pénalisant que la volatilité downside.
    - **Calmar Ratio** : CAGR / Max DD (performance ajustée au pire cas).
    - **Alpha vs Buy & Hold** : excès de rendement annualisé vs benchmark.

Ces métriques sont consommées par le Backtester (app/engine/backtest.py)
et exposées dans l'UI/CLI. Elles permettent de distinguer :
    - Stratégie à Sharpe élevé mais Calmar faible → gains réguliers mais
      risque de grosse perte ponctuelle.
    - Stratégie à alpha négatif → perd même en cas de bull market.
    - Sortino > Sharpe → asymétrie positive (gains > pertes en magnitude).

Références :
    - Sortino, F. (1994). "Performance Measurement in a Downside Risk Framework."
    - Calmar ratio : California Managed Accounts Research Inc.
    - CAPM alpha : Sharpe, W. (1964). "Capital Asset Prices."
"""
import math
from statistics import mean, stdev
from typing import Optional


def sortino_ratio(returns: list, risk_free_rate: float = 0.0,
                  periods_per_year: int = 252) -> float:
    """Sortino ratio (rendement - sans risque) / volatilité downside.

    Parameters
    ----------
    returns : list of float
        Rendements par période (ex. daily, hourly).
    risk_free_rate : float
        Taux sans risque annualisé (défaut 0).
    periods_per_year : int
        Nombre de périodes par an (252 = daily, 252*24 = hourly).

    Returns
    -------
    float
        Sortino ratio annualisé. NaN si non calculable.
    """
    if len(returns) < 2:
        return float('nan')
    avg_return = mean(returns)
    downside_returns = [r for r in returns if r < 0]
    if not downside_returns:
        # Pas de rendements négatifs → volatilité downside = 0 → Sortino infini
        # On plafonne à un nombre élevé pour éviter l'infini
        return 100.0 if avg_return > 0 else 0.0
    downside_dev = stdev(downside_returns, xbar=0.0) if len(downside_returns) > 1 \
                   else abs(downside_returns[0])
    if downside_dev == 0:
        return 100.0 if avg_return > 0 else 0.0
    excess = (avg_return * periods_per_year) - risk_free_rate
    downside_annualized = downside_dev * math.sqrt(periods_per_year)
    return round(excess / downside_annualized, 4) if downside_annualized > 0 else float('nan')


def calmar_ratio(cagr: float, max_drawdown: float) -> float:
    """Calmar ratio = CAGR / |Max DD|.

    Parameters
    ----------
    cagr : float
        Compound Annual Growth Rate (en %, ex. 15.0 pour +15%/an).
    max_drawdown : float
        Maximum drawdown (en %, ex. -20.0 pour -20%).

    Returns
    -------
    float
        Calmar ratio. NaN si Max DD = 0 (pas de perte → infini).
    """
    if max_drawdown == 0:
        return 100.0 if cagr > 0 else 0.0
    return round(cagr / abs(max_drawdown), 4)


def compute_cagr(initial_capital: float, final_capital: float,
                 years: float) -> float:
    """Calcule le CAGR (Compound Annual Growth Rate).

    Parameters
    ----------
    initial_capital : float
        Capital initial.
    final_capital : float
        Capital final.
    years : float
        Durée en années (peut être fractionnaire, ex. 0.5 pour 6 mois).

    Returns
    -------
    float
        CAGR en % (ex. 15.0 pour +15%/an).
    """
    if years <= 0 or initial_capital <= 0:
        return 0.0
    if final_capital <= 0:
        return -100.0  # ruine
    return round(((final_capital / initial_capital) ** (1.0 / years) - 1.0) * 100.0, 4)


def alpha_vs_buy_hold(strategy_returns: list,
                       benchmark_returns: list,
                       risk_free_rate: float = 0.0,
                       periods_per_year: int = 252) -> float:
    """Alpha annualisé vs Buy & Hold (CAPM alpha).

    Alpha = R_strategy - [R_f + β × (R_benchmark - R_f)]
    où β = Cov(R_strat, R_bench) / Var(R_bench).

    Parameters
    ----------
    strategy_returns : list of float
        Rendements de la stratégie par période.
    benchmark_returns : list of float
        Rendements du benchmark (Buy & Hold) par période — même longueur.
    risk_free_rate : float
        Taux sans risque annualisé (défaut 0).
    periods_per_year : int
        Périodes par an pour annualiser.

    Returns
    -------
    float
        Alpha annualisé en % (ex. 5.0 pour +5%/an de plus que le benchmark).
    """
    n = min(len(strategy_returns), len(benchmark_returns))
    if n < 2:
        return 0.0
    strat = strategy_returns[:n]
    bench = benchmark_returns[:n]

    avg_strat = mean(strat) * periods_per_year
    avg_bench = mean(bench) * periods_per_year

    # β = Cov(Strat, Bench) / Var(Bench)
    cov_sb = sum((s - mean(strat)) * (b - mean(bench)) for s, b in zip(strat, bench)) / (n - 1)
    var_b = sum((b - mean(bench)) ** 2 for b in bench) / (n - 1)
    if var_b == 0:
        beta = 0.0
    else:
        beta = cov_sb / var_b

    # Alpha = R_strat_annual - [R_f + β × (R_bench_annual - R_f)]
    alpha = avg_strat - (risk_free_rate + beta * (avg_bench - risk_free_rate))
    return round(alpha * 100.0, 4)  # en %


def buy_hold_returns(prices: list) -> list:
    """Calcule les rendements composés d'une stratégie Buy & Hold.

    Parameters
    ----------
    prices : list of float
        Série de prix (ex. close du BTC sur la période).

    Returns
    -------
    list of float
        Rendements par période (ex. daily). Le premier rendement est 0
        (pas de période précédente).
    """
    if len(prices) < 2:
        return [0.0] * len(prices)
    returns = [0.0]  # première période = 0
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        else:
            returns.append(0.0)
    return returns


def compute_extended_metrics(
        trades: list,
        equity_curve: list,
        initial_capital: float,
        prices: Optional[list] = None,
        years: Optional[float] = None,
        periods_per_year: int = 252,
        risk_free_rate: float = 0.0,
) -> dict:
    """Calcule l'ensemble des métriques étendues.

    Returns
    -------
    dict
        {
            'sortino': float,
            'calmar': float,
            'cagr': float,
            'alpha_vs_bh': float,
            'beta_vs_bh': float,
            'sortino_annualized': float,
        }
    """
    # Rendements de l'équity curve
    if len(equity_curve) >= 2:
        strat_returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            if equity_curve[i - 1] > 0 else 0.0
            for i in range(1, len(equity_curve))
        ]
    else:
        strat_returns = []

    # Sortino
    sortino = sortino_ratio(strat_returns, risk_free_rate, periods_per_year)

    # CAGR + Calmar
    final_capital = equity_curve[-1] if equity_curve else initial_capital
    if years is None and len(equity_curve) > 1:
        years = len(equity_curve) / periods_per_year
    cagr = compute_cagr(initial_capital, final_capital, years) if years else 0.0

    # Max DD depuis l'équity curve
    peak = equity_curve[0] if equity_curve else initial_capital
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            max_dd = max(max_dd, dd)
    calmar = calmar_ratio(cagr, -max_dd)

    # Alpha vs Buy & Hold
    bh_returns = buy_hold_returns(prices) if prices else []
    alpha = alpha_vs_buy_hold(strat_returns, bh_returns, risk_free_rate, periods_per_year)

    return {
        'sortino': sortino,
        'calmar': calmar,
        'cagr': cagr,
        'alpha_vs_bh': alpha,
        'max_drawdown_pct': round(max_dd, 2),
    }
