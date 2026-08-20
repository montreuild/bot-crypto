"""
PositionRestoreMixin — restauration des positions après redémarrage.

Extrait de PositionMixin (ARCH-003 : découpage en 4 mixins spécialisés).
Regroupe la reprise depuis la BDD :
  - _restore_open_positions   : restauration au démarrage (vérif exchange)
  - _verify_restored_position : cohérence entry/taille avec l'ordre réel

Requiert que l'instance possède (fournis par LiveTrader.__init__) :
  self.exchange, self.cfg, self.risk, self.notif
  self.open_positions, self._positions_lock
  self._trailing_cfg, self.SessionLocal
  self._sync_spot_balance (défini dans BalanceSyncMixin)
  self._exchange_stops_enabled, _adopt_or_place_exchange_stop (PositionManageMixin)
"""
import logging

from app.core.database import (
    delete_open_position,
    load_open_positions,
    session_scope,
)
from app.core.trailing import TrailingStopManager

# Helpers partagés (ARCH-003 : centralisés dans position_open_mixin.py)
from app.live.position_open_mixin import _apply_trail_override
from app.live.protocols import LiveHost

logger = logging.getLogger(__name__)


class PositionRestoreMixin(LiveHost):
    """Restauration après redémarrage (voir docstring module)."""

    # ── Restauration au démarrage ──────────────────────────────────────────

    def _restore_open_positions(self) -> None:
        """
        Restaure les positions ouvertes depuis la BDD au démarrage.

        En mode live, vérifie que chaque position existe réellement sur l'exchange
        pour éviter de gérer des "positions fantômes".
        """
        with session_scope(self.SessionLocal) as session:
            positions = load_open_positions(session)
        if not positions:
            return
        n = len(positions)
        logger.warning(f"[Reprise] {n} position(s) trouvée(s) en BDD — restauration...")

        # En mode live : vérifier les positions réelles sur l'exchange.
        # L-03 : fetch_positions() est l'API dérivés. Sur spot/margin OKX
        # une liste vide n'est pas « aucune position » — c'est « on ne sait
        # pas ». On ne l'utilise donc que pour les perps.
        exchange_symbols_with_pos = None
        if not self.cfg["trading"].get("paper_mode", True):
            try:
                from app.core.bot_identity import resolve_venue
                _v = resolve_venue(self.cfg)
                if getattr(_v, "market_type", "") == "perp":
                    ex_positions = self.exchange.fetch_positions() or []
                    exchange_symbols_with_pos = set()
                    for ep in ex_positions:
                        contracts = float(ep.get("contracts") or ep.get("size") or 0)
                        if contracts > 0:
                            exchange_symbols_with_pos.add(ep.get("symbol", ""))
                    logger.info(
                        f"[Reprise] {len(exchange_symbols_with_pos)} position(s) "
                        f"perp active(s) : {exchange_symbols_with_pos}"
                    )
                else:
                    logger.info(
                        f"[Reprise] venue {getattr(_v, 'market_type', '?')} : "
                        f"pas de détection de fantômes via fetch_positions — "
                        f"toutes les positions BDD sont restaurées."
                    )
            except Exception as _ep_err:
                logger.warning(
                    f"[Reprise] Impossible de vérifier les positions exchange : {_ep_err} "
                    f"— toutes les positions BDD seront restaurées."
                )

        for pos in positions:
            pos_id = pos["id"]
            symbol = pos["symbol"]

            # L-03 / L-04 : un désaccord n'est pas une preuve. On marque
            # orphelin et on restaure — une suppression est irréversible.
            if (exchange_symbols_with_pos is not None
                    and not self.cfg["trading"].get("paper_mode", True)
                    and symbol not in exchange_symbols_with_pos):
                logger.warning(
                    f"[Reprise] Position {pos_id} ({symbol}) absente de l'exchange "
                    f"— marquée orpheline, conservée (pas de suppression)."
                )
                pos["_orphaned"] = True
                notif = getattr(self, "notif", None)
                if notif is not None:
                    try:
                        notif.send(
                            f"⚠️ Reprise : `{symbol}` absente de l'exchange "
                            f"— position {pos_id} conservée (orpheline).",
                            async_=False,
                        )
                    except Exception as _nerr:
                        logger.error(
                            f"[Reprise] notification orpheline {symbol} KO : {_nerr}"
                        )

            # Validate entry price is sane
            if pos.get("entry", 0) <= 0:
                logger.warning(
                    f"[Reprise] Position {pos_id} ({symbol}) a un prix d'entrée invalide "
                    f"({pos.get('entry')}) — supprimée."
                )
                with session_scope(self.SessionLocal) as _sess:
                    delete_open_position(_sess, pos_id)
                continue

            # Cohérence entry/taille avec l'ordre réel (live) : si le bot a
            # crashé entre l'exécution de l'ordre et la persistance, la BDD
            # peut contenir un prix d'entrée pré-exécution ou une taille non
            # ajustée du remplissage partiel → stops/PnL faux à la reprise.
            if not self.cfg["trading"].get("paper_mode", True) and pos.get("order_id"):
                self._verify_restored_position(pos)

            # Validate stop is not already breached (if we can get a ticker)
            try:
                ticker = self.exchange.fetch_ticker(symbol) if hasattr(self, 'exchange') else None
                if ticker:
                    last_price = ticker.get("last", 0)
                    side = pos.get("side", "long")
                    if last_price > 0:
                        stop_breached = (
                            (side == "long" and last_price <= pos.get("stop", 0))
                            or (side == "short" and last_price >= pos.get("stop", 0))
                        )
                        if stop_breached:
                            logger.warning(
                                f"[Reprise] Position {pos_id} ({symbol}) stop déjà franchi "
                                f"(prix={last_price:.4f}, stop={pos['stop']:.4f}) "
                                f"— sera clôturée au prochain cycle."
                            )
            except Exception as _tk_err:
                logger.debug(f"[Reprise] Impossible de vérifier le prix de {symbol} : {_tk_err}")

            trail_cfg = _apply_trail_override(self._trailing_cfg, pos.get("trail_override") or {})
            trailing = TrailingStopManager(**trail_cfg)
            trailing.init_from_stop(pos["entry"], pos["stop"], pos["side"])
            pos["_trailing"] = trailing
            with self._positions_lock:
                self.open_positions[pos_id] = pos
            self.risk.register_open(pos)
            # Re-protège la position : adopte le stop exchange existant si
            # présent (l'id n'est pas persisté en BDD), sinon en pose un.
            if self._exchange_stops_enabled():
                self._adopt_or_place_exchange_stop(pos)
            logger.info(
                f"  [Reprise] {pos['side'].upper()} {pos['symbol']} "
                f"@ {pos['entry']:.4f} | stop={pos['stop']:.4f} "
                f"| strat={pos['strategy']}"
            )

        if not self.cfg["trading"].get("paper_mode", True):
            self._sync_spot_balance()

        self.notif.send(
            f"🔄 *Reprise après redémarrage*\n"
            f"`{n}` position(s) restaurée(s) depuis la BDD.\n"
            f"⚠️ Vérifiez que les stops sont cohérents avec le marché.",
            async_=False
        )

    def _verify_restored_position(self, pos: dict) -> None:
        """Croise la position BDD avec l'ordre d'ouverture réel de l'exchange.

        Ajuste ``entry`` (prix moyen réellement exécuté) si l'écart dépasse
        0,1 % et ``size``/``notional`` (quantité réellement remplie) si l'écart
        dépasse 2 %. Best-effort : ordre introuvable ou erreur réseau → la
        position BDD est conservée telle quelle (log debug).
        """
        order_id = str(pos.get("order_id") or "")
        if not order_id or order_id.startswith("paper_"):
            return
        try:
            order = self.exchange.fetch_order(order_id, pos["symbol"]) or {}
        except Exception as e:
            logger.debug(
                f"[Reprise] Vérification ordre {order_id} ({pos['symbol']}) KO : {e}"
            )
            return

        real_entry = order.get("average") or order.get("price")
        try:
            real_entry = float(real_entry) if real_entry else 0.0
        except (TypeError, ValueError):
            real_entry = 0.0
        if real_entry > 0 and pos.get("entry", 0) > 0:
            drift = abs(real_entry - pos["entry"]) / pos["entry"]
            if drift > 0.001:
                logger.warning(
                    f"[Reprise] {pos['symbol']} : entry BDD {pos['entry']:.6f} ≠ "
                    f"prix exécuté réel {real_entry:.6f} ({drift * 100:.2f}%) — corrigé."
                )
                pos["entry"] = real_entry

        try:
            filled = float(order.get("filled") or 0)
        except (TypeError, ValueError):
            filled = 0.0
        if filled > 0 and pos.get("size", 0) > 0:
            drift = abs(filled - pos["size"]) / pos["size"]
            if drift > 0.02:
                logger.warning(
                    f"[Reprise] {pos['symbol']} : taille BDD {pos['size']:.6f} ≠ "
                    f"quantité remplie réelle {filled:.6f} ({drift * 100:.1f}%) — corrigée."
                )
                pos["size"]     = round(filled, 6)
                pos["notional"] = round(filled * pos["entry"], 4)
