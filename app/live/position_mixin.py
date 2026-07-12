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

from app.core.execution import close_pnl, trade_fees
from app.core.trailing import TrailingStopManager
from app.core.database import (save_trade, update_daily_stats,
                                persist_open_position, delete_open_position,
                                load_open_positions, session_scope)
from app.core.indicators import atr_val as _compute_atr
from app.core.bot_identity import build_slot_key
from app.core.config import DEFAULT_TAKER_FEE

logger = logging.getLogger(__name__)

# Mapping TF → secondes — source unique (V4-A).
from app.core.timeframes import TF_SECONDS as _TF_SECS  # noqa: E402


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

    # ── Sizing : distance au stop initial (parité backtest) ─────────────────

    def _initial_stop_distance(self, side: str, price: float, atr: float,
                               signal: dict) -> float:
        """Distance absolue entry→stop initial, pour un sizing par le risque réel.

        Réplique la logique de stop initial de :meth:`_open_position` SANS ouvrir
        la position, afin que ``RiskManager.compute_size`` dimensionne par la
        distance au stop (comme le backtest) plutôt que par l'ATR brut — sinon le
        risque réel vaut ``risk% × (stop_dist / ATR)`` (sur-risque). Ordre de
        priorité identique à l'ouverture : ``sl_atr_mult`` → ``stop_hint`` →
        trailing initial (``mult × ATR``). Retourne 0.0 si indéterminable
        (``compute_size`` retombe alors sur l'ATR).
        """
        try:
            if signal.get("sl_atr_mult") is not None:
                return abs(float(signal["sl_atr_mult"]) * atr)
            if signal.get("stop_hint") is not None:
                return abs(float(price) - float(signal["stop_hint"]))
            trail_cfg = _apply_trail_override(
                self._trailing_cfg, signal.get("trail_override") or {}
            )
            return abs(float(trail_cfg.get("mult", 2.5)) * atr)
        except (TypeError, ValueError):
            return 0.0

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

            # Cohérence entry/taille avec l'ordre réel (live) : si le bot a
            # crashé entre l'exécution de l'ordre et la persistance, la BDD
            # peut contenir un prix d'entrée pré-exécution ou une taille non
            # ajustée du remplissage partiel → stops/PnL faux à la reprise.
            if not self.cfg["trading"].get("paper_mode") and pos.get("order_id"):
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

            trail_cfg = _apply_trail_override(self._trailing_cfg, pos.get("trail_override"))
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

        if not self.cfg["trading"].get("paper_mode"):
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
        else:
            # Remplissage partiel : aligner la taille trackée sur la taille
            # réellement exécutée (sinon stops/PnL calculés sur une taille fausse).
            try:
                filled = float(order.get("filled") or 0)
                if 0 < filled < size * 0.98:
                    logger.warning(
                        f"[OPEN] {symbol} remplissage partiel : "
                        f"{filled:.6f}/{size:.6f} — taille de position ajustée"
                    )
                    size     = filled
                    notional = size * exec_price
            except (TypeError, ValueError):
                pass

        fee_rate = self.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE)
        fees     = trade_fees(exec_price, size, fee_rate)
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

        # Stop-loss côté exchange (filet de sécurité si le bot tombe) —
        # le stop logiciel/trailing reste la référence de gestion.
        if self._exchange_stops_enabled():
            self._place_exchange_stop(pos)

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
                # Replace le stop exchange au nouveau niveau ; si l'ancien stop
                # a déjà été exécuté côté exchange, clôture locale immédiate.
                filled_stop = self._update_exchange_stop(pos)
                if filled_stop is not None:
                    pos["_closed_by_exchange_stop"] = filled_stop
                    self._close_position(pos_id, price)
                    return

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

    # ── Stop-loss / take-profit côté exchange ───────────────────────────────
    #
    # En live réel, un stop purement logiciel laisse la position sans protection
    # si le bot crash ou perd le réseau. On pose donc un ordre protecteur côté
    # exchange en miroir du stop logiciel, remplacé quand le trailing remonte le
    # stop, annulé à la clôture.
    #
    # Sur OKX, si la position porte un take-profit, on pose un **OCO natif**
    # (SL + TP en un seul ordre algo lié : ``ordType: 'oco'`` via ccxt) — les
    # deux jambes vivent sur l'exchange (le TP est capté même bot éteint) et
    # l'exécution de l'une annule l'autre. Sinon (pas de TP, ou exchange sans
    # support OCO) : stop simple STOP_LOSS_LIMIT, comportement initial.
    #
    # NB : l'OCO attaché à l'ordre d'entrée (attachAlgoOrds) est réservé aux
    # perp/swap chez OKX — indisponible en spot/margin, d'où l'OCO standalone.
    #
    # Opt-out via trading.exchange_stop_orders: false. Dégradation gracieuse :
    # un échec de pose n'empêche pas le trade (le stop logiciel reste actif)
    # mais déclenche une notification.

    def _exchange_stops_enabled(self) -> bool:
        return (not self.cfg["trading"].get("paper_mode")
                and bool(self.cfg["trading"].get("exchange_stop_orders", True)))

    def _exchange_oco_supported(self) -> bool:
        """True si l'exchange supporte un OCO standalone SL+TP (OKX)."""
        return getattr(self.exchange, "_name", "") == "okx"

    def _place_exchange_stop(self, pos: dict) -> None:
        """Pose la protection exchange en miroir du stop logiciel.

        OCO natif (SL+TP) si un take-profit est défini et que l'exchange le
        supporte (OKX) ; sinon stop-loss-limit simple.
        """
        try:
            close_side = "sell" if pos["side"] == "long" else "buy"
            stop_price = float(pos["stop"])
            offset     = float(self.cfg["trading"].get("exchange_stop_limit_offset", 0.005))
            # Long → on vend légèrement SOUS le déclencheur ; short → on achète
            # légèrement AU-DESSUS — garantit le remplissage du limit au trigger.
            edge = (1 - offset) if pos["side"] == "long" else (1 + offset)
            sl_limit = stop_price * edge

            tp_price = pos.get("take_profit")
            if tp_price and self._exchange_oco_supported():
                tp_price = float(tp_price)
                tp_limit = tp_price * edge
                # ccxt OKX : stopLossPrice + takeProfitPrice ⇒ ordType 'oco'.
                # slOrdPx / tpOrdPx = prix limite de chaque jambe (sinon marché).
                order = self.exchange.create_order(
                    pos["symbol"], "limit", close_side, pos["size"], None,
                    params={
                        "stopLossPrice":   stop_price,
                        "slOrdPx":         sl_limit,
                        "takeProfitPrice": tp_price,
                        "tpOrdPx":         tp_limit,
                    },
                )
                pos["stop_order_id"] = order.get("id")
                pos["_exchange_oco"] = True
                logger.info(
                    f"[StopExchange] {pos['symbol']} OCO posé "
                    f"SL@{stop_price:.4f} / TP@{tp_price:.4f} "
                    f"(id={pos['stop_order_id']})"
                )
            else:
                order = self.exchange.create_order(
                    pos["symbol"], "limit", close_side, pos["size"], sl_limit,
                    params={"stopPrice": stop_price},
                )
                pos["stop_order_id"] = order.get("id")
                pos.pop("_exchange_oco", None)
                logger.info(
                    f"[StopExchange] {pos['symbol']} stop posé @ {stop_price:.4f} "
                    f"(limit {sl_limit:.4f}, id={pos['stop_order_id']})"
                )
        except Exception as e:
            pos.pop("stop_order_id", None)
            pos.pop("_exchange_oco", None)
            logger.error(
                f"[StopExchange] Pose de la protection {pos['symbol']} KO : {e} "
                f"— position protégée par le stop logiciel uniquement"
            )
            self.notif.send(
                f"⚠️ *Stop exchange non posé* `{pos['symbol']}` : {e}\n"
                f"La position n'est protégée que par le stop logiciel.",
                async_=True
            )

    def _cancel_exchange_stop(self, pos: dict):
        """Annule le stop exchange. Retourne l'ordre s'il a DÉJÀ été exécuté
        (position clôturée côté exchange pendant que le bot ne regardait pas),
        None sinon."""
        oid = pos.pop("stop_order_id", None)
        if not oid:
            return None
        try:
            o = self.exchange.fetch_order(oid, pos["symbol"]) or {}
            status = str(o.get("status", "")).lower()
            if status in ("closed", "filled"):
                logger.warning(
                    f"[StopExchange] {pos['symbol']} : stop {oid} déjà exécuté "
                    f"côté exchange (avg={o.get('average')})"
                )
                return o
            if status not in ("canceled", "cancelled", "expired", "rejected"):
                self.exchange.cancel_order(oid, pos["symbol"])
        except Exception as e:
            logger.warning(f"[StopExchange] Annulation stop {pos['symbol']} KO : {e}")
        return None

    def _update_exchange_stop(self, pos: dict):
        """Remplace le stop exchange après remontée du trailing. Retourne
        l'ordre stop s'il était déjà exécuté (la position doit être clôturée
        localement sans nouvel ordre), None sinon."""
        if not self._exchange_stops_enabled():
            return None
        filled = self._cancel_exchange_stop(pos)
        if filled is not None:
            return filled
        self._place_exchange_stop(pos)
        return None

    def _adopt_or_place_exchange_stop(self, pos: dict) -> None:
        """À la restauration : adopte un stop déjà ouvert sur l'exchange pour ce
        symbole (évite les stops dupliqués qui vendraient deux fois), sinon en
        pose un nouveau."""
        try:
            close_side = "sell" if pos["side"] == "long" else "buy"
            open_orders = self.exchange.fetch_open_orders(pos["symbol"]) or []
            for o in open_orders:
                info_type = str(o.get("type", "")).lower()
                info      = o.get("info") or {}
                # Reconnaît stop simple ET OCO/algo OKX (slTriggerPx / tpTriggerPx /
                # ordType oco|conditional|trigger) pour éviter un ordre dupliqué.
                is_protective = (
                    "stop" in info_type or "oco" in info_type
                    or o.get("stopPrice") or o.get("stopLossPrice")
                    or info.get("stopPrice") or info.get("slTriggerPx")
                    or info.get("tpTriggerPx")
                    or str(info.get("ordType", "")).lower() in ("oco", "conditional", "trigger")
                )
                if o.get("side") == close_side and is_protective:
                    pos["stop_order_id"] = o.get("id")
                    if info.get("tpTriggerPx") or "oco" in str(info.get("ordType", "")).lower():
                        pos["_exchange_oco"] = True
                    logger.info(
                        f"[StopExchange] {pos['symbol']} : protection existante adoptée "
                        f"(id={pos['stop_order_id']})"
                    )
                    return
        except Exception as e:
            logger.debug(f"[StopExchange] fetch_open_orders {pos['symbol']} KO : {e}")
        self._place_exchange_stop(pos)

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
        # Sizing de l'unité par la distance au stop courant (parité backtest).
        add_stop_dist = abs(float(price) - float(pos.get("stop", 0.0)))
        add_size, add_notional = self.risk.compute_size(
            price, atr, score=float(pos.get("score", 0)),
            threshold=strat_threshold, size_factor=sf,
            stop_dist=add_stop_dist,
        )
        if add_size <= 0 or add_notional <= 0:
            return

        slot_key = build_slot_key(pos.get('strategy', ''),
                                  pos.get('timeframe', self.tf),
                                  pos.get('symbol', ''))
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

        fee_rate = self.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE)
        add_fees = trade_fees(exec_price, add_size, fee_rate)
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
        # Le stop exchange couvre l'ancienne taille → replacement avec la nouvelle
        filled_stop = self._update_exchange_stop(pos)
        if filled_stop is not None:
            pos["_closed_by_exchange_stop"] = filled_stop
            self._close_position(pos_id, exec_price)
            return

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
        if self.cfg.get("exchange", {}).get("margin") and borrow_est > 0:
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

    def _close_position(self, pos_id: str, exit_price: float) -> None:
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

        close_side = "sell" if pos["side"] == "long" else "buy"
        if ex_fill is not None:
            order      = ex_fill
            exec_price = ex_fill.get("average") or ex_fill.get("price") or exit_price
        else:
            order      = self.exchange.create_order(
                pos["symbol"], "market", close_side, pos["size"]
            )
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
            slip = self.cfg["trading"].get("paper_slippage", 0.001)
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
