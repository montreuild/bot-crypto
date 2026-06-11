"""Calculs d'exécution partagés backtest ↔ live (frais, emprunt, PnL, sizing).

Source unique des formules monétaires, consommée par ``app/engine/backtest.py``
(Backtester) ET ``app/live/position_mixin.py`` (LiveTrader). Avant ce module,
chaque chemin réimplémentait ses propres formules avec de petites divergences
(ex. coût d'emprunt simple en backtest vs composé en live) — rendant les
écarts paper/backtest vs live impossibles à attribuer.

Conventions :
  - ``side`` : "long" | "short"
  - les frais sont proportionnels : prix × taille × taux
  - le coût d'emprunt suit la convention Binance Margin : ``periods_per_day``
    périodes par jour (3 par défaut) à intérêts composés — c'est la formule
    du live, désormais utilisée aussi par le backtest.

La parité est verrouillée par ``tests/test_execution_parity.py``.
"""
from __future__ import annotations


def trade_fees(price: float, size: float, fee_rate: float) -> float:
    """Frais proportionnels d'un fill (entrée OU sortie)."""
    return float(price) * float(size) * float(fee_rate)


def borrow_cost(notional: float, daily_rate: float, hours_held: float,
                periods_per_day: int = 3) -> float:
    """Coût d'emprunt margin à intérêts composés (convention Binance).

    ``periods_per_day`` périodes de facturation par jour ; chaque période
    capitalise au taux ``daily_rate / periods_per_day``.
    """
    if notional <= 0 or daily_rate <= 0 or hours_held <= 0:
        return 0.0
    periods_per_day = max(int(periods_per_day), 1)
    r_period  = float(daily_rate) / periods_per_day
    n_periods = float(hours_held) * periods_per_day / 24.0
    return float(notional) * ((1 + r_period) ** n_periods - 1)


def gross_pnl(side: str, entry: float, exit_price: float, size: float) -> float:
    """PnL brut directionnel (hors frais et emprunt)."""
    direction = 1.0 if side == "long" else -1.0
    return (float(exit_price) - float(entry)) * float(size) * direction


def net_pnl(side: str, entry: float, exit_price: float, size: float,
            exit_fees: float, borrow: float = 0.0) -> float:
    """PnL net d'une clôture : brut − frais de sortie − coût d'emprunt.

    Les frais d'entrée sont, par convention commune aux deux chemins, déduits
    du capital au moment de l'ouverture — ils ne sont pas re-déduits ici.
    """
    return gross_pnl(side, entry, exit_price, size) - float(exit_fees) - float(borrow)


def close_pnl(side: str, entry: float, exit_price: float, size: float,
              notional: float, fee_rate: float, daily_rate: float,
              hours_held: float, periods_per_day: int = 3) -> tuple:
    """Décompte complet d'une clôture, partagé backtest ↔ live.

    Retourne ``(pnl_net, fees_sortie, cout_emprunt)`` :
      fees   = exit_price × size × fee_rate
      borrow = intérêts composés Binance sur le notional
      pnl    = brut directionnel − fees − borrow
    """
    fees   = trade_fees(exit_price, size, fee_rate)
    borrow = borrow_cost(notional, daily_rate, hours_held, periods_per_day)
    return net_pnl(side, entry, exit_price, size, fees, borrow), fees, borrow


def risk_position_size(capital: float, risk_pct: float, entry: float,
                       stop: float, max_notional_pct: float = 1.0) -> tuple:
    """Sizing par risque fixe : taille = (capital × risque) / distance au stop,
    plafonnée à ``max_notional_pct`` du capital. Retourne (size, notional)."""
    stop_dist = abs(float(entry) - float(stop))
    if stop_dist <= 0 or entry <= 0 or capital <= 0:
        return 0.0, 0.0
    size     = capital * float(risk_pct) / stop_dist
    notional = size * entry
    max_notional = capital * float(max_notional_pct)
    if notional > max_notional:
        size     = max_notional / entry
        notional = max_notional
    return size, notional


def cap_notional(size: float, price: float, max_notional: float) -> tuple:
    """Plafonne (size, notional) à un notionnel maximal absolu."""
    notional = float(size) * float(price)
    if notional > max_notional:
        notional = max(float(max_notional), 0.0)
        size     = notional / price if price > 0 else 0.0
    return size, notional
