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
            from app.core.database import DailyStats, session_scope
            with session_scope(self.SessionLocal) as sess:
                last = sess.query(DailyStats).order_by(DailyStats.date.desc()).first()
                if last and last.equity_close and last.equity_close > 0:
                    logger.info(
                        f"[Paper] Capital settled restauré : "
                        f"{last.equity_close:.2f} (date={last.date})"
                    )
                    return float(last.equity_close)
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
            if pos.get("_reserved"):
                continue
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
        Récupère le solde libre sur l'exchange et recalcule l'équité totale.

        L'équité = cash libre + valeur de marché signée des positions − emprunts.
        Le cash libre seul sous-estime l'équité car le notionnel immobilisé dans
        l'actif détenu (long) n'y figure plus ; on le réintègre via size×prix.
        Cohérent avec le mode paper et robuste aux faux drawdowns.
        """
        try:
            detail = self.exchange.fetch_balance_detail()
            free   = detail["free"]
            if free > 0:
                total = free + self._open_positions_market_value() \
                        - detail.get("borrowed", 0.0)
                with self._capital_lock:
                    self.capital_display = round(total, 4)
                self._balance_detail = detail
                self.risk.update_equity(self.capital_display)
                self.allocator.update_equity(self.capital_display)
        except Exception as e:
            logger.warning(f"[Spot Sync] KO : {e}")

    def _open_positions_market_value(self) -> float:
        """
        Valeur de marché signée des positions ouvertes (au prix ticker courant) :
          + size×prix pour un long  (actif détenu)
          − size×prix pour un short (passif à racheter ; les fonds de la vente
                                      sont déjà comptés dans le cash libre)
        Au levier 1, free + cette valeur − borrowed reconstitue l'équité réelle.
        """
        adj = 0.0
        for pos in self.open_positions.values():
            if pos.get("_reserved"):
                continue
            ticker = self._safe_ticker(pos["symbol"])
            if not ticker:
                continue
            price = ticker.get("last", pos["entry"])
            mv = pos["size"] * price
            adj += mv if pos["side"] == "long" else -mv
        return adj

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
                ml_alert    = float(self.cfg["trading"].get("margin_level_alert", 3.0))
                ml_critical = float(self.cfg["trading"].get("margin_level_critical", 1.5))
                if ml_ < ml_critical:
                    # Seuil critique : HALT immédiat des nouvelles entrées
                    # (notification synchrone — pas de délai de queue). NB : sur
                    # OKX le niveau de marge est adjEq/mmr (ratio décimal,
                    # liquidation ≈ 1.0) — cf. exchange.fetch_margin_account.
                    logger.critical(
                        f"[MARGIN] 🚨 Margin level CRITIQUE : {ml_:.3f} "
                        f"< {ml_critical} — HALT du trading"
                    )
                    if not self.risk.halted:
                        self.risk.halted      = True
                        self.risk.halt_reason = f"Margin level critique : {ml_:.3f}"
                    self.notif.send(
                        f"🚨 *MARGIN LEVEL CRITIQUE* : `{ml_:.3f}`\n"
                        f"Trading HALTÉ — réduisez les positions ou ajoutez de la marge.",
                        async_=False
                    )
                elif ml_ < ml_alert:
                    logger.warning(f"[MARGIN] ⚠ Margin level bas : {ml_:.3f}")
                    self.notif.send(
                        f"⚠ MARGIN LEVEL BAS : {ml_:.3f}", async_=True
                    )
            if not self.cfg["trading"].get("paper_mode"):
                detail = self.exchange.fetch_balance_detail()
                if detail["free"] > 0:
                    # Équité margin = cash libre + valeur de marché signée des
                    # positions − emprunts (USDC). Le cash libre seul ignore
                    # l'actif détenu (long) → faux drawdown / faux HALT.
                    total = detail["free"] + self._open_positions_market_value() \
                            - detail.get("borrowed", 0.0)
                    with self._capital_lock:
                        self.capital_display = round(total, 4)
                    self._balance_detail = detail
                    self.risk.update_equity(self.capital_display)
                    self.allocator.update_equity(self.capital_display)
        except Exception as e:
            logger.warning(f"[MARGIN] sync KO : {e}")

    # ── Vérification pré-exécution ────────────────────────────────────────

    def _pre_execution_check(self, symbol: str, side: str,
                             size: float, price: float, notional: float) -> bool:
        """
        Vérifie que le capital disponible est suffisant avant d'ouvrir une position.

        En paper mode : compare le capital settled moins les positions ouvertes.
        En live mode  : vérifie les seuils minimaux de capital, PLUS (S1-07) le
        solde/marge RÉELS remontés par l'exchange (``_balance_detail``/
        ``_margin_level``, rafraîchis par ``_sync_spot_balance``/
        ``_sync_margin_account``) — ``capital_display`` seul est une valeur
        agrégée resynchronisée une fois par cycle : plusieurs ouvertures dans
        le même tour peuvent la laisser stale entre deux trades.

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

        is_margin = bool(self.cfg.get("exchange", {}).get("margin")
                         or self.cfg["trading"].get("margin_mode") is not None)
        detail = getattr(self, "_balance_detail", None)
        if detail and not is_margin:
            # Spot pur (sans levier) : le notionnel nécessite le même montant
            # en cash libre — pas d'emprunt possible pour combler l'écart.
            free = float(detail.get("free", 0.0) or 0.0)
            if free < notional:
                logger.warning(
                    f"[PreCheck] {symbol} : solde spot insuffisant "
                    f"(free={free:.2f} < notional={notional:.2f})"
                )
                return False
        elif is_margin:
            margin_level = getattr(self, "_margin_level", None)
            if margin_level is not None:
                ml_critical = float(self.cfg["trading"].get("margin_level_critical", 1.5))
                if margin_level < ml_critical:
                    logger.warning(
                        f"[PreCheck] {symbol} : margin level critique "
                        f"({margin_level:.3f} < {ml_critical}) — entrée refusée"
                    )
                    return False
        return True
