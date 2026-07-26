"""Calculs d'exécution partagés backtest ↔ live (frais, emprunt, PnL, sizing).

Source unique des formules monétaires, consommée par ``app/engine/backtest.py``
(Backtester) ET ``app/live/position_mixin.py`` (LiveTrader). Avant ce module,
chaque chemin réimplémentait ses propres formules avec de petites divergences
(ex. coût d'emprunt simple en backtest vs composé en live) — rendant les
écarts paper/backtest vs live impossibles à attribuer.

Conventions :
  - ``side`` : "long" | "short"
  - les frais sont proportionnels : prix × taille × taux
  - le coût d'emprunt margin est à intérêts composés sur ``periods_per_day``
    périodes par jour (OKX facture l'intérêt margin à l'heure → 24) — c'est la
    formule du live, aussi utilisée par le backtest.

La parité est verrouillée par ``tests/test_execution_parity.py``.
"""
from __future__ import annotations


def trade_fees(price: float, size: float, fee_rate: float) -> float:
    """Frais proportionnels d'un fill (entrée OU sortie)."""
    return float(price) * float(size) * float(fee_rate)


def borrow_cost(notional: float, daily_rate: float, hours_held: float,
                periods_per_day: int = 24) -> float:
    """Coût d'emprunt margin à intérêts composés.

    ``periods_per_day`` périodes de facturation par jour (OKX = 24, horaire) ;
    chaque période capitalise au taux ``daily_rate / periods_per_day``.
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
              hours_held: float, periods_per_day: int = 24,
              venue=None) -> tuple:
    """Décompte complet d'une clôture, partagé backtest ↔ live.

    Retourne ``(pnl_net, fees_sortie, cout_emprunt)`` :
      fees   = coût du fill de sortie (cf. :func:`venue_trade_cost`)
      borrow = intérêts composés margin sur le notional
      pnl    = brut directionnel − fees − borrow

    ``venue`` (G2, optionnel) fait passer les frais par le modèle de coûts de
    la venue (fixe + plancher + taxe de transaction). ``None`` = frais
    strictement proportionnels, comportement historique.
    """
    fees   = venue_trade_cost(exit_price, size, fee_rate, side=side,
                              venue=venue, is_entry=False)
    borrow = borrow_cost(notional, daily_rate, hours_held, periods_per_day)
    return net_pnl(side, entry, exit_price, size, fees, borrow), fees, borrow


# ── Contraintes et coûts par venue (G2 — actions) ───────────────────────────
#
# Une action ne s'achète pas en fractions (lot_size/fractional), son prix vit
# sur une grille (tick_size), et son coût d'entrée n'est pas qu'un pourcentage :
# commission fixe, plancher de courtage, et taxe sur les transactions
# financières (TTF française, due à l'achat seulement). Ces trois formules sont
# ici — donc partagées backtest ↔ live comme le reste du module — et **neutres
# quand la venue est None ou crypto** (défauts : lot_size=0, tick_size=0,
# fractional=True, fee_pct=None, fee_fixed=0, taxe=0).


def quantize_size(size: float, venue=None) -> float:
    """Arrondit une quantité aux contraintes de la venue (à la baisse).

    ``fractional=False`` (actions) → entier ; ``lot_size > 0`` → multiple du
    lot. Toujours **vers le bas** : dépasser la taille voulue augmenterait le
    risque engagé au-delà de ce que le sizing a autorisé.
    """
    size = float(size)
    if venue is None or size <= 0:
        return size
    lot = float(getattr(venue, "lot_size", 0.0) or 0.0)
    if lot <= 0 and not getattr(venue, "fractional", True):
        lot = 1.0
    if lot <= 0:
        return size
    return (size // lot) * lot


def quantize_price(price: float, venue=None) -> float:
    """Aligne un prix sur la grille de cotation (``tick_size``) de la venue."""
    price = float(price)
    if venue is None or price <= 0:
        return price
    tick = float(getattr(venue, "tick_size", 0.0) or 0.0)
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


def venue_trade_cost(price: float, size: float, fee_rate: float,
                     side: str = "long", venue=None,
                     is_entry: bool = True) -> float:
    """Coût total d'un fill : commission + plancher + taxe de transaction.

    ``fee_rate`` reste le taux de repli (``trading.taker_fee``) : une venue qui
    ne déclare pas de ``fee_pct`` le conserve. Sans venue, le résultat est
    **exactement** ``trade_fees(price, size, fee_rate)`` — les chemins crypto
    ne changent pas d'un centime.

    Taxe de transaction (TTF) : assise sur le **notionnel**, appliquée à
    l'acquisition uniquement quand ``tax_on_buy_only`` (le cas français), donc
    à l'entrée d'un long et à la sortie d'un short.
    """
    notional = float(price) * float(size)
    if venue is None:
        return notional * float(fee_rate)

    pct = getattr(venue, "fee_pct", None)
    rate = float(fee_rate) if pct is None else float(pct)
    commission = notional * rate + float(getattr(venue, "fee_fixed", 0.0) or 0.0)
    fee_min = float(getattr(venue, "fee_min", 0.0) or 0.0)
    if notional > 0:
        commission = max(commission, fee_min)

    tax_pct = float(getattr(venue, "transaction_tax_pct", 0.0) or 0.0)
    tax = 0.0
    if tax_pct > 0:
        buy_only = bool(getattr(venue, "tax_on_buy_only", True))
        is_buy = (side == "long") == bool(is_entry)
        if not buy_only or is_buy:
            tax = notional * tax_pct
    return commission + tax


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


def size_impact_cost(notional: float, spread_pct: float, slippage_k: float,
                     avg_quote_volume: float) -> float:
    """Coût d'impact croissant avec la taille RELATIVE du trade (participation
    au volume) — 0.0 si le volume moyen est absent/nul (BT-10, FIN-07).

    ``notional × spread_pct × k × (notional / volume_quote_moyen)`` : un trade
    à 1 % du volume moyen coûte ~1 % de spread en plus ; à 50 %, ×50. Linéaire
    en participation, quadratique en notional (impact de marché standard).

    Formule UNIQUE partagée par ``Backtester._impact_cost`` (modèle
    ``backtest.slippage_model: size``, moyenne 20 barres causale) et le
    slippage paper trading live (``trading.paper_slippage_model: size``,
    moyenne glissante de ``OHLCVCache``) — évite de re-diverger les deux
    chemins comme l'estimation frais maker/taker (cf. FIN-06).
    """
    if avg_quote_volume <= 0 or notional <= 0:
        return 0.0
    return float(notional) * float(spread_pct) * float(slippage_k) * (float(notional) / float(avg_quote_volume))


