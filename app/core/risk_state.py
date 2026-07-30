"""Primitives partagées du moteur de risque (ARCH-011).

Contient :
- ``SlotRiskState`` : dataclass d'état circuit-breaker par slot.
- ``_locked`` : décorateur sérialisant l'accès à ``self._lock`` (RLock réentrant).
- ``_safe_div`` : division protégée contre la division par zéro.
- ``today_utc`` : clé date YYYY-MM-DD en UTC.

Ces primitives sont partagées par ``RiskGate`` (détient l'état) et les mixins
``RiskSizer`` / ``RiskNotifier`` (méthodes pures opérant sur ``self.*``).
"""
import functools
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / max(denominator, 1e-9)


def _locked(method):
    """Sérialise l'accès à l'état partagé du moteur de risque (S1-01).

    ``open_positions``/``equity``/``slot_states`` sont lus et mutés depuis le
    thread de trading (``can_trade``, ``register_open``/``close``,
    ``update_equity``) ET depuis les threads API (``status_dict`` pour
    ``/api/status``) — sans verrou, races possibles (compteurs perdus, lecture
    torn d'un dict en cours de mutation). RLock : réentrant, les méthodes
    verrouillées s'appellent entre elles (ex. ``update_equity`` →
    ``_check_circuit_breakers`` → ``trip_kill_switch`` → ``persist_state``).

    NB (ARCH-011) : ``self._lock`` est créé par ``RiskGate.__init__`` AVANT tout
    accès à l'état partagé ; les mixins ``RiskSizer``/``RiskNotifier`` peuvent
    donc décorer leurs méthodes avec ``@_locked`` sans posséder eux-mêmes
    l'attribut."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def today_utc() -> str:
    """Clé date YYYY-MM-DD en UTC (source unique pour les reset journaliers)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Circuit breaker par slot ───────────────────────────────────────────────

@dataclass
class SlotRiskState:
    slot_key: str
    consecutive_losses: int = 0
    last_trades: deque = field(default_factory=lambda: deque(maxlen=15))
    daily_pnl: float = 0.0
    daily_trades: int = 0           # V6.1 : nb de trades ouverts sur le slot ce jour
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
