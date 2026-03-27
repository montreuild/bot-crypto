"""
PositionMixin — cycle de vie des positions (ouverture, suivi, clôture, restauration).

Regroupe toute la logique de position pour LiveTrader :
  - _restore_open_positions : restauration au démarrage depuis la BDD
  - _open_position          : ouverture d'une position (ordre + trailing stop + persistence)
  - _manage_position        : suivi tick-by-tick (trailing stop, gap, alertes)
  - _close_position         : clôture (ordre + P&L + BDD + notifications)
  - _serialize_position     : sérialisation pour l'API

Requiert que l'instance possède :
  self.exchange, self.cfg, self.risk, self.notif, self.scanner
  self.capital_display, self._capital_lock, self._paper_base
  self.open_positions, self.signal_log
  self._trailing_cfg, self._strat_thresholds, self.threshold
  self.SessionLocal, self.tf
  self._loss_notified, self._cooldown, self._margin_interest
  self.ohlcv_cache            (OHLCVCache — fournit get/get_cached_atr/set_atr)
  self.allocator              (CapitalAllocator — pour register_close)
  self._sync_spot_balance()   (défini dans BalanceSyncMixin)
"""
import logging
import time
from datetime import datetime, timezone

from app.core.trailing import TrailingStopManager
from app.core.database import (save_trade, update_daily_stats,
                                persist_open_position, delete_open_position,
                                load_open_positions, session_scope)
from app.core.indicators import atr_val as _compute_atr

logger = logging.getLogger(__name__)

# Mapping TF → secondes
_TF_SECS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


def _calc_unreal_pct(side: str, entry: float, price: float) -> float:
    """PnL non réalisé en % (protégé division par zéro)."""
    if entry <= 0:
        return 0.0
    return (price - entry) / entry * 100 if side == "long" \
           else (entry - price) / entry * 100


class PositionMixin:

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

        # En mode live : vérifier les positions réelles sur l'exchange
        exchange_symbols_with_pos = None
        if not self.cfg["trading"].get("paper_mode"):
            try:
                ex_positions = self.exchange.fetch_positions() or []
                exchange_symbols_with_pos = set()
                for ep in ex_positions:
                    contracts = float(ep.get("contracts") or ep.get("size") or 0)
                    if contracts > 0:
                        exchange_symbols_with_pos.add(ep.get("symbol", ""))
                logger.info(
                    f"[Reprise] {len(exchange_symbols_with_pos)} position(s) active(s) "
                    f"sur l'exchange : {exchange_symbols_with_pos}"
                )
            except Exception as _ep_err:
                logger.warning(
                    f"[Reprise] Impossible de vérifier les positions exchange : {_ep_err} "
                    f"— toutes les positions BDD seront restaurées."
                )

        for pos in positions:
            pos_id = pos["id"]
            symbol = pos["symbol"]

            # Écarter les positions fantômes (absentes de l'exchange)
            if (exchange_symbols_with_pos is not None
                    and not self.cfg["trading"].get("paper_mode")
                    and symbol not in exchange_symbols_with_pos):
                logger.warning(
                    f"[Reprise] Position {pos_id} ({symbol}) absente de l'exchange "
                    f"— ignorée (probablement clôturée hors-bot)."
                )
                with session_scope(self.SessionLocal) as _sess:
                    delete_open_position(_sess, pos_id)
                continue

            trailing = TrailingStopManager(**self._trailing_cfg)
            trailing.init_from_stop(pos["entry"], pos["stop"], pos["side"])
            pos["_trailing"] = trailing
            with self._positions_lock:
                self.open_positions[pos_id] = pos
            self.risk.register_open(pos)
            logger.info(
                f"  [Reprise] {pos['side'].upper()} {pos['symbol']} "
                f"@ {pos['entry']:.4f} | stop={pos['stop']:.4f} "
                f"| strat={pos['strategy']}"
            )

        if not self.cfg["trading"].get("paper_mode"):
            self._sync_spot_balance()

        self.notif.send(
            f"🔄 *Reprise après redémarrage*\n"
            f"`{n}` position(s) restaurée(s) depuis la BDD.\n"
            f"⚠️ Vérifiez que les stops sont cohérents avec le marché.",
            async_=False
        )

    # ── Ouverture ──────────────────────────────────────────────────────────

    def _open_position(self, pos_key: str, symbol: str, signal: dict,
                       price: float, size: float, notional: float,
                       atr: float, leverage: float, tf: str) -> None:
        trailing  = TrailingStopManager(**self._trailing_cfg)
        stop      = trailing.initial_stop(price, atr, signal["side"])
        order     = self.exchange.create_order(
            symbol, "market", signal["side"], size,
            params={"leverage": int(leverage)}
        )
        exec_price = order.get("price") or order.get("average") or price
        if not exec_price and not self.cfg["trading"].get("paper_mode"):
            try:
                filled = self.exchange.fetch_order(order.get("id", ""), symbol)
                exec_price = filled.get("average") or filled.get("price") or price
            except Exception as _fe:
                logger.warning(
                    f"[OPEN] {symbol} — fetch_order KO ({_fe}), "
                    f"utilisation du prix ticker pré-exécution {price:.6f} "
                    f"(PnL et stops peuvent être légèrement décalés)"
                )
                exec_price = price

        # Slippage adverse en paper mode (achat plus cher)
        if self.cfg["trading"].get("paper_mode"):
            slip = self.cfg["trading"].get("paper_slippage", 0.001)
            exec_price *= (1 + slip) if signal["side"] == "long" else (1 - slip)

        fee_rate = self.cfg["trading"].get("taker_fee", 0.001)
        fees     = exec_price * size * fee_rate
        with self._capital_lock:
            if self.cfg["trading"].get("paper_mode") and hasattr(self, "_paper_base"):
                self._paper_base -= fees
            else:
                self.capital_display -= fees

        strat_name = signal.get("name", "")
        pos = {
            "id":        pos_key,
            "symbol":    symbol,
            "side":      signal["side"],
            "strategy":  strat_name,
            "timeframe": tf,
            "score":     signal.get("score", 0),
            "entry":     exec_price,
            "stop":      stop,
            "size":      size,
            "notional":  notional,
            "leverage":  leverage,
            "open_time": time.time(),
            "fees":      fees,
            "pnl":       0.0,
            "reason":    signal.get("reason", ""),
            "order_id":  order.get("id", ""),
            "_trailing": trailing,
        }
        with self._positions_lock:
            self.open_positions[pos_key] = pos
        self.risk.register_open(pos)
        with session_scope(self.SessionLocal) as _sess:
            persist_open_position(_sess, pos)

        strat_threshold = self._strat_thresholds.get(strat_name, self.threshold)
        self.signal_log.append({
            "time":      datetime.now(timezone.utc).isoformat(),
            "symbol":    symbol,
            "strategy":  strat_name,
            "side":      signal.get("side", "?"),
            "score":     round(float(signal.get("score", 0)), 3),
            "threshold": round(float(strat_threshold), 3),
            "timeframe": tf,
            "status":    "opened",
            "entry":     round(float(exec_price), 4),
            "reason":    signal.get("reason", ""),
        })

        score_factor = round(
            0.5 + 0.5 * (signal.get("score", 0) - strat_threshold)
            / max(1.0 - strat_threshold, 1e-9),
            2,
        )
        logger.info(
            f"[OPEN] {signal['side'].upper()} {symbol} @ {exec_price:.4f} "
            f"| Strat={strat_name}@{tf} | Score={signal['score']:.2f} "
            f"| Sizing={score_factor * 100:.0f}% | Size={size:.6f} | Stop={stop:.4f}"
        )
        self.notif.notify_trade_open(pos)

    # ── Gestion (suivi tick-by-tick) ──────────────────────────────────────

    def _manage_position(self, pos_id: str) -> None:
        pos = self.open_positions.get(pos_id)
        if not pos:
            return
        symbol = pos["symbol"]
        pos_tf = pos.get("timeframe", self.tf)
        ticker = self._safe_ticker(symbol)
        if ticker is None:
            return
        price = ticker.get("last", pos["entry"])

        # Récupération ATR (cache → fetch → fallback 1%)
        atr = self.ohlcv_cache.get_cached_atr(symbol)
        if atr is None:
            df = self.ohlcv_cache.get(symbol, pos_tf, self.open_positions)
            if df is None:
                df = self.scanner.fetch_ohlcv(symbol, pos_tf, limit=100)
            if df is not None:
                atr = float(_compute_atr(df))
                self.ohlcv_cache.set_atr(symbol, atr)

        if atr is None or atr <= 0:
            atr = price * 0.01  # fallback 1%

        # Détection gap (prix franchit le stop de plus de 2%)
        if pos["side"] == "long" and price < pos["stop"] * 0.98:
            logger.warning(
                f"[Gap] {symbol} prix {price:.4f} < stop {pos['stop']:.4f} "
                f"— clôture forcée"
            )
            self._close_position(pos_id, price)
            return
        if pos["side"] == "short" and price > pos["stop"] * 1.02:
            logger.warning(
                f"[Gap] {symbol} prix {price:.4f} > stop {pos['stop']:.4f} "
                f"— clôture forcée"
            )
            self._close_position(pos_id, price)
            return

        _pos_tf_secs = _TF_SECS.get(pos.get("timeframe", "1h"), 3600)
        bars_held    = int((time.time() - pos["open_time"]) / _pos_tf_secs)
        trailing     = pos.get("_trailing")
        if trailing is None:
            trailing       = TrailingStopManager(**self._trailing_cfg)
            pos["_trailing"] = trailing

        new_stop = trailing.update_stop(
            price, pos["stop"], atr, pos["side"],
            entry=pos.get("entry"), bars_held=bars_held
        )
        if new_stop != pos["stop"]:
            pos["stop"] = new_stop
            with session_scope(self.SessionLocal) as _sess:
                persist_open_position(_sess, pos)

        # Notification perte non réalisée
        if pos_id not in self._loss_notified:
            unreal_pct = _calc_unreal_pct(pos["side"], pos["entry"], price)
            self.notif.notify_position_loss(
                symbol, pos.get("strategy", ""), pos["side"], unreal_pct
            )
            if unreal_pct < -self.notif._loss_warn_pct:
                self._loss_notified.add(pos_id)
        elif _calc_unreal_pct(pos["side"], pos["entry"], price) >= 0:
            self._loss_notified.discard(pos_id)

        if trailing.is_triggered(price, new_stop, pos["side"]):
            self._close_position(pos_id, price)

    # ── Clôture ───────────────────────────────────────────────────────────

    def _close_position(self, pos_id: str, exit_price: float) -> None:
        with self._positions_lock:
            pos = self.open_positions.pop(pos_id, None)
        if not pos:
            return
        close_side = "sell" if pos["side"] == "long" else "buy"
        order      = self.exchange.create_order(
            pos["symbol"], "market", close_side, pos["size"]
        )
        exec_price = order.get("price") or exit_price

        # Slippage adverse en paper mode (vente moins chère)
        if self.cfg["trading"].get("paper_mode"):
            slip = self.cfg["trading"].get("paper_slippage", 0.001)
            exec_price *= (1 - slip) if pos["side"] == "long" else (1 + slip)

        fee_rate    = self.cfg["trading"].get("taker_fee", 0.001)
        fees        = exec_price * pos["size"] * fee_rate
        hours_held  = (time.time() - pos["open_time"]) / 3600

        # Coût d'emprunt (Binance Margin : 3 périodes/jour avec intérêts composés)
        daily_rate      = self.cfg["trading"].get("borrow_rate_daily", 0.0002)
        periods_per_day = self.cfg["trading"].get("borrow_periods_per_day", 3)
        r_period        = daily_rate / periods_per_day
        n_periods       = hours_held * periods_per_day / 24
        borrow_cost     = pos["notional"] * ((1 + r_period) ** n_periods - 1)
        self._margin_interest += borrow_cost

        gross = (
            (exec_price - pos["entry"]) * pos["size"] if pos["side"] == "long"
            else (pos["entry"] - exec_price) * pos["size"]
        )
        pnl = gross - fees - borrow_cost

        with self._capital_lock:
            if self.cfg["trading"].get("paper_mode") and hasattr(self, "_paper_base"):
                self._paper_base += pnl
            else:
                self.capital_display += pnl

        self.risk.update_equity(self.capital_display)
        self.risk.register_close(pos_id)

        # Mise à jour circuit breakers par slot
        slot_key = f"{pos.get('strategy', '')}::{pos.get('timeframe', self.tf)}"
        self.risk.update_slot_result(slot_key, pnl, pnl > 0)

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
            "borrow_cost":   round(borrow_cost, 6),
            "status":        "closed",
            "duration_bars": bars_since,
            "exit_time":     datetime.now(timezone.utc),
            "timeframe":     pos.get("timeframe", self.tf),
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
            "reason":    "stop" if pnl < 0 else "trailing",
        })

        with session_scope(self.SessionLocal) as session:
            save_trade(session, trade)
            update_daily_stats(
                session,
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                pnl, pnl > 0, fees, self.capital_display
            )

        if "stop" in str(pos.get("reason", "")).lower() or pnl < 0:
            cooldown_secs = (
                self.cfg["trading"].get("reentry_cooldown_bars", 3) * self.interval
            )
            self._cooldown[pos["symbol"]] = time.time() + cooldown_secs

        self.notif.notify_trade(trade)
        logger.info(
            f"[CLOSE] {pos['side'].upper()} {pos['symbol']} @ {exec_price:.4f} "
            f"| PnL={pnl:+.4f} | Strat={pos.get('strategy', '')}@{pos.get('timeframe', '?')}"
        )

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
        except Exception:
            pass
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
