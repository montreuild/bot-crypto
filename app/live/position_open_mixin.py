"""
PositionOpenMixin — ouverture de positions pour LiveTrader.

Extrait de PositionMixin (ARCH-003 : découpage en 4 mixins spécialisés).
Regroupe tout le chemin d'ouverture :
  - _initial_stop_distance   : distance au stop initial (sizing par le risque)
  - _paper_slippage_fraction  : slippage paper trading (FIN-07)
  - _open_position            : ouverture (ordre + trailing stop + persistence)
  - _try_open_from_signal     : chemin unique d'ouverture (gating complet)
  - _scan_symbol_strategy     : scan direct par stratégie (compatibilité tests)

Ce module héberge aussi les **helpers module-level partagés** par les 3 autres
mixins (position_manage/close/restore_mixin.py) et par health_mixin.py :
  - _calc_unreal_pct, _order_failed, _order_fail_reason, _apply_trail_override
  - publish_trade_opened / publish_trade_closed (fallback si events.py absent)

Requiert que l'instance possède (fournis par LiveTrader.__init__) :
  self.exchange, self.cfg, self.risk, self.notif, self.scanner
  self.capital_display, self._capital_lock, self._paper_base
  self.open_positions, self.signal_log, self._positions_lock
  self._trailing_cfg, self._strat_thresholds, self.threshold
  self.ohlcv_cache, self.ledger, self.rejections, self.envelopes, self.tf
  self._pre_execution_check, self._safe_ticker, self._get_ohlcv
"""
import logging
import time
from datetime import datetime, timezone

import polars as pl

from app.core.bot_identity import build_pos_key, build_slot_key, resolve_venue
from app.core.config import DEFAULT_TAKER_FEE
from app.core.database import delete_open_position, persist_open_position, session_scope
from app.core.execution import (
    plan_partial_targets,
    quantize_price,
    quantize_size,
    size_impact_cost,
    venue_trade_cost,
)
from app.core.indicators import atr_val as _compute_atr
from app.core.risk_envelope import resolve_envelope
from app.core.timeframes import HTF_MAP as _HTF_MAP
from app.core.trailing import TrailingStopManager
from app.live.protocols import LiveHost

# WebSocket temps réel — non bloquant, jamais critique pour le trading
try:
    from app.core.events import publish_trade_closed, publish_trade_opened
except Exception:  # pragma: no cover — fallback si events.py indisponible
    def publish_trade_opened(*_a, **_kw): pass
    def publish_trade_closed(*_a, **_kw): pass

logger = logging.getLogger(__name__)


# ── Helpers module-level partagés entre mixins ─────────────────────────────


def _calc_unreal_pct(side: str, entry: float, price: float) -> float:
    """PnL non réalisé en % (protégé division par zéro)."""
    if entry <= 0:
        return 0.0
    return (price - entry) / entry * 100 if side == "long" \
           else (entry - price) / entry * 100


_REJECTED_ORDER_STATUSES = frozenset({"rejected", "canceled", "cancelled", "expired"})


def _order_failed(order: dict | None) -> bool:
    """True si l'ordre exchange n'a manifestement pas été exécuté.

    Un ``None`` ou un statut rejeté/annulé/expiré signifie qu'aucune position
    réelle n'existe côté exchange — poursuivre comme si l'ordre avait réussi
    créerait une position fantôme (trackée par le bot, absente de l'exchange).
    """
    if order is None:
        return True
    status = str(order.get("status") or "").lower()
    if status in _REJECTED_ORDER_STATUSES:
        return True
    filled = float(order.get("filled") or 0)
    if filled > 0:
        return False
    if status in ("closed", "filled"):
        return False
    if status in ("open", "new", "pending", "unfilled"):
        return True
    # LIVE-02 : rien de rempli et un statut qu'on ne reconnaît pas. Le repli
    # valait False — « pas d'échec » — et l'appelant enregistrait alors une
    # position au prix théorique, possiblement inexistante côté exchange. Le
    # bot suivait un fantôme : stop posé sur du vide, capital engagé à tort.
    # On refuse par défaut : ne pas ouvrir une position dont on n'a pas la
    # preuve qu'elle existe. Le statut est journalisé pour compléter les listes
    # ci-dessus au fil des connecteurs rencontrés.
    logger.warning(
        "[Ordre] statut inconnu %r avec filled=0 — traité comme un ÉCHEC "
        "(LIVE-02). Compléter les listes de _order_failed si ce statut est "
        "légitime pour votre connecteur.",
        status or "<vide>",
    )
    return True


def _order_rejected(order: dict | None) -> bool:
    """L'ordre a-t-il été REFUSÉ par l'exchange ? — pour les ordres au repos.

    ``_order_failed`` répond à « mon ordre au marché a-t-il été exécuté ? » :
    un statut ``open``/``pending`` y est un échec, puisqu'un ordre au marché
    doit se remplir immédiatement.

    Un stop ou une jambe OCO, eux, sont posés pour **attendre** : ``open`` est
    leur état nominal et ``filled=0`` la situation normale. Leur appliquer
    ``_order_failed`` revenait à déclarer en échec toute pose de stop réussie
    sur un exchange qui renvoie ``status="open"`` — le bot annulait alors un
    stop bel et bien actif, ou renonçait à le poser.

    Seul un refus explicite compte donc ici.
    """
    if order is None:
        return True
    status = str(order.get("status") or "").lower()
    if status in _REJECTED_ORDER_STATUSES:
        return True
    # Un identifiant est la preuve que l'exchange a bien enregistré l'ordre.
    return not (order.get("id") or order.get("orderId") or order.get("ordId"))


def _order_fail_reason(order: dict | None) -> str:
    if order is None:
        return "create_order a retourné None"
    status = order.get("status") or "inconnu"
    info   = order.get("info") if isinstance(order.get("info"), dict) else {}
    detail = order.get("failReason") or info.get("msg") or info.get("sMsg")
    return f"status={status}" + (f" — {detail}" if detail else "")


def _apply_trail_override(base_cfg: dict, override: dict) -> dict:
    """Fusionne la config trailing globale avec un trail_override signal/position.

    Les clés d'override utilisent les noms du signal (trail_wide, trail_tight…)
    tandis que _trailing_cfg utilise mult / trail_tight_mult.
    """
    if not override:
        return base_cfg
    merged = dict(base_cfg)
    if "trail_wide"  in override:
        merged["mult"]             = float(override["trail_wide"])
    if "trail_tight" in override:
        merged["trail_tight_mult"] = float(override["trail_tight"])
    if "grace_bars"  in override:
        merged["grace_bars"]       = int(override["grace_bars"])
    if "breakeven_r" in override:
        merged["breakeven_r"]      = float(override["breakeven_r"])
    if "lock_r"      in override:
        merged["lock_r"]           = float(override["lock_r"])
    if "tight_r"     in override:
        merged["tight_r"]          = float(override["tight_r"])
    if "lock_ratio"  in override:
        merged["lock_ratio"]       = float(override["lock_ratio"])
    if "use_swing"   in override:
        merged["use_swing"]        = bool(override["use_swing"])
    if "mode"        in override:
        merged["mode"]             = str(override["mode"])
    return merged


class PositionOpenMixin(LiveHost):
    """Ouverture de positions (voir docstring module)."""

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

    # ── Slippage paper trading (FIN-07) ─────────────────────────────────────

    def _paper_slippage_fraction(self, symbol: str, tf: str, notional: float) -> float:
        """Fraction de slippage à appliquer au prix en paper mode.

        ``trading.paper_slippage_model`` (défaut ``"static"``, comportement
        byte-identique à avant FIN-07) : fraction fixe ``paper_slippage``.
        ``"size"`` : ajoute un coût d'impact croissant avec la participation
        du trade au volume moyen 20 barres — même formule que le backtest
        (``backtest.slippage_model: size``, BT-10), via
        ``app.core.execution.size_impact_cost`` partagée. Repli silencieux sur
        la valeur statique si le volume moyen n'est pas encore en cache
        (symbole tout juste scanné) pour ne jamais bloquer une exécution.
        """
        base = float(self.cfg["trading"].get("paper_slippage", 0.001))
        if self.cfg["trading"].get("paper_slippage_model", "static") != "size":
            return base
        avg_qv = (self.ohlcv_cache.get_avg_quote_volume(symbol, tf)
                 if getattr(self, "ohlcv_cache", None) else None)
        if not avg_qv or notional <= 0:
            return base
        k = float(self.cfg["trading"].get("slippage_k", 1.0))
        return base + size_impact_cost(notional, base, k, avg_qv) / notional

    # ── Ouverture ──────────────────────────────────────────────────────────

    # ── Venue (G2) ─────────────────────────────────────────────────────────

    def _venue_for(self, symbol: str, strategy: str | None = None, tf: str | None = None):
        """Venue applicable au trade — crypto par défaut (aucun changement)."""
        return resolve_venue(self.cfg, strategy, tf, symbol)

    # ── Enveloppe du slot (S12) ────────────────────────────────────────────

    def _slot_peers(self, symbol: str) -> list:
        """Slots actifs du même symbole — le dénominateur de la répartition.

        Les poids se répartissent À L'INTÉRIEUR d'un symbole (§2.3) : deux bots
        sur BTC sont des quasi-substituts, pas des risques additionnels."""
        peers = []
        for tf, slots in (self._active_per_tf or {}).items():
            for slot in slots:
                name = slot.get("name")
                if name and slot.get("symbol", symbol) == symbol:
                    peers.append(build_slot_key(name, tf, symbol))
        return peers

    def _slot_is_disabled(self, slot_key: str) -> bool:
        """Slot explicitement désactivé (``risk.disabled_slots``).

        S12 — « inconnu de l'allocateur » n'est plus un motif de refus : les
        slots ne sont plus un registre à tenir à jour, l'enveloppe se dérive
        des slots réellement actifs. Seule une désactivation explicite
        bloque."""
        disabled = (self.cfg.get("risk") or {}).get("disabled_slots") or []
        if slot_key in disabled:
            return True
        # Clé 2-parties héritée (stratégie::tf), tolérée en lecture seule.
        parts = slot_key.split("::")
        return len(parts) == 3 and "::".join(parts[:2]) in disabled

    def _envelope_for(self, slot_key: str, symbol: str, venue):
        """Enveloppe courante du slot.

        Le cache ``self.envelopes`` est reconstruit par le thread de cycle de
        vie (edges mesurées à jour). Le repli résout à la volée en traitant
        toutes les edges comme inconnues — répartition égale entre les slots
        du symbole, ce qui est exactement le régime de démarrage (§2.3)."""
        env = self.envelopes.get(slot_key)
        if env is not None:
            return env
        peers = self._slot_peers(symbol) or [slot_key]
        if slot_key not in peers:
            peers.append(slot_key)
        return resolve_envelope(self.cfg, venue, symbol, slot_key,
                                peers=peers, edges={k: None for k in peers})

    def _execute_order(self, venue, symbol: str, order_type: str, side: str,
                       amount: float, price: float | None = None,
                       params: dict | None = None) -> dict:
        """Transmet l'ordre, ou le simule si la venue n'a pas d'exécution.

        Décision prise **ici**, au plus près du trade, et pas seulement dans
        ``ProviderRouter`` : une venue peut très bien lire ses données via ccxt
        (donc sans routeur) tout en n'ayant aucun courtier branché. Le garde-fou
        doit tenir dans tous les câblages, y compris quand un test ou un script
        injecte directement un exchange.
        """
        if venue is not None and not venue.can_execute:
            logger.warning(
                f"[OPEN] {symbol} — venue '{venue.name}' sans exécution branchée : "
                f"ordre {side} {amount} NON transmis, notification de trade émise."
            )
            return {"id": f"signal_{int(time.time() * 1000)}", "status": "closed",
                    "symbol": symbol, "side": side, "amount": amount,
                    "price": price or 0, "filled": amount,
                    "simulated": True, "venue": venue.name}
        return self.exchange.create_order(symbol, order_type, side, amount,
                                          price, params)

    def _open_position(self, pos_key: str, symbol: str, signal: dict,
                       price: float, size: float, notional: float,
                       atr: float, leverage: float, tf: str) -> None:
        venue            = self._venue_for(symbol, signal.get("name", ""), tf)
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

        # Stop et TP calculés par le bot : les aligner sur la grille de cotation
        # de la venue (no-op en crypto, tick_size=0) — un stop hors grille est
        # rejeté par un carnet actions, et fausse le prix affiché dans la
        # notification de trade que l'utilisateur va saisir à la main.
        stop = quantize_price(stop, venue)
        if take_profit is not None:
            take_profit = quantize_price(take_profit, venue)

        # L-08 : persister AVANT l'ordre. Un crash après fill et avant
        # persist laissait un ordre réel sans position. La reprise relit
        # cette ligne (pending_open) et reprend la gestion.
        with session_scope(self.SessionLocal) as _sess:
            persist_open_position(_sess, {
                "id": pos_key, "symbol": symbol, "side": signal["side"],
                "strategy": signal.get("name", ""), "entry": price,
                "stop": stop, "size": size, "notional": notional,
                "leverage": leverage, "open_time": time.time(),
                "fees": 0.0, "order_id": "", "reason": "pending_open",
            })
        order     = self._execute_order(
            venue, symbol, "market", signal["side"], size,
            params={"leverage": int(leverage)}
        )
        if _order_failed(order):
            try:
                with session_scope(self.SessionLocal) as _sess:
                    delete_open_position(_sess, pos_key)
            except Exception as _rb:
                logger.error(
                    f"[OPEN] {symbol} rollback delete_open_position KO : {_rb}",
                    exc_info=True,
                )
            raise RuntimeError(
                f"[OPEN] {symbol} : ordre non exécuté — {_order_fail_reason(order)}"
            )
        exec_price = order.get("price") or order.get("average") or price
        if not exec_price and not self.cfg["trading"].get("paper_mode", True):
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
        if self.cfg["trading"].get("paper_mode", True):
            slip = self._paper_slippage_fraction(symbol, tf, notional)
            exec_price *= (1 + slip) if signal["side"] == "long" else (1 - slip)
            # LIVE-03 : le paper applique le même partial_fill que le backtest
            # (sinon le paper est plus optimiste, et le chemin d'ajustement
            # de taille n'est jamais exercé avant la première session réelle).
            pf = float((self.cfg.get("backtest") or {}).get("partial_fill_pct") or 1.0)
            if 0.0 < pf < 1.0:
                size = size * pf
            # L-09 : le notionnel suit le prix slippé (sinon le ledger et
            # le sizing restent sur le ticker pré-exécution).
            notional = size * exec_price
        else:
            # Remplissage partiel : aligner la taille trackée sur la taille
            # réellement exécutée (sinon stops/PnL calculés sur une taille fausse).
            try:
                filled = float(order.get("filled") or 0)
                if filled < size * 0.98:
                    logger.warning(
                        f"[OPEN] {symbol} remplissage partiel : "
                        f"{filled:.6f}/{size:.6f} — taille de position ajustée"
                    )
                    size     = filled
                    notional = size * exec_price
            except (TypeError, ValueError):
                pass

        fee_rate = self.cfg["trading"].get("taker_fee", DEFAULT_TAKER_FEE)
        # Coûts d'entrée par venue (G2) : commission % + fixe + plancher +
        # taxe de transaction (TTF à l'achat). Sans venue actions déclarée, le
        # résultat est identique au `trade_fees` proportionnel d'avant.
        fees     = venue_trade_cost(exec_price, size, fee_rate,
                                    side=signal["side"], venue=venue, is_entry=True)
        with self._capital_lock:
            if self.cfg["trading"].get("paper_mode", True) and hasattr(self, "_paper_base"):
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
            "venue":          venue.name,
            "_trailing":      trailing,
            # ── L1 (§29) — sorties partielles, mêmes niveaux qu'en backtest ───
            "size_initial":   size,
            "partial_targets": plan_partial_targets(signal, exec_price, stop),
            "be_after_partial": bool(signal.get("be_after_partial", True)),
            "realized_pnl":   0.0,
            "exits":          [],
            # ── L0 (§99) — contexte de décision, pour le journal ──────────────
            "module":         signal.get("module"),
            "session":        signal.get("session"),
            "htf_bias":       signal.get("htf_bias"),
            "structure_state": signal.get("structure_state"),
            "sequence_type":  signal.get("sequence_type"),
            "sequence_id":    signal.get("sequence_id"),
            "market_event_id": signal.get("market_event_id"),
            "tier":           signal.get("tier"),
            "net_rr":         signal.get("net_rr"),
        }
        with self._positions_lock:
            self.open_positions[pos_key] = pos
        self.risk.register_open(pos)
        with session_scope(self.SessionLocal) as _sess:
            persist_open_position(_sess, pos)

        # Stop-loss côté exchange (filet de sécurité si le bot tombe) —
        # le stop logiciel/trailing reste la référence de gestion. Sans venue
        # d'exécution branchée (G2), il n'y a aucun ordre à protéger : le stop
        # part dans la notification de trade, à saisir avec l'ordre.
        if venue.can_execute and self._exchange_stops_enabled():
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

        logger.info(
            f"[OPEN] {signal['side'].upper()} {symbol} @ {exec_price:.4f} "
            f"| Strat={strat_name}@{tf} | Score={signal['score']:.2f} "
            f"| Size={size:.6f} | Stop={stop:.4f}"
        )
        # G2 : une venue data-only n'a reçu AUCUN ordre — la notification de
        # trade (symbole / sens / ouverture / SL / TP) est le seul chemin vers
        # l'exécution tant que G3 n'est pas livré. On n'envoie donc pas le
        # « position ouverte » habituel, qui laisserait croire à un fill réel.
        if venue.can_execute:
            self.notif.notify_trade_open(pos)
        else:
            self.notif.notify_trade_signal(pos, venue=venue.name, action="open")

        # WebSocket temps réel — publish non bloquant (jamais critique)
        try:
            publish_trade_opened(
                slot_key=build_slot_key(strat_name, tf, symbol),
                symbol=symbol,
                side=signal["side"],
                size=size,
                entry_price=exec_price,
                stop_price=stop,
                strategy=strat_name,
                timeframe=tf,
                score=float(signal.get("score", 0)),
                reason=signal.get("reason", ""),
            )
        except Exception:
            pass

    # ── Ouverture de position (chemin unique) ──────────────────────────────

    def _try_open_from_signal(
        self,
        pos_key: str,
        symbol: str,
        strategy_name: str,
        side: str,
        score: float,
        strat_threshold: float,
        tf: str,
        slot_key: str,
        signal_dict: dict,
        atr: float,
        price: float,
    ) -> bool:
        """
        Chemin unique d'ouverture de position.
        Applique dans l'ordre : venue → global risk → slot enabled → slot CB →
        enveloppe/sizing → réservation → pre_execution_check → ouverture.
        Retourne True si la position a été ouverte, False sinon.

        S12 — un seul point de refus, un seul motif compté (§4.5). Les codes
        sont ceux de ``app.core.rejections.REASONS``, partagés avec le
        backtest : c'est ce qui permet d'expliquer une divergence entre les
        deux plutôt que de la constater.
        """
        # N-04 : même résolution de mode de sortie que le backtest, sinon un
        # paramétrage validé sous `trailing` serait tradé en `as_declared`.
        from app.core.execution import apply_exit_mode
        _bcfg = (self.cfg.get("backtest") or {})
        _mode = str(signal_dict.get("exit_mode") or _bcfg.get("exit_mode", "as_declared"))
        if _mode != "as_declared":
            signal_dict = apply_exit_mode(
                signal_dict, _mode, dict(_bcfg.get("exit_mode_params") or {}))

        now_iso = datetime.now(timezone.utc).isoformat()

        def _reject(tag: str, reason: str) -> bool:
            logger.debug(f"[Trade] {symbol}/{slot_key} rejeté ({tag}: {reason})")
            self.rejections.record(tag, venue=venue.name, symbol=symbol,
                                   slot_key=slot_key)
            self.signal_log.append({
                "time": now_iso, "symbol": symbol, "strategy": strategy_name,
                "side": side, "score": round(score, 3),
                "threshold": round(strat_threshold, 3),
                "timeframe": tf, "status": "rejected",
                "reason": f"{tag}: {reason}",
            })
            return False

        venue = self._venue_for(symbol, strategy_name, tf)

        # 0. Contraintes de la venue (G2) — une action au comptant ne se vend
        # pas à découvert : mieux vaut refuser ici que produire un ticket de
        # trade inexécutable chez le courtier.
        if side == "short" and not venue.allow_short:
            return _reject("venue", f"short interdit sur la venue '{venue.name}'")

        # 1. Risque global
        ok_global, reason_global = self.risk.can_trade(side)
        if not ok_global:
            return _reject("risk", reason_global)

        # 2. Slot explicitement désactivé
        if self._slot_is_disabled(slot_key):
            return _reject("slot_disabled", f"slot '{slot_key}' désactivé")

        # 3. Slot circuit breaker (pause)
        ok_slot, reason_slot = self.risk.can_slot_trade(slot_key)
        if not ok_slot:
            return _reject("slot_cb", reason_slot)

        # 4. Enveloppe du slot — un poids nul signifie « edge non prouvée
        # positive » : le slot ne trade pas tant qu'elle ne l'est pas (§2.3).
        env = self._envelope_for(slot_key, symbol, venue)
        if env.weight <= 0 or env.slot_envelope <= 0:
            return _reject("slot_disabled",
                           "poids nul (edge non prouvée positive) — enveloppe vide")

        # 5. Sizing par la distance au stop réel. Sans stop exploitable, on
        # REFUSE : dimensionner sur l'ATR brut faisait risquer un multiple du
        # risque annoncé (le sur-risque ×2,5 du modèle précédent).
        stop_dist = self._initial_stop_distance(side, price, atr, signal_dict)
        if stop_dist <= 0:
            return _reject("stop_invalide",
                           "distance au stop indéterminable — trade refusé "
                           "plutôt que dimensionné à l'aveugle")
        size, notional = self.risk.compute_size(
            price, stop_dist, env,
            size_factor=float(signal_dict.get("size_factor", 1.0)),
        )
        # Quantification par la venue (G2) : arrondi à la baisse au lot/à
        # l'unité entière quand l'instrument n'est pas fractionnable. No-op en
        # crypto — mais sur actions, une taille de 3,7 titres n'existe pas, et
        # un capital trop faible pour 1 titre doit être refusé ici plutôt que
        # de partir en ordre impossible.
        q_size = quantize_size(size, venue)
        if q_size <= 0:
            return _reject(
                "venue",
                f"taille {size:.6f} < 1 unité négociable sur '{venue.name}' "
                f"(prix {price:.4f}, enveloppe insuffisante pour ce symbole)",
            )
        if q_size != size:
            size, notional = q_size, round(q_size * price, 4)
        leverage = min(max(notional / env.slot_envelope, 1.0),
                       max(env.max_leverage, 1.0))

        # 6. Réservation sous enveloppe et budget de risque. Le risque réservé
        # est celui qui a dimensionné la position (taille × distance au stop) :
        # toute autre expression rouvrirait l'écart de base que S12 supprime.
        decision = self.ledger.reserve(env, risk=size * stop_dist,
                                       notional=notional, pos_key=pos_key)
        if not decision.allowed:
            return _reject(decision.reason_code, decision.detail)

        # 7. Pre-execution check (ordres réels)
        if not self._pre_execution_check(symbol, side, size, price, notional):
            self.ledger.release(pos_key)
            return False

        # 8. Vérification atomique + ouverture (protège contre les races concurrentes)
        with self._positions_lock:
            if pos_key in self.open_positions:
                self.ledger.release(pos_key)
                return False
            # Réserve le slot avant de relâcher le verrou
            self.open_positions[pos_key] = {"_reserved": True}

        try:
            self._open_position(pos_key, symbol, signal_dict, price, size, notional, atr, leverage, tf)
        except Exception:
            with self._positions_lock:
                self.open_positions.pop(pos_key, None)
            self.ledger.release(pos_key)
            try:
                with session_scope(self.SessionLocal) as _sess:
                    delete_open_position(_sess, pos_key)
            except Exception as _rb:
                logger.error(
                    f"[OPEN] {symbol} rollback delete_open_position KO : {_rb}",
                    exc_info=True,
                )
            raise
        # F-11 : le jeton anti-spam n'est consommé qu'après un fill réussi.
        self.risk.consume_rate_token()
        # Le fill réel peut différer de la taille demandée (remplissage partiel,
        # slippage) : réaligner le risque engagé sur la position effective.
        opened = self.open_positions.get(pos_key) or {}
        if opened.get("size") and opened.get("stop") is not None:
            self.ledger.update_risk(pos_key, self.risk.engaged_risk(
                opened["entry"], opened["stop"], opened["size"]))
        return True

    # ── Scan direct par stratégie (conservé pour compatibilité tests) ──────

    def _scan_symbol_strategy(self, symbol: str, df: pl.DataFrame,
                               strategy, params: dict, tf: str) -> None:
        """
        Exécute une stratégie sur un symbole/TF et ouvre une position si le signal
        passe tous les filtres. Conservé pour les appels directs et les tests unitaires ;
        la boucle principale utilise SignalPipeline.collect().
        Applique le même chemin de gating que _cycle() via _try_open_from_signal().
        """
        htf    = _HTF_MAP.get(tf)
        df_htf = self._get_ohlcv(symbol, htf) if htf and htf != tf else None
        try:
            signal = strategy.score(df, params, df_htf=df_htf, symbol=symbol)
        except Exception as e:
            logger.error(f"[Scan] {strategy.name}/{symbol}/{tf} score KO : {e}")
            return

        if signal.get("side") == "none":
            return

        strat_threshold = self._strat_thresholds.get(strategy.name, self.threshold)
        score = signal.get("score", 0)
        if score < strat_threshold:
            self.signal_log.append({
                "time":      datetime.now(timezone.utc).isoformat(),
                "symbol":    symbol, "strategy": strategy.name,
                "side":      signal.get("side", "?"), "score": round(float(score), 3),
                "threshold": round(float(strat_threshold), 3),
                "timeframe": tf, "status": "rejected",
                "reason":    f"score {score:.2f} < threshold {strat_threshold:.2f}",
            })
            return

        ticker = self._safe_ticker(symbol)
        if ticker is None:
            return
        price = ticker.get("last", 0)
        if price <= 0:
            return

        atr = _compute_atr(df)
        # V4-C : la clé 2-parties héritée ne matchait plus aucun slot 3-parties
        # de l'allocateur (budget introuvable sur le chemin scan direct).
        slot_key = build_slot_key(strategy.name, tf, symbol)
        pos_key  = build_pos_key(symbol, strategy.name, tf)
        if pos_key in self.open_positions:
            return

        self._try_open_from_signal(
            pos_key=pos_key,
            symbol=symbol,
            strategy_name=strategy.name,
            side=signal["side"],
            score=float(signal.get("score", 0)),
            strat_threshold=strat_threshold,
            tf=tf,
            slot_key=slot_key,
            signal_dict=signal,
            atr=atr,
            price=price,
        )
