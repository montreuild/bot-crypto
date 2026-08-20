"""RiskSizer — mixin de sizing des positions (ARCH-011, réécrit S12).

Mixin (pas d'``__init__``, pas d'état propre) : les méthodes opèrent sur
``self.*`` fournis par ``RiskGate.__init__``. Importé par ``app.core.risk.gate``
via héritage multiple.

S12 — une seule base : l'enveloppe du slot
------------------------------------------
``compute_size`` ne connaît plus ni l'équité globale, ni un « budget de bot »,
ni ``max_notional_pct`` : tout vient de l'``Envelope`` passée en paramètre.
C'est ce qui supprime le défaut d'origine — un notionnel calculé sur une base
et confronté à un plafond calculé sur une autre (cf.
docs/CONCEPTION_ENVELOPPES_DE_RISQUE.md §1). Le notionnel produit ici est,
par construction, toujours acceptable par ``RiskLedger.reserve`` sur un
registre vide (invariant verrouillé par ``tests/test_sizing_coherence.py``).
"""
import logging
import math
from typing import Any

from app.core.risk.envelope import Envelope
from app.core.risk.state import _locked, _safe_div

logger = logging.getLogger(__name__)


def _floor_to(value: float, decimals: int) -> float:
    """Arrondi À LA BAISSE — jamais au plus proche.

    Les plafonds de ``RiskLedger.reserve`` sont stricts (aucune tolérance) :
    un arrondi au plus proche peut franchir le plafond d'un demi-ulp et faire
    refuser un trade que le sizer croyait conforme."""
    factor = 10 ** decimals
    return math.floor(value * factor) / factor


class RiskSizer:
    """Sizing des positions et registration des positions ouvertes.

    Aucune initialisation propre — doit être combiné à ``RiskGate`` (ou tout
    hôte exposant ``equity``, ``peak_equity``, ``base_risk``,
    ``volatility_brake_factor``, ``open_positions``)."""

    equity: float
    peak_equity: float
    base_risk: float
    volatility_brake_factor: float
    open_positions: dict
    register_slot_open: Any

    # ── Courbe de dé-risquage en drawdown (BT-09) ──────────────────────────
    def _drawdown_multiplier(self) -> float:
        """Multiplicateur seul, sans le taux de risque.

        ``compute_size`` dimensionne sur ``env.slot_risk_amount``, qui intègre
        déjà ``trade_risk_pct`` : appliquer ``compute_risk()`` (taux × courbe)
        compterait le taux deux fois."""
        from app.core.risk.curve import risk_multiplier
        return risk_multiplier(_safe_div(self.peak_equity - self.equity, self.peak_equity))

    @_locked
    def compute_risk(self) -> float:
        """Taux de risque courant (taux de base × courbe de drawdown).

        Conservé pour l'exposé API (``status_dict()["current_risk"]``) : c'est
        le taux affiché, pas la base de sizing — celle-ci est portée par
        l'``Envelope``. Courbe partagée backtest/live (BT-09), source unique
        ``risk_curve.py``."""
        from app.core.risk.curve import risk_multiplier
        dd = _safe_div(self.peak_equity - self.equity, self.peak_equity)
        return self.base_risk * risk_multiplier(dd)

    # ── Position sizing ────────────────────────────────────────────────────
    @_locked
    def compute_size(self, entry: float, stop_dist: float, env: Envelope,
                     size_factor: float = 1.0) -> tuple:
        """(taille, notionnel) pour un trade, sur l'enveloppe du slot.

        ``stop_dist`` (distance absolue entrée→stop) est **obligatoire et
        strictement positif** : le repli historique sur l'ATR brut est
        supprimé — il faisait risquer ``risk% × (stop_dist / ATR)``, soit un
        sur-risque d'un facteur 2,5 au multiplicateur de stop usuel.
        L'appelant qui n'a pas de stop compte ``stop_invalide`` et refuse le
        trade plutôt que de dimensionner à l'aveugle.

        ``size_factor`` (demi-Kelly d'une stratégie) est borné [0, 2] et
        appliqué avant le plafond. Le facteur de score interne
        (``0.5 + 0.5 × …``) est supprimé : il modulait la taille sans être
        répliqué par le backtest, donc cassait la parité.

        Le frein de volatilité et la courbe de drawdown restent appliqués —
        ce sont des réductions conjoncturelles du risque engagé, pas une
        seconde base de calcul.
        """
        if stop_dist is None or stop_dist <= 0:
            raise ValueError(
                f"stop_dist doit être > 0 (reçu {stop_dist!r}) — le sizing par "
                f"l'ATR brut est supprimé (sur-risque)."
            )
        if entry <= 0:
            raise ValueError(f"entry doit être > 0 (reçu {entry!r})")

        sf = max(0.0, min(float(size_factor), 2.0))
        risk_amount = (env.slot_risk_amount * sf
                       * self.volatility_brake_factor * self._drawdown_multiplier())

        size = _floor_to(risk_amount / stop_dist, 6)
        if size * entry > env.max_notional:
            size = _floor_to(env.max_notional / entry, 6)
        return size, _floor_to(size * entry, 4)

    @staticmethod
    def engaged_risk(entry: float, stop: float, size: float) -> float:
        """Risque réellement engagé par une position : |entrée − stop| × taille.

        Source unique pour ``RiskLedger.reserve`` (à l'ouverture) et
        ``update_risk`` (quand le trailing remonte le stop) — les deux doivent
        mesurer la même grandeur, sinon le budget se dérègle silencieusement."""
        return abs(float(entry) - float(stop)) * float(size)

    # ── Positions ──────────────────────────────────────────────────────────
    @_locked
    def register_open(self, position: dict):
        self.open_positions[position["id"]] = position
        # V6.1 : incrémente le compteur quotidien du slot
        strat = position.get("strategy", "")
        tf    = position.get("timeframe", "")
        if strat and tf:
            from app.core.bot_identity import build_slot_key
            self.register_slot_open(build_slot_key(strat, tf,
                                                   position.get("symbol", "")))

    @_locked
    def register_close(self, position_id: str):
        self.open_positions.pop(position_id, None)

    @_locked
    def has_hedge(self, symbol: str) -> bool:
        positions_for_symbol = [p for p in self.open_positions.values()
                                 if p.get("symbol") == symbol]
        sides = {p["side"] for p in positions_for_symbol}
        return "long" in sides and "short" in sides
