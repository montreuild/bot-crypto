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


def _apply_trail_override(base_cfg: dict, override: dict) -> dict:
    """Fusionne la config trailing globale avec un trail_override signal/position.

    Les clés d'override utilisent les noms du signal (trail_wide, trail_tight…)
    tandis que _trailing_cfg utilise mult / trail_tight_mult.
    """
    if not override:
        return base_cfg
    merged = dict(base_cfg)
    if "trail_wide"  in override: merged["mult"]             = float(override["trail_wide"])
    if "trail_tight" in override: merged["trail_tight_mult"] = float(override["trail_tight"])
    if "grace_bars"  in override: merged["grace_bars"]       = int(override["grace_bars"])
    if "breakeven_r" in override: merged["breakeven_r"]      = float(override["breakeven_r"])
    if "lock_r"      in override: merged["lock_r"]           = float(override["lock_r"])
    if "tight_r"     in override: merged["tight_r"]          = float(override["tight_r"])
    if "lock_ratio"  in override: merged["lock_ratio"]       = float(override["lock_ratio"])
    if "use_swing"   in override: merged["use_swing"]        = bool(override["use_swing"])
    if "mode"        in override: merged["mode"]             = str(override["mode"])
    return merged


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

            # Validate entry price is sane
            if pos.get("entry", 0) <= 0:
                logger.warning(
                    f"[Reprise] Position {pos_id} ({symbol}) a un prix d'entrée invalide "
                    f"({pos.get('entry')}) — supprimée."
                )
                with session_scope(self.SessionLocal) as _sess:
                    delete_open_position(_sess, pos_id)
                continue

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

            trail_cfg = _apply_trail_override(self._trailing_cfg, pos.get("trail_override"))
            trailing = TrailingStopManager(**trail_cfg)
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
        trail_override   = signal.get("trail_override") or {}
        disable_trailing = bool(signal.get("disable_trailing", False))
        trail_cfg        = _apply_trail_override(self._trailing_cfg, trail_override)
        trailing         = TrailingStopManager(**trail_cfg)
        # Stop initial : priorité au multiplicateur ATR (calé sur le prix exécuté),
        # sinon stop_hint absolu, sinon le trailing manager.
        if signal.get("sl_atr_mult") is not None:
            _sl_mult = float(signal["sl_atr_mult"])
            stop = (price - _sl_mult * atr) if signal["side"] == "long" \
                   else (price + _sl_mult * atr)
            trailing.init_from_stop(price, stop, signal["side"])
        elif signal.get("stop_hint") is not None:
            stop = float(signal["stop_hint"])
            # On initialise le trailing (peak / distance) pour qu'il puisse
            # reprendre la main si disable_trailing passe à False en cours de route.
            trailing.init_from_stop(price, stop, signal["side"])
        else:
            stop = trailing.initial_stop(price, atr, signal["side"])

        # Take-profit fixe optionnel — multiplicateur prioritaire pour rester
        # cohérent avec le prix d'exécution réel (robuste aux slippages).
        if signal.get("tp_atr_mult") is not None:
            _tp_mult = float(signal["tp_atr_mult"])
            take_profit = (price + _tp_mult * atr) if signal["side"] == "long" \
                          else (price - _tp_mult * atr)
        elif signal.get("tp_hint") is not None:
            take_profit = float(signal["tp_hint"])
        else:
            take_profit = None
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
            "id":             pos_key,
            "symbol":         symbol,
            "side":           signal["side"],
            "strategy":       strat_name,
            "timeframe":      tf,
            "score":          signal.get("score", 0),
            "entry":          exec_price,
            "stop":           stop,
            "size":           size,
            "notional":       notional,
            "leverage":       leverage,
            "open_time":      time.time(),
            "fees":           fees,
            "pnl":            0.0,
            "reason":         signal.get("reason", ""),
            "order_id":       order.get("id", ""),
            "trail_override": trail_override or None,
            "disable_trailing": disable_trailing,
            "take_profit":    take_profit,
            # Indicators + setup conservés pour les hooks (check_early_exit V6.1)
            "indicators":     signal.get("indicators") or {},
            "setup":          signal.get("setup"),
            "_trailing":      trailing,
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

        # ── Take-profit fixe (vérifié sur ticker, en complément du SL) ────────
        tp_val = pos.get("take_profit")
        if tp_val is not None:
            tp_hit = (pos["side"] == "long"  and price >= tp_val) or \
                     (pos["side"] == "short" and price <= tp_val)
            if tp_hit:
                logger.info(
                    f"[TP] {symbol} {pos['side']} TP={tp_val:.4f} touché @ {price:.4f}"
                )
                self._close_position(pos_id, tp_val)
                return

        # ── Sortie anticipée pilotée par la stratégie (check_early_exit) ───
        # Permet à la stratégie de clore une position sur changement de régime,
        # inversion du signal directionnel, etc. (V6.1 should_exit_early).
        # Évaluée APRÈS gap/TP mais AVANT trailing : si la stratégie demande la
        # sortie, on l'honore immédiatement au prix ticker courant.
        strat_name = pos.get("strategy", "")
        strat = self._loaded_strategies.get(strat_name) if strat_name else None
        if strat is not None and hasattr(strat, "check_early_exit"):
            df_ee = self.ohlcv_cache.get(symbol, pos_tf, self.open_positions)
            if df_ee is None:
                try:
                    df_ee = self.scanner.fetch_ohlcv(symbol, pos_tf, limit=300)
                except Exception:
                    df_ee = None
            if df_ee is not None and len(df_ee) > 50:
                try:
                    early_reason = strat.check_early_exit(df_ee, pos, self.strat_params)
                except Exception as _ee:
                    logger.warning(
                        f"[EarlyExit] {strat_name} KO sur {symbol} : {_ee}"
                    )
                    early_reason = None
                if early_reason:
                    pos["reason"] = str(early_reason)
                    logger.info(
                        f"[EarlyExit] {symbol} {pos['side']} clôture sur "
                        f"'{early_reason}' (stratégie {strat_name})"
                    )
                    self._close_position(pos_id, price)
                    return

        _pos_tf_secs = _TF_SECS.get(pos.get("timeframe", "1h"), 3600)
        bars_held    = int((time.time() - pos["open_time"]) / _pos_tf_secs)
        trailing     = pos.get("_trailing")
        if trailing is None:
            trail_cfg        = _apply_trail_override(self._trailing_cfg, pos.get("trail_override"))
            trailing         = TrailingStopManager(**trail_cfg)
            pos["_trailing"] = trailing

        # Trailing désactivé : le stop reste figé à sa valeur initiale.
        if pos.get("disable_trailing"):
            new_stop = pos["stop"]
        else:
            new_stop = trailing.update_stop(
                price, pos["stop"], atr, pos["side"],
                entry=pos.get("entry"), bars_held=bars_held
            )
            if new_stop != pos["stop"]:
                pos["stop"] = new_stop
                with session_scope(self.SessionLocal) as _sess:
                    persist_open_position(_sess, pos)

        # ── Pyramidage piloté par la stratégie (check_scale_in) ────────────
        # La stratégie peut demander l'ajout d'une unité sur position gagnante
        # (ex. snowball_pyramid). L'unité passe par les mêmes garde-fous que
        # l'entrée : risk.can_trade, sizing RiskManager, budget du slot.
        if strat is not None and hasattr(strat, "check_scale_in"):
            df_si = self.ohlcv_cache.get(symbol, pos_tf, self.open_positions)
            if df_si is not None and len(df_si) > 50:
                scale = None
                try:
                    scale = strat.check_scale_in(df_si, pos, self.strat_params)
                except Exception as _si:
                    logger.warning(f"[ScaleIn] {strat_name} KO sur {symbol} : {_si}")
                if scale:
                    try:
                        self._scale_in_position(pos_id, pos, price, atr, scale)
                    except Exception as _si:
                        logger.error(
                            f"[ScaleIn] Ajout d'unité {symbol} KO : {_si}", exc_info=True
                        )

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

    # ── Pyramidage (ajout d'unité) ─────────────────────────────────────────

    def _scale_in_position(self, pos_id: str, pos: dict, price: float,
                           atr: float, scale: dict) -> None:
        """Ajoute une unité à une position existante (pyramidage).

        Sizing identique à une entrée (RiskManager.compute_size × size_factor),
        contraint par le budget du slot. Le prix d'entrée moyen, la taille, le
        notional et les frais de la position sont recalculés puis persistés.
        """
        side   = pos["side"]
        symbol = pos["symbol"]
        ok_global, reason = self.risk.can_trade(side)
        if not ok_global:
            logger.debug(f"[ScaleIn] {symbol} refusé (risk: {reason})")
            return
        if price <= 0 or atr <= 0:
            return

        strat_threshold = self._strat_thresholds.get(pos.get("strategy", ""), self.threshold)
        sf = max(0.0, min(float(scale.get("size_factor", 1.0)), 2.0))
        add_size, add_notional = self.risk.compute_size(
            price, atr, score=float(pos.get("score", 0)),
            threshold=strat_threshold, size_factor=sf,
        )
        if add_size <= 0 or add_notional <= 0:
            return

        slot_key = f"{pos.get('strategy', '')}::{pos.get('timeframe', self.tf)}"
        ok_budget, reason_budget = self.allocator.can_allocate(slot_key, add_notional)
        if not ok_budget:
            logger.debug(f"[ScaleIn] {symbol} refusé (budget: {reason_budget})")
            return
        if not self._pre_execution_check(symbol, side, add_size, price, add_notional):
            return

        order = self.exchange.create_order(
            symbol, "market", side, add_size,
            params={"leverage": int(pos.get("leverage", 1))}
        )
        exec_price = order.get("price") or order.get("average") or price
        if self.cfg["trading"].get("paper_mode"):
            slip = self.cfg["trading"].get("paper_slippage", 0.001)
            exec_price *= (1 + slip) if side == "long" else (1 - slip)

        fee_rate = self.cfg["trading"].get("taker_fee", 0.001)
        add_fees = exec_price * add_size * fee_rate
        with self._capital_lock:
            if self.cfg["trading"].get("paper_mode") and hasattr(self, "_paper_base"):
                self._paper_base -= add_fees
            else:
                self.capital_display -= add_fees

        new_size = pos["size"] + add_size
        pos["entry"]    = (pos["entry"] * pos["size"] + exec_price * add_size) / new_size
        pos["size"]     = round(new_size, 6)
        pos["notional"] = round(pos.get("notional", 0.0) + add_notional, 4)
        pos["fees"]     = round(pos.get("fees", 0.0) + add_fees, 6)
        pos["scale_ins"] = pos.get("scale_ins", 0) + 1
        with session_scope(self.SessionLocal) as _sess:
            persist_open_position(_sess, pos)
        self.allocator.register_open(slot_key, add_notional)

        self.signal_log.append({
            "time":      datetime.now(timezone.utc).isoformat(),
            "symbol":    symbol,
            "strategy":  pos.get("strategy", ""),
            "side":      side,
            "score":     round(float(pos.get("score", 0)), 3),
            "threshold": round(float(strat_threshold), 3),
            "timeframe": pos.get("timeframe", self.tf),
            "status":    "scale_in",
            "entry":     round(float(exec_price), 4),
            "reason":    str(scale.get("reason", "pyramid")),
        })
        logger.info(
            f"[SCALE-IN] {side.upper()} {symbol} +{add_size:.6f} @ {exec_price:.4f} "
            f"| unité #{pos['scale_ins'] + 1} | entry moyen={pos['entry']:.4f} "
            f"| notional={pos['notional']:.2f}"
        )
        self.notif.send(
            f"➕ *Pyramidage* `{symbol}` {side} +{add_size:.6f} @ `{exec_price:.4f}` "
            f"({scale.get('reason', 'pyramid')})",
            async_=True
        )

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
        except Exception as e:
            logger.debug(f"[PositionMixin] upnl {pos.get('symbol', '?')} : {e}")
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
