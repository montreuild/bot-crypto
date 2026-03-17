"""
Module 3 — Gestion du risque & portfolio :
  - Risk-per-trade dynamique (réduit en drawdown)
  - Circuit breaker journalier + global
  - Anti-spam (max trades/min)
  - Gestion levier et exposition max
  - Hedge detection
"""
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)


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
        self._notifier           = None  # attaché par LiveTrader via attach_notifier()

        # Equity tracking
        self.equity            = self.initial_capital
        self.peak_equity       = self.initial_capital
        self.daily_start       = self.initial_capital
        self.day_key: str      = self._today()

        # Positions ouvertes
        self.open_positions: Dict[str, dict] = {}

        # Anti-spam
        self._trade_times: deque = deque()

        # Flags
        self.halted         = False
        self.halt_reason    = ""

    # ── Equity ──────────────────────────────────────────────────────────────
    def update_equity(self, new_equity: float):
        self.equity = new_equity
        self.peak_equity = max(self.peak_equity, new_equity)
        today = self._today()
        if today != self.day_key:          # nouveau jour
            self.daily_start = new_equity
            self.day_key     = today
        self._check_circuit_breakers()

    def _check_circuit_breakers(self):
        # Drawdown journalier
        daily_dd = (self.daily_start - self.equity) / max(self.daily_start, 1e-9)
        warn_threshold = self.daily_dd_limit * self._dd_warn_ratio
        # Pré-alerte (ex: 80% du seuil)
        if daily_dd >= warn_threshold and not self.halted:
            self._trigger_dd_warning(daily_dd)
        if daily_dd >= self.daily_dd_limit and not self.halted:
            self.halted      = True
            self.halt_reason = f"Circuit breaker : DD journalier {daily_dd:.1%} ≥ {self.daily_dd_limit:.1%}"
            logger.critical(f"🔴 HALT — {self.halt_reason}")
        # Drawdown global
        global_dd = (self.peak_equity - self.equity) / max(self.peak_equity, 1e-9)
        if global_dd >= self.global_dd_limit and not self.halted:
            self.halted      = True
            self.halt_reason = f"Circuit breaker : DD global {global_dd:.1%} ≥ {self.global_dd_limit:.1%}"
            logger.critical(f"🔴 HALT — {self.halt_reason}")

    def _trigger_dd_warning(self, daily_dd: float):
        """Délégué au notifier si disponible."""
        if hasattr(self, "_notifier") and self._notifier:
            self._notifier.notify_dd_warning(
                daily_dd * 100, self.daily_dd_limit * 100
            )

    def attach_notifier(self, notifier) -> None:
        """Attache un Notifier pour les alertes de risque."""
        self._notifier = notifier

    def reset_halt(self):
        """Réinitialisation manuelle du circuit breaker."""
        self.halted     = False
        self.halt_reason = ""
        logger.warning("[Risk] Circuit breaker réinitialisé manuellement.")

    # ── Position sizing ──────────────────────────────────────────────────────
    def compute_risk(self) -> float:
        """Risk-per-trade dynamique : réduit linéairement en cas de drawdown."""
        dd = (self.peak_equity - self.equity) / max(self.peak_equity, 1e-9)
        if dd > 0.10:
            factor = 0.5                  # réduit de moitié au-delà de 10% DD
        elif dd > 0.05:
            factor = 0.75
        else:
            factor = 1.0
        return self.base_risk * factor

    def compute_size(self, entry: float, atr: float,
                     score: float = 1.0, threshold: float = 0.60) -> tuple:
        """
        Calcule la taille de position (units) et le notionnel.
        Le sizing est modulé par le score du signal :
          - score = threshold (0.60) → 50% de la taille max
          - score = 1.0             → 100% de la taille max
        Formule linéaire : factor = 0.5 + 0.5 * (score - threshold) / (1 - threshold)
        """
        risk_amount  = self.equity * self.compute_risk()
        size         = risk_amount / max(atr, 1e-8)
        notional     = size * entry
        max_notional = self.equity * self.max_notional_pct

        # Modulation par le score
        score_range = max(1.0 - threshold, 1e-9)
        score_factor = 0.5 + 0.5 * min(max(score - threshold, 0) / score_range, 1.0)
        size     *= score_factor
        notional  = size * entry

        if notional > max_notional:
            size     = max_notional / entry
            notional = max_notional
        return round(size, 6), round(notional, 4)

    def compute_leverage(self, notional: float) -> float:
        """Levier effectif plafonné au max configuré."""
        lev = notional / max(self.equity, 1e-9)
        return min(lev, self.max_leverage)

    # ── Vérifications avant entrée ───────────────────────────────────────────
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

    # ── Positions ────────────────────────────────────────────────────────────
    def register_open(self, position: dict):
        self.open_positions[position["id"]] = position

    def register_close(self, position_id: str):
        self.open_positions.pop(position_id, None)

    def has_hedge(self, symbol: str) -> bool:
        """Vérifie si le bot est hedgé (long + short sur même symbole)."""
        positions_for_symbol = [p for p in self.open_positions.values()
                                if p.get("symbol") == symbol]
        sides = {p["side"] for p in positions_for_symbol}
        return "long" in sides and "short" in sides

    # ── Stats ────────────────────────────────────────────────────────────────
    @property
    def daily_pnl_pct(self) -> float:
        return (self.equity - self.daily_start) / max(self.daily_start, 1e-9)

    @property
    def global_dd_pct(self) -> float:
        return (self.peak_equity - self.equity) / max(self.peak_equity, 1e-9)

    def status_dict(self) -> dict:
        return {
            "equity": round(self.equity, 4),
            "peak_equity": round(self.peak_equity, 4),
            "daily_pnl_pct": round(self.daily_pnl_pct * 100, 2),
            "global_dd_pct": round(self.global_dd_pct * 100, 2),
            "open_positions": len(self.open_positions),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "current_risk": round(self.compute_risk() * 100, 2),
            "daily_dd_limit": round(self.daily_dd_limit, 4),
            "global_dd_limit": round(self.global_dd_limit, 4),
        }

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
