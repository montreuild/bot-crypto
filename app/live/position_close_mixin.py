"""
PositionCloseMixin — clôture, PnL et sérialisation des positions pour LiveTrader.

Extrait de PositionMixin (ARCH-003 : découpage en 4 mixins spécialisés).
Regroupe la fin du cycle de vie :
  - _close_position         : clôture (ordre + PnL + BDD + notifications)
  - _reconcile_close_costs  : remplace les frais/emprunts estimés par les réels
  - _serialize_position     : sérialisation pour l'API

Requiert que l'instance possède (fournis par LiveTrader.__init__) :
  self.exchange, self.cfg, self.risk, self.notif
  self.capital_display, self._capital_lock, self._paper_base
  self.open_positions, self._positions_lock, self.signal_log
  self._strat_thresholds, self.threshold, self.tf, self.interval
  self.SessionLocal, self._margin_interest, self._loss_notified, self._cooldown
  self.allocator, self._safe_ticker
  self._paper_slippage_fraction, _cancel_exchange_stop (autres mixins)
"""
import logging
import time
from datetime import datetime, timezone

from app.core.bot_identity import build_slot_key
from app.core.config import DEFAULT_TAKER_FEE
from app.core.database import (
    delete_open_position,
    save_trade,
    session_scope,
    update_daily_stats,
)
from app.core.execution import close_pnl
from app.core.timeframes import TF_SECONDS as _TF_SECS

# Helpers partagés (ARCH-003 : centralisés dans position_open_mixin.py)
from app.live.position_open_mixin import (
    _order_fail_reason,
    _order_failed,
    publish_trade_closed,
)

logger = logging.getLogger(__name__)


class PositionCloseMixin:
    """Clôture, PnL et sérialisation (voir docstring module)."""

    # ── Réconciliation des coûts réels (live) ──────────────────────────────

    def _reconcile_close_costs(self, pos: dict, close_order: dict,
                               fees_est: float, borrow_est: float,
                               pnl_est: float) -> tuple:
        """Remplace les frais/emprunts ESTIMÉS par les valeurs RÉELLES exchange.

        - Frais du fill de clôture : somme des ``fee.cost`` des trades du
          close order (``fetch_my_trades``). Seuls les frais en devise de
          cotation (ou USDT/USDC) sont sommés — frais dans une devise tierce
          (ex. OKB) ignorés (pas de conversion fiable), on garde l'estimation.
        - Coût d'emprunt : intérêts réels accumulés depuis l'ouverture via
          ``fetch_borrow_interest`` (ccxt — supporté par OKX ; appel
          défensif avec repli sur l'estimation si indisponible).

        Best-effort : tout échec retombe sur les estimations (aucune exception
        propagée). Alerte si l'écart dépasse 5 % du coût estimé.
        Retourne ``(pnl, fees, borrow)`` ajustés.
        """
        symbol = pos["symbol"]
        quote  = symbol.split("/")[-1].split(":")[0]
        since  = max(0, int(pos.get("open_time", time.time()) * 1000) - 60_000)
        fees_real = None
        borrow_real = None

        # 1. Frais réels du fill de clôture
        try:
            close_id = str((close_order or {}).get("id") or "")
            if close_id and not close_id.startswith("paper_"):
                my_trades = self.exchange.fetch_my_trades(symbol, since=since) or []
                total, found, convertible = 0.0, False, True
                for t in my_trades:
                    if str(t.get("order")) != close_id:
                        continue
                    fee = t.get("fee") or {}
                    cost, cur = fee.get("cost"), fee.get("currency")
                    if cost is None:
                        continue
                    found = True
                    if cur in (quote, "USDT", "USDC"):
                        total += float(cost)
                    else:
                        convertible = False   # frais en devise tierce (ex. OKB) → estimation conservée
                        break
                if found and convertible:
                    fees_real = total
        except Exception as e:
            logger.debug(f"[Reconcile] fetch_my_trades {symbol} KO : {e}")

        # 2. Intérêts d'emprunt réels (margin uniquement)
        # S11 : `borrow_est > 0` suffit et est plus juste que la globale
        # `exchange.margin` — c'est la VENUE du trade qui décide s'il y a eu
        # emprunt (cf. execution.venue_borrow_rate). La globale pouvait bloquer
        # la réconciliation d'un trade margin quand elle était restée à false.
        if borrow_est > 0:
            try:
                fetch_bi = getattr(self.exchange, "fetch_borrow_interest", None)
                if callable(fetch_bi):
                    rows = fetch_bi(code=quote, symbol=symbol, since=since) or []
                    borrow_real = sum(float(r.get("interest") or 0) for r in rows)
            except Exception as e:
                logger.debug(f"[Reconcile] fetch_borrow_interest {symbol} KO : {e}")

        fees   = fees_real   if fees_real   is not None else fees_est
        borrow = borrow_real if borrow_real is not None else borrow_est
        if fees == fees_est and borrow == borrow_est:
            return pnl_est, fees_est, borrow_est

        pnl = pnl_est + (fees_est - fees) + (borrow_est - borrow)
        d_fees   = fees - fees_est
        d_borrow = borrow - borrow_est
        logger.info(
            f"[Reconcile] {symbol} coûts réels : fees {fees_est:.6f}→{fees:.6f} "
            f"(Δ{d_fees:+.6f}), borrow {borrow_est:.6f}→{borrow:.6f} "
            f"(Δ{d_borrow:+.6f}) — PnL ajusté {pnl_est:.6f}→{pnl:.6f}"
        )
        # Alerte si l'estimation était fausse de > 5 % (config locale à revoir :
        # taker_fee / borrow_rate_daily ne reflètent pas le compte réel).
        for label, est, real in (("frais", fees_est, fees), ("emprunt", borrow_est, borrow)):
            if est > 0 and abs(real - est) / est > 0.05:
                gap_pct = abs(real - est) / est * 100
                logger.warning(
                    f"[Reconcile] {symbol} : écart {label} estimé vs réel "
                    f"{gap_pct:.1f}% — ajustez la config "
                    f"(taker_fee / borrow_rate_daily)."
                )
                # Mismatch de réconciliation = alerte critique (Phase 4).
                try:
                    self.notif.notify_reconciliation_mismatch(
                        symbol, label, est, real, gap_pct
                    )
                except Exception:
                    pass
        return pnl, fees, borrow

    # ── Clôture ───────────────────────────────────────────────────────────

    def _close_position(self, pos_id: str, exit_price: float,
                        exit_reason: str = "unknown") -> None:
        """``exit_reason`` (FIN-06) : motif de clôture persisté dans
        ``Trade.exit_reason`` — distinct de ``pos["reason"]`` (motif
        d'OUVERTURE, jamais modifié ici). Vocabulaire aligné sur le backtest
        (``stop_loss``/``trailing_stop``/``take_profit``) + valeurs propres au
        live (``gap``, ``early_exit``, ``manual``). "unknown" par défaut pour
        tout appelant qui ne le précise pas encore.

        NOTE maker/taker (cf. FIN-06) : le live n'implémente aujourd'hui
        AUCUNE distinction maker/taker à l'exécution (toujours ``taker_fee``,
        cf. ``fee_rate`` ci-dessous) — contrairement au backtest
        (``Backtester._close_at``, ``maker=True`` pour TP/time-exit,
        ``maker=False`` pour stop). ``fee_taker``/``fee_maker`` reflètent donc
        honnêtement cette réalité (100 % taker) plutôt que de simuler une
        distinction non implémentée à l'exécution réelle.
        """
        with self._positions_lock:
            pos = self.open_positions.pop(pos_id, None)
        if not pos:
            return
        # Stop exchange : annulation avant la clôture market. S'il a déjà été
        # exécuté (position vendue par l'exchange), pas de second ordre —
        # on solde la position localement au prix du stop exécuté.
        ex_fill = pos.pop("_closed_by_exchange_stop", None)
        if ex_fill is None and pos.get("stop_order_id"):
            ex_fill = self._cancel_exchange_stop(pos)

        # G2 : `venue` porte le modèle de coûts de sortie ET la capacité
        # d'exécution — None-safe et strictement neutre en crypto.
        venue = self._venue_for(pos["symbol"], pos.get("strategy", ""),
                                pos.get("timeframe", self.tf))

        close_side = "sell" if pos["side"] == "long" else "buy"
        if ex_fill is not None:
            order      = ex_fill
            exec_price = ex_fill.get("average") or ex_fill.get("price") or exit_price
        else:
            order = self._execute_order(
                venue, pos["symbol"], "market", close_side, pos["size"],
                price=exit_price,
            )
            if _order_failed(order):
                # La position a déjà été retirée de self.open_positions (ligne 959) :
                # si on continue, l'ordre rejeté serait comptabilisé comme une
                # clôture réelle (PnL calculé sur le prix ticker, position supprimée
                # de la BDD) alors qu'elle reste ouverte côté exchange. On la remet
                # en gestion pour une nouvelle tentative au cycle suivant plutôt que
                # de la faire disparaître silencieusement.
                reason = _order_fail_reason(order)
                logger.critical(
                    f"[CLOSE] {pos['symbol']} : ordre de clôture NON exécuté "
                    f"({reason}) — position remise en gestion, nouvelle tentative "
                    f"au prochain cycle."
                )
                with self._positions_lock:
                    self.open_positions[pos_id] = pos
                self.notif.send(
                    f"🚨 *Échec de clôture* `{pos['symbol']}` : {reason}\n"
                    f"La position reste ouverte, nouvelle tentative au prochain cycle.",
                    async_=False
                )
                return
            exec_price = order.get("price") or order.get("average") or 0
            if not exec_price and not self.cfg["trading"].get("paper_mode"):
                # Ordres market réels : récupérer le prix moyen réellement exécuté
                try:
                    filled = self.exchange.fetch_order(order.get("id", ""), pos["symbol"])
                    exec_price = filled.get("average") or filled.get("price") or 0
                except Exception as _fe:
                    logger.warning(
                        f"[CLOSE] {pos['symbol']} — fetch_order KO ({_fe}), "
                        f"utilisation du prix ticker {exit_price:.6f} "
                        f"(PnL potentiellement décalé)"
                    )
            if not exec_price:
                exec_price = exit_price

        # Slippage adverse en paper mode (vente moins chère)
        if self.cfg["trading"].get("paper_mode"):
            slip = self._paper_slippage_fraction(
                pos["symbol"], pos.get("timeframe", self.tf), pos.get("notional", 0.0))
            exec_price *= (1 - slip) if pos["side"] == "long" else (1 + slip)

        # Décompte de clôture (frais, emprunt composé, PnL net) : formules
        # partagées avec le Backtester — app/core/execution.py (parité
        # verrouillée par tests/test_execution_parity.py).
        fee_rate    = self.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE)
        hours_held  = (time.time() - pos["open_time"]) / 3600
        pnl, fees, borrow_cost = close_pnl(
            side=pos["side"], entry=pos["entry"], exit_price=exec_price,
            size=pos["size"], notional=pos["notional"], fee_rate=fee_rate,
            daily_rate=self.cfg["trading"].get("borrow_rate_daily", 0.0002),
            hours_held=hours_held,
            periods_per_day=self.cfg["trading"].get("borrow_periods_per_day", 24),
            venue=venue,
        )
        # Réconciliation avec les coûts RÉELS de l'exchange (live uniquement) :
        # frais du fill de clôture via fetch_my_trades, intérêts d'emprunt réels
        # via l'historique margin. Remplace les estimations dans le PnL persisté.
        if (not self.cfg["trading"].get("paper_mode")
                and self.cfg["trading"].get("reconcile_real_costs", True)):
            pnl, fees, borrow_cost = self._reconcile_close_costs(
                pos, order, fees, borrow_cost, pnl
            )
        self._margin_interest += borrow_cost

        with self._capital_lock:
            if self.cfg["trading"].get("paper_mode") and hasattr(self, "_paper_base"):
                self._paper_base += pnl
            else:
                self.capital_display += pnl

        self.risk.update_equity(self.capital_display)
        self.risk.register_close(pos_id)

        # Mise à jour circuit breakers par slot
        slot_key = build_slot_key(pos.get('strategy', ''),
                                  pos.get('timeframe', self.tf),
                                  pos.get('symbol', ''))
        self.risk.update_slot_result(slot_key, pnl, pnl > 0)

        # S12 : l'enveloppe ET le budget de risque du slot sont rendus — la
        # position ne consomme plus rien dès qu'elle est close.
        self.ledger.release(pos_id)

        # Libération du budget du slot
        if hasattr(self, "allocator"):
            self.allocator.register_close(slot_key, pos.get("notional", 0), pnl)

        with session_scope(self.SessionLocal) as _sess:
            delete_open_position(_sess, pos_id)
        self._loss_notified.discard(pos_id)

        pnl_pct    = round(pnl / pos["notional"] * 100, 4) if pos.get("notional", 0) > 0 else 0.0
        _tf_s_pos  = _TF_SECS.get(pos.get("timeframe", "1h"), self.interval or 3600)
        bars_since = int((time.time() - pos["open_time"]) / max(_tf_s_pos, 1))

        trade = {k: v for k, v in pos.items() if k != "_trailing"}
        trade.update({
            "exit":          exec_price,
            "pnl":           round(pnl, 6),
            "pnl_pct":       pnl_pct,
            "fees":          round(fees, 6),
            # FIN-06 : aucune distinction maker/taker à l'exécution live
            # aujourd'hui (cf. docstring ci-dessus) — 100% taker, honnête.
            "fee_taker":     round(fees, 6),
            "fee_maker":     0.0,
            "borrow_cost":   round(borrow_cost, 6),
            "status":        "closed",
            "duration_bars": bars_since,
            "exit_time":     datetime.now(timezone.utc),
            "timeframe":     pos.get("timeframe", self.tf),
            "exit_reason":   exit_reason,
        })

        strat_threshold = self._strat_thresholds.get(pos.get("strategy", ""), self.threshold)
        self.signal_log.append({
            "time":      datetime.now(timezone.utc).isoformat(),
            "symbol":    pos["symbol"],
            "strategy":  pos.get("strategy", ""),
            "side":      pos["side"],
            "score":     round(float(pos.get("score", 0)), 3),
            "threshold": round(float(strat_threshold), 3),
            "timeframe": pos.get("timeframe", self.tf),
            "status":    "closed",
            "entry":     round(float(pos.get("entry", 0)), 4),
            "exit":      round(float(exec_price), 4),
            "pnl":       round(float(pnl), 4),
            "pnl_pct":   pnl_pct,
            "reason":    exit_reason,
        })

        with session_scope(self.SessionLocal) as session:
            save_trade(session, trade)
            update_daily_stats(
                session,
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                pnl, pnl > 0, fees, self.capital_display
            )

        if exit_reason in ("stop_loss", "trailing_stop", "gap") or pnl < 0:
            cooldown_secs = (
                self.cfg["trading"].get("reentry_cooldown_bars", 3) * self.interval
            )
            self._cooldown[pos["symbol"]] = time.time() + cooldown_secs

        self.notif.notify_trade(trade)
        # G2 : venue data-only — aucun ordre de vente n'est parti, il faut le
        # dire explicitement, sinon le PnL affiché correspondrait à une sortie
        # que l'utilisateur n'a pas passée.
        if not venue.can_execute:
            self.notif.notify_trade_signal(trade, venue=venue.name, action="close")
        logger.info(
            f"[CLOSE] {pos['side'].upper()} {pos['symbol']} @ {exec_price:.4f} "
            f"| PnL={pnl:+.4f} | Strat={pos.get('strategy', '')}@{pos.get('timeframe', '?')}"
        )

        # WebSocket temps réel — publish non bloquant (jamais critique)
        try:
            publish_trade_closed(
                slot_key=slot_key,
                symbol=pos["symbol"],
                side=pos["side"],
                entry_price=pos["entry"],
                exit_price=exec_price,
                pnl=float(pnl),
                pnl_pct=float(pnl_pct),
                fees=float(fees),
                reason=pos.get("reason", ""),
                duration_bars=int(bars_since),
            )
        except Exception:
            pass

    # ── Sérialisation pour l'API ──────────────────────────────────────────

    def _serialize_position(self, pos: dict) -> dict:
        upnl = 0.0
        try:
            ticker = self._safe_ticker(pos["symbol"])
            if ticker:
                price = ticker.get("last", pos["entry"])
                upnl  = (
                    (price - pos["entry"]) * pos["size"] if pos["side"] == "long"
                    else (pos["entry"] - price) * pos["size"]
                )
        except Exception as e:
            logger.debug(f"[PositionCloseMixin] upnl {pos.get('symbol', '?')} : {e}")
        return {
            "id":        pos.get("id", ""),
            "symbol":    pos.get("symbol", ""),
            "side":      pos.get("side", ""),
            "strategy":  pos.get("strategy", ""),
            "timeframe": pos.get("timeframe", ""),
            "score":     round(float(pos.get("score", 0)), 3),
            "entry":     round(float(pos.get("entry", 0)), 4),
            "stop":      round(float(pos.get("stop", 0)), 4),
            "size":      round(float(pos.get("size", 0)), 6),
            "notional":  round(float(pos.get("notional", 0)), 2),
            "fees":      round(float(pos.get("fees", 0)), 4),
            "upnl":      round(float(upnl), 4),
            "open_time": pos.get("open_time", 0),
            "reason":    pos.get("reason", ""),
        }
