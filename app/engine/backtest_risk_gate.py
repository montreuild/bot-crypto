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

    Pas de dépendance au temps réel : tout est indexé sur l'index de bougie `i`
    et sur un `day_key` dérivé du timestamp de la bougie.

    Circuit breakers répliqués, et LEURS CLÉS DE CONFIG — qui doivent être
    exactement celles de `app/core/risk_gate.py`, sinon le backtest tourne sur
    ses défauts pendant que le live applique les seuils réglés par l'opérateur,
    et les deux ne se comparent plus :

        1. `risk.consecutive_loss_limit` (3) : N pertes consécutives → pause du
           slot pendant `risk.consecutive_pause_secs` (1800 s), converti en
           bougies selon le timeframe.
        2. `risk.slot_daily_dd_limit` (3 %) : DD journalier d'un slot → pause
           jusqu'au lendemain.
        3. `risk.max_trades_per_day` (0 = illimité) : limite trades/jour/slot.
        4. `risk.volatility_threshold` (5 %) : volatilité forte → sizing ×0.5.
        5. `trading.daily_drawdown_limit` (5 %) : DD journalier GLOBAL → HALT,
           levé au changement de jour (le seuil est journalier par nature).
        6. `trading.max_drawdown_global` (20 %) : DD depuis le pic → HALT
           définitif. En live il exige un `reset_halt()` de l'opérateur ; en
           backtest il n'y a personne pour le lever, et c'est fidèle : une
           stratégie qui perd 20 % depuis son pic arrête le bot.

    Divergence assumée n°1 — le volatility brake du live regarde l'ATR de BTC
    comme baromètre du marché entier. En backtest, seul le symbole tradé est
    chargé : on utilise SON ATR. Même intention (« marché agité → on réduit »),
    référentiel différent.

    Divergence assumée n°2 — la pause pour pertes consécutives s'exprime en
    bougies, donc sa granularité est celle du timeframe : sur du 1d, une pause
    de 1800 s est arrondie à 1 bougie, soit un jour entier.
    """

    consec_loss_limit: int = 3
    slot_daily_dd_limit: float = 0.03
    max_trades_per_day: int = 0
    # Durée de la pause « pertes consécutives », en bougies (live : 1800 s).
    pause_bars: int = 1
    # Nombre de bougies dans une journée, pour le garde-fou des pauses
    # journalières. Elles se lèvent normalement au changement de `day_key` ;
    # ce compte ne sert que si les timestamps sont inexploitables et que le
    # `day_key` reste constant — sans lui la pause serait définitive.
    bars_per_day: int = 24
    daily_dd_limit: float = 0.05
    global_dd_limit: float = 0.20
    volatility_threshold: float = 0.05
    halted: bool = False
    halt_reason: str = ""
    # Nature du HALT, en clair machine — même raison d'être que `pause_kind` :
    # décider si le HALT se lève au changement de jour ou non, sans re-parser
    # `halt_reason`.
    halt_kind: str = ""          # "" | "daily_dd" | "global_dd"
    volatility_brake_active: bool = False
    volatility_brake_factor: float = 1.0
    # Nombre de bougies pendant lesquelles le frein a mordu — seule façon de
    # savoir a posteriori s'il a pesé sur le résultat du run.
    volatility_brake_bars: int = 0

    # État global journalier (pour le DD journalier global)
    day_key: str = ""
    daily_start_capital: float = 0.0

    # État par slot
    _slots: Dict[str, _SlotState] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict, timeframe: str = "1h") -> "BacktestRiskGate":
        """Construit le gate depuis la config du bot.

        Lit les MÊMES clés que le `RiskGate` live — `trading.*` pour les seuils
        globaux, `risk.*` pour ceux par slot. Une config absente donne les
        défauts (jamais None) : le mode realistic_risk reste utilisable sur une
        config minimale.

        `timeframe` sert à convertir la durée de pause du live (en secondes) en
        nombre de bougies. Sans lui, on retombait sur 24 bougies en dur, soit
        24 h sur du 1h — 48× la pause réelle de 30 minutes.
        """
        risk_cfg = cfg.get("risk") or {}
        trading_cfg = cfg.get("trading") or {}

        pause_secs = int(risk_cfg.get("consecutive_pause_secs", 1800))
        try:
            from app.core.timeframes import TF_SECONDS
            tf_secs = int(TF_SECONDS.get(timeframe) or 3600)
        except Exception:
            tf_secs = 3600
        # Au moins 1 bougie : une pause plus courte que la bougie n'aurait
        # aucun effet observable, ce qui reviendrait à désactiver le breaker.
        pause_bars = max(1, round(pause_secs / max(tf_secs, 1)))
        bars_per_day = max(1, round(86_400 / max(tf_secs, 1)))

        return cls(
            consec_loss_limit=int(risk_cfg.get("consecutive_loss_limit", 3)),
            slot_daily_dd_limit=float(risk_cfg.get("slot_daily_dd_limit", 0.03)),
            max_trades_per_day=int(risk_cfg.get("max_trades_per_day", 0)),
            pause_bars=pause_bars,
            bars_per_day=bars_per_day,
            daily_dd_limit=float(trading_cfg.get("daily_drawdown_limit", 0.05)),
            global_dd_limit=float(trading_cfg.get("max_drawdown_global", 0.20)),
            volatility_threshold=float(risk_cfg.get("volatility_threshold", 0.05)),
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
            Capital courant (pour les DD journalier et global).
        peak_capital : float
            Capital peak (pour calculer le DD global).

        Returns
        -------
        (ok, reason)
            ok=True si le slot peut trader, ok=False + reason sinon.
        """
        # ── Bascule de journée, AVANT tout test de blocage ───────────────────
        # L'ordre compte : un HALT « DD journalier » posé hier doit pouvoir être
        # levé aujourd'hui. Tester `self.halted` d'abord le rendrait définitif.
        if self.day_key != day_key:
            self.day_key = day_key
            self.daily_start_capital = current_capital
            if self.halted and self.halt_kind == "daily_dd":
                logger.info(
                    f"[BacktestRiskGate] Nouveau jour ({day_key}) — HALT journalier levé "
                    f"(était : {self.halt_reason})"
                )
                self.halted = False
                self.halt_reason = ""
                self.halt_kind = ""

        if self.halted:
            return False, self.halt_reason or "HALT global actif"

        # ── DD journalier GLOBAL (trading.daily_drawdown_limit) ──────────────
        # Le breaker le plus susceptible de se déclencher : perdre 5 % dans la
        # journée est bien plus courant que perdre 20 % depuis le pic.
        if self.daily_start_capital > 0 and self.daily_dd_limit > 0:
            daily_dd = (self.daily_start_capital - current_capital) / self.daily_start_capital
            if daily_dd >= self.daily_dd_limit:
                self.halted = True
                self.halt_kind = "daily_dd"
                self.halt_reason = (
                    f"DD journalier {daily_dd:.1%} ≥ seuil {self.daily_dd_limit:.1%} "
                    f"— HALT jusqu'au lendemain (realistic_risk)"
                )
                logger.warning(f"[BacktestRiskGate] {self.halt_reason}")
                return False, self.halt_reason

        # ── DD global depuis le pic (trading.max_drawdown_global) ────────────
        # Définitif : en live il faut un `reset_halt()` de l'opérateur, et il
        # n'y a personne pour le faire pendant un backtest.
        if peak_capital > 0 and self.global_dd_limit > 0:
            global_dd = (peak_capital - current_capital) / peak_capital
            if global_dd >= self.global_dd_limit:
                self.halted = True
                self.halt_kind = "global_dd"
                self.halt_reason = (
                    f"DD global {global_dd:.1%} ≥ seuil {self.global_dd_limit:.1%} "
                    f"— backtest HALTé définitivement (realistic_risk)"
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
            # Le live remet AUSSI les pertes consécutives à zéro au changement
            # de jour (`RiskGate._reset_slot_daily_pnl`). Sans ça, 2 pertes
            # lundi + 1 perte mardi pausent le slot en backtest alors que le
            # live serait reparti de zéro mardi matin.
            state.consecutive_losses = 0
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
                # `bars_per_day` — et NON `pause_bars`, qui vaut ~1 bougie — n'est
                # ici qu'un garde-fou si le day_key reste constant (df sans
                # timestamps exploitables) : sans lui la pause serait définitive,
                # avec `pause_bars` elle ne tiendrait pas la journée.
                state.pause_until_bar = i + self.bars_per_day
                logger.info(
                    f"[BacktestRiskGate] Slot {slot_key} pausé "
                    f"({state.pause_reason}) jusqu'au prochain jour"
                )

    def update_volatility(self, atr_pct: float) -> None:
        """Met à jour le volatility brake (ATR en % du prix).

        Appelé à chaque bougie par le `Backtester`. Quand il est actif,
        `volatility_brake_factor` passe à 0.5 et `_try_enter` en multiplie la
        taille de position.

        `atr_pct` est ici l'ATR du SYMBOLE TRADÉ, là où le live prend celui de
        BTC comme baromètre du marché entier : un backtest ne charge que le
        symbole demandé. Divergence assumée, même intention.

        Journalisé en debug et non en warning : sur un run de plusieurs milliers
        de bougies, un marché agité fait osciller le brake, et ces bascules ne
        sont pas des incidents d'exploitation mais des faits de simulation.
        """
        was_active = self.volatility_brake_active
        self.volatility_brake_active = atr_pct > self.volatility_threshold
        self.volatility_brake_factor = 0.5 if self.volatility_brake_active else 1.0
        if self.volatility_brake_active and not was_active:
            logger.debug(
                f"[BacktestRiskGate] Volatility brake ACTIF — "
                f"ATR {atr_pct:.1%} > {self.volatility_threshold:.1%} → sizing ×0.5"
            )
        elif not self.volatility_brake_active and was_active:
            logger.debug("[BacktestRiskGate] Volatility brake désactivé.")
        if self.volatility_brake_active:
            self.volatility_brake_bars += 1

    def reset(self) -> None:
        """Réinitialise l'état du gate (pour un nouveau run)."""
        self.halted = False
        self.halt_reason = ""
        self.halt_kind = ""
        self.volatility_brake_active = False
        self.volatility_brake_factor = 1.0
        self.volatility_brake_bars = 0
        self.day_key = ""
        self.daily_start_capital = 0.0
        self._slots.clear()

    def to_diagnostics(self) -> dict:
        """Retourne un résumé des circuit breakers déclenchés (pour audit).

        `halt_kind` distingue un arrêt journalier (levé le lendemain, la suite
        du run reste exploitable) d'un arrêt définitif sur DD global — sans
        quoi un `halted: true` ne dit pas si le backtest s'est arrêté de
        trader pour de bon.
        """
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "halt_kind": self.halt_kind,
            "volatility_brake_active": self.volatility_brake_active,
            "volatility_brake_bars": self.volatility_brake_bars,
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
