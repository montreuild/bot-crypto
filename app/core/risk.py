"""
Gestion du risque et du portfolio :
circuit breakers global et par slot, sizing, volatility brake, anti-spam.
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / max(denominator, 1e-9)


# ── Circuit breaker par slot ───────────────────────────────────────────────

@dataclass
class SlotRiskState:
    slot_key: str
    consecutive_losses: int = 0
    last_trades: deque = field(default_factory=lambda: deque(maxlen=15))
    daily_pnl: float = 0.0
    day_key: str = ""
    paused_until: float = 0.0    # timestamp Unix
    pause_reason: str = ""

    def is_paused(self) -> bool:
        return time.time() < self.paused_until

    def win_rate(self) -> float:
        trades = list(self.last_trades)
        if not trades:
            return 1.0
        return sum(1 for w in trades if w) / len(trades)

    def reset_pause(self):
        self.paused_until = 0.0
        self.pause_reason = ""


class RiskManager:
    def __init__(self, cfg: dict):
        t = cfg["trading"]
        self.initial_capital     = t["capital"]
        self.daily_dd_limit      = t.get("daily_drawdown_limit", 0.05)
        self.global_dd_limit     = t.get("max_drawdown_global", 0.20)
        self.max_positions       = t.get("max_positions", 5)
        self.max_longs           = t.get("max_longs", 3)
        self.max_shorts          = t.get("max_shorts", 3)
        self.max_trades_per_min  = t.get("max_trades_per_minute", 3)
        self.max_leverage        = t.get("max_leverage", 1)
        self.max_notional_pct    = float(cfg.get("backtest", {}).get("max_notional_pct", 0.20))
        self.base_risk           = t.get("risk_per_trade", 0.01)
        self._dd_warn_ratio      = cfg.get("notifications", {}).get("dd_warning_ratio", 0.80)
        self._notifier           = None

        # Config circuit breakers slot (avec valeurs par défaut)
        _risk_cfg = cfg.get("risk", {})
        self._consec_loss_limit    = int(_risk_cfg.get("consecutive_loss_limit", 3))
        self._slot_daily_dd_limit  = float(_risk_cfg.get("slot_daily_dd_limit", 0.03))
        self._win_rate_floor       = float(_risk_cfg.get("win_rate_floor", 0.25))
        self._consec_pause_secs    = int(_risk_cfg.get("consecutive_pause_secs", 1800))  # 30 min
        self._volatility_threshold = float(_risk_cfg.get("volatility_threshold", 0.05))  # 5% ATR BTC

        # Equity tracking
        self.equity        = self.initial_capital
        self.peak_equity   = self.initial_capital
        self.daily_start   = self.initial_capital
        self.day_key: str  = self._today()

        # Positions ouvertes
        self.open_positions: Dict[str, dict] = {}

        # Anti-spam
        self._trade_times: deque = deque()

        # Flags globaux
        self.halted      = False
        self.halt_reason = ""

        # Circuit breakers par slot
        self.slot_states: Dict[str, SlotRiskState] = {}

        # Volatility brake
        self.volatility_brake_active: bool  = False
        self.volatility_brake_factor: float = 1.0  # 0.5 si actif

    # ── Equity ────────────────────────────────────────────────────────────
    def update_equity(self, new_equity: float):
        self.equity      = new_equity
        self.peak_equity = max(self.peak_equity, new_equity)
        today            = self._today()
        if today != self.day_key:
            self.daily_start = new_equity
            self.day_key     = today
            self._reset_slot_daily_pnl()
        self._check_circuit_breakers()

    def _reset_slot_daily_pnl(self):
        today = self._today()
        for state in self.slot_states.values():
            if state.day_key != today:
                state.daily_pnl = 0.0
                state.consecutive_losses = 0
                state.day_key   = today
                # Lever la pause "daily DD" si nouveau jour
                if "DD journalier" in state.pause_reason:
                    state.reset_pause()

    def _check_circuit_breakers(self):
        daily_dd = _safe_div(self.daily_start - self.equity, self.daily_start)
        warn_threshold = self.daily_dd_limit * self._dd_warn_ratio
        if daily_dd >= warn_threshold and not self.halted:
            self._trigger_dd_warning(daily_dd)
        if daily_dd >= self.daily_dd_limit and not self.halted:
            self.halted      = True
            self.halt_reason = f"CB global : DD journalier {daily_dd:.1%} ≥ {self.daily_dd_limit:.1%}"
            logger.critical(f"HALT — {self.halt_reason}")

        global_dd = _safe_div(self.peak_equity - self.equity, self.peak_equity)
        if global_dd >= self.global_dd_limit and not self.halted:
            self.halted      = True
            self.halt_reason = f"CB global : DD global {global_dd:.1%} ≥ {self.global_dd_limit:.1%}"
            logger.critical(f"HALT — {self.halt_reason}")

    def _trigger_dd_warning(self, daily_dd: float):
        if self._notifier:
            self._notifier.notify_dd_warning(daily_dd * 100, self.daily_dd_limit * 100)

    def attach_notifier(self, notifier) -> None:
        self._notifier = notifier

    def reset_halt(self):
        self.halted      = False
        self.halt_reason = ""
        logger.warning("[Risk] Circuit breaker global réinitialisé manuellement.")

    # ── Circuit breakers par slot ──────────────────────────────────────────
    def _get_slot_state(self, slot_key: str) -> SlotRiskState:
        if slot_key not in self.slot_states:
            self.slot_states[slot_key] = SlotRiskState(slot_key=slot_key, day_key=self._today())
        return self.slot_states[slot_key]

    def can_slot_trade(self, slot_key: str) -> tuple[bool, str]:
        """Vérifie les circuit breakers propres à un slot."""
        state = self._get_slot_state(slot_key)
        if state.is_paused():
            remaining = int(state.paused_until - time.time())
            return False, f"Slot {slot_key} pausé ({state.pause_reason}) — {remaining}s restantes"
        return True, ""

    def update_slot_result(self, slot_key: str, pnl: float, won: bool):
        """Met à jour l'état de risque d'un slot après clôture et déclenche les CBs si besoin."""
        state = self._get_slot_state(slot_key)

        today = self._today()
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

    def reset_slot_pause(self, slot_key: str):
        """Réinitialisation manuelle d'une pause de slot."""
        state = self._get_slot_state(slot_key)
        state.reset_pause()
        logger.warning(f"[Risk] Pause slot {slot_key} réinitialisée manuellement.")

    # ── Volatility brake ───────────────────────────────────────────────────
    def update_volatility(self, btc_atr_pct: float):
        """Met à jour le volatility brake (ATR BTC en % du prix). Tailles ×0.5 si actif."""
        was_active = self.volatility_brake_active
        self.volatility_brake_active = btc_atr_pct > self._volatility_threshold
        self.volatility_brake_factor = 0.5 if self.volatility_brake_active else 1.0

        if self.volatility_brake_active and not was_active:
            logger.warning(
                f"[Risk] Volatility brake ACTIF — ATR BTC {btc_atr_pct:.1%} > {self._volatility_threshold:.1%}"
                " → tailles ×0.5"
            )
        elif not self.volatility_brake_active and was_active:
            logger.info("[Risk] Volatility brake désactivé.")

    # ── Position sizing ────────────────────────────────────────────────────
    def compute_risk(self) -> float:
        dd = _safe_div(self.peak_equity - self.equity, self.peak_equity)
        if dd > 0.10:
            factor = 0.5
        elif dd > 0.05:
            factor = 0.75
        else:
            factor = 1.0
        return self.base_risk * factor

    def compute_size(self, entry: float, atr: float,
                     score: float = 1.0, threshold: float = 0.60) -> tuple:
        """Calcule taille et notionnel, en intégrant score_factor et volatility_brake."""
        risk_amount  = self.equity * self.compute_risk()
        size         = risk_amount / max(atr, 1e-8)
        notional     = size * entry
        max_notional = self.equity * self.max_notional_pct

        score_range  = max(1.0 - threshold, 1e-9)
        score_factor = 0.5 + 0.5 * min(max(score - threshold, 0) / score_range, 1.0)
        size        *= score_factor * self.volatility_brake_factor

        notional = size * entry
        if notional > max_notional:
            size     = max_notional / entry
            notional = max_notional
        return round(size, 6), round(notional, 4)

    def compute_leverage(self, notional: float) -> float:
        lev = _safe_div(notional, self.equity)
        return min(lev, self.max_leverage)

    # ── Vérifications avant entrée ─────────────────────────────────────────
    def can_trade(self, side: str) -> tuple[bool, str]:
        if self.halted:
            return False, self.halt_reason
        if len(self.open_positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) atteint"
        longs  = sum(1 for p in self.open_positions.values() if p["side"] == "long")
        shorts = sum(1 for p in self.open_positions.values() if p["side"] == "short")
        if side == "long"  and longs  >= self.max_longs:
            return False, f"Max longs ({self.max_longs}) atteint"
        if side == "short" and shorts >= self.max_shorts:
            return False, f"Max shorts ({self.max_shorts}) atteint"
        if not self._check_rate():
            return False, "Trop de trades/minute (anti-spam)"
        return True, ""

    def _check_rate(self) -> bool:
        now = time.time()
        self._trade_times = deque(t for t in self._trade_times if now - t < 60)
        if len(self._trade_times) >= self.max_trades_per_min:
            return False
        self._trade_times.append(now)
        return True

    # ── Positions ──────────────────────────────────────────────────────────
    def register_open(self, position: dict):
        self.open_positions[position["id"]] = position

    def register_close(self, position_id: str):
        self.open_positions.pop(position_id, None)

    def has_hedge(self, symbol: str) -> bool:
        positions_for_symbol = [p for p in self.open_positions.values()
                                 if p.get("symbol") == symbol]
        sides = {p["side"] for p in positions_for_symbol}
        return "long" in sides and "short" in sides

    # ── Stats pour l'API ───────────────────────────────────────────────────
    @property
    def daily_pnl_pct(self) -> float:
        return _safe_div(self.equity - self.daily_start, self.daily_start)

    @property
    def global_dd_pct(self) -> float:
        return _safe_div(self.peak_equity - self.equity, self.peak_equity)

    def status_dict(self) -> dict:
        return {
            "equity":              round(self.equity, 4),
            "peak_equity":         round(self.peak_equity, 4),
            "daily_pnl_pct":       round(self.daily_pnl_pct * 100, 2),
            "global_dd_pct":       round(self.global_dd_pct * 100, 2),
            "open_positions":      len(self.open_positions),
            "halted":              self.halted,
            "halt_reason":         self.halt_reason,
            "current_risk":        round(self.compute_risk() * 100, 2),
            "daily_dd_limit":      round(self.daily_dd_limit, 4),
            "global_dd_limit":     round(self.global_dd_limit, 4),
            "volatility_brake":    self.volatility_brake_active,
            "volatility_factor":   self.volatility_brake_factor,
        }

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

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
