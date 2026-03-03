"""
Module 7 — Live Trading sécurisé :
  - Circuit breaker intégré
  - Simulation pré-exécution (dry run)
  - Scanner multi-paires
  - Intégration ML optionnelle
  - Logs complets des décisions
"""
import importlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from app.core.exchange       import RobustExchange
from app.core.risk           import RiskManager
from app.core.database       import init_db, save_trade, update_daily_stats
from app.core.notifications  import Notifier
from app.core.trailing       import TrailingStopManager
from app.engine.engine       import Engine
from app.scanner.scanner     import MarketScanner

logger = logging.getLogger(__name__)


class LiveTrader:
    def __init__(self, cfg: dict, exchange: RobustExchange):
        self.cfg      = cfg
        self.exchange = exchange
        self.risk     = RiskManager(cfg)
        self.notif    = Notifier(cfg)
        self.trailing = TrailingStopManager(
            mult=cfg.get("strategy_params",{}).get("trend",{}).get("atr_mult",1.5))
        self.scanner  = MarketScanner(exchange, cfg)
        t             = cfg["trading"]
        self.tf       = t["timeframe"]
        self.interval = t["scan_interval"]
        self.threshold= t["score_threshold"]
        self.strat_params = cfg.get("strategy_params",{})

        # DB
        _, self.SessionLocal = init_db(cfg["database"]["url"])

        # Stratégies
        self.engine = Engine()
        for name in cfg["strategies"]["enabled"]:
            try:
                mod = importlib.import_module(f"app.strategies.{name}")
                self.engine.register(mod.Strategy())
                logger.info(f"[LiveTrader] Stratégie chargée : {name}")
            except Exception as e:
                logger.error(f"[LiveTrader] Impossible de charger {name} : {e}")

        # ML (optionnel)
        self.ml = None
        if cfg.get("ml",{}).get("enabled"):
            try:
                from app.ml.model import MLPredictor
                self.ml = MLPredictor(cfg)
                if not self.ml.load():
                    logger.info("[LiveTrader] Modèle ML non trouvé — sera entraîné au premier cycle.")
            except Exception as e:
                logger.warning(f"[LiveTrader] ML non chargé : {e}")

        # State
        self.open_positions: Dict[str, dict] = {}
        self.running       = False
        self.cycle_count   = 0
        self._ml_retrain_at= 0
        self.capital_display = cfg["trading"]["capital"]

        logger.info(f"[LiveTrader] Démarré — Paper={cfg['trading']['paper_mode']} | TF={self.tf}")

    def start(self):
        """Boucle principale."""
        self.running = True
        self.notif.notify_start(self.cfg)
        logger.info("=" * 60)
        logger.info("  BOT DÉMARRÉ")
        logger.info("=" * 60)
        try:
            while self.running:
                self._cycle()
                self._maybe_retrain_ml()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            logger.info("[LiveTrader] Arrêt demandé (Ctrl+C)")
        except Exception as e:
            logger.exception(f"[LiveTrader] Erreur critique : {e}")
            self.notif.send(f"🚨 Erreur critique : {e}", async_=False)
        finally:
            self.running = False
            logger.info("[LiveTrader] Boucle terminée.")

    def stop(self):
        self.running = False

    def _cycle(self):
        self.cycle_count += 1
        logger.info(f"[Cycle {self.cycle_count}] Scan — {len(self.open_positions)} position(s) ouvertes")

        if self.risk.halted:
            logger.warning(f"[Cycle] HALTED — {self.risk.halt_reason}")
            return

        # Mise à jour equity
        self.risk.update_equity(self.capital_display)
        if self.risk.halted:
            self.notif.notify_halt(self.risk.halt_reason)
            return

        # Gestion des positions ouvertes
        for pos_id in list(self.open_positions.keys()):
            self._manage_position(pos_id)

        # Scan et nouvelles entrées
        symbols = self.scanner.get_symbols()
        for symbol in symbols:
            if symbol in self.open_positions:
                continue
            self._scan_symbol(symbol)

        # Rapport périodique (toutes les 10 cycles)
        if self.cycle_count % 10 == 0:
            self.notif.notify_status(self.risk.status_dict())

    def _scan_symbol(self, symbol: str):
        df = self.scanner.fetch_ohlcv(symbol, self.tf, limit=200)
        if df is None or len(df) < 60:
            return

        regime = self.scanner.detect_regime(df)
        signal = self.engine.best_signal(df, self.strat_params)

        if signal["side"] == "none" or signal["score"] < self.threshold:
            return

        # Blend ML si activé
        if self.ml and self.ml.trained:
            score, side = self.ml.blend_signal(signal["score"], signal["side"], df, regime)
            if score < self.threshold:
                logger.debug(f"[Scan] {symbol} : signal atténué par ML ({signal['score']:.2f} → {score:.2f})")
                return
            signal["score"] = score

        can, reason = self.risk.can_trade(signal["side"])
        if not can:
            logger.debug(f"[Scan] {symbol} : rejeté ({reason})")
            return

        # Simuler l'ordre avant envoi (dry run check)
        ticker = self._safe_ticker(symbol)
        if ticker is None:
            return
        price  = ticker.get("last", 0)
        if price <= 0:
            return

        from app.scanner.scanner import _compute_atr
        atr              = _compute_atr(df)
        size, notional   = self.risk.compute_size(price, atr)
        leverage         = self.risk.compute_leverage(notional)

        if not self._pre_execution_check(symbol, signal["side"], size, price, notional):
            return

        self._open_position(symbol, signal, price, size, notional, atr, leverage)

    def _open_position(self, symbol: str, signal: dict, price: float,
                       size: float, notional: float, atr: float, leverage: float):
        stop  = self.trailing.initial_stop(price, atr, signal["side"])
        order = self.exchange.create_order(
            symbol, "market", signal["side"], size, params={"leverage": int(leverage)}
        )
        exec_price = order.get("price") or price
        fee_rate   = self.cfg["trading"].get("taker_fee", 0.001)
        fees       = exec_price * size * fee_rate
        self.capital_display -= fees

        pos = {
            "id":          f"{symbol}_{int(time.time())}",
            "symbol":      symbol,
            "side":        signal["side"],
            "strategy":    signal.get("name",""),
            "score":       signal.get("score", 0),
            "entry":       exec_price,
            "stop":        stop,
            "size":        size,
            "notional":    notional,
            "leverage":    leverage,
            "open_time":   time.time(),
            "fees":        fees,
            "pnl":         0.0,
            "reason":      signal.get("reason",""),
            "order_id":    order.get("id",""),
        }
        self.open_positions[pos["id"]] = pos
        self.risk.register_open(pos)
        logger.info(f"[OPEN] {signal['side'].upper()} {symbol} @ {exec_price:.4f} "
                    f"| Score={signal['score']:.2f} | Size={size:.6f} | Stop={stop:.4f}")

    def _manage_position(self, pos_id: str):
        pos    = self.open_positions.get(pos_id)
        if not pos: return
        symbol = pos["symbol"]
        ticker = self._safe_ticker(symbol)
        if ticker is None: return

        price     = ticker.get("last", pos["entry"])
        df        = self.scanner.fetch_ohlcv(symbol, self.tf, limit=100)
        if df is None: return

        from app.scanner.scanner import _compute_atr
        atr      = _compute_atr(df)
        new_stop = self.trailing.update_stop(price, pos["stop"], atr, pos["side"])
        pos["stop"] = new_stop

        if self.trailing.is_triggered(price, new_stop, pos["side"]):
            self._close_position(pos_id, price)

    def _close_position(self, pos_id: str, exit_price: float):
        pos  = self.open_positions.pop(pos_id, None)
        if not pos: return

        close_side  = "sell" if pos["side"] == "long" else "buy"
        order       = self.exchange.create_order(pos["symbol"], "market", close_side, pos["size"])
        exec_price  = order.get("price") or exit_price
        fee_rate    = self.cfg["trading"].get("taker_fee", 0.001)
        fees        = exec_price * pos["size"] * fee_rate
        days_held   = (time.time() - pos["open_time"]) / 86400
        borrow_cost = pos["notional"] * self.cfg["trading"].get("borrow_rate_daily",0.0002) * days_held

        if pos["side"] == "long":
            gross = (exec_price - pos["entry"]) * pos["size"]
        else:
            gross = (pos["entry"] - exec_price) * pos["size"]
        pnl = gross - fees - borrow_cost
        self.capital_display += pnl
        self.risk.update_equity(self.capital_display)
        self.risk.register_close(pos_id)

        trade = {**pos, "exit": exec_price, "pnl": round(pnl,6), "fees": round(fees,6),
                 "borrow_cost": round(borrow_cost,6), "status": "closed"}
        session = self.SessionLocal()
        try:
            save_trade(session, trade)
            update_daily_stats(session, datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                               pnl, pnl>0, fees, self.capital_display)
        finally:
            session.close()

        self.notif.notify_trade(trade)
        logger.info(f"[CLOSE] {pos['side'].upper()} {pos['symbol']} @ {exec_price:.4f} "
                    f"| PnL={pnl:+.4f} | Borrow={borrow_cost:.4f}")

    def _pre_execution_check(self, symbol: str, side: str, size: float,
                              price: float, notional: float) -> bool:
        """Simulation pré-exécution pour vérifier marge/liquidité."""
        if self.cfg["trading"].get("paper_mode"):
            return True  # Paper = toujours OK
        if self.capital_display < notional * 0.05:
            logger.warning(f"[PreCheck] {symbol} : capital insuffisant pour la marge requise")
            return False
        if notional > self.capital_display * 0.25:
            logger.warning(f"[PreCheck] {symbol} : notionnel ({notional:.2f}) > 25% du capital")
            return False
        return True

    def _safe_ticker(self, symbol: str) -> Optional[dict]:
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.warning(f"[LiveTrader] fetch_ticker {symbol} : {e}")
            return None

    def _maybe_retrain_ml(self):
        if not self.ml:
            return
        interval = self.cfg.get("ml",{}).get("retrain_interval", 3600)
        if time.time() < self._ml_retrain_at:
            return
        self._ml_retrain_at = time.time() + interval
        logger.info("[ML] Déclenchement réentraînement…")
        try:
            symbols = self.scanner.get_symbols()[:3]
            dfs = [self.scanner.fetch_ohlcv(s, self.tf, 1000) for s in symbols]
            dfs = [d for d in dfs if d is not None and len(d) >= self.cfg["ml"]["min_samples"]]
            if dfs:
                df_combined = pd.concat(dfs, ignore_index=True)
                self.ml.train(df_combined)
                self.ml.save()
        except Exception as e:
            logger.error(f"[ML] Réentraînement KO : {e}")

    @property
    def status(self) -> dict:
        return {
            "running":        self.running,
            "cycle":          self.cycle_count,
            "capital":        round(self.capital_display, 4),
            "open_positions": list(self.open_positions.values()),
            **self.risk.status_dict(),
        }
