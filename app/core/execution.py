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

from typing import Optional


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


def venue_borrow_rate(daily_rate: float, venue=None) -> float:
    """Taux d'emprunt journalier applicable à un trade sur ``venue`` (S11).

    Sans venue : ``daily_rate`` tel quel (comportement historique, chemins
    crypto inchangés). Avec venue : c'est **elle** qui tranche — un marché spot
    (spot crypto comme actions au comptant) n'emprunte rien, donc 0.

    Avant S11, ``trading.borrow_rate_daily`` était appliqué inconditionnellement
    des deux côtés (``backtest.py`` et ``position_close_mixin.py``) : chaque
    trade SBF 120 payait 0,072 %/jour d'intérêt fictif — ~30 %/an sur un achat
    comptant, de quoi effacer l'edge d'une stratégie actions en journalier.
    """
    if venue is None:
        return float(daily_rate)
    # L2 (§27) — un perpétuel ne s'EMPRUNTE pas, il paie (ou encaisse) un
    # funding par périodes discrètes. Le facturer au taux d'emprunt margin
    # donnait un coût de portage toujours positif et de la mauvaise magnitude.
    # Le vrai coût est calculé par `trade_economics.funding_cost`, alimenté par
    # la série `funding_rate` de `app/core/derivatives.py`.
    if getattr(venue, "market_type", "spot") == "perp":
        return 0.0
    fn = getattr(venue, "effective_borrow_rate", None)
    if callable(fn):
        return float(fn(daily_rate))
    # Venue « canard » (test double, dict-like) : on retombe sur le marché.
    if getattr(venue, "market_type", "spot") == "margin":
        return float(daily_rate)
    return 0.0


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
    la venue (fixe + plancher + taxe de transaction) **et** gouverne le coût
    d'emprunt (S11 : 0 sur une venue spot — cf. :func:`venue_borrow_rate`).
    ``None`` = frais proportionnels + emprunt au taux global, comportement
    historique.
    """
    fees   = venue_trade_cost(exit_price, size, fee_rate, side=side,
                              venue=venue, is_entry=False)
    borrow = borrow_cost(notional, venue_borrow_rate(daily_rate, venue),
                         hours_held, periods_per_day)
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


#: Modes de sortie GÉNÉRIQUES, utilisables par n'importe quelle stratégie.
#:
#: Chaque stratégie déclarait jusqu'ici sa gestion de sortie à la main, par un
#: assemblage de champs (`exits`, `disable_trailing`, `trail_override`,
#: `be_after_partial`). Résultat : comparer deux stratégies revenait à comparer
#: aussi deux gestions de position, et personne ne pouvait dire laquelle des
#: deux expliquait l'écart. Le dossier `smc_ml_edge` a buté exactement là-dessus
#: — le vrai coupable était un stop trop serré, pas le signal.
#:
#: Nommer les modes rend la question mesurable : on fixe le signal, on fait
#: varier le mode, et l'écart est attribuable.
EXIT_MODES = (
    "as_declared",            # ne touche à rien — défaut, aucune régression
    "sl_tp",                  # stop et cible fixes, aucun suivi
    "trailing",               # suiveur actif dès l'entrée
    "trailing_after_profit",  # suiveur armé seulement une fois en profit
    "tp1_tp2_runner",         # partielles + point mort + suiveur sur le reliquat
)

#: Cibles partielles du mode `tp1_tp2_runner` — 25 % à 1R, 25 % à 2R, le
#: reliquat (50 %) laissé courir sous suiveur. Valeurs des deux spécifications ;
#: elles sont un POINT DE DÉPART à optimiser, pas une recommandation mesurée.
TP1_TP2_RUNNER_EXITS = ({"r": 1.0, "fraction": 0.25, "reason": "tp1"},
                        {"r": 2.0, "fraction": 0.25, "reason": "tp2"})

#: Profit, en multiples du risque, à partir duquel `trailing_after_profit` arme
#: le suiveur. En dessous, le stop initial tient : c'est tout l'objet du mode —
#: ne pas resserrer un trade qui n'a pas encore fait ses preuves.
TRAIL_ACTIVATE_R_DEFAUT = 1.0


def apply_exit_mode(signal: dict, mode: str,
                    params: Optional[dict] = None) -> dict:
    """Applique un mode de sortie à ``signal`` et rend une COPIE.

    Le signal reste la source de vérité pour tout le reste (sens, score,
    stop) : un mode ne décide jamais d'entrer, il décide seulement comment on
    sort. C'est ce qui permet de faire varier la sortie sans toucher au signal.

    ``as_declared`` rend le signal inchangé — c'est le défaut, donc brancher ce
    mécanisme ne modifie aucun backtest existant tant que personne ne demande
    autre chose.

    Un mode inconnu est refusé bruyamment plutôt que silencieusement ignoré :
    une faute de frappe dans un YAML doit se voir, pas produire un backtest
    dont on croit qu'il teste autre chose.
    """
    mode = str(mode or "as_declared").strip()
    if mode not in EXIT_MODES:
        raise ValueError(
            f"mode de sortie inconnu : {mode!r} — attendus {list(EXIT_MODES)}")
    if mode == "as_declared":
        return signal

    p = params or {}
    out = dict(signal)

    if mode == "sl_tp":
        out["exits"] = []
        out["disable_trailing"] = True
        return out

    if mode == "trailing":
        out["exits"] = []
        out["disable_trailing"] = False
        out.pop("_trail_activate_r", None)
        return out

    if mode == "trailing_after_profit":
        out["exits"] = []
        out["disable_trailing"] = False
        out["_trail_activate_r"] = float(
            p.get("trail_activate_r", TRAIL_ACTIVATE_R_DEFAUT))
        return out

    # tp1_tp2_runner
    out["exits"] = [dict(e) for e in (p.get("exits") or TP1_TP2_RUNNER_EXITS)]
    out["disable_trailing"] = False
    out["be_after_partial"] = bool(p.get("be_after_partial", True))
    return out


def plan_partial_targets(signal: dict, entry: float, stop: float) -> list:
    """L1 (§29) — cibles de sortie partielle d'un signal, en prix absolus.

    Contrat : ``signal["exits"] = [{"r": 1.0, "fraction": 0.25}, …]``, chaque
    entrée portant soit ``r`` (multiple du risque entrée→stop), soit ``price``
    (niveau absolu, typiquement une poche de liquidité). Le runner est le
    reliquat : des fractions qui somment à moins de 1 le laissent courir.

    Absent → liste vide → comportement tout-ou-rien strictement inchangé.

    Ici et non dans le backtest : le live doit planifier EXACTEMENT les mêmes
    niveaux, sinon les deux divergent dès le premier TP partiel.
    """
    spec = signal.get("exits") or []
    if not spec:
        return []
    risque = abs(entry - stop)
    sens = 1 if signal.get("side") == "long" else -1
    out, cumul = [], 0.0
    for n, e in enumerate(spec, 1):
        frac = float(e.get("fraction", 0) or 0)
        if frac <= 0 or cumul + frac > 1.0:
            continue
        if e.get("price") is not None:
            px = float(e["price"])
        elif e.get("r") is not None and risque > 0:
            px = entry + sens * float(e["r"]) * risque
        else:
            continue
        # Une cible du mauvais côté de l'entrée serait touchée dès la première
        # barre : c'est une erreur de spécification, pas une sortie.
        if (sens > 0 and px <= entry) or (sens < 0 and px >= entry):
            continue
        cumul += frac
        out.append({"price": px, "fraction": frac,
                    "reason": str(e.get("reason") or f"tp{n}")})
    return out


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


# ── Description du modèle de coûts (S11) ────────────────────────────────────
#
# Un backtest ou une optimisation ne dit rien de ce qu'il a facturé : deux runs
# aux chiffres très différents peuvent ne différer que par la venue résolue
# (spot vs margin, crypto vs actions). `cost_model` rend ce contexte explicite,
# à partir des MÊMES sources que les formules ci-dessus — pas d'un commentaire
# qui dériverait. Consommé par le log de démarrage du Backtester, par
# `BacktestResult.to_dict()` et par la fiche de job de l'optimiseur.


def cost_model(cfg: dict, venue=None) -> dict:
    """Décompte des coûts **effectivement appliqués** pour cette venue.

    Chaque champ est la valeur réellement utilisée au calcul, pas la valeur
    configurée : ``borrow_rate_daily`` vaut 0 sur une venue spot, et
    ``fee_rate`` reflète le ``fee_pct`` de la venue quand elle en déclare un.
    """
    tcfg = cfg.get("trading", {}) or {}
    bcfg = cfg.get("backtest", {}) or {}

    taker = float(tcfg.get("taker_fee", 0.001))
    maker = float(tcfg.get("maker_fee", 0.0004))
    fee_pct = getattr(venue, "fee_pct", None) if venue is not None else None
    borrow = venue_borrow_rate(float(tcfg.get("borrow_rate_daily", 0.0) or 0.0), venue)
    periods = int(tcfg.get("borrow_periods_per_day", 24) or 24)

    return {
        # ── Identité de la venue ────────────────────────────────────────────
        "venue": getattr(venue, "name", None) or "(globales)",
        "market_type": getattr(venue, "market_type", "spot"),
        "margin_mode": getattr(venue, "margin_mode", None),
        "max_leverage": float(getattr(venue, "max_leverage", 1.0) or 1.0),
        "asset_class": getattr(venue, "asset_class", "crypto"),
        "quote_currency": getattr(venue, "quote_currency", "USDC"),
        "exchange": getattr(venue, "exchange", None),
        "calendar": getattr(venue, "calendar", "") or "",
        "can_execute": bool(getattr(venue, "can_execute", True)),
        "allow_short": bool(getattr(venue, "allow_short", True)),
        # ── Frais ───────────────────────────────────────────────────────────
        # `fee_rate_*` = taux réellement appliqué (override de venue prioritaire).
        "fee_rate_taker": taker if fee_pct is None else float(fee_pct),
        "fee_rate_maker": maker if fee_pct is None else float(fee_pct),
        "fee_pct_override": None if fee_pct is None else float(fee_pct),
        "fee_fixed": float(getattr(venue, "fee_fixed", 0.0) or 0.0),
        "fee_min": float(getattr(venue, "fee_min", 0.0) or 0.0),
        "transaction_tax_pct": float(getattr(venue, "transaction_tax_pct", 0.0) or 0.0),
        "tax_on_buy_only": bool(getattr(venue, "tax_on_buy_only", True)),
        # ── Emprunt ─────────────────────────────────────────────────────────
        "borrows": bool(borrow > 0),
        "borrow_rate_daily": borrow,
        "borrow_periods_per_day": periods,
        "borrow_rate_annual": ((1 + borrow / periods) ** (periods * 365) - 1
                               if borrow > 0 else 0.0),
        # ── Friction d'exécution ────────────────────────────────────────────
        "spread_pct": float(bcfg.get("spread_pct", 0.0005)),
        "slippage_model": str(bcfg.get("slippage_model", "static")),
        "slippage_k": float(bcfg.get("slippage_k", 1.0)),
        "partial_fill_pct": float(bcfg.get("partial_fill_pct", 0.95)),
        # ── Contraintes d'instrument ────────────────────────────────────────
        "fractional": bool(getattr(venue, "fractional", True)),
        "lot_size": float(getattr(venue, "lot_size", 0.0) or 0.0),
        "tick_size": float(getattr(venue, "tick_size", 0.0) or 0.0),
        "min_notional": float(getattr(venue, "min_notional", 0.0) or 0.0),
        "max_notional_pct": float(bcfg.get("max_notional_pct", 0.20)),
    }


def _pct(x: float, decimals: int = 3) -> str:
    return f"{x * 100:.{decimals}f} %"


def format_cost_model(m: dict, symbol: str = "", timeframe: str = "") -> str:
    """Rend :func:`cost_model` lisible en trois lignes, pour les logs.

    Volontairement explicite sur ce qui NE s'applique pas (« pas d'emprunt ») :
    l'absence de ligne se lit comme un oubli, pas comme une information.
    """
    quoi = " ".join(x for x in (symbol, timeframe) if x)
    lev = f"levier ×{m['max_leverage']:g}"
    mode = f"/{m['margin_mode']}" if m.get("margin_mode") else ""
    cal = f" [{m['calendar']}]" if m.get("calendar") else ""
    exe = "" if m.get("can_execute", True) else " (data-only)"
    short = "" if m.get("allow_short", True) else ", short interdit"

    tete = (f"{quoi} @ venue '{m['venue']}' — {m.get('exchange') or '?'}:"
            f"{m['market_type']}{mode}, {lev}, {m['asset_class']}/"
            f"{m['quote_currency']}{cal}{exe}{short}")

    # Frais : le détail dépend de la classe d'actif (les actions ont des frais
    # fixes, un plancher de courtage et une taxe de transaction).
    frais = [f"taker {_pct(m['fee_rate_taker'])} / maker {_pct(m['fee_rate_maker'])}"]
    if m["fee_fixed"]:
        frais.append(f"+ {m['fee_fixed']:.2f} fixe/ordre")
    if m["fee_min"]:
        frais.append(f"plancher {m['fee_min']:.2f}")
    if m["transaction_tax_pct"]:
        assiette = "à l'achat" if m["tax_on_buy_only"] else "aux deux sens"
        frais.append(f"taxe transaction {_pct(m['transaction_tax_pct'])} {assiette}")
    frais.append(f"spread {_pct(m['spread_pct'])}")
    frais.append(f"slippage {m['slippage_model']}"
                 + (f" (k={m['slippage_k']:g})" if m["slippage_model"] != "static" else ""))
    if m["partial_fill_pct"] < 1.0:
        frais.append(f"fill partiel {m['partial_fill_pct']:.0%}")

    if m["borrows"]:
        emprunt = (f"emprunt {_pct(m['borrow_rate_daily'], 4)}/jour "
                   f"× {m['borrow_periods_per_day']} périodes "
                   f"(≈ {_pct(m['borrow_rate_annual'], 1)}/an)")
    else:
        emprunt = f"pas d'emprunt (marché {m['market_type']})"

    contraintes = []
    if not m["fractional"]:
        contraintes.append("quantité entière")
    if m["lot_size"]:
        contraintes.append(f"lot {m['lot_size']:g}")
    if m["tick_size"]:
        contraintes.append(f"tick {m['tick_size']:g}")
    if m["min_notional"]:
        contraintes.append(f"notionnel min {m['min_notional']:.2f}")
    contraintes.append(f"notionnel max {m['max_notional_pct']:.0%} du capital")

    return (f"[Coûts] {tete}\n"
            f"        frais : {' · '.join(frais)}\n"
            f"        {emprunt} · {' · '.join(contraintes)}")


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


