"""Lecture d'un ordre et surcharge de trailing — partagés entre mixins live.

DETTE-04 — extraits de `position_open_mixin.py`, qui avait dépassé 700 lignes
sans figurer dans les onze du constat. Fonctions pures : aucune ne touche à
l'état du `LiveTrader`.

`_order_failed` et `_order_rejected` posent deux questions différentes (LIVE-02) :
« l'exécution a-t-elle eu lieu ? » pour une ouverture, « l'ordre a-t-il été
refusé ? » pour un stop au repos, dont `status="open"` est le cas NOMINAL.
"""
import logging

logger = logging.getLogger(__name__)

# ── Helpers module-level partagés entre mixins ─────────────────────────────


def _calc_unreal_pct(side: str, entry: float, price: float) -> float:
    """PnL non réalisé en % (protégé division par zéro)."""
    if entry <= 0:
        return 0.0
    return (price - entry) / entry * 100 if side == "long" \
           else (entry - price) / entry * 100


_REJECTED_ORDER_STATUSES = frozenset({"rejected", "canceled", "cancelled", "expired"})


def _order_failed(order: dict | None) -> bool:
    """True si l'ordre exchange n'a manifestement pas été exécuté.

    Un ``None`` ou un statut rejeté/annulé/expiré signifie qu'aucune position
    réelle n'existe côté exchange — poursuivre comme si l'ordre avait réussi
    créerait une position fantôme (trackée par le bot, absente de l'exchange).
    """
    if order is None:
        return True
    status = str(order.get("status") or "").lower()
    if status in _REJECTED_ORDER_STATUSES:
        return True
    filled = float(order.get("filled") or 0)
    if filled > 0:
        return False
    if status in ("closed", "filled"):
        return False
    if status in ("open", "new", "pending", "unfilled"):
        return True
    # LIVE-02 : rien de rempli et un statut qu'on ne reconnaît pas. Le repli
    # valait False — « pas d'échec » — et l'appelant enregistrait alors une
    # position au prix théorique, possiblement inexistante côté exchange. Le
    # bot suivait un fantôme : stop posé sur du vide, capital engagé à tort.
    # On refuse par défaut : ne pas ouvrir une position dont on n'a pas la
    # preuve qu'elle existe. Le statut est journalisé pour compléter les listes
    # ci-dessus au fil des connecteurs rencontrés.
    logger.warning(
        "[Ordre] statut inconnu %r avec filled=0 — traité comme un ÉCHEC "
        "(LIVE-02). Compléter les listes de _order_failed si ce statut est "
        "légitime pour votre connecteur.",
        status or "<vide>",
    )
    return True


def _order_rejected(order: dict | None) -> bool:
    """L'ordre a-t-il été REFUSÉ par l'exchange ? — pour les ordres au repos.

    ``_order_failed`` répond à « mon ordre au marché a-t-il été exécuté ? » :
    un statut ``open``/``pending`` y est un échec, puisqu'un ordre au marché
    doit se remplir immédiatement.

    Un stop ou une jambe OCO, eux, sont posés pour **attendre** : ``open`` est
    leur état nominal et ``filled=0`` la situation normale. Leur appliquer
    ``_order_failed`` revenait à déclarer en échec toute pose de stop réussie
    sur un exchange qui renvoie ``status="open"`` — le bot annulait alors un
    stop bel et bien actif, ou renonçait à le poser.

    Seul un refus explicite compte donc ici.
    """
    if order is None:
        return True
    status = str(order.get("status") or "").lower()
    if status in _REJECTED_ORDER_STATUSES:
        return True
    # Un identifiant est la preuve que l'exchange a bien enregistré l'ordre.
    return not (order.get("id") or order.get("orderId") or order.get("ordId"))


def _order_fail_reason(order: dict | None) -> str:
    if order is None:
        return "create_order a retourné None"
    status = order.get("status") or "inconnu"
    _info = order.get("info")
    info: dict = _info if isinstance(_info, dict) else {}
    detail = order.get("failReason") or info.get("msg") or info.get("sMsg")
    return f"status={status}" + (f" — {detail}" if detail else "")


def _apply_trail_override(base_cfg: dict, override: dict) -> dict:
    """Fusionne la config trailing globale avec un trail_override signal/position.

    Les clés d'override utilisent les noms du signal (trail_wide, trail_tight…)
    tandis que _trailing_cfg utilise mult / trail_tight_mult.
    """
    if not override:
        return base_cfg
    merged = dict(base_cfg)
    if "trail_wide"  in override:
        merged["mult"]             = float(override["trail_wide"])
    if "trail_tight" in override:
        merged["trail_tight_mult"] = float(override["trail_tight"])
    if "grace_bars"  in override:
        merged["grace_bars"]       = int(override["grace_bars"])
    if "breakeven_r" in override:
        merged["breakeven_r"]      = float(override["breakeven_r"])
    if "lock_r"      in override:
        merged["lock_r"]           = float(override["lock_r"])
    if "tight_r"     in override:
        merged["tight_r"]          = float(override["tight_r"])
    if "lock_ratio"  in override:
        merged["lock_ratio"]       = float(override["lock_ratio"])
    if "use_swing"   in override:
        merged["use_swing"]        = bool(override["use_swing"])
    if "mode"        in override:
        merged["mode"]             = str(override["mode"])
    return merged


