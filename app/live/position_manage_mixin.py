"""
PositionManageMixin — gestion des positions ouvertes pour LiveTrader.

Extrait de PositionMixin (ARCH-003 : découpage en 4 mixins spécialisés).
Regroupe le suivi tick-by-tick et la protection exchange :
  - _manage_position               : trailing stop, gap, TP, early-exit, scale-in
  - _exchange_stops_enabled        : opt-in stops exchange
  - _exchange_oco_supported        : détection OCO natif (OKX)
  - _place_exchange_stop           : pose SL/OCO côté exchange
  - _cancel_exchange_stop          : annulation + détection exécution
  - _update_exchange_stop          : replacement après remontée trailing
  - _adopt_or_place_exchange_stop  : adoption à la restauration
  - _scale_in_position             : pyramidage (ajout d'unité)

Requiert que l'instance possède (fournis par LiveTrader.__init__) :
  self.exchange, self.cfg, self.risk, self.notif, self.scanner
  self.open_positions, self._positions_lock, self.signal_log
  self._trailing_cfg, self._strat_thresholds, self.threshold
  self.ohlcv_cache, self.ledger, self.rejections, self.envelopes, self.tf
  self._loaded_strategies, self.strat_params, self._safe_ticker
  self._pre_execution_check, self._paper_slippage_fraction (PositionOpenMixin)
  self._close_position (PositionCloseMixin)
"""
import logging
import time
from datetime import datetime, timezone

from app.core.bot_identity import build_slot_key
from app.core.config import DEFAULT_TAKER_FEE
from app.core.database import persist_open_position, session_scope
from app.core.execution import close_pnl, quantize_size, venue_trade_cost
from app.core.indicators import atr_val as _compute_atr
from app.core.timeframes import TF_SECONDS as _TF_SECS
from app.core.trailing import TrailingStopManager

# Helpers partagés (ARCH-003 : centralisés dans position_open_mixin.py)
from app.live.position_open_mixin import (
    _apply_trail_override,
    _calc_unreal_pct,
    _order_fail_reason,
    _order_failed,
)

logger = logging.getLogger(__name__)


class PositionManageMixin:
    """Gestion des positions ouvertes (voir docstring module)."""

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
        # L-01 : le stop/TP se jugent sur le range de la bougie en formation
        # (plus-bas pour un long, plus-haut pour un short). `ticker.last` ne
        # voit qu'un échantillon ponctuel par cycle — une mèche entre deux
        # cycles n'était jamais vue.
        probe = price
        lo_hi = None
        cache = getattr(self, "ohlcv_cache", None)
        if cache is not None and hasattr(cache, "get_forming_range"):
            lo_hi = cache.get_forming_range(symbol, pos_tf)
        if lo_hi is not None:
            lo, hi = lo_hi
            probe = lo if pos["side"] == "long" else hi

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

        # Détection gap (prix franchit le stop de plus que le seuil config)
        _gap = float(self.cfg["trading"].get("gap_threshold", 0.02))
        if pos["side"] == "long" and probe < pos["stop"] * (1.0 - _gap):
            logger.warning(
                f"[Gap] {symbol} prix {price:.4f} < stop {pos['stop']:.4f} "
                f"— clôture forcée"
            )
            self._close_position(pos_id, price, exit_reason="gap")
            return
        if pos["side"] == "short" and probe > pos["stop"] * (1.0 + _gap):
            logger.warning(
                f"[Gap] {symbol} prix {price:.4f} > stop {pos['stop']:.4f} "
                f"— clôture forcée"
            )
            self._close_position(pos_id, price, exit_reason="gap")
            return

        # ── Take-profit fixe (vérifié sur ticker, en complément du SL) ────────
        tp_val = pos.get("take_profit")
        if tp_val is not None:
            tp_probe = (lo_hi[1] if pos["side"] == "long" else lo_hi[0]) \
                if lo_hi is not None else price
            tp_hit = (pos["side"] == "long"  and tp_probe >= tp_val) or \
                     (pos["side"] == "short" and tp_probe <= tp_val)
            if tp_hit:
                logger.info(
                    f"[TP] {symbol} {pos['side']} TP={tp_val:.4f} touché @ {price:.4f}"
                )
                self._close_position(pos_id, tp_val, exit_reason="take_profit")
                return

        # ── L1 (§29) — sorties partielles TP1 / TP2, runner ───────────────────
        # Après le gap et le TP plein, avant l'early-exit : mêmes priorités que
        # `Backtester._manage_open_position`, sinon la parité tombe sur les
        # barres où plusieurs sorties se déclenchent ensemble.
        cibles = pos.get("partial_targets") or []
        if cibles:
            atteintes = [c for c in cibles
                         if (pos["side"] == "long" and price >= c["price"])
                         or (pos["side"] == "short" and price <= c["price"])]
            for cible in atteintes:
                cibles.remove(cible)
                self._partial_close_position(pos_id, pos, cible, price)
            if atteintes:
                if pos_id not in self.open_positions:
                    return                    # soldée par la dernière jambe
                pos = self.open_positions[pos_id]

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
                    self._close_position(pos_id, price, exit_reason="early_exit")
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
                # S12 : le stop qui remonte réduit la perte encourue — ce
                # budget de risque est rendu immédiatement aux autres slots du
                # symbole (§2.1). L'enveloppe, elle, reste consommée.
                self.ledger.update_risk(pos_id, self.risk.engaged_risk(
                    pos["entry"], new_stop, pos["size"]))
                with session_scope(self.SessionLocal) as _sess:
                    persist_open_position(_sess, pos)
                # Replace le stop exchange au nouveau niveau ; si l'ancien stop
                # a déjà été exécuté côté exchange, clôture locale immédiate.
                filled_stop = self._update_exchange_stop(pos)
                if filled_stop is not None:
                    pos["_closed_by_exchange_stop"] = filled_stop
                    self._close_position(pos_id, price, exit_reason=(
                        "stop_loss" if pos.get("disable_trailing") else "trailing_stop"))
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

        if trailing.is_triggered(probe, new_stop, pos["side"]):
            # L-02 : en paper, simuler le stop exchange — fill au niveau
            # (le slippage paper est appliqué dans _close_position).
            fill = new_stop if self.cfg["trading"].get("paper_mode") else price
            self._close_position(pos_id, fill, exit_reason=(
                "stop_loss" if pos.get("disable_trailing") else "trailing_stop"))

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
                if _order_failed(order):
                    raise RuntimeError(f"OCO non posé — {_order_fail_reason(order)}")
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
                if _order_failed(order):
                    raise RuntimeError(f"Stop non posé — {_order_fail_reason(order)}")
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

    def _partial_close_position(self, pos_id: str, pos: dict, cible: dict,
                                price: float) -> None:
        """L1 (§29) — solde une fraction de la position, symétrique de
        ``_scale_in_position``.

        La position reste ouverte tant qu'il reste du runner : seule la clôture
        finale passe par ``_close_position`` et écrit le trade. Si le reliquat
        n'est plus négociable après quantification par la venue, on solde tout
        plutôt que de laisser une poussière impossible à sortir.
        """
        side   = pos["side"]
        symbol = pos["symbol"]
        size0  = float(pos.get("size_initial") or pos["size"])
        venue  = self._venue_for(symbol, pos.get("strategy", ""),
                                 pos.get("timeframe", self.tf))
        part = quantize_size(min(size0 * float(cible["fraction"]), pos["size"]),
                             venue)
        if part <= 0:
            return
        sens_sortie = "sell" if side == "long" else "buy"

        try:
            order = self.exchange.create_order(symbol, "market", sens_sortie, part)
        except Exception:
            logger.exception(f"[PartialTP] {symbol} ordre KO")
            return
        if _order_failed(order):
            logger.error(f"[PartialTP] {symbol} : ordre non exécuté — "
                         f"{_order_fail_reason(order)}")
            return
        exec_price = order.get("price") or order.get("average") or price
        if self.cfg["trading"].get("paper_mode"):
            slip = self._paper_slippage_fraction(
                symbol, pos.get("timeframe", self.tf), part * exec_price)
            exec_price *= (1 - slip) if side == "long" else (1 + slip)

        notional_part = pos.get("notional", 0.0) * (part / pos["size"]) \
            if pos["size"] else 0.0
        heures = max((time.time() - pos["open_time"]) / 3600.0, 0.0)
        pnl, fees, borrow = close_pnl(
            side=side, entry=pos["entry"], exit_price=exec_price, size=part,
            notional=notional_part,
            fee_rate=self.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE),
            daily_rate=self.cfg["trading"].get("borrow_rate_daily", 0.0),
            hours_held=heures,
            periods_per_day=int(self.cfg["trading"].get("borrow_periods_per_day", 24)),
            venue=venue,
        )
        with self._capital_lock:
            if self.cfg["trading"].get("paper_mode") and hasattr(self, "_paper_base"):
                self._paper_base += pnl
            else:
                self.capital_display += pnl

        pos["size"]     = round(pos["size"] - part, 8)
        pos["notional"] = round(pos.get("notional", 0.0) - notional_part, 6)
        pos["fees"]     = round(pos.get("fees", 0.0) + fees, 8)
        pos["borrow_cost"] = round(pos.get("borrow_cost", 0.0) + borrow, 8)
        pos["realized_pnl"] = round(pos.get("realized_pnl", 0.0) + pnl, 6)
        pos.setdefault("exits", []).append({
            "time": datetime.now(timezone.utc).isoformat(),
            "price": round(float(exec_price), 6), "size": round(part, 8),
            "fraction": round(part / size0, 4) if size0 else 0.0,
            "reason": str(cible.get("reason", "tp")), "pnl": round(pnl, 6),
        })

        # §30 — point mort frais compris après la première jambe.
        if pos.get("be_after_partial") and not pos.get("_be_done"):
            cout = 2 * float(self.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE))
            be = pos["entry"] * (1 + cout) if side == "long" \
                else pos["entry"] * (1 - cout)
            if (side == "long" and be > pos["stop"]) or \
                    (side == "short" and be < pos["stop"]):
                pos["stop"] = round(be, 6)
            pos["_be_done"] = True

        logger.info(
            f"[PARTIAL-TP] {side.upper()} {symbol} -{part:.6f} @ {exec_price:.4f} "
            f"({cible.get('reason', 'tp')}) | pnl={pnl:+.2f} "
            f"| reliquat={pos['size']:.6f}"
        )
        self.notif.send(
            f"🎯 *TP partiel* `{symbol}` {side} -{part:.6f} @ `{exec_price:.4f}` "
            f"({cible.get('reason', 'tp')}, pnl {pnl:+.2f})", async_=True)

        # Reliquat non négociable → on solde ; sinon on met à jour les réserves
        # et le stop exchange, qui couvrait encore l'ancienne taille.
        if pos["size"] <= 0 or quantize_size(pos["size"], venue) <= 0:
            self._close_position(pos_id, exec_price, exit_reason="partial_final")
            return
        self.ledger.resize(pos_id,
                           risk=self.risk.engaged_risk(pos["entry"], pos["stop"],
                                                       pos["size"]),
                           notional=pos["notional"])
        with session_scope(self.SessionLocal) as _sess:
            persist_open_position(_sess, pos)
        filled_stop = self._update_exchange_stop(pos)
        if filled_stop is not None:
            pos["_closed_by_exchange_stop"] = filled_stop
            self._close_position(pos_id, exec_price, exit_reason=(
                "stop_loss" if pos.get("disable_trailing") else "trailing_stop"))

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
        if add_stop_dist <= 0:
            logger.debug(f"[ScaleIn] {symbol} refusé (stop_invalide)")
            return
        venue = self._venue_for(symbol, pos.get("strategy", ""),
                                pos.get("timeframe", self.tf))
        slot_key = build_slot_key(pos.get('strategy', ''),
                                  pos.get('timeframe', self.tf),
                                  pos.get('symbol', ''))
        env = self._envelope_for(slot_key, symbol, venue)
        add_size, add_notional = self.risk.compute_size(
            price, add_stop_dist, env, size_factor=sf,
        )
        # G2 : quantification par la venue (lot/unité entière) — mêmes bornes
        # qu'à l'ouverture et qu'au backtest. Le notionnel n'est recalculé que
        # si l'arrondi a effectivement bougé la taille : en crypto, où c'est un
        # no-op, on conserve exactement le notionnel de ``compute_size``.
        q_size = quantize_size(add_size, venue)
        if q_size != add_size:
            add_size, add_notional = q_size, round(q_size * price, 4)
        if add_size <= 0 or add_notional <= 0:
            return

        # L'unité ajoutée se réserve comme une entrée à part entière : c'est le
        # budget de risque du symbole qui borne le pyramidage, plus un compteur
        # `max_pyramiding` qui n'en était qu'un proxy grossier (§2.2).
        add_key = f"{pos_id}#scale{pos.get('scale_ins', 0) + 1}"
        decision = self.ledger.reserve(env, risk=add_size * add_stop_dist,
                                       notional=add_notional, pos_key=add_key)
        if not decision.allowed:
            logger.debug(f"[ScaleIn] {symbol} refusé "
                         f"({decision.reason_code}: {decision.detail})")
            self.rejections.record(decision.reason_code, venue=venue.name,
                                   symbol=symbol, slot_key=slot_key)
            return
        if not self._pre_execution_check(symbol, side, add_size, price, add_notional):
            self.ledger.release(add_key)
            return

        try:
            order = self.exchange.create_order(
                symbol, "market", side, add_size,
                params={"leverage": int(pos.get("leverage", 1))}
            )
        except Exception:
            self.ledger.release(add_key)
            raise
        if _order_failed(order):
            self.ledger.release(add_key)
            raise RuntimeError(
                f"[SCALE-IN] {symbol} : ordre non exécuté — {_order_fail_reason(order)}"
            )
        exec_price = order.get("price") or order.get("average") or price
        if self.cfg["trading"].get("paper_mode"):
            slip = self._paper_slippage_fraction(
                symbol, pos.get("timeframe", self.tf), add_notional)
            exec_price *= (1 + slip) if side == "long" else (1 - slip)

        fee_rate = self.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE)
        add_fees = venue_trade_cost(exec_price, add_size, fee_rate,
                                    side=side, venue=venue, is_entry=True)
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
        # Toute la position se réserve désormais sous `pos_id` : on agrandit
        # d'abord, on libère l'incrément ensuite — l'ordre inverse ouvrirait
        # une fenêtre où un slot concurrent verrait trop de marge.
        self.ledger.resize(pos_id,
                           risk=self.risk.engaged_risk(pos["entry"], pos["stop"], pos["size"]),
                           notional=pos["notional"])
        self.ledger.release(add_key)
        self.risk.consume_rate_token()
        with session_scope(self.SessionLocal) as _sess:
            persist_open_position(_sess, pos)
        # Le stop exchange couvre l'ancienne taille → replacement avec la nouvelle
        filled_stop = self._update_exchange_stop(pos)
        if filled_stop is not None:
            pos["_closed_by_exchange_stop"] = filled_stop
            self._close_position(pos_id, exec_price, exit_reason=(
                "stop_loss" if pos.get("disable_trailing") else "trailing_stop"))
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
