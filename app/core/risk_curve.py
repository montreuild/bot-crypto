"""Courbe de dé-risquage en drawdown — partagée backtest/live (BT-09).

Source unique de la règle appliquée par ``RiskManager.compute_risk`` (live) :
le risque par trade est réduit quand l'équité s'éloigne de son plus-haut.
Le Backtester applique désormais la MÊME courbe (avant, il simulait des
tailles pleines en pleine séquence de pertes → drawdown/Sharpe de sélection
non représentatifs du comportement réel du bot).
"""


def risk_multiplier(drawdown_frac: float) -> float:
    """Facteur de risque selon le drawdown courant (fraction, ex. 0.07 = 7 %).

    ×0.5 au-delà de 10 % de drawdown, ×0.75 au-delà de 5 %, ×1 sinon —
    identique à ``RiskGate.compute_risk`` (app/core/risk_sizer.py — ARCH-011).
    """
    if drawdown_frac > 0.10:
        return 0.5
    if drawdown_frac > 0.05:
        return 0.75
    return 1.0
