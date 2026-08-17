"""RiskGate — circuit breakers, kill-switch et décisions binaires (ARCH-011).

La classe ``RiskGate`` (anciennement ``RiskManager`` dans le monolithe
``app/core/risk.py`` — scission ARCH-011) détient **l'état partagé** du moteur
de risque et expose les décisions binaires d'autorisation de trade
(``can_trade``, ``can_slot_trade``), les circuit breakers global et par slot,
le kill-switch d'équité persistant, ainsi que le volatility brake.

Elle hérite des mixins :
- ``RiskSizer`` (sizing des positions, levier, registration des positions) ;
- ``RiskNotifier`` (notifications, persistance DB, exposé API).

Architecture (Option B — mixin) :
- ``RiskGate.__init__`` crée ``self._lock`` AVANT tout accès à l'état partagé,
  puis initialise tous les attributs (équité, slots, halt, kill-switch, etc.).
- Les mixins ``RiskSizer``/``RiskNotifier`` ne possèdent ni ``__init__`` ni
  état propre : ils opèrent sur ``self.*`` via héritage.
- ``RiskManager = RiskGate`` : alias de compatibilité pour les consommateurs
  existants. La façade ``app/core/risk.py`` a été **supprimée** (ARCH-011) ;
  tous les imports doivent pointer vers ``app.core.risk_gate``.
"""
import logging
import threading
import time
from collections import deque
from typing import Dict

from app.core.risk_envelope import trade_risk_pct
from app.core.risk_notifier import RiskNotifier
from app.core.risk_sizer import RiskSizer
from app.core.risk_state import SlotRiskState, _locked, _safe_div, today_utc

logger = logging.getLogger(__name__)


def _default_venue_capital(cfg: dict) -> float:
    """Capital de la venue par défaut — équité de départ du moteur de risque.

    ``_validate_risk_envelopes`` garantit au chargement qu'une enveloppe existe
    pour toute venue atteignable ; le repli à 0 ne sert qu'aux configs de test
    minimales, qui n'exercent pas les circuit breakers d'équité."""
    default = (cfg.get("venues") or {}).get("default")
    envelopes = (cfg.get("risk") or {}).get("envelopes") or {}
    return float((envelopes.get(default) or {}).get("capital", 0.0))


class RiskGate(RiskSizer, RiskNotifier):
    """Moteur de risque — décisions binaires + état partagé (ARCH-011).

    L'ordre MRO est ``(RiskGate, RiskSizer, RiskNotifier)`` : ``RiskGate``
    fournit ``__init__`` et l'état ; les mixins apportent les méthodes
    spécialisées. Aucune redéfinition conflictuelle (les mixins ne définissent
    pas ``__init__``).
    """

    def __init__(self, cfg: dict):
        # Créé AVANT tout accès à l'état partagé (cf. ``_locked`` docstring).
        self._lock = threading.RLock()
        t = cfg["trading"]
        # S12 : le capital n'est plus une globale — il appartient à la venue.
        # L'équité suivie ici est celle de la venue par défaut, seule base des
        # circuit breakers globaux et du kill-switch ; le sizing, lui, passe
        # exclusivement par l'``Envelope`` du slot (cf. risk_sizer).
        self.initial_capital     = _default_venue_capital(cfg)
        self.daily_dd_limit      = t.get("daily_drawdown_limit", 0.05)
        self.global_dd_limit     = t.get("max_drawdown_global", 0.20)
        self.max_trades_per_min  = t.get("max_trades_per_minute", 3)
        # Taux de risque par trade (profil) — sert à l'affichage du risque
        # courant ; la base monétaire vient de l'enveloppe du slot.
        self.base_risk           = trade_risk_pct(cfg)
        self._dd_warn_ratio      = cfg.get("notifications", {}).get("dd_warning_ratio", 0.80)
        self._notifier           = None

        # Config circuit breakers slot (avec valeurs par défaut)
        _risk_cfg = cfg.get("risk", {})
        self._consec_loss_limit    = int(_risk_cfg.get("consecutive_loss_limit", 3))
        self._slot_daily_dd_limit  = float(_risk_cfg.get("slot_daily_dd_limit", 0.03))
        self._win_rate_floor       = float(_risk_cfg.get("win_rate_floor", 0.25))
        self._consec_pause_secs    = int(_risk_cfg.get("consecutive_pause_secs", 1800))  # 30 min
        self._volatility_threshold = float(_risk_cfg.get("volatility_threshold", 0.05))  # 5% ATR BTC
        # V6.1 : limite de trades par jour par slot (0 = désactivé)
        self._max_trades_per_day   = int(_risk_cfg.get("max_trades_per_day", 0))

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

        # ── Mode de veto (Phase 1 — suppression progressive des vetos globaux)
        # "enforce" (défaut) : les vetos de capacité (max_positions/longs/shorts,
        #   anti-spam) et les pauses CB de slot bloquent réellement les entrées.
        # "shadow"  : ils n'empêchent plus d'entrer mais sont **comptés** et
        #   loggés — on mesure l'écart avant de retirer un garde-fou. Le
        #   kill-switch global (``halted``) reste TOUJOURS appliqué. Par sécurité,
        #   "shadow" n'est honoré qu'en paper (cf. _veto_shadow_active()).
        self._veto_mode  = str(_risk_cfg.get("veto_mode", "enforce")).lower()
        self._paper_mode = bool(cfg.get("trading", {}).get("paper_mode", True))
        # Compteurs d'« écart » : combien de fois chaque veto aurait bloqué.
        self.veto_shadow_blocks: Dict[str, int] = {}

        # Circuit breakers par slot
        self.slot_states: Dict[str, SlotRiskState] = {}

        # Volatility brake
        self.volatility_brake_active: bool  = False
        self.volatility_brake_factor: float = 1.0  # 0.5 si actif

        # ── Kill-switch d'équité persistant (Phase 3 — veto catastrophe) ──────
        # Plancher d'équité absolu : sous ce niveau, HALT définitif et PERSISTANT
        # (survit au redémarrage, non levable sans ``force``). C'est le « seul
        # veto global » de la doc §4. ``equity_kill_switch_dd`` = 0 → désactivé.
        _kill_dd = float(_risk_cfg.get("equity_kill_switch_dd", 0.0))
        self.kill_switch_equity = (self.initial_capital * (1.0 - _kill_dd)
                                   if _kill_dd > 0 else 0.0)
        self._kill_switch_tripped = False
        # Persistance de l'état de risque (compteurs/pauses/halt) — reprise propre.
        self._session_factory = None

    # ── Equity ────────────────────────────────────────────────────────────
    @_locked
    def update_equity(self, new_equity: float):
        if new_equity < 0:
            logger.critical(f"[Risk] Equity négative ({new_equity:.2f}) — circuit breaker déclenché")
            self.halted = True
            self.halt_reason = f"Equity négative : {new_equity:.2f}"
            self.equity = 0.0
            return
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
                state.daily_trades = 0
                state.consecutive_losses = 0
                state.day_key   = today
                # Lever la pause "daily DD" et "daily trades" si nouveau jour
                if ("DD journalier" in state.pause_reason or
                        "trades/jour" in state.pause_reason):
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
            self.persist_state()

        # Kill-switch d'équité : plancher absolu → HALT persistant et sticky.
        if (self.kill_switch_equity > 0 and self.equity <= self.kill_switch_equity
                and not self._kill_switch_tripped):
            self.trip_kill_switch(
                f"plancher d'équité {self.equity:.2f} ≤ {self.kill_switch_equity:.2f}"
            )

    def _trigger_dd_warning(self, daily_dd: float):
        if self._notifier:
            self._notifier.notify_dd_warning(daily_dd * 100, self.daily_dd_limit * 100)

    @_locked
    def trip_kill_switch(self, reason: str) -> None:
        """Déclenche le kill-switch catastrophe : HALT persistant et sticky."""
        self.halted = True
        self._kill_switch_tripped = True
        self.halt_reason = f"KILL-SWITCH — {reason}"
        logger.critical(f"🛑 {self.halt_reason}")
        if self._notifier:
            self._notifier.send(f"🛑 *KILL-SWITCH déclenché*\n{reason}",
                                async_=False, level="critical")
        self.persist_state()

    @_locked
    def reset_halt(self, force: bool = False):
        # Le kill-switch catastrophe n'est pas levable par un reset normal :
        # il faut un acquittement explicite (force=True) — évite qu'un simple
        # clic relance un bot en situation de ruine.
        if self._kill_switch_tripped and not force:
            logger.warning("[Risk] reset_halt ignoré : kill-switch actif "
                           "(acquittement explicite requis).")
            return
        self.halted      = False
        self.halt_reason = ""
        self._kill_switch_tripped = False
        logger.warning("[Risk] Circuit breaker global réinitialisé"
                       + (" (kill-switch forcé)" if force else " manuellement."))
        self.persist_state()

    # ── Mode veto (shadow / enforce) ──────────────────────────────────────
    def _veto_shadow_active(self) -> bool:
        """Le mode shadow n'est honoré qu'en paper (sécurité : on ne retire jamais
        un garde-fou en live sans l'avoir mesuré en paper au préalable)."""
        return self._veto_mode == "shadow" and self._paper_mode

    @property
    def veto_shadow(self) -> bool:
        """Accès public : les vetos de capacité sont-ils en mode shadow ?"""
        return self._veto_shadow_active()

    def _shadow_allow(self, reason: str) -> bool:
        """Enregistre qu'un veto *aurait* bloqué, puis autorise (mode shadow)."""
        self.veto_shadow_blocks[reason] = self.veto_shadow_blocks.get(reason, 0) + 1
        logger.info(f"[Risk][shadow] veto ignoré (mesure d'écart) : {reason}")
        return True

    # ── Circuit breakers par slot ──────────────────────────────────────────
    @_locked
    def can_slot_trade(self, slot_key: str) -> tuple[bool, str]:
        """Vérifie les circuit breakers propres à un slot."""
        state = self._get_slot_state(slot_key)
        # Garantie : nouveau jour → reset compteurs
        today = self._today()
        if state.day_key != today:
            state.daily_pnl = 0.0
            state.daily_trades = 0
            state.day_key = today
        shadow = self._veto_shadow_active()
        if state.is_paused():
            if shadow and self._shadow_allow(f"slot_pause::{slot_key}"):
                return True, ""
            remaining = int(state.paused_until - time.time())
            return False, f"Slot {slot_key} pausé ({state.pause_reason}) — {remaining}s restantes"
        # V6.1 : limite trades/jour par slot
        if self._max_trades_per_day > 0 and state.daily_trades >= self._max_trades_per_day:
            if shadow and self._shadow_allow(f"slot_daily_limit::{slot_key}"):
                return True, ""
            return False, (
                f"Slot {slot_key} : limite quotidienne atteinte "
                f"({state.daily_trades}/{self._max_trades_per_day} trades)"
            )
        return True, ""

    # ── Volatility brake ───────────────────────────────────────────────────
    @_locked
    def update_volatility(self, atr_pct: float):
        """Volatility brake : ATR du symbole scanné / du cache, en % du prix.

        L-12 : ce n'est plus « ATR BTC » — le live alimente le ratio ATR/prix
        du marché effectivement observé.
        """
        was_active = self.volatility_brake_active
        self.volatility_brake_active = atr_pct > self._volatility_threshold
        self.volatility_brake_factor = 0.5 if self.volatility_brake_active else 1.0

        if self.volatility_brake_active and not was_active:
            logger.warning(
                f"[Risk] Volatility brake ACTIF — ATR {atr_pct:.1%} > {self._volatility_threshold:.1%}"
                " → tailles ×0.5"
            )
        elif not self.volatility_brake_active and was_active:
            logger.info("[Risk] Volatility brake désactivé.")

    # ── Vérifications avant entrée ─────────────────────────────────────────
    @_locked
    def can_trade(self, side: str) -> tuple[bool, str]:
        """Vetos globaux restants : kill-switch et anti-spam.

        S12 — les plafonds de capacité (``max_positions``, ``max_longs``,
        ``max_shorts``) sont supprimés : compter les positions était un proxy
        grossier du budget de risque venue, qui borne désormais la perte
        directement (§2.2). ``side`` est conservé dans la signature — les
        appelants le passent et les vetos directionnels peuvent revenir par
        la venue (``allow_short``), pas par un compteur global.
        """
        # Kill-switch global : TOUJOURS appliqué, même en mode shadow.
        if self.halted:
            return False, self.halt_reason
        if not self._is_rate_ok():
            if not (self._veto_shadow_active() and self._shadow_allow("anti_spam")):
                return False, "Trop de trades/minute (anti-spam)"
        return True, ""

    def _is_rate_ok(self) -> bool:
        """Lecture pure : un signal refusé plus loin ne consomme plus de jeton (F-11)."""
        now = time.time()
        self._trade_times = deque(t for t in self._trade_times if now - t < 60)
        return len(self._trade_times) < self.max_trades_per_min

    @_locked
    def consume_rate_token(self) -> None:
        """À appeler seulement après une ouverture (ou un scale-in) réussie."""
        now = time.time()
        self._trade_times = deque(t for t in self._trade_times if now - t < 60)
        self._trade_times.append(now)

    def _check_rate(self) -> bool:
        """Compat : lecture + consommation. Préférer ``_is_rate_ok`` + ``consume_rate_token``."""
        if not self._is_rate_ok():
            return False
        self.consume_rate_token()
        return True

    # ── Stats pour l'API (properties) ──────────────────────────────────────
    @property
    @_locked
    def daily_pnl_pct(self) -> float:
        return _safe_div(self.equity - self.daily_start, self.daily_start)

    @property
    @_locked
    def global_dd_pct(self) -> float:
        return _safe_div(self.peak_equity - self.equity, self.peak_equity)

    # ── Helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _today() -> str:
        return today_utc()


# ── Alias de compatibilité (ARCH-011) ───────────────────────────────────────
# La façade ``app/core/risk.py`` a été supprimée : les consommateurs doivent
# importer ``RiskGate`` (ou l'alias ``RiskManager`` ci-dessous) depuis
# ``app.core.risk_gate``. L'alias évite un churn nom partout dans le code live
# et les tests — signature d'API strictement préservée.
RiskManager = RiskGate
