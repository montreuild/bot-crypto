"""
HealthMixin — santé du process, résilience et reporting de LiveTrader.

Extrait de LiveTrader (V4-J / ARCH-06). Regroupe :
  - heartbeat + dead-man switch (watchdog séparé, Phase 3)
  - reprise après coupure réseau et purge mémoire périodique
  - rapport de statut périodique (notifications)
  - propriété ``status`` (API) + agrégats DB + identités des bots

Requiert que l'instance possède (fournis par LiveTrader.__init__) :
  self.cfg, self.risk, self.notif, self.scanner, self.ohlcv_cache,
  self.allocator, self.open_positions, self.signal_log, self.SessionLocal,
  self.capital_display, self.running, self.cycle_count, self._cooldown,
  self._loss_notified, self._status_db_cache*, self._active_per_tf,
  self._lifecycle_snapshot, self._shadow_alloc, self._margin_*
"""
import logging
import time
from collections import Counter

from app.core.timeframes import bars_per_year as _bars_per_year
from app.live.position_open_mixin import _calc_unreal_pct
from app.live.utils import _safe_float, _sanitize

logger = logging.getLogger(__name__)


class HealthMixin:
    """Santé, résilience et reporting (voir docstring module)."""

    # ── Watchdog dead-man (Phase 3) ────────────────────────────────────────
    def _heartbeat(self) -> None:
        """Écrit le battement de cœur lu par le watchdog séparé."""
        try:
            from app.live.watchdog import write_heartbeat
            write_heartbeat({
                "running": self.running,
                "cycle": self.cycle_count,
                "equity": round(self.capital_display, 2),
                "halted": self.risk.halted,
                "open_positions": len([p for p in self.open_positions.values()
                                       if not p.get("_reserved")]),
            })
        except Exception as e:
            logger.debug(f"[Heartbeat] KO : {e}")

    def _check_dead_man(self) -> None:
        """Si le watchdog a armé le kill-switch fichier → HALT immédiat."""
        try:
            from app.live.watchdog import kill_switch_armed, kill_switch_reason
            if kill_switch_armed() and not self.risk._kill_switch_tripped:
                self.risk.trip_kill_switch(
                    f"watchdog dead-man : {kill_switch_reason() or 'kill-switch fichier'}"
                )
        except Exception as e:
            logger.debug(f"[DeadMan] KO : {e}")

    def _recover_after_gap(self, gap_secs: float) -> None:
        """Gère la reprise après une coupure réseau : vide les caches et clôture
        les positions dont le stop a été franchi pendant la coupure."""
        gap_min = gap_secs / 60
        self.notif.send(
            f"⚠️ *Reprise après coupure réseau*\n"
            f"Durée estimée : `{gap_min:.0f} min`\n"
            f"Révision des `{len(self.open_positions)}` position(s)…",
            async_=True
        )
        self.ohlcv_cache.clear()
        self._status_db_cache = None

        positions_to_close = []
        for pos_id, pos in list(self.open_positions.items()):
            ticker = self._safe_ticker(pos["symbol"])
            if ticker is None:
                continue
            price = ticker.get("last", pos["entry"])
            side  = pos["side"]
            if ((side == "long"  and price <= pos["stop"])
                    or (side == "short" and price >= pos["stop"])):
                positions_to_close.append((pos_id, price))

        for pos_id, price in positions_to_close:
            self._close_position(pos_id, price, exit_reason="stop_loss")

        if positions_to_close:
            self.notif.send(
                f"✅ *Reprise réseau terminée*\n"
                f"{len(positions_to_close)} position(s) clôturée(s).",
                async_=True
            )

    def _purge_memory(self) -> None:
        """
        Nettoyage périodique de la mémoire (tous les _purge_every_n cycles).
        Délègue la purge OHLCV/ATR à OHLCVCache ; gère cooldowns et jobs ici.
        """
        try:
            active_symbols = set(self.scanner.get_symbols())
        except Exception:
            active_symbols = set()

        # Purge cache OHLCV/ATR/erreurs exchange
        self.ohlcv_cache.purge(active_symbols)

        now = time.time()
        # Purge cooldowns expirés
        self._cooldown = {s: t for s, t in self._cooldown.items() if t > now}
        # Purge loss_notified pour les positions clôturées
        self._loss_notified &= set(self.open_positions.keys())
        # Purge jobs d'optimisation terminés depuis > 24h
        try:
            from app.engine.auto_optimizer import _jobs, _jobs_lock
            cutoff = now - 86400
            with _jobs_lock:
                stale = [
                    jid for jid, job in _jobs.items()
                    if job.get("status") in ("done", "error")
                    and job.get("finished_at", now) < cutoff
                ]
                for jid in stale:
                    del _jobs[jid]
        except Exception as e:
            logger.debug(f"[LiveTrader] nettoyage jobs auto-opt : {e}")

    # ── Rapport de statut ──────────────────────────────────────────────────

    def _send_status_report(self) -> None:
        positions_detail = []
        for pos in self.open_positions.values():
            if pos.get("_reserved"):
                continue
            ticker = self._safe_ticker(pos["symbol"])
            if ticker:
                price = ticker.get("last", pos["entry"])
                upnl  = _calc_unreal_pct(pos["side"], pos["entry"], price)
                positions_detail.append({
                    "symbol":         pos["symbol"],
                    "side":           pos["side"],
                    "strategy":       pos.get("strategy", "?"),
                    "timeframe":      pos.get("timeframe", "?"),
                    "unrealized_pct": round(upnl, 2),
                })
        status = self.risk.status_dict()
        status["positions_detail"] = positions_detail
        self.notif.notify_status(status)

    def get_bot_identities(self) -> list:
        """Identité (lecture seule) de chaque bot actif — pour l'API/UI.

        Mise en cache (le ``status`` est sollicité ~1×/s) : on ne recalcule —
        et on ne relit ``data/bot_generations.json`` — qu'à l'invalidation
        (changement du set actif / application d'une optimisation).
        """
        if getattr(self, "_bots_cache", None) is not None:
            return self._bots_cache
        from app.core.bot_identity import _load_generations, peek_identity
        gens = _load_generations()   # une seule lecture disque pour tous les bots
        out = []
        for tf, slots in self._active_per_tf.items():
            for slot in slots:
                name = slot.get("name")
                if not name:
                    continue
                params = slot.get("params", {}).get(name, {})
                try:
                    out.append(peek_identity(name, tf, params, self.cfg, gens=gens,
                                             symbol=slot.get("symbol", "")).to_dict())
                except Exception:
                    continue
        self._bots_cache = out
        return out

    # ── Propriété status (API) ─────────────────────────────────────────────

    @property
    def status(self) -> dict:
        risk      = self.risk.status_dict()
        positions = [self._serialize_position(p) for p in self.open_positions.values()
                     if not p.get("_reserved")]

        now = time.time()
        if (self._status_db_cache is None
                or now - self._status_db_cache_ts > self._status_db_cache_ttl):
            self._status_db_cache    = self._load_db_stats()
            self._status_db_cache_ts = now
        db = self._status_db_cache

        return _sanitize({
            "running":              self.running,
            "cycle":                self.cycle_count,
            "capital":              round(self.capital_display, 4),
            "positions":            positions,
            "active_per_tf":        {tf: [s["name"] for s in v]
                                     for tf, v in self._active_per_tf.items()},
            "total_pnl":            db["total_pnl"],
            "total_pnl_pct":        round(db["total_pnl"] / self.capital_display * 100, 2)
                                    if self.capital_display > 0 else 0.0,
            "total_trades":         db["total_trades"],
            "total_fees":           round(
                db["total_fees"]
                + sum(p.get("fees", 0) for p in self.open_positions.values()), 4
            ),
            "win_rate":             db["win_rate"],
            "profit_factor":        db["profit_factor"],
            "best_trade":           db["best_trade"],
            "by_strategy":          db["by_strategy"],
            "last_scan_time":       self.last_scan_time.isoformat()
                                    if self.last_scan_time else None,
            "last_symbols_scanned": self.last_symbols_scanned,
            **risk,
            "circuit_breaker_active": risk.get("halted", False),
            "circuit_breaker_reason": risk.get("halt_reason", ""),
            "signal_log":           list(reversed(list(self.signal_log)))[:50],
            "margin_enabled":       self._margin_enabled,
            "margin_level":         self._margin_level,
            "margin_interest":      round(self._margin_interest, 4),
            "margin_mode":          self.cfg["trading"].get("margin_mode", "isolated")
                                    if self._margin_enabled else None,
            "balance_detail":       self._balance_detail,
            "paper_mode":           self.cfg["trading"].get("paper_mode", True),
            "capital_allocation":   self.allocator.get_status(),
            "circuit_breakers":     self.risk.get_circuit_breakers_status(),
            "slot_states":          self.risk.get_slot_states(),
            "volatility_brake":     self.risk.volatility_brake_active,
            # Phase 1/2 — identité des bots, cycle de vie, allocation shadow.
            "bots":                 self.get_bot_identities(),
            "lifecycle":            self._lifecycle_snapshot,
            "shadow_allocation":    self._shadow_alloc,
        })

    def _load_db_stats(self) -> dict:
        """Agrège les statistiques de trading depuis la table Trade.

        S4-06 : les compteurs GLOBAUX (total_pnl/fees/trades, win_rate,
        profit_factor, best_trade) sont calculés par une requête SQL agrégée
        (COUNT/SUM/MAX) au lieu d'une boucle Python sur jusqu'à 10 000 objets
        ORM. Le détail PAR STRATÉGIE reste calculé en Python : le Sharpe a
        besoin de la séquence ORDONNÉE des PnL (courbe d'équité synthétique,
        S4-01), non exprimable en agrégats SQL simples — même fetch qu'avant.
        """
        total_pnl = total_fees = best_trade = gross_win = gross_loss = 0.0
        total_trades = wins = 0
        by_strategy: dict = {}
        try:
            from app.core.database import get_trade_global_aggregates as _agg
            from app.core.database import get_trades as _gt
            from app.core.database import session_scope
            with session_scope(self.SessionLocal) as _sess:
                agg = _agg(_sess)
                total_trades = agg["total_trades"]
                total_pnl    = agg["total_pnl"]
                total_fees   = agg["total_fees"]
                best_trade   = agg["best_trade"]
                wins         = agg["wins"]
                gross_win    = agg["gross_win"]
                gross_loss   = agg["gross_loss"]

                # get_trades() ordonne du plus récent au plus ancien ; on
                # reverse pour itérer chronologiquement — indispensable pour
                # la courbe d'équité synthétique par stratégie (Sharpe, S4-01).
                for t in reversed(_gt(_sess, limit=10000)):
                    p   = float(t.pnl or 0)
                    fee = float(t.fees or 0)
                    sname = t.strategy or "unknown"
                    if sname not in by_strategy:
                        by_strategy[sname] = {
                            "trades": 0, "wins": 0,
                            "pnl": 0.0, "fees": 0.0, "pnls": [], "timeframes": [],
                        }
                    by_strategy[sname]["trades"] += 1
                    by_strategy[sname]["pnl"]    += p
                    by_strategy[sname]["fees"]   += fee
                    by_strategy[sname]["pnls"].append(p)
                    by_strategy[sname]["timeframes"].append(t.timeframe or "1h")
                    if p > 0:
                        by_strategy[sname]["wins"] += 1
        except Exception as e:
            logger.debug(f"[LiveTrader] agrégation trades : {e}")

        win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0
        pf = (round(gross_win / gross_loss, 3) if gross_loss > 0
              else (999.0 if gross_win > 0 else 0.0))

        import numpy as _np
        initial_capital = float(getattr(self.risk, "initial_capital", 0.0) or 0.0)
        for sname, d in by_strategy.items():
            n    = d["trades"]
            pnls = d.pop("pnls", [])
            tfs  = d.pop("timeframes", [])
            gw   = sum(p for p in pnls if p > 0)
            gl   = abs(sum(p for p in pnls if p < 0))
            d["win_rate"]      = round(d["wins"] / n * 100, 1) if n > 0 else 0.0
            d["total_pnl"]     = round(d["pnl"], 4)
            d["total_fees"]    = round(d["fees"], 4)
            d["total_trades"]  = n
            d["profit_factor"] = round(gw / gl, 3) if gl > 0 else (999.0 if gw > 0 else 0.0)
            # S4-01 : Sharpe aligné sur BacktestResult._compute_metrics()
            # (engine/backtest.py) — courbe d'équité synthétique PAR TRADE
            # (pas les PnL bruts en $), retours relatifs au capital avant
            # chaque trade, annualisation par bars_per_year() du TF dominant
            # de la stratégie (pas un sqrt(252) fixe indépendant du TF réel).
            # Les deux Sharpe (live/backtest) redeviennent comparables.
            if len(pnls) >= 3 and initial_capital > 0:
                eq = [initial_capital]
                cap = initial_capital
                for p in pnls:
                    cap += p
                    eq.append(cap)
                eq_arr = _np.array(eq, dtype=float)
                denom  = _np.where(eq_arr[:-1] > 0, eq_arr[:-1], 1.0)
                rets   = _np.diff(eq_arr) / denom
                std    = float(rets.std())
                if std > 0:
                    dominant_tf = Counter(tfs).most_common(1)[0][0] if tfs else "1h"
                    ann = float(_np.sqrt(_bars_per_year(dominant_tf)))
                    raw = float(rets.mean() / std * ann)
                    d["sharpe"] = round(_safe_float(raw, 0.0), 3)
                else:
                    d["sharpe"] = 0.0
            else:
                d["sharpe"] = 0.0
            if len(pnls) >= 2:
                eq   = _np.cumsum(pnls)
                peak = _np.maximum.accumulate(eq)
                raw  = float(_np.min((eq - peak) / (peak + 1e-9) * 100))
                d["max_drawdown"] = round(_safe_float(raw, 0.0), 2)
            else:
                d["max_drawdown"] = 0.0

        return _sanitize({
            "total_pnl":     round(total_pnl, 4),
            "total_trades":  total_trades,
            "total_fees":    round(total_fees, 4),
            "win_rate":      win_rate,
            "profit_factor": pf,
            "best_trade":    round(best_trade, 4),
            "by_strategy":   by_strategy,
        })
