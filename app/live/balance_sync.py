"""
BalanceSyncMixin — synchronisation du capital et vérifications pré-exécution.

Regroupe toute la logique de réconciliation de l'équité entre le bot et
l'exchange : paper mode, spot réel et compte margin.

Requiert que l'instance possède :
  self.exchange, self.cfg
  self.open_positions
  self.capital_display, self._capital_lock
  self._paper_base          (initialisé via _restore_paper_base)
  self._margin_level        (initialisé à None)
  self.risk                 (RiskManager)
  self.allocator            (CapitalAllocator)
  self.SessionLocal         (pour _restore_paper_base)
"""
import logging

logger = logging.getLogger(__name__)


class BalanceSyncMixin:
    """Mixin de synchronisation du capital pour LiveTrader."""

    # ── Restauration du capital paper au démarrage ────────────────────────

    def _restore_paper_base(self, initial: float) -> float:
        """
        Restaure le capital settled paper depuis la dernière equity_close en BDD.
        Retourne initial si le mode live est actif ou si la BDD est vide.
        """
        if not self.cfg["trading"].get("paper_mode"):
            return initial
        try:
            from app.core.database import DailyStats
            sess = self.SessionLocal()
            try:
                last = sess.query(DailyStats).order_by(DailyStats.date.desc()).first()
                if last and last.equity_close and last.equity_close > 0:
                    logger.info(
                        f"[Paper] Capital settled restauré : "
                        f"{last.equity_close:.2f} (date={last.date})"
                    )
                    return float(last.equity_close)
            finally:
                sess.close()
        except Exception as e:
            logger.warning(f"[Paper] Impossible de restaurer capital settled : {e}")
        return initial

    # ── Synchro paper ─────────────────────────────────────────────────────

    def _sync_paper_balance(self) -> None:
        """
        Paper mode : capital_display = equity settled + PnL non réalisé des positions ouvertes.
        Appelé à chaque cycle en mode paper.
        """
        unrealized = 0.0
        for pos in self.open_positions.values():
            ticker = self._safe_ticker(pos["symbol"])
            if ticker:
                price = ticker.get("last", pos["entry"])
                if pos["side"] == "long":
                    unrealized += (price - pos["entry"]) * pos["size"]
                else:
                    unrealized += (pos["entry"] - price) * pos["size"]
        total = self._paper_base + unrealized
        with self._capital_lock:
            self.capital_display = round(total, 4)
        self.risk.update_equity(self.capital_display)
        self.allocator.update_equity(self.capital_display)

    # ── Synchro spot réel ─────────────────────────────────────────────────

    def _sync_spot_balance(self) -> None:
        """
        Récupère le solde USDC/USDT libre sur l'exchange et recalcule
        capital_display en y ajoutant le PnL non réalisé.
        """
        try:
            bal  = self.exchange.fetch_balance()
            free = float(
                bal.get("USDC", {}).get("free", 0)
                or bal.get("USDT", {}).get("free", 0)
                or 0
            )
            if free > 0:
                unrealized = 0.0
                for pos in self.open_positions.values():
                    ticker = self._safe_ticker(pos["symbol"])
                    if ticker:
                        price = ticker.get("last", pos["entry"])
                        if pos["side"] == "long":
                            unrealized += (price - pos["entry"]) * pos["size"]
                        else:
                            unrealized += (pos["entry"] - price) * pos["size"]
                total = free + unrealized
                with self._capital_lock:
                    self.capital_display = round(total, 4)
                self.risk.update_equity(self.capital_display)
        except Exception as e:
            logger.warning(f"[Spot Sync] KO : {e}")

    # ── Synchro compte margin ─────────────────────────────────────────────

    def _sync_margin_account(self) -> None:
        """
        Récupère le margin level et le solde USDC libre sur le compte margin.
        Émet une alerte si le margin level est critique (< 1.15).
        """
        try:
            acct = self.exchange.fetch_margin_account()
            ml_  = float(acct.get("marginLevel", 0) or 0)
            if ml_ > 0:
                self._margin_level = round(ml_, 3)
                if ml_ < 1.15:
                    logger.warning(f"[MARGIN] ⚠ Margin level CRITIQUE : {ml_:.3f}")
                    self.notif.send(
                        f"⚠ MARGIN LEVEL CRITIQUE : {ml_:.3f}", async_=True
                    )
            if not self.cfg["trading"].get("paper_mode"):
                free_usdc = self.exchange.fetch_margin_balance_usdc()
                if free_usdc > 0:
                    with self._capital_lock:
                        self.capital_display = free_usdc
                    self.risk.update_equity(free_usdc)
        except Exception as e:
            logger.warning(f"[MARGIN] sync KO : {e}")

    # ── Vérification pré-exécution ────────────────────────────────────────

    def _pre_execution_check(self, symbol: str, side: str,
                             size: float, price: float, notional: float) -> bool:
        """
        Vérifie que le capital disponible est suffisant avant d'ouvrir une position.

        En paper mode : compare le capital settled moins les positions ouvertes.
        En live mode  : vérifie les seuils minimaux de capital.

        Retourne True si l'exécution peut continuer, False sinon.
        """
        if self.cfg["trading"].get("paper_mode"):
            locked    = sum(p.get("notional", 0) for p in self.open_positions.values())
            available = self._paper_base - locked
            if notional > available:
                logger.warning(
                    f"[Paper] {symbol} : capital simulé insuffisant "
                    f"(dispo={available:.2f} < notional={notional:.2f})"
                )
                return False
            return True

        if self.capital_display < notional * 0.05:
            logger.warning(f"[PreCheck] {symbol} : capital insuffisant")
            return False
        if notional > self.capital_display * 0.25:
            logger.warning(
                f"[PreCheck] {symbol} : notionnel trop élevé ({notional:.2f})"
            )
            return False
        return True
