"""S4-04 : Vraie mesure de corrélation entre bots.

L'audit V2 notait que `check_correlation` (capital_allocator.py) ne mesurait
pas une vraie corrélation : il rejetait si ≥75% des positions étaient dans le
même sens — une garde de concentration *directionnelle*, pas de corrélation.

Ce module calcule une **vraie matrice de corrélation des rendements** par
symbole, utilisée comme :
    - Malus d'allocation (bots corrélés → budget réduit).
    - Détection de bots redondants (corrélation > 0.85 → un seul suffit).
    - Stress test par scénarios (bull/bear/range).

Référence :
    López de Prado, L. (2016). "Building Diversified Portfolios that
    Outperform Out-of-Sample." Journal of Portfolio Management 42(4): 59-69.
"""
import logging
import math
from typing import Dict, List, Optional
from statistics import mean, stdev

logger = logging.getLogger(__name__)


def compute_returns_correlation(returns_a: List[float],
                                  returns_b: List[float]) -> float:
    """Corrélation de Pearson entre deux séries de rendements.

    Returns
    -------
    float
        Corrélation ∈ [-1, 1]. 0 si non calculable.
    """
    n = min(len(returns_a), len(returns_b))
    if n < 3:
        return 0.0  # pas assez de samples
    a = returns_a[:n]
    b = returns_b[:n]
    avg_a = mean(a)
    avg_b = mean(b)
    cov = sum((a[i] - avg_a) * (b[i] - avg_b) for i in range(n)) / max(n - 1, 1)
    std_a = stdev(a) if len(a) > 1 else 0.0
    std_b = stdev(b) if len(b) > 1 else 0.0
    if std_a == 0 or std_b == 0:
        return 0.0
    return round(cov / (std_a * std_b), 4)


def build_correlation_matrix(
        bots_returns: Dict[str, List[float]]
) -> Dict[str, Dict[str, float]]:
    """Construit la matrice de corrélation des rendements entre bots.

    Parameters
    ----------
    bots_returns : dict
        {bot_key: [returns_by_period]} — par ex. les rendements daily
        de chaque bot actif.

    Returns
    -------
    dict of dict
        matrix[bot_a][bot_b] = corrélation ∈ [-1, 1]
    """
    keys = list(bots_returns.keys())
    matrix = {k: {} for k in keys}
    for i, k1 in enumerate(keys):
        for k2 in keys[i:]:
            if k1 == k2:
                corr = 1.0
            else:
                corr = compute_returns_correlation(
                    bots_returns[k1], bots_returns[k2]
                )
            matrix[k1][k2] = corr
            matrix[k2][k1] = corr
    return matrix


def correlation_malus(corr_matrix: Dict[str, Dict[str, float]],
                     bot_key: str,
                     active_bots: List[str],
                     max_malus: float = 0.5) -> float:
    """Calcule un malus d'allocation basé sur la corrélation moyenne.

    Un bot très corrélé aux bots actifs reçoit un malus :
        malus = max_malus × avg(|corr| avec les autres bots actifs)

    Le budget alloué est alors :
        budget × (1 - malus)

    Parameters
    ----------
    corr_matrix : dict
        Matrice de corrélation (cf. build_correlation_matrix).
    bot_key : str
        Bot à évaluer.
    active_bots : list
        Autres bots actifs (sans bot_key).
    max_malus : float
        Malus maximal (défaut 50% de réduction).

    Returns
    -------
    float
        Malus ∈ [0, max_malus].
    """
    if not active_bots or bot_key not in corr_matrix:
        return 0.0
    corrs = []
    for other in active_bots:
        if other == bot_key:
            continue
        c = corr_matrix.get(bot_key, {}).get(other, 0.0)
        corrs.append(abs(c))
    if not corrs:
        return 0.0
    avg_corr = mean(corrs)
    # Le malus augmente avec la corrélation moyenne
    # Un bot avec avg|corr|=1 → malus = max_malus
    # Un bot avec avg|corr|=0 → malus = 0
    return round(min(avg_corr * max_malus, max_malus), 4)


def detect_redundant_bots(corr_matrix: Dict[str, Dict[str, float]],
                          threshold: float = 0.85) -> List[tuple]:
    """Détecte les paires de bots redondants (corrélation > threshold).

    Parameters
    ----------
    corr_matrix : dict
        Matrice de corrélation.
    threshold : float
        Seuil de redondance (défaut 0.85).

    Returns
    -------
    list of (bot_a, bot_b, corr)
        Paires redondantes (par corr décroissant).
    """
    pairs = []
    keys = list(corr_matrix.keys())
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            c = corr_matrix.get(k1, {}).get(k2, 0.0)
            if abs(c) >= threshold:
                pairs.append((k1, k2, c))
    pairs.sort(key=lambda x: -abs(x[2]))
    return pairs


def diversification_ratio(corr_matrix: Dict[str, Dict[str, float]]) -> float:
    """Ratio de diversification du portefeuille (Choueifaty 2008).

    DR = (somme des poids × vol_individuelle) / vol_portefeuille

    Pour simplifier (poids équipondérés) :
        DR = mean(vol_i) / vol_pondérée

    Plus DR est élevé, plus le portefeuille est diversifié. DR=1 si tous
    les bots sont parfaitement corrélés (aucune diversification).

    Returns
    -------
    float
        Ratio de diversification ≥ 1. Plus élevé = plus diversifié.
    """
    keys = list(corr_matrix.keys())
    n = len(keys)
    if n < 2:
        return 1.0
    # Approximation : 1 + (1 - avg_corr) × (n - 1) / n
    # Si corr moyenne = 0 → DR = 1 + (n-1)/n
    # Si corr moyenne = 1 → DR = 1
    total_corr = 0.0
    count = 0
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            c = corr_matrix.get(k1, {}).get(k2, 0.0)
            total_corr += c
            count += 1
    avg_corr = total_corr / count if count > 0 else 0.0
    dr = 1.0 + (1.0 - avg_corr) * (n - 1) / n
    return round(dr, 4)
