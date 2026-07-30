"""RiskSizer — mixin de sizing des positions (ARCH-011).

Mixin (pas d'``__init__``, pas d'état propre) : toutes les méthodes opèrent
sur ``self.*`` fournis par ``RiskGate.__init__`` (équity, peak_equity,
max_leverage, max_notional_pct, base_risk, volatility_brake_factor,
open_positions). Importé par ``app.core.risk_gate`` via héritage multiple.

Responsabilité : calcul de la taille / notionnel / levier, et registration
des positions ouvertes auprès du moteur de risque.
"""
import logging

from app.core.risk_state import _locked, _safe_div

logger = logging.getLogger(__name__)


class RiskSizer:
    """Sizing des positions, levier et registration des positions ouvertes.

    Aucune initialisation propre — doit être combiné à ``RiskGate`` (ou tout
    hôte exposant les attributs ``equity``, ``peak_equity``, ``base_risk``,
    ``max_leverage``, ``max_notional_pct``, ``volatility_brake_factor``,
    ``open_positions``)."""

    # ── Position sizing ────────────────────────────────────────────────────
    @_locked
    def compute_risk(self) -> float:
        # Courbe partagée backtest/live (BT-09) — source unique : risk_curve.py.
        from app.core.risk_curve import risk_multiplier
        dd = _safe_div(self.peak_equity - self.equity, self.peak_equity)
        return self.base_risk * risk_multiplier(dd)

    @_locked
    def compute_size(self, entry: float, atr: float,
                     score: float = 1.0, threshold: float = 0.60,
                     size_factor: float = 1.0,
                     budget: float = None, max_leverage: float = None,
                     stop_dist: float = None) -> tuple:
        """Calcule taille et notionnel, en intégrant score_factor et volatility_brake.

        ``size_factor`` (optionnel) est un facteur multiplicatif fourni par la
        stratégie (par ex. demi-Kelly : ×confidence ; ou boost setup V7 ×1.5).
        Borné [0, 2] et appliqué après le facteur score interne et le frein
        de volatilité. ``max_notional_pct`` reste la garde-fou de risque global.

        Sizing par la distance au stop (parité backtest)
        ------------------------------------------------
        Quand ``stop_dist`` (distance absolue entry→stop) est fourni, la taille
        vaut ``risk_amount / stop_dist`` : le risque réellement engagé est alors
        **exactement** ``risk%`` du capital, quel que soit le multiplicateur ATR
        du stop (cf. ``app/engine/backtest.py:_try_enter``). Sans ``stop_dist``
        (rétro-compat), on retombe sur l'ATR brut — mais le risque réel devient
        ``risk% × (stop_dist / ATR)`` (sur-risque ~2,5× quand le stop est à
        2,5×ATR), d'où l'importance de toujours passer la distance au stop.

        Sizing par bot (Phase 1)
        ------------------------
        Si ``budget`` (USDC alloué au bot) est fourni, le bot dimensionne sur
        **son** budget et non sur l'équité globale : le montant risqué devient
        ``budget × risk%`` et le notionnel est plafonné à ``budget × levier``
        (cf. doc §3 « Sizing : cap notional ≤ budget × levier »). C'est la
        fidélité au backtest — un bot ne peut engager que son budget. Sans
        ``budget`` (None), comportement historique inchangé (sizing sur équité).
        """
        base         = float(budget) if budget is not None else self.equity
        risk_amount  = base * self.compute_risk()
        # Dénominateur de risque : distance au stop réel si connue (parité
        # backtest → risque = risk% exact), sinon ATR brut (rétro-compat).
        risk_per_unit = (float(stop_dist) if (stop_dist is not None and stop_dist > 0)
                         else max(atr, 1e-8))
        size         = risk_amount / risk_per_unit
        notional     = size * entry
        if budget is not None:
            lev          = float(max_leverage) if max_leverage is not None else self.max_leverage
            max_notional = base * max(lev, 1.0)
        else:
            max_notional = self.equity * self.max_notional_pct

        score_range  = max(1.0 - threshold, 1e-9)
        score_internal_factor = 0.5 + 0.5 * min(max(score - threshold, 0) / score_range, 1.0)
        sf           = max(0.0, min(float(size_factor), 2.0))
        size        *= score_internal_factor * self.volatility_brake_factor * sf

        notional = size * entry
        if notional > max_notional:
            size     = max_notional / entry
            notional = max_notional
        return round(size, 6), round(notional, 4)

    @_locked
    def compute_leverage(self, notional: float) -> float:
        lev = _safe_div(notional, self.equity)
        return min(lev, self.max_leverage)

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
