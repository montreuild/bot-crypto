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
from statistics import mean
from typing import Optional


def returns_per_year(n_returns: int, years: Optional[float],
                     max_per_year: float = 252.0) -> float:
    """Cadence annuelle d'une série de rendements, plafonnée.

    Annualiser une série exige de savoir à quelle cadence elle est
    échantillonnée. C'est l'évidence que le dépôt a manquée à trois endroits :
    la courbe d'équité du backtest — comme celle du live — ne reçoit un point
    qu'à chaque trade clôturé, mais elle était annualisée avec « bougies par
    an ». Un bot qui prend 8 trades en 5,5 ans se voyait attribuer la cadence
    de 365 observations annuelles : Sharpe et Sortino gonflés d'un facteur
    sqrt(bougies / trades), soit environ ×15 dans ce cas.

    ``max_per_year`` est à la fois le repli quand la durée est inconnue ET un
    plafond. Passer la cadence des bougies exprime un fait, pas une politique :
    une série dérivée des trades ne peut pas avoir plus d'observations
    indépendantes que la série de bougies qui les engendre. Sans ce plafond,
    trois trades en deux heures donneraient 13 140 observations par an et un
    Sharpe qui explose sur un échantillon minuscule.
    """
    if years and years > 0 and n_returns > 0:
        return min(n_returns / years, max_per_year)
    return max_per_year


def sortino_ratio(returns: list, risk_free_rate: float = 0.0,
                  periods_per_year: float = 252) -> float:
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
    # F-09 : déviation downside standard = √( (1/N) Σ min(r, 0)² ) sur
    # TOUTES les observations, pas seulement les négatives, et divisée
    # par N (moyenne imposée à 0), pas N−1.
    n = len(returns)
    downside_ss = sum(min(r, 0.0) ** 2 for r in returns)
    if downside_ss <= 0:
        # F-10 : aucun rendement négatif → non mesurable, pas 100.0.
        return float('nan') if avg_return > 0 else 0.0
    downside_dev = math.sqrt(downside_ss / n)
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
        # F-10 : drawdown nul → non mesurable, pas 100.0.
        return float('nan') if cagr > 0 else 0.0
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

    # F-13 : mean() hors des compréhensions — sinon O(n²).
    m_s = mean(strat)
    m_b = mean(bench)
    # β = Cov(Strat, Bench) / Var(Bench)
    cov_sb = sum((s - m_s) * (b - m_b) for s, b in zip(strat, bench)) / (n - 1)
    var_b = sum((b - m_b) ** 2 for b in bench) / (n - 1)
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


def annualized_excess_vs_buy_hold(prices: list, cagr_strategy: float,
                                  years: Optional[float]) -> float:
    """Excès de rendement annualisé de la stratégie sur le Buy & Hold, en %.

    Repli du CAPM alpha quand la série de la stratégie et celle du benchmark
    ne sont PAS sur le même axe temporel (cf. ``compute_extended_metrics``).
    Un bêta calculé entre deux axes différents n'a aucun sens ; la différence
    de rendements annualisés, elle, reste bien définie — elle répond à
    « la stratégie a-t-elle battu l'achat-conservation, et de combien par an ? »
    sans prétendre corriger du risque de marché.
    """
    if not prices or len(prices) < 2 or not years or years <= 0:
        return 0.0
    bh_cagr = compute_cagr(prices[0], prices[-1], years)
    return round(cagr_strategy - bh_cagr, 4)


def compute_extended_metrics(
        trades: list,
        equity_curve: list,
        initial_capital: float,
        prices: Optional[list] = None,
        years: Optional[float] = None,
        periods_per_year: int = 252,
        risk_free_rate: float = 0.0,
        equity_periods_per_year: Optional[float] = None,
) -> dict:
    """Calcule l'ensemble des métriques étendues.

    ⚠ Les deux séries n'ont pas forcément la même fréquence
    ------------------------------------------------------
    ``prices`` est échantillonné **par bougie**, alors que ``equity_curve``,
    telle que produite par le ``Backtester``, ne reçoit un point **qu'à chaque
    trade clôturé**. Confondre les deux fréquences est la source d'erreurs
    d'échelle spectaculaires : un backtest de 2 000 bougies journalières
    comptant 8 trades donnait un CAGR de 3 809 %/an au lieu de 1,66 %, parce
    que la durée était déduite du nombre de points d'équité (9 / 365 ≈ 0,025 an)
    au lieu de la durée réelle (2 000 / 365 ≈ 5,5 ans).

    D'où trois paramètres distincts, à ne pas mélanger :

    - ``years`` : durée RÉELLE couverte par le backtest. À fournir par
      l'appelant, qui seul connaît le nombre de bougies. Sans elle, CAGR et
      Calmar valent 0 — mieux vaut un zéro visible qu'un nombre absurde.
    - ``periods_per_year`` : fréquence des ``prices`` (bougies par an).
    - ``equity_periods_per_year`` : fréquence de ``equity_curve`` (trades par
      an). Déduite de ``len(returns) / years`` si non fournie.

    Returns
    -------
    dict
        {'sortino', 'calmar', 'cagr', 'alpha_vs_bh', 'max_drawdown_pct'}
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

    # Fréquence d'échantillonnage de l'équité. Annualiser une série de
    # rendements PAR TRADE avec un facteur « bougies par an » gonfle le Sortino
    # d'un facteur sqrt(bougies/trades) — ici environ ×15.
    if equity_periods_per_year is None:
        equity_periods_per_year = returns_per_year(
            len(strat_returns), years, periods_per_year)

    sortino = sortino_ratio(strat_returns, risk_free_rate, equity_periods_per_year)

    # CAGR + Calmar. Pas de repli sur `len(equity_curve) / periods_per_year` :
    # c'était précisément le calcul faux.
    final_capital = equity_curve[-1] if equity_curve else initial_capital
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

    # Alpha vs Buy & Hold. Le CAPM exige deux séries alignées période par
    # période ; ce n'est le cas que si l'équité est échantillonnée comme les
    # prix. Sinon, `zip` apparierait le trade n°3 avec la 3ᵉ bougie — un bêta
    # calculé sur ce couplage ne mesure rien.
    bh_returns = buy_hold_returns(prices) if prices else []
    if bh_returns and len(bh_returns) == len(strat_returns):
        alpha = alpha_vs_buy_hold(strat_returns, bh_returns,
                                  risk_free_rate, periods_per_year)
    else:
        alpha = annualized_excess_vs_buy_hold(prices or [], cagr, years)

    return {
        'sortino': sortino,
        'calmar': calmar,
        'cagr': cagr,
        'alpha_vs_bh': alpha,
        'max_drawdown_pct': round(max_dd, 2),
    }
