"""QW-6 / Étape 6 — Circuit breakers pour le backtest (mode realistic_risk).

L'audit backtest (exigence 3 « close to live ») a identifié que le backtest
n'applique PAS les circuit breakers du `RiskGate` live :
    - consecutive_loss_limit (3 pertes consécutives → pause slot)
    - slot_daily_dd_limit (DD journalier par slot → pause)
    - max_trades_per_day (limite trades/jour par slot)
    - volatility_brake (ATR BTC > 5% → sizing ×0.5)
    - kill-switch global (DD global > seuil → halt)

Un bot live peut HALTer sur 3 pertes consécutives alors que le backtest
continue à trader — c'est un écart de réalisme qui peut faire sur-évaluer
une stratégie fragile.

Ce module fournit un `BacktestRiskGate` SIMPLIFIÉ qui réplique ces circuit
breakers SANS dépendre du temps réel (pas de `_today()`, pas de `paused_until`
en secondes epoch — les seuils sont évalués en bougie-par-bougie). Il est
opt-in : le backtest historique (par défaut) reste inchangé pour préserver
la parité avec les backtests existants et les tests `test_backtest_live_parity`.

Activation côté `Backtester` :
    bt = Backtester(eng, cfg, realistic_risk=True)
    res = bt.run(df, symbol, timeframe=tf)

Le mode `realistic_risk=True` instancie un `BacktestRiskGate` et l'interroge
avant chaque `_try_enter`. Les refus sont comptés dans `rejections` sous le
code `circuit_breaker` (avec sous-codes dans `diag`).

Côté API, la route `/api/backtest` accepte `realistic_risk=true` (query param)
qui active le mode. Le payload de réponse inclut `realistic_risk: bool`.

Note : ce mode N'est PAS équivalent à un live trader complet (pas de
`fetch_positions`, pas de `reconcile_real_costs`, pas de stops exchange) —
c'est une approximation raisonnable pour évaluer la robustesse d'une
stratégie face aux circuit breakers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass
class _SlotState:
    """État circuit breaker par slot (stratégie@tf@symbol)."""
    consecutive_losses: int = 0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    day_key: str = ""  # YYYY-MM-DD dérivé de la bougie (pas time.time())
    paused: bool = False
    pause_reason: str = ""       # message lisible (affiché dans les diagnostics)
    # Nature de la pause, en clair machine. Sert à décider COMMENT la pause se
    # lève : "consec_loss" expire après `pause_bars` bougies, "daily_dd" se lève
    # au changement de jour. Ne JAMAIS déduire ce type en re-parsant
    # `pause_reason` — c'est un texte destiné à l'humain, il change librement.
    pause_kind: str = ""         # "" | "consec_loss" | "daily_dd"
    pause_until_bar: int = -1    # index de bougie jusqu'où la pause tient


@dataclass
class BacktestRiskGate:
    """Circuit breakers pour backtest — version simplifiée du RiskGate live.

    Initialise avec la config `risk` du bot (mêmes clés que `app/core/risk_gate.py`).
    Pas de dépendance au temps réel : tout est indexé sur l'index de bougie `i`
    passé à `can_slot_trade(i, slot_key, day_key)`.

    Circuit breakers répliqués :
        1. consecutive_loss_limit (défaut 3) : N pertes consécutives → pause
           le slot pour `pause_bars` bougies (défaut 24 = 1 jour en 1h).
        2. slot_daily_dd_limit (défaut 0.03 = 3%) : DD journalier par slot >
           seuil → pause pour le reste du jour.
        3. max_trades_per_day (défaut 0 = illimité) : limite trades/jour.
        4. volatility_brake : si ATR BTC > 5% du prix → sizing ×0.5.
        5. kill-switch global : DD global > 20% → halt tous les slots.

    Note : le `volatility_brake` n'est PAS appliqué ici en sizing (le sizing
    est géré par `_try_enter` via `_risk_multiplier`). On l'expose juste comme
    information via `volatility_brake_factor` pour que `_try_enter` puisse le
    multiplier si souhaité.
    """

    consec_loss_limit: int = 3
    slot_daily_dd_limit: float = 0.03
    max_trades_per_day: int = 0
    pause_bars: int = 24  # 1 jour en 1h par défaut
    global_dd_limit: float = 0.20
    volatility_threshold: float = 0.05
    halted: bool = False
    halt_reason: str = ""
    volatility_brake_active: bool = False
    volatility_brake_factor: float = 1.0

    # État par slot
    _slots: Dict[str, _SlotState] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict) -> "BacktestRiskGate":
        """Construit le gate depuis la config `risk` du bot.

        Si la config n'a pas de section `risk`, retourne un gate aux valeurs
        par défaut (jamais None) — le mode realistic_risk reste donc utilisable
        sur une config minimale.
        """
        risk_cfg = cfg.get("risk") or {}
        if not risk_cfg:
            return cls()  # défauts
        return cls(
            consec_loss_limit=int(risk_cfg.get("consecutive_loss_limit", 3)),
            slot_daily_dd_limit=float(risk_cfg.get("slot_daily_dd_limit", 0.03)),
            max_trades_per_day=int(risk_cfg.get("max_trades_per_day", 0)),
            pause_bars=int(risk_cfg.get("circuit_breaker_pause_bars", 24)),
            global_dd_limit=float(risk_cfg.get("global_dd_limit", 0.20)),
            volatility_threshold=float(risk_cfg.get("volatility_brake_threshold", 0.05)),
        )

    def _get_slot(self, slot_key: str) -> _SlotState:
        if slot_key not in self._slots:
            self._slots[slot_key] = _SlotState()
        return self._slots[slot_key]

    def can_slot_trade(
        self,
        i: int,
        slot_key: str,
        day_key: str,
        current_capital: float,
        peak_capital: float,
    ) -> Tuple[bool, str]:
        """Vérifie les circuit breakers pour ce slot à la bougie `i`.

        Parameters
        ----------
        i : int
            Index de bougie courant.
        slot_key : str
            Clé du slot (ex: "trend@1h@BTC/USDC").
        day_key : str
            Date du jour (YYYY-MM-DD) dérivée du timestamp de la bougie.
        current_capital : float
            Capital courant (pour kill-switch global).
        peak_capital : float
            Capital peak (pour calculer le DD global).

        Returns
        -------
        (ok, reason)
            ok=True si le slot peut trader, ok=False + reason sinon.
        """
        # Kill-switch global d'abord
        if self.halted:
            return False, self.halt_reason or "HALT global actif"

        # Mise à jour du DD global
        if peak_capital > 0:
            global_dd = (peak_capital - current_capital) / peak_capital
            if global_dd >= self.global_dd_limit and not self.halted:
                self.halted = True
                self.halt_reason = (
                    f"DD global {global_dd:.1%} ≥ seuil {self.global_dd_limit:.1%} "
                    f"— backtest HALTé (realistic_risk)"
                )
                logger.warning(f"[BacktestRiskGate] {self.halt_reason}")
                return False, self.halt_reason

        state = self._get_slot(slot_key)

        # Nouveau jour → reset des compteurs journaliers
        if state.day_key != day_key:
            state.daily_pnl = 0.0
            state.daily_trades = 0
            state.day_key = day_key
            # Une pause « DD journalier » ne dure que la journée : le changement
            # de jour la lève, quel que soit `pause_until_bar`.
            if state.paused and state.pause_kind == "daily_dd":
                state.paused = False
                state.pause_reason = ""
                state.pause_kind = ""
                state.pause_until_bar = -1

        # Pause encore active ?
        if state.paused:
            if i < state.pause_until_bar:
                return False, f"Slot {slot_key} pausé ({state.pause_reason}) — bar {i}/{state.pause_until_bar}"
            else:
                # Pause expirée
                state.paused = False
                state.pause_reason = ""
                state.pause_kind = ""
                state.pause_until_bar = -1

        # Limite trades/jour
        if self.max_trades_per_day > 0 and state.daily_trades >= self.max_trades_per_day:
            return False, (
                f"Slot {slot_key} : limite quotidienne atteinte "
                f"({state.daily_trades}/{self.max_trades_per_day} trades)"
            )

        return True, ""

    def record_trade_result(
        self,
        i: int,
        slot_key: str,
        pnl: float,
        day_key: str,
        capital_before: float,
    ) -> None:
        """Met à jour l'état du slot après clôture d'un trade.

        À appeler dans `_close_at` (ou après) pour incrémenter les compteurs
        de pertes consécutives et le DD journalier.

        Parameters
        ----------
        i : int
            Index de bougie de clôture.
        slot_key : str
            Clé du slot.
        pnl : float
            PnL du trade clôturé (net frais).
        day_key : str
            Date du jour (YYYY-MM-DD).
        capital_before : float
            Capital avant le trade (pour calculer le DD journalier en %).
        """
        state = self._get_slot(slot_key)
        # Synchroniser le day_key : si nouveau jour, reset des compteurs
        # journaliers (sinon un trade à J+1 serait compté dans la journée J).
        if state.day_key != day_key:
            state.daily_pnl = 0.0
            state.daily_trades = 0
            state.day_key = day_key
        state.daily_pnl += pnl
        state.daily_trades += 1

        # Perte consécutive
        if pnl < 0:
            state.consecutive_losses += 1
        else:
            state.consecutive_losses = 0

        # Pause sur consecutive losses
        if state.consecutive_losses >= self.consec_loss_limit and not state.paused:
            state.paused = True
            state.pause_kind = "consec_loss"
            state.pause_reason = (
                f"{state.consecutive_losses} pertes consécutives "
                f"(≥ {self.consec_loss_limit})"
            )
            state.pause_until_bar = i + self.pause_bars
            logger.info(
                f"[BacktestRiskGate] Slot {slot_key} pausé "
                f"({state.pause_reason}) jusqu'à la bar {state.pause_until_bar}"
            )

        # Pause sur DD journalier
        if capital_before > 0:
            daily_dd = abs(min(0, state.daily_pnl)) / capital_before
            if daily_dd >= self.slot_daily_dd_limit and not state.paused:
                state.paused = True
                state.pause_kind = "daily_dd"
                state.pause_reason = (
                    f"DD journalier {daily_dd:.1%} ≥ {self.slot_daily_dd_limit:.1%}"
                )
                # Pause jusqu'au prochain jour : on ne connaît pas l'index de la
                # première bougie de J+1, donc la levée se fait sur le changement
                # de `day_key` dans `can_slot_trade` (cf. pause_kind == "daily_dd").
                # `pause_until_bar` n'est ici qu'un garde-fou si le day_key reste
                # constant (df sans timestamps exploitables) : sans lui la pause
                # serait définitive.
                state.pause_until_bar = i + self.pause_bars
                logger.info(
                    f"[BacktestRiskGate] Slot {slot_key} pausé "
                    f"({state.pause_reason}) jusqu'au prochain jour"
                )

    def update_volatility(self, btc_atr_pct: float) -> None:
        """Met à jour le volatility brake (ATR BTC en % du prix).

        Si actif, `volatility_brake_factor` passe à 0.5 — l'appelant
        (`_try_enter`) peut multiplier le `size_factor` par cette valeur.
        """
        was_active = self.volatility_brake_active
        self.volatility_brake_active = btc_atr_pct > self.volatility_threshold
        self.volatility_brake_factor = 0.5 if self.volatility_brake_active else 1.0
        if self.volatility_brake_active and not was_active:
            logger.warning(
                f"[BacktestRiskGate] Volatility brake ACTIF — "
                f"ATR BTC {btc_atr_pct:.1%} > {self.volatility_threshold:.1%} → sizing ×0.5"
            )
        elif not self.volatility_brake_active and was_active:
            logger.info("[BacktestRiskGate] Volatility brake désactivé.")

    def reset(self) -> None:
        """Réinitialise l'état du gate (pour un nouveau run)."""
        self.halted = False
        self.halt_reason = ""
        self.volatility_brake_active = False
        self.volatility_brake_factor = 1.0
        self._slots.clear()

    def to_diagnostics(self) -> dict:
        """Retourne un résumé des circuit breakers déclenchés (pour audit)."""
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "volatility_brake_active": self.volatility_brake_active,
            "n_slots_paused": sum(1 for s in self._slots.values() if s.paused),
            "slots": {
                k: {
                    "consecutive_losses": v.consecutive_losses,
                    "daily_pnl": round(v.daily_pnl, 4),
                    "daily_trades": v.daily_trades,
                    "paused": v.paused,
                    "pause_reason": v.pause_reason,
                    "pause_kind": v.pause_kind,
                }
                for k, v in self._slots.items()
            },
        }
