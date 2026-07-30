"""RiskNotifier — mixin notifications + persistance + statut API (ARCH-011).

Mixin (pas d'``__init__``, pas d'état propre) : opère sur ``self.*`` fournis
par ``RiskGate.__init__`` (``_notifier``, ``_session_factory``, ``slot_states``,
``halted``, ``halt_reason``, ``_kill_switch_tripped``, ``peak_equity``,
``daily_start``, ``day_key``, etc.).

Responsabilité :
- Branchement du ``Notifier`` (Telegram) et de la persistance DB.
- Snapshot / restauration de l'état (``_state_blob`` / ``_restore_state``).
- Mise à jour des CBs par slot (``update_slot_result``).
- Statut agrégé pour l'API (``status_dict``, ``get_slot_states``,
  ``get_circuit_breakers_status``).
"""
import logging
import time
from datetime import datetime, timezone
from typing import List

from app.core.risk_state import SlotRiskState, _locked, _safe_div, today_utc

logger = logging.getLogger(__name__)


class RiskNotifier:
    """Notifications, persistance DB et exposé API du moteur de risque.

    Aucune initialisation propre — doit être combiné à ``RiskGate`` (ou tout
    hôte exposant les attributs d'état requis — voir ``RiskGate.__init__``)."""

    # ── Branchement Notifier + persistance ─────────────────────────────────
    def attach_notifier(self, notifier) -> None:
        self._notifier = notifier

    @_locked
    def attach_persistence(self, session_factory) -> None:
        """Branche la persistance DB et restaure l'état (reprise propre)."""
        self._session_factory = session_factory
        self._restore_state()

    # ── Snapshot / persistance de l'état (Phase 3) ────────────────────────
    def _state_blob(self) -> dict:
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "kill_switch_tripped": self._kill_switch_tripped,
            "peak_equity": self.peak_equity,
            "daily_start": self.daily_start,
            "day_key": self.day_key,
            "slots": {
                k: {
                    "consecutive_losses": s.consecutive_losses,
                    "paused_until": s.paused_until,
                    "pause_reason": s.pause_reason,
                    "daily_pnl": s.daily_pnl,
                    "daily_trades": s.daily_trades,
                    "day_key": s.day_key,
                }
                for k, s in self.slot_states.items()
            },
        }

    @_locked
    def persist_state(self) -> None:
        """Sauvegarde l'état de risque en base (no-op si non branché)."""
        if not self._session_factory:
            return
        try:
            from app.core.database import save_risk_state, session_scope
            with session_scope(self._session_factory) as sess:
                save_risk_state(sess, "global", self._state_blob())
        except Exception as e:
            logger.debug(f"[Risk] persist_state KO : {e}")

    @_locked
    def _restore_state(self) -> None:
        if not self._session_factory:
            return
        try:
            from app.core.database import load_risk_state, session_scope
            with session_scope(self._session_factory) as sess:
                blob = load_risk_state(sess, "global")
        except Exception as e:
            logger.debug(f"[Risk] _restore_state KO : {e}")
            return
        if not blob:
            return
        self.halted = bool(blob.get("halted", False))
        self.halt_reason = blob.get("halt_reason", "")
        self._kill_switch_tripped = bool(blob.get("kill_switch_tripped", False))
        self.peak_equity = float(blob.get("peak_equity", self.peak_equity))
        self.daily_start = float(blob.get("daily_start", self.daily_start))
        self.day_key = blob.get("day_key", self.day_key)
        for k, sd in (blob.get("slots") or {}).items():
            st = self._get_slot_state(k)
            st.consecutive_losses = int(sd.get("consecutive_losses", 0))
            st.paused_until = float(sd.get("paused_until", 0.0))
            st.pause_reason = sd.get("pause_reason", "")
            st.daily_pnl = float(sd.get("daily_pnl", 0.0))
            st.daily_trades = int(sd.get("daily_trades", 0))
            st.day_key = sd.get("day_key", "")
        n_paused = sum(1 for s in self.slot_states.values() if s.is_paused())
        logger.info(
            f"[Risk] État restauré — halted={self.halted}"
            f"{' (KILL-SWITCH)' if self._kill_switch_tripped else ''}, "
            f"{len(self.slot_states)} slot(s), {n_paused} en pause."
        )

    # ── Circuit breakers par slot ──────────────────────────────────────────
    def _get_slot_state(self, slot_key: str) -> SlotRiskState:
        if slot_key not in self.slot_states:
            self.slot_states[slot_key] = SlotRiskState(slot_key=slot_key, day_key=today_utc())
        return self.slot_states[slot_key]

    @_locked
    def register_slot_open(self, slot_key: str) -> None:
        """Incrémente le compteur de trades du slot (appelé à l'ouverture)."""
        state = self._get_slot_state(slot_key)
        today = today_utc()
        if state.day_key != today:
            state.daily_pnl = 0.0
            state.daily_trades = 0
            state.day_key = today
        state.daily_trades += 1

    @_locked
    def update_slot_result(self, slot_key: str, pnl: float, won: bool):
        """Met à jour l'état de risque d'un slot après clôture et déclenche les CBs si besoin."""
        state = self._get_slot_state(slot_key)

        today = today_utc()
        if state.day_key != today:
            state.daily_pnl = 0.0
            state.day_key   = today
        state.daily_pnl += pnl
        state.last_trades.append(won)

        if won:
            state.consecutive_losses = 0
        else:
            state.consecutive_losses += 1

        # CB : pertes consécutives
        if state.consecutive_losses >= self._consec_loss_limit and not state.is_paused():
            state.paused_until = time.time() + self._consec_pause_secs
            state.pause_reason = f"{state.consecutive_losses} pertes consécutives"
            logger.warning(
                f"[Risk] CB slot {slot_key} — {state.pause_reason} → pause {self._consec_pause_secs//60} min"
            )
            if self._notifier:
                self._notifier.send(
                    f"⚠️ CB slot *{slot_key}* — {state.pause_reason}\nPause {self._consec_pause_secs//60} min.",
                    async_=True
                )

        # CB : DD journalier du slot
        slot_dd_pct = _safe_div(-state.daily_pnl, max(self.equity, 1.0))
        if slot_dd_pct >= self._slot_daily_dd_limit and not state.is_paused():
            # Use tomorrow 00:00:00 UTC to avoid race condition at midnight
            from datetime import timedelta
            tomorrow = (datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1))
            state.paused_until = tomorrow.timestamp()
            state.pause_reason = f"DD journalier {slot_dd_pct:.1%} ≥ {self._slot_daily_dd_limit:.1%}"
            logger.warning(f"[Risk] CB slot {slot_key} — {state.pause_reason} → pause jusqu'à minuit")
            if self._notifier:
                self._notifier.send(
                    f"⚠️ CB slot *{slot_key}* — {state.pause_reason}\nPause jusqu'à minuit UTC.",
                    async_=True
                )

        # CB : win rate plancher (15 derniers trades)
        if len(state.last_trades) >= 15:
            wr = state.win_rate()
            if wr < self._win_rate_floor and not state.is_paused():
                state.paused_until = time.time() + 86400
                state.pause_reason = f"Win rate {wr:.0%} < {self._win_rate_floor:.0%} (15 trades)"
                logger.warning(f"[Risk] CB slot {slot_key} — {state.pause_reason} → pause 24h")
                if self._notifier:
                    self._notifier.send(
                        f"⚠️ CB slot *{slot_key}* — {state.pause_reason}\nPause 24h.",
                        async_=True
                    )

        # Persiste compteurs/pauses du slot pour une reprise propre après crash.
        self.persist_state()

    @_locked
    def reset_slot_pause(self, slot_key: str):
        """Réinitialisation manuelle d'une pause de slot."""
        state = self._get_slot_state(slot_key)
        state.reset_pause()
        logger.warning(f"[Risk] Pause slot {slot_key} réinitialisée manuellement.")

    # ── Stats pour l'API ───────────────────────────────────────────────────
    @_locked
    def status_dict(self) -> dict:
        return {
            "equity":              round(self.equity, 4),
            "peak_equity":         round(self.peak_equity, 4),
            "daily_pnl_pct":       round(self.daily_pnl_pct * 100, 2),
            "global_dd_pct":       round(self.global_dd_pct * 100, 2),
            "open_positions":      len(self.open_positions),
            "halted":              self.halted,
            "halt_reason":         self.halt_reason,
            "kill_switch":         self._kill_switch_tripped,
            "current_risk":        round(self.compute_risk() * 100, 2),
            "daily_dd_limit":      round(self.daily_dd_limit, 4),
            "global_dd_limit":     round(self.global_dd_limit, 4),
            "volatility_brake":    self.volatility_brake_active,
            "volatility_factor":   self.volatility_brake_factor,
            "veto_mode":           self._veto_mode,
            "veto_shadow_active":  self._veto_shadow_active(),
            "veto_shadow_blocks":  dict(self.veto_shadow_blocks),
        }

    @_locked
    def get_slot_states(self) -> List[dict]:
        """Retourne l'état CB de tous les slots pour l'API."""
        result = []
        for key, state in self.slot_states.items():
            result.append({
                "slot_key":           key,
                "paused":             state.is_paused(),
                "pause_reason":       state.pause_reason if state.is_paused() else "",
                "paused_until":       (
                    datetime.fromtimestamp(state.paused_until, tz=timezone.utc).isoformat()
                    if state.is_paused() else None
                ),
                "consecutive_losses": state.consecutive_losses,
                "win_rate_15t":       round(state.win_rate() * 100, 1),
                "daily_pnl":          round(state.daily_pnl, 4),
                "total_trades_seen":  len(state.last_trades),
            })
        return result

    @_locked
    def get_circuit_breakers_status(self) -> List[dict]:
        """Retourne le statut de tous les CBs (global + slots)."""
        cbs = [
            {
                "name":    "daily_drawdown",
                "scope":   "global",
                "active":  self.halted and "journalier" in self.halt_reason,
                "value":   round(self.daily_pnl_pct * 100, 2),
                "limit":   round(self.daily_dd_limit * 100, 1),
                "reason":  self.halt_reason if self.halted else "",
            },
            {
                "name":    "global_drawdown",
                "scope":   "global",
                "active":  self.halted and "global" in self.halt_reason,
                "value":   round(self.global_dd_pct * 100, 2),
                "limit":   round(self.global_dd_limit * 100, 1),
                "reason":  self.halt_reason if self.halted else "",
            },
            {
                "name":    "volatility_brake",
                "scope":   "global",
                "active":  self.volatility_brake_active,
                "value":   None,
                "limit":   round(self._volatility_threshold * 100, 1),
                "reason":  "Tailles ×0.5 (volatilité BTC élevée)" if self.volatility_brake_active else "",
            },
        ]
        for state in self.slot_states.values():
            if state.is_paused():
                cbs.append({
                    "name":   f"slot_pause::{state.slot_key}",
                    "scope":  f"slot:{state.slot_key}",
                    "active": True,
                    "value":  None,
                    "limit":  None,
                    "reason": state.pause_reason,
                })
        return cbs
