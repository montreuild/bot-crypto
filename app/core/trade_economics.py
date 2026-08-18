"""L2 (§2, §4, §23, §27, §36, §93) — l'économie du trade AVANT de le prendre.

Le dépôt facturait correctement, mais **après coup** : frais, emprunt et impact
n'entraient dans le calcul qu'à la clôture (``Backtester._close_at``). La
décision d'entrée, elle, comparait un gain BRUT à un risque BRUT. Le R/R annoncé
n'était donc pas celui qu'on encaissait.

L1 en a donné la démonstration chiffrée : sur BTC 4 h, `partiel_struct` affiche
un profit factor de 1,147 — gains bruts supérieurs aux pertes brutes — pour un
PnL net de −0,33 %. Chaque jambe supplémentaire est un fill de plus, et ce coût
n'entrait dans aucune décision.

Ce module ne réimplémente aucune formule monétaire : il compose celles
d'``app/core/execution.py``, qui restent la source unique partagée backtest ↔
live. Il ne fait qu'appliquer ces coûts **au moment du choix**.
"""
from dataclasses import dataclass
from typing import Optional

from app.core.execution import borrow_cost, size_impact_cost, venue_trade_cost

#: Périodes de funding par jour sur un perpétuel (OKX : toutes les 8 h).
FUNDING_PERIODES_PAR_JOUR = 3


@dataclass(frozen=True)
class Couts:
    """Coûts d'un aller-retour, en devise de cotation."""

    frais_entree: float
    frais_sortie: float
    slippage: float
    portage: float          # emprunt margin OU funding perp, jamais les deux

    @property
    def total(self) -> float:
        return self.frais_entree + self.frais_sortie + self.slippage + self.portage


def funding_cost(notional: float, funding_rate: float, hours_held: float,
                 periods_per_day: int = FUNDING_PERIODES_PAR_JOUR) -> float:
    """Coût de portage d'un perpétuel — §27.

    ⚠ Distinct de ``borrow_cost`` : le funding se règle par périodes DISCRÈTES
    (toutes les 8 h chez OKX), pas en intérêt composé continu, et **il change de
    signe** — un funding négatif est encaissé, pas payé. Facturer un perp au
    ``borrow_rate_daily`` comme le faisait le dépôt donne donc un coût de
    portage toujours positif et de la mauvaise magnitude.

    Un signe négatif en sortie est légitime : c'est un gain.
    """
    if notional <= 0 or hours_held <= 0 or periods_per_day <= 0:
        return 0.0
    heures_par_periode = 24.0 / periods_per_day
    periodes = hours_held / heures_par_periode
    return float(notional) * float(funding_rate) * periodes


def round_trip_cost(entree: float, taille: float, *, venue=None,
                    fee_rate: float = 0.001, spread_pct: float = 0.0,
                    heures_estimees: float = 0.0,
                    borrow_rate_daily: float = 0.0,
                    borrow_periods_per_day: int = 24,
                    funding_rate: Optional[float] = None,
                    slippage_k: float = 1.0,
                    volume_quote: float = 0.0,
                    side: str = "long",
                    cible: Optional[float] = None) -> Couts:
    """Coût complet d'un aller-retour, estimé à l'entrée.

    ``funding_rate`` non nul bascule le portage sur le modèle perpétuel et
    **remplace** l'emprunt : un perp ne paie pas les deux.
    """
    notional = max(float(entree) * float(taille), 0.0)
    if notional <= 0:
        return Couts(0.0, 0.0, 0.0, 0.0)
    f_in = venue_trade_cost(entree, taille, fee_rate, side=side, venue=venue,
                            is_entry=True)
    f_out = venue_trade_cost(cible if cible is not None else entree, taille, fee_rate, side=side, venue=venue,
                             is_entry=False)
    # Le spread est payé des DEUX côtés : à l'entrée on achète au-dessus du
    # milieu, à la sortie on vend en dessous.
    slip = notional * float(spread_pct) * 2.0
    if volume_quote > 0:
        slip += 2.0 * size_impact_cost(notional, spread_pct, slippage_k,
                                       volume_quote)
    if funding_rate is not None:
        portage = funding_cost(notional, funding_rate, heures_estimees)
    else:
        portage = borrow_cost(notional, borrow_rate_daily, heures_estimees,
                              borrow_periods_per_day)
    return Couts(f_in, f_out, slip, portage)


def net_rr(entree: float, stop: float, cible: float, taille: float,
           couts: Couts, side: str = "long") -> float:
    """§2 — rendement net rapporté au risque net.

    Le risque NET est plus grand que le risque brut (les coûts s'ajoutent à la
    perte) et le gain net plus petit : les deux effets vont dans le même sens,
    et c'est pourquoi un R/R brut de 2,0 peut valoir 1,4 en net sur un petit
    mouvement. Retourne 0.0 si le risque net est nul ou négatif.
    """
    sens = 1 if side == "long" else -1
    brut_gain = (float(cible) - float(entree)) * sens * float(taille)
    brut_risque = (float(entree) - float(stop)) * sens * float(taille)
    if brut_risque <= 0:
        return 0.0
    gain_net = brut_gain - couts.total
    risque_net = brut_risque + couts.total
    if risque_net <= 0:
        return 0.0
    return gain_net / risque_net


def economic_edge_ok(gain_brut_attendu: float, couts: Couts,
                     multiple: float = 5.0) -> bool:
    """§4 §36 — le gain attendu doit valoir plusieurs fois son coût.

    Un seuil de gain ABSOLU (`min_gain_pct: 0.4`) ne dit rien : 0,4 % couvre
    largement les frais sur une venue à 0,08 %, et pas du tout sur une action
    avec commission fixe et taxe de transaction. Le rapporter aux coûts RÉELS de
    la venue rend le filtre transposable d'un marché à l'autre — c'est tout
    l'intérêt de la formulation de la spécification.
    """
    if multiple <= 0:
        return True
    if couts.total <= 0:
        return gain_brut_attendu > 0
    return gain_brut_attendu >= multiple * couts.total


def execution_score(spread_pct: float, notional: float, volume_quote: float,
                    gain_attendu: float, *, spread_max: float = 0.0,
                    slippage_k: float = 1.0) -> tuple:
    """§93 — qualité d'exécution : ``(note ∈ [0, 1], motif_de_refus | None)``.

    Deux composantes seulement, parce que ce sont les deux que le dépôt sait
    mesurer sans carnet d'ordres : la largeur du spread, et la part du gain
    attendu qu'un impact de taille viendrait manger.
    """
    if spread_max > 0 and spread_pct > spread_max:
        return 0.0, f"spread {spread_pct:.4%} > {spread_max:.4%}"
    note_spread = max(0.0, 1.0 - spread_pct / max(spread_max or 0.01, 1e-9))
    impact = (size_impact_cost(notional, spread_pct, slippage_k, volume_quote)
              if volume_quote > 0 else 0.0)
    part = (impact / gain_attendu) if gain_attendu > 0 else 0.0
    note_impact = max(0.0, 1.0 - min(part, 1.0))
    return round(0.5 * note_spread + 0.5 * note_impact, 4), None
