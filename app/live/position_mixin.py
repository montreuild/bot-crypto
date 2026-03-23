"""
PositionMixin — cycle de vie des positions pour LiveTrader.

Regroupe les méthodes privées de gestion de position :
  _open_position      Ouverture : ordre, calcul frais, persistance BDD
  _manage_position    Suivi : trailing stop, gap detection, notifications perte
  _close_position     Clôture : ordre, PnL, BDD, cooldown, notifications
  _serialize_position Sérialisation vers dict JSON (pour /api/status)

Ces méthodes accèdent aux attributs de l'instance LiveTrader via `self` —
le pattern mixin Python garantit la résolution au runtime.
"""
import logging
import time
from datetime import datetime, timezone

from app.core.trailing import TrailingStopManager
from app.core.database import (save_trade, update_daily_stats,
                                persist_open_position, delete_open_position)
from app.core.indicators import atr_val as _compute_atr

logger = logging.getLogger(__name__)

# Mapping TF → secondes (partagé avec live_trader pour _close_position)
_TF_SECS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


def _calc_unreal_pct(side: str, entry: float, price: float) -> float:
    """Calcule le % de PnL non réalisé d'une position (protégé division par zéro)."""
    if entry <= 0:
        return 0.0
    return (price - entry) / entry * 100 if side == "long" \
           else (entry - price) / entry * 100


class PositionMixin:
    """
    Mixin de gestion de position pour LiveTrader.

    Requiert que l'instance possède :
      self.exchange, self.cfg, self.risk, self.notif
      self.capital_display, self._capital_lock
      self.open_positions, self.signal_log
      self._trailing_cfg, self._strat_thresholds, self.threshold
      self.SessionLocal, self.tf
      self._loss_notified, self._cooldown, self._margin_interest
      self._get_ohlcv()     (méthode définie dans LiveTrader)
      self._get_cached_atr() (méthode définie dans LiveTrader)
    """

    # ── Ouverture ──────────────────────────────────────────────────────────
    def _open_position(self, pos_key: str, symbol: str, signal: dict,
                       price: float, size: float, notional: float,
                       atr: float, leverage: float, tf: str):
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
            except Exception:
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
        self.open_positions[pos_key] = pos
        self.risk.register_open(pos)
        _sess = self.SessionLocal()
        try:
            persist_open_position(_sess, pos)
        finally:
            _sess.close()

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

    # ── Gestion ────────────────────────────────────────────────────────────
    def _manage_position(self, pos_id: str):
        pos = self.open_positions.get(pos_id)
        if not pos:
            return
        symbol = pos["symbol"]
        pos_tf = pos.get("timeframe", self.tf)
        ticker = self._safe_ticker(symbol)
        if ticker is None:
            return
        price = ticker.get("last", pos["entry"])

        atr = self._get_cached_atr(symbol)
        if atr is None:
            df = self._get_ohlcv(symbol, pos_tf)
            if df is None:
                df = self.scanner.fetch_ohlcv(symbol, pos_tf, limit=100)
            if df is not None:
                atr = _compute_atr(df)
                self._atr_cache[symbol] = (time.time(), atr)

        if atr is None or atr <= 0:
            atr = price * 0.01  # fallback 1 %

        # Détection gap
        if pos["side"] == "long" and price < pos["stop"] * 0.98:
            logger.warning(
                f"[Gap] {symbol} prix {price:.4f} < stop {pos['stop']:.4f} — clôture forcée"
            )
            self._close_position(pos_id, price)
            return
        if pos["side"] == "short" and price > pos["stop"] * 1.02:
            logger.warning(
                f"[Gap] {symbol} prix {price:.4f} > stop {pos['stop']:.4f} — clôture forcée"
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
            _sess = self.SessionLocal()
            try:
                persist_open_position(_sess, pos)
            finally:
                _sess.close()

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

    # ── Clôture ────────────────────────────────────────────────────────────
    def _close_position(self, pos_id: str, exit_price: float):
        pos = self.open_positions.pop(pos_id, None)
        if not pos:
            return
        close_side = "sell" if pos["side"] == "long" else "buy"
        order      = self.exchange.create_order(
            pos["symbol"], "market", close_side, pos["size"]
        )
        exec_price  = order.get("price") or exit_price
        # Slippage adverse en paper mode (vente moins chère)
        if self.cfg["trading"].get("paper_mode"):
            slip = self.cfg["trading"].get("paper_slippage", 0.001)
            exec_price *= (1 - slip) if pos["side"] == "long" else (1 + slip)
        fee_rate    = self.cfg["trading"].get("taker_fee", 0.001)
        fees        = exec_price * pos["size"] * fee_rate
        hours_held  = (time.time() - pos["open_time"]) / 3600
        hourly_rate = self.cfg["trading"].get("borrow_rate_daily", 0.0002) / 24
        borrow_cost = pos["notional"] * hourly_rate * hours_held
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

        _sess = self.SessionLocal()
        try:
            delete_open_position(_sess, pos_id)
        finally:
            _sess.close()
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
        session = self.SessionLocal()
        try:
            save_trade(session, trade)
            update_daily_stats(
                session,
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                pnl, pnl > 0, fees, self.capital_display
            )
        finally:
            session.close()

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

    # ── Sérialisation ──────────────────────────────────────────────────────
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
