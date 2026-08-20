"""Courbe de dé-risquage en drawdown — partagée backtest/live (BT-09).

Source unique de la règle appliquée par ``RiskManager.compute_risk`` (live) :
le risque par trade est réduit quand l'équité s'éloigne de son plus-haut.
Le Backtester applique désormais la MÊME courbe (avant, il simulait des
tailles pleines en pleine séquence de pertes → drawdown/Sharpe de sélection
non représentatifs du comportement réel du bot).
"""


def risk_multiplier_steps(drawdown_frac: float) -> float:
    """Ancienne marche (FIN-11) : ×1 / ×0,75 / ×0,5 aux seuils 5 % et 10 %."""
    if drawdown_frac > 0.10:
        return 0.5
    if drawdown_frac > 0.05:
        return 0.75
    return 1.0


def risk_multiplier(drawdown_frac: float) -> float:
    """Facteur de risque selon le drawdown (fraction, ex. 0.07 = 7 %).

    Rampe linéaire 5 % → 15 % de ×1 à ×0,5 (FIN-11). Aux bornes : même
    politique que l'escalier historique (plein sous 5 %, moitié à 15 %+).
    À 10 % le facteur vaut encore 0,75 — sans le saut 0,75 → 0,50.
    """
    if drawdown_frac <= 0.05:
        return 1.0
    if drawdown_frac >= 0.15:
        return 0.5
    return 1.0 - 0.5 * (drawdown_frac - 0.05) / 0.10
