"""
LiveTrader V11 — Multi-Timeframe + CandleStore

Nouveautés V11 :
  - Toutes les données OHLCV passent par CandleStore (Parquet persistant)
  - Fetch incrémental : seules les nouvelles bougies sont récupérées à chaque cycle
  - Historique accumulé : backtest, optimizer, paper mode, retrain ML bénéficient
    des données stockées localement sans re-fetcher depuis l'exchange

Structure :
  live/position_mixin.py  — cycle de vie des positions (_open/_manage/_close/_serialize)
  live/live_trader.py     — boucle principale, cache OHLCV, scan, ML, auto-opt, status
"""
import importlib
import logging
import math
import time
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


def _safe_float(v, fallback=None):
    """Convertit nan/inf en fallback pour garantir la sérialisation JSON."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return fallback
        return f
    except (TypeError, ValueError):
        return fallback


def _sanitize(obj):
    """Parcourt récursivement un dict/list et remplace les float nan/inf par None."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        return _safe_float(obj)
    return obj

import polars as pl

from app.core.exchange           import RobustExchange
from app.core.risk               import RiskManager
from app.core.database           import init_db, load_open_positions
from app.core.notifications      import Notifier
from app.core.trailing           import TrailingStopManager
from app.engine.engine           import Engine
from app.engine.scanner          import MarketScanner
from app.engine.optimizer        import get_active_strategies_per_tf, RECOMMENDED_LIMIT
from app.core.indicators         import precompute_df, atr_val as _compute_atr
from app.live.position_mixin     import PositionMixin, _calc_unreal_pct
from app.live.capital_allocator  import CapitalAllocator
from app.live.signal_pipeline    import SignalPipeline

logger = logging.getLogger(__name__)

# TTL cache OHLCV par TF (secondes)
_OHLCV_TTL = {
    "1m": 30, "5m": 60, "15m": 180, "30m": 360,
    "1h": 600, "4h": 2400, "1d": 14400,
}

# Timeframe supérieur (HTF) pour chaque TF — utilisé pour le filtre de tendance
_HTF_MAP = {
    "1m": "15m", "3m": "30m", "5m": "1h", "15m": "4h",
    "30m": "4h", "1h": "4h",  "2h": "1d", "4h": "1d", "1d": "1d",
}


def _merge_params(base: dict, optimized: dict) -> dict:
    """
    Fusionne les params de config (base) avec les params optimisés.

    - base     : cfg["strategy_params"] complet — ex. {"trend": {...}, "supertrend_macd": {...}}
    - optimized: params d'un entry _active_per_tf — ex. {"trend": {"adx_min": 25, ...}}

    Résultat : dict complet avec tous les stratégies, params optimisés écrasant le base.
    Les valeurs NaN/None sont remplacées par les valeurs base ou les défauts.
    """
    import math
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for strat_key, strat_params in optimized.items():
        if not isinstance(strat_params, dict):
            continue
        base_for_strat = dict(merged.get(strat_key, {}))
        for k, v in strat_params.items():
            # Ignorer les valeurs invalides (NaN, None) — garder la valeur base
            if v is None:
                continue
            try:
                if isinstance(v, float) and math.isnan(v):
                    continue
            except (TypeError, ValueError):
                pass
            base_for_strat[k] = v
        merged[strat_key] = base_for_strat
    return merged


class LiveTrader(PositionMixin):
    def __init__(self, cfg: dict, exchange: RobustExchange):
        self.cfg      = cfg
        self.exchange = exchange
        self.risk     = RiskManager(cfg)
        self.notif    = Notifier(cfg)
        self.risk.attach_notifier(self.notif)

        self._trailing_cfg = {
            "mult":             float(cfg.get("backtest",{}).get("trail_wide",     2.5)),
            "grace_bars":       int(cfg.get("backtest",{}).get("grace_bars",       4)),
            "breakeven_r":      float(cfg.get("backtest",{}).get("breakeven_r",    1.2)),
            "trail_tight_mult": float(cfg.get("backtest",{}).get("trail_tight",    1.0)),
            "lock_r":           float(cfg.get("backtest",{}).get("lock_r",         2.5)),
            "tight_r":          float(cfg.get("backtest",{}).get("tight_r",        4.0)),
            "lock_ratio":       float(cfg.get("backtest",{}).get("lock_ratio",     0.60)),
            "use_swing":        bool(cfg.get("backtest",{}).get("use_swing",       True)),
        }

        self.scanner  = MarketScanner(exchange, cfg)
        t             = cfg["trading"]
        # Multi-TF : liste de timeframes actifs
        self.timeframes  = t.get("timeframes") or [t.get("timeframe", "1h")]
        self.tf          = self.timeframes[0]  # TF principal (compat backtest/status)
        self.interval    = t["scan_interval"]
        self.threshold   = t["score_threshold"]
        self.strat_params = cfg.get("strategy_params", {})

        _, self.SessionLocal = init_db(cfg["database"]["url"])

        # Engine : charge toutes les stratégies connues
        self.engine = Engine()
        self._loaded_strategies: Dict[str, object] = {}
        self._load_all_strategies()

        # Chargement des modèles ML persistés — réentraînement géré par _auto_opt_thread
        from app.ml.trainer import MLStrategyTrainer
        self._ml_trainer = MLStrategyTrainer(cfg)
        self._ml_trainer.load_models(self._loaded_strategies, self.timeframes)

        # Stratégies actives par TF (depuis optimizer_results)
        self._active_per_tf: Dict[str, List[dict]] = {}
        self._build_active_per_tf()

        # Threshold par stratégie
        self._strat_thresholds: Dict[str, float] = {}
        for name in self._loaded_strategies:
            sp = self.strat_params.get(name, {})
            self._strat_thresholds[name] = float(sp.get("score_threshold", self.threshold))

        # ML optionnel
        self.ml = None
        if cfg.get("ml", {}).get("enabled"):
            try:
                from app.ml.model import MLPredictor
                self.ml = MLPredictor(cfg)
                if not self.ml.load():
                    logger.info("[LiveTrader] Modèle ML non trouvé — sera entraîné au 1er cycle.")
            except Exception as e:
                logger.warning(f"[LiveTrader] ML non chargé : {e}")

        # ── State ──────────────────────────────────────────────────────────
        self.open_positions: Dict[str, dict] = {}
        self._capital_lock    = threading.Lock()
        self.running          = False
        self.cycle_count      = 0
        self._ml_retrain_at        = 0
        self.capital_display  = cfg["trading"]["capital"]
        self._paper_base      = self._restore_paper_base(cfg["trading"]["capital"])  # settled equity paper
        self.last_scan_time   = None
        self.last_symbols_scanned = []

        self._margin_enabled  = cfg["exchange"].get("margin", False) or \
                                 cfg["trading"].get("margin_mode") is not None
        self._margin_level    = None
        self._margin_interest = 0.0
        self._margin_next_sync= 0

        from collections import deque
        self.signal_log: deque = deque(maxlen=100)

        self._auto_opt_enabled  = cfg.get("optimizer", {}).get("enabled", False)
        self._auto_opt_interval = int(cfg.get("optimizer", {}).get("auto_interval_h", 24)) * 3600
        self._auto_opt_next_run = 0

        self._exchange_errors: Dict[str, int] = {}

        # Cache OHLCV multi-TF : (symbol, tf) → (timestamp, df)
        self._ohlcv_cache: Dict[Tuple[str, str], Tuple[float, pl.DataFrame]] = {}

        # Cache ATR par symbole (pour _manage_position)
        self._atr_cache: Dict[str, tuple] = {}
        self._atr_cache_ttl = max(self.interval // 2, 30)

        # "Nouvelle bougie" filter par (symbol, tf)
        self._last_candle_ts: Dict[Tuple[str, str], int] = {}

        # Re-entry cooldown
        self._cooldown: Dict[str, float] = {}

        # Cache status DB
        self._status_db_cache: Optional[dict] = None
        self._status_db_cache_ts: float = 0.0
        self._status_db_cache_ttl: float = 10.0

        self._loss_notified: set = set()
        self._purge_every_n = 200
        self._purge_counter = 0

        # Capital allocator (budgets par slot strategy::tf)
        self.allocator = CapitalAllocator(
            capital=self.capital_display,
            active_per_tf=self._active_per_tf,
        )

        # Signal pipeline (collecte + ranking)
        self.pipeline = SignalPipeline(
            loaded_strategies=self._loaded_strategies,
            cfg=cfg,
            scanner=self.scanner,
        )

        # Timestamp dernier cycle de rééquilibrage budgets
        self._last_day_key: str = ""

        logger.info(
            f"[LiveTrader] Démarré — Paper={cfg['trading']['paper_mode']} "
            f"| TFs configurés={self.timeframes}"
        )

    # ── Chargement des stratégies ─────────────────────────────────────────
    def _load_all_strategies(self):
        """Charge toutes les stratégies disponibles dans PARAM_SPACES + enabled."""
        import re as _re
        from app.engine.optimizer import PARAM_SPACES
        to_load = set(PARAM_SPACES.keys()) | set(self.cfg["strategies"].get("enabled", []))
        for name in to_load:
            # Validation du nom : lettres minuscules, chiffres et underscores uniquement.
            # Protège contre l'injection de modules arbitraires via le fichier de config.
            if not _re.match(r'^[a-z][a-z0-9_]*$', name):
                logger.warning(f"[LiveTrader] Nom de stratégie invalide ignoré : {name!r}")
                continue
            try:
                mod = importlib.import_module(f"app.strategies.{name}")
                strat = mod.Strategy()
                self.engine.register(strat)
                self._loaded_strategies[name] = strat
                logger.info(f"[LiveTrader] Stratégie chargée : {name}")
            except Exception as e:
                logger.warning(f"[LiveTrader] Impossible de charger {name} : {e}")

    def _build_active_per_tf(self):
        """
        Construit self._active_per_tf depuis optimizer_results.
        Format : { "1h": [{"name": "trend", "params": {...}, "score": 0.82}, ...] }
        """
        self._active_per_tf = get_active_strategies_per_tf(self.cfg)
        for tf, strats in self._active_per_tf.items():
            names   = [s["name"] for s in strats]
            has_oos = any(s.get("score", 0) > 0 for s in strats)
            origin  = "optimizer_results" if has_oos else "fallback (stratégies activées manuellement)"
            logger.info(f"[LiveTrader] TF={tf} → {names} [{origin}]")

    def reload_active_strategies(self):
        """Rechargement à chaud après optimisation (appelé par l'API)."""
        self._build_active_per_tf()
        if hasattr(self, "allocator"):
            self.allocator.rebuild_slots(self._active_per_tf)
        if hasattr(self, "pipeline"):
            self.pipeline.update_strategies(self._loaded_strategies)
        logger.info("[LiveTrader] Stratégies actives rechargées depuis optimizer_results")

    # ── Boucle principale ──────────────────────────────────────────────────
    def start(self):
        self.running = True
        self._restore_open_positions()
        self.notif.notify_start(self.cfg)
        logger.info("=" * 60)
        logger.info("  BOT DÉMARRÉ (Multi-TF)")
        logger.info(f"  Timeframes actifs     : {self.timeframes}")
        logger.info(f"  → Pour ajouter des TFs : config.yaml › trading.timeframes")
        logger.info(f"  → Puis lancer une optimisation pour activer les stratégies")
        logger.info("=" * 60)
        _last_successful_cycle = time.time()
        try:
            while self.running:
                try:
                    self._cycle()
                    _last_successful_cycle = time.time()
                except Exception as cycle_err:
                    logger.exception(f"[LiveTrader] Erreur cycle : {cycle_err}")
                    gap_secs = time.time() - _last_successful_cycle
                    if gap_secs > 300:
                        logger.warning(f"[LiveTrader] Coupure réseau ({gap_secs/60:.1f} min) — reprise...")
                        self._recover_after_gap(gap_secs)
                        _last_successful_cycle = time.time()
                self._maybe_retrain_ml()
                self._maybe_auto_optimize()
                self._purge_counter += 1
                if self._purge_counter >= self._purge_every_n:
                    self._purge_counter = 0
                    self._purge_memory()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            logger.info("[LiveTrader] Arrêt demandé (Ctrl+C)")
            self.notif.notify_stop("Ctrl+C")
        except Exception as e:
            logger.exception(f"[LiveTrader] Erreur critique : {e}")
            self.notif.send(f"🚨 Erreur critique : {e}", async_=False)
        finally:
            self.running = False
            logger.info("[LiveTrader] Boucle terminée.")

    def stop(self, close_positions: bool = False):
        self.running = False
        if close_positions and self.open_positions:
            logger.warning(f"[LiveTrader] Arrêt avec clôture de {len(self.open_positions)} position(s)…")
            self.notif.send(
                f"🛑 *Bot arrêté — clôture des positions*\n"
                f"Clôture de `{len(self.open_positions)}` position(s) en cours…",
                async_=False
            )
            for pos_id in list(self.open_positions.keys()):
                try:
                    pos    = self.open_positions.get(pos_id)
                    ticker = self._safe_ticker(pos["symbol"]) if pos else None
                    price  = ticker.get("last", pos["entry"]) if ticker else pos["entry"]
                    self._close_position(pos_id, price)
                except Exception as e:
                    logger.error(f"[Stop] Impossible de clôturer {pos_id} : {e}")
        elif self.open_positions:
            n    = len(self.open_positions)
            syms = ", ".join(p["symbol"] for p in self.open_positions.values())
            logger.info(f"[LiveTrader] Arrêt sans clôture — {n} position(s) conservée(s) : {syms}")
            self.notif.send(
                f"⏸ *Bot arrêté — surveillance suspendue*\n"
                f"`{n}` position(s) conservée(s) sur Binance : `{syms}`\n"
                f"Elles seront reprises au prochain démarrage.",
                async_=False
            )
        self.notif.notify_stop("Arrêt normal")

    def _restore_open_positions(self):
        session = self.SessionLocal()
        try:
            positions = load_open_positions(session)
        finally:
            session.close()
        if not positions:
            return
        n = len(positions)
        logger.warning(f"[Reprise] {n} position(s) trouvée(s) en BDD — restauration...")
        for pos in positions:
            pos_id   = pos["id"]
            trailing = TrailingStopManager(**self._trailing_cfg)
            trailing.init_from_stop(pos["entry"], pos["stop"], pos["side"])
            pos["_trailing"] = trailing
            self.open_positions[pos_id] = pos
            self.risk.register_open(pos)
            logger.info(
                f"  [Reprise] {pos['side'].upper()} {pos['symbol']} "
                f"@ {pos['entry']:.4f} | stop={pos['stop']:.4f} | strat={pos['strategy']}"
            )
        if not self.cfg["trading"].get("paper_mode"):
            self._sync_spot_balance()
        self.notif.send(
            f"🔄 *Reprise après redémarrage*\n"
            f"`{n}` position(s) restaurée(s) depuis la BDD.\n"
            f"⚠️ Vérifiez que les stops sont cohérents avec le marché.",
            async_=False
        )

    # ── Cycle principal ────────────────────────────────────────────────────
    def _cycle(self):
        self.cycle_count += 1
        n_pos = len(self.open_positions)
        logger.info(f"[Cycle {self.cycle_count}] Scan — {n_pos} position(s) ouvertes")

        # 1. Sanity checks globaux
        if self.risk.halted:
            logger.warning(f"[Cycle] HALTED — {self.risk.halt_reason}")
            return

        self.risk.update_equity(self.capital_display)
        self.allocator.update_equity(self.capital_display)

        if self.risk.halted:
            self.notif.notify_halt(
                self.risk.halt_reason,
                equity=self.capital_display,
                dd_pct=self.risk.global_dd_pct * 100
            )
            return

        if self.risk.day_key != self._last_day_key:
            self._last_day_key = self.risk.day_key
            self.notif.reset_dd_warning()

        # 2. Volatility brake (ATR BTC/USDC 1h)
        self._update_volatility_brake()

        # 3. Gérer les positions ouvertes
        for pos_id in list(self.open_positions.keys()):
            try:
                self._manage_position(pos_id)
            except Exception as e:
                logger.error(f"[Cycle] Erreur gestion position {pos_id} : {e}", exc_info=True)

        # 4. Pipeline signaux : collecte + ranking
        symbols = self.scanner.get_symbols()
        self.last_scan_time       = datetime.now(timezone.utc)
        self.last_symbols_scanned = list(symbols)

        try:
            signals = self.pipeline.collect(
                symbols=symbols,
                active_per_tf=self._active_per_tf,
                ohlcv_fn=self._get_ohlcv,
                open_positions=self.open_positions,
                cooldowns=self._cooldown,
                signal_log=self.signal_log,
                ml=self.ml,
            )
        except Exception as e:
            logger.error(f"[Cycle] Erreur pipeline signaux : {e}", exc_info=True)
            signals = []

        # 5. Exécution des signaux rankés
        for sig in signals:
            slot_key = sig.slot_key
            pos_key  = f"{sig.symbol}::{sig.strategy}::{sig.tf}"
            if pos_key in self.open_positions:
                continue

            # Vérifications dans l'ordre : global, slot, corrélation, budget
            ok_global, reason_global = self.risk.can_trade(sig.side)
            if not ok_global:
                logger.debug(f"[Cycle] {sig.symbol}/{slot_key} rejeté (global: {reason_global})")
                self.signal_log.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "symbol": sig.symbol, "strategy": sig.strategy,
                    "side": sig.side, "score": round(sig.score, 3),
                    "threshold": round(self._strat_thresholds.get(sig.strategy, self.threshold), 3),
                    "timeframe": sig.tf, "status": "rejected", "reason": f"risk: {reason_global}",
                })
                continue

            ok_slot, reason_slot = self.risk.can_slot_trade(slot_key)
            if not ok_slot:
                logger.debug(f"[Cycle] {sig.symbol}/{slot_key} rejeté (slot CB: {reason_slot})")
                self.signal_log.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "symbol": sig.symbol, "strategy": sig.strategy,
                    "side": sig.side, "score": round(sig.score, 3),
                    "threshold": round(self._strat_thresholds.get(sig.strategy, self.threshold), 3),
                    "timeframe": sig.tf, "status": "rejected", "reason": f"slot_cb: {reason_slot}",
                })
                continue

            ok_corr, reason_corr = self.allocator.check_correlation(sig.side, self.open_positions)
            if not ok_corr:
                logger.debug(f"[Cycle] {sig.symbol}/{slot_key} rejeté (corrélation: {reason_corr})")
                continue

            # Prix et taille
            ticker = self._safe_ticker(sig.symbol)
            if ticker is None:
                continue
            price = ticker.get("last", 0)
            if price <= 0:
                continue

            strat_threshold = self._strat_thresholds.get(sig.strategy, self.threshold)
            atr   = sig.atr if sig.atr > 0 else _compute_atr(self._get_ohlcv(sig.symbol, sig.tf) or pl.DataFrame())
            size, notional = self.risk.compute_size(price, atr, score=sig.score, threshold=strat_threshold)
            leverage       = self.risk.compute_leverage(notional)

            ok_budget, reason_budget = self.allocator.can_allocate(slot_key, notional)
            if not ok_budget:
                logger.debug(f"[Cycle] {sig.symbol}/{slot_key} rejeté (budget: {reason_budget})")
                self.signal_log.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "symbol": sig.symbol, "strategy": sig.strategy,
                    "side": sig.side, "score": round(sig.score, 3),
                    "threshold": round(strat_threshold, 3),
                    "timeframe": sig.tf, "status": "rejected", "reason": f"budget: {reason_budget}",
                })
                continue

            if not self._pre_execution_check(sig.symbol, sig.side, size, price, notional):
                continue

            self._open_position(
                pos_key, sig.symbol, sig.to_signal_dict(),
                price, size, notional, atr, leverage, sig.tf
            )
            self.allocator.register_open(slot_key, notional)

        # 6. Synchro solde + rapport
        if self.cfg["trading"].get("paper_mode"):
            self._sync_paper_balance()
        elif self._margin_enabled and time.time() >= self._margin_next_sync:
            self._sync_margin_account()
            self._margin_next_sync = time.time() + 60
        elif self.cycle_count % 10 == 0:
            self._sync_spot_balance()
        if self.cycle_count % 10 == 0:
            self._send_status_report()

        # 7. Rééquilibrage hebdomadaire des budgets
        self.allocator.rebalance_if_due()

    # ── Cache OHLCV multi-TF ───────────────────────────────────────────────
    def _get_ohlcv(self, symbol: str, tf: str) -> Optional[pl.DataFrame]:
        """
        Retourne le DataFrame OHLCV pour (symbol, tf), avec cache TTL.
        Applique aussi le filtre "nouvelle bougie" pour éviter les recalculs.
        """
        key = (symbol, tf)
        ttl = _OHLCV_TTL.get(tf, 120)
        cached = self._ohlcv_cache.get(key)
        if cached and (time.time() - cached[0]) < ttl:
            return cached[1]

        limit = RECOMMENDED_LIMIT.get(tf, 500)
        # Pour le live trader, 500 barres suffisent pour les indicateurs
        # Le RECOMMENDED_LIMIT sert surtout pour l'optimisation
        fetch_limit = min(limit, 500)
        df = self.scanner.fetch_ohlcv(symbol, tf, limit=fetch_limit)
        if df is None or len(df) < 220:
            self._exchange_errors[symbol] = self._exchange_errors.get(symbol, 0) + 1
            self.notif.notify_exchange_error(
                symbol, f"fetch_ohlcv {tf} retourné vide ou trop court",
                self._exchange_errors[symbol]
            )
            return None
        self._exchange_errors[symbol] = 0

        # Filtre "nouvelle bougie"
        try:
            last_ts_raw = df["time"][-1]
            last_ts = int(last_ts_raw.timestamp() * 1000) if hasattr(last_ts_raw, "timestamp") \
                      else int(last_ts_raw) if isinstance(last_ts_raw, (int, float)) else 0
            prev_ts = self._last_candle_ts.get(key, 0)
            if last_ts == prev_ts and not self.open_positions:
                logger.debug(f"[Filtre] {symbol}/{tf} : même bougie — skip")
                return None
            self._last_candle_ts[key] = last_ts
        except Exception:
            pass

        # Mise à jour cache ATR (utilise le TF primaire pour _manage_position)
        atr = _compute_atr(df)
        if atr > 0:
            self._atr_cache[symbol] = (time.time(), atr)

        # Pré-calcul vectorisé des indicateurs partagés (RSI, ATR, ADX, MACD, vol_ratio).
        # Appelé une seule fois par fetch — toutes les stratégies lisent ensuite via
        # pre_val(df, "_pre_rsi14") en O(1) au lieu de recalculer en O(n) chacune.
        # Gain mesuré : ~5–7 ms par symbole×TF sur 500 barres.
        try:
            df = precompute_df(df)
        except Exception as _pc_err:
            logger.debug(f"[precompute] {symbol}/{tf} KO : {_pc_err}")

        self._ohlcv_cache[key] = (time.time(), df)
        return df

    # ── Volatility brake ───────────────────────────────────────────────────
    def _update_volatility_brake(self):
        """Calcule l'ATR BTC/USDC 1h et met à jour le volatility brake."""
        try:
            btc_symbol = "BTC/USDC"
            df_btc = self._get_ohlcv(btc_symbol, "1h")
            if df_btc is not None and len(df_btc) > 10:
                atr   = _compute_atr(df_btc)
                price = float(df_btc["close"][-1])
                if price > 0 and atr > 0:
                    atr_pct = atr / price
                    self.risk.update_volatility(atr_pct)
        except Exception as e:
            logger.debug(f"[VolBrake] Calcul ATR BTC KO : {e}")

    # ── Scan par stratégie (conservé pour compatibilité tests / appels directs) ──
    def _scan_symbol_strategy(self, symbol: str, df: pl.DataFrame,
                               strategy, params: dict, tf: str):
        htf = _HTF_MAP.get(tf)
        # Pas de HTF pour 1d (déjà le TF le plus haut) → df_htf = None → htf_trend = 0 (neutre)
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
                "time":     datetime.now(timezone.utc).isoformat(),
                "symbol":   symbol, "strategy": strategy.name,
                "side":     signal.get("side", "?"), "score": round(float(score), 3),
                "threshold":round(float(strat_threshold), 3),
                "timeframe":tf, "status": "rejected",
                "reason":   f"score {score:.2f} < threshold {strat_threshold:.2f}",
            })
            return

        if self.ml and self.ml.is_ready:
            regime = self.scanner.detect_regime(df)
            score, side = self.ml.blend_signal(signal["score"], signal["side"], df, regime)
            if score < strat_threshold:
                return
            signal["score"] = score

        can, reason = self.risk.can_trade(signal["side"])
        if not can:
            logger.debug(f"[Scan] {symbol}/{strategy.name}/{tf} : rejeté ({reason})")
            self.signal_log.append({
                "time":     datetime.now(timezone.utc).isoformat(),
                "symbol":   symbol, "strategy": strategy.name,
                "side":     signal.get("side", "?"), "score": round(float(signal.get("score", 0)), 3),
                "threshold":round(float(strat_threshold), 3),
                "timeframe":tf, "status": "rejected", "reason": f"risk: {reason}",
            })
            return

        ticker = self._safe_ticker(symbol)
        if ticker is None:
            return
        price = ticker.get("last", 0)
        if price <= 0:
            return

        atr  = _compute_atr(df)
        size, notional = self.risk.compute_size(price, atr, score=signal["score"], threshold=strat_threshold)
        leverage = self.risk.compute_leverage(notional)

        if not self._pre_execution_check(symbol, signal["side"], size, price, notional):
            return

        pos_key = f"{symbol}::{strategy.name}::{tf}"
        self._open_position(pos_key, symbol, signal, price, size, notional, atr, leverage, tf)

    # ── Rapport de statut ─────────────────────────────────────────────────
    def _send_status_report(self):
        positions_detail = []
        for pos in self.open_positions.values():
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

    # ── Utilitaires ───────────────────────────────────────────────────────
    def _get_cached_atr(self, symbol: str) -> Optional[float]:
        cached = self._atr_cache.get(symbol)
        if cached and (time.time() - cached[0]) < self._atr_cache_ttl:
            return cached[1]
        return None

    def _pre_execution_check(self, symbol: str, side: str, size: float,
                              price: float, notional: float) -> bool:
        if self.cfg["trading"].get("paper_mode"):
            # Vérification capital disponible simulé
            locked = sum(p.get("notional", 0) for p in self.open_positions.values())
            available = self._paper_base - locked
            if notional > available:
                logger.warning(
                    f"[Paper] {symbol} : capital simulé insuffisant "
                    f"(dispo={available:.2f} < notional={notional:.2f})"
                )
                return False
            return True
        if self.capital_display < notional * 0.05:
            logger.warning(f"[PreCheck] {symbol} : capital insuffisant")
            return False
        if notional > self.capital_display * 0.25:
            logger.warning(f"[PreCheck] {symbol} : notionnel trop élevé ({notional:.2f})")
            return False
        return True

    def _safe_ticker(self, symbol: str) -> Optional[dict]:
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.warning(f"[LiveTrader] fetch_ticker {symbol} : {e}")
            return None

    def _recover_after_gap(self, gap_secs: float):
        gap_min = gap_secs / 60
        self.notif.send(
            f"⚠️ *Reprise après coupure réseau*\n"
            f"Durée estimée : `{gap_min:.0f} min`\n"
            f"Révision des `{len(self.open_positions)}` position(s)…",
            async_=True
        )
        self._atr_cache.clear()
        self._ohlcv_cache.clear()
        self._status_db_cache = None
        positions_to_close = []
        for pos_id, pos in list(self.open_positions.items()):
            symbol = pos["symbol"]
            ticker = self._safe_ticker(symbol)
            if ticker is None:
                continue
            price = ticker.get("last", pos["entry"])
            side  = pos["side"]
            if (side == "long" and price <= pos["stop"]) or \
               (side == "short" and price >= pos["stop"]):
                positions_to_close.append((pos_id, price))
        for pos_id, price in positions_to_close:
            self._close_position(pos_id, price)
        if positions_to_close:
            self.notif.send(
                f"✅ *Reprise réseau terminée*\n"
                f"{len(positions_to_close)} position(s) clôturée(s).",
                async_=True
            )

    def _maybe_retrain_ml(self):
        if not self.ml:
            return
        interval = self.cfg.get("ml", {}).get("retrain_interval", 3600)
        if time.time() < self._ml_retrain_at:
            return
        self._ml_retrain_at = time.time() + interval
        threading.Thread(target=self._retrain_ml_thread, daemon=True).start()

    def _retrain_ml_thread(self):
        logger.info("[ML] Réentraînement en arrière-plan…")
        try:
            symbols = self.scanner.get_symbols()
            dfs = [self.scanner.fetch_ohlcv(s, self.tf, 1000) for s in symbols]
            dfs = [d for d in dfs if d is not None and len(d) >= self.cfg["ml"]["min_samples"]]
            if dfs:
                df_combined = pl.concat(dfs)
                self.ml.train(df_combined)
                self.ml.save()
        except Exception as e:
            logger.error(f"[ML] Réentraînement KO : {e}")

    def _sync_margin_account(self):
        try:
            acct = self.exchange.fetch_margin_account()
            ml_  = float(acct.get("marginLevel", 0) or 0)
            if ml_ > 0:
                self._margin_level = round(ml_, 3)
                if ml_ < 1.15:
                    logger.warning(f"[MARGIN] ⚠ Margin level CRITIQUE : {ml_:.3f}")
                    self.notif.send(f"⚠ MARGIN LEVEL CRITIQUE : {ml_:.3f}", async_=True)
            if not self.cfg["trading"].get("paper_mode"):
                free_usdc = self.exchange.fetch_margin_balance_usdc()
                if free_usdc > 0:
                    with self._capital_lock:
                        self.capital_display = free_usdc
                    self.risk.update_equity(free_usdc)
        except Exception as e:
            logger.warning(f"[MARGIN] sync KO : {e}")

    def _restore_paper_base(self, initial: float) -> float:
        """Restaure le capital settled paper depuis la dernière equity_close en BDD."""
        if not self.cfg["trading"].get("paper_mode"):
            return initial
        try:
            from app.core.database import DailyStats
            sess = self.SessionLocal()
            try:
                last = sess.query(DailyStats).order_by(DailyStats.date.desc()).first()
                if last and last.equity_close and last.equity_close > 0:
                    logger.info(f"[Paper] Capital settled restauré : {last.equity_close:.2f} (date={last.date})")
                    return float(last.equity_close)
            finally:
                sess.close()
        except Exception as e:
            logger.warning(f"[Paper] Impossible de restaurer capital settled : {e}")
        return initial

    def _sync_paper_balance(self):
        """Paper mode : capital_display = equity settled + PnL non réalisé des positions ouvertes."""
        unrealized = 0.0
        for pos in self.open_positions.values():
            ticker = self._safe_ticker(pos["symbol"])
            if ticker:
                price = ticker.get("last", pos["entry"])
                if pos["side"] == "long":
                    unrealized += (price - pos["entry"]) * pos["size"]
                else:
                    unrealized += (pos["entry"] - price) * pos["size"]
        total = self._paper_base + unrealized
        with self._capital_lock:
            self.capital_display = round(total, 4)
        self.risk.update_equity(self.capital_display)
        self.allocator.update_equity(self.capital_display)

    def _sync_spot_balance(self):
        try:
            bal  = self.exchange.fetch_balance()
            free = float(bal.get("USDC", {}).get("free", 0) or bal.get("USDT", {}).get("free", 0) or 0)
            if free > 0:
                unrealized = 0.0
                for pos in self.open_positions.values():
                    ticker = self._safe_ticker(pos["symbol"])
                    if ticker:
                        price = ticker.get("last", pos["entry"])
                        if pos["side"] == "long":
                            unrealized += (price - pos["entry"]) * pos["size"]
                        else:
                            unrealized += (pos["entry"] - price) * pos["size"]
                total = free + unrealized
                with self._capital_lock:
                    self.capital_display = round(total, 4)
                self.risk.update_equity(self.capital_display)
        except Exception as e:
            logger.warning(f"[Spot Sync] KO : {e}")

    def _purge_memory(self):
        active_symbols = set(self.scanner.get_symbols())
        now = time.time()
        # Purge cache OHLCV périmé (TTL ×10)
        to_del = [k for k, v in self._ohlcv_cache.items()
                  if (now - v[0]) > _OHLCV_TTL.get(k[1], 120) * 10]
        for k in to_del:
            del self._ohlcv_cache[k]
        # Purge last_candle_ts symboles inactifs
        for key in list(self._last_candle_ts.keys()):
            if key[0] not in active_symbols:
                del self._last_candle_ts[key]
        # Purge cooldowns expirés
        self._cooldown = {s: t for s, t in self._cooldown.items() if t > now}
        # Purge ATR cache périmé
        cutoff_atr = now - self._atr_cache_ttl * 10
        self._atr_cache = {s: v for s, v in self._atr_cache.items() if v[0] > cutoff_atr}
        # loss_notified
        open_ids = set(self.open_positions.keys())
        self._loss_notified &= open_ids
        # exchange_errors
        self._exchange_errors = {s: v for s, v in self._exchange_errors.items() if s in active_symbols}
        # Jobs optimisation terminés > 24h
        try:
            from app.engine.auto_optimizer import _jobs, _jobs_lock
            cutoff_jobs = now - 86400
            with _jobs_lock:
                to_del_jobs = [
                    jid for jid, job in _jobs.items()
                    if job.get("status") in ("done", "error")
                    and job.get("finished_at", now) < cutoff_jobs
                ]
                for jid in to_del_jobs:
                    del _jobs[jid]
        except Exception:
            pass
        logger.debug(f"[Purge] Mémoire nettoyée. OHLCV cache : {len(self._ohlcv_cache)} entrées.")

    def _maybe_auto_optimize(self):
        now     = time.time()
        opt_due = self._auto_opt_enabled and now >= self._auto_opt_next_run
        ml_due  = self._ml_trainer.any_due(self._loaded_strategies)
        if not opt_due and not ml_due:
            return
        if opt_due:
            self._auto_opt_next_run = now + self._auto_opt_interval
            logger.info("[AutoOpt] Démarrage optimisation planifiée…")
        threading.Thread(target=self._auto_opt_thread, args=(opt_due,), daemon=True).start()

    def _auto_opt_thread(self, run_optimization: bool = True):
        try:
            # Réentraînement des stratégies ML dont l'intervalle est écoulé
            self._ml_trainer.retrain_due(self._loaded_strategies, self.scanner, self.timeframes)

            if not run_optimization:
                return

            from app.engine.auto_optimizer import AutoOptimizer
            symbol = "BTC/USDC"  # paire représentative
            tfs    = self.timeframes
            df_map = {}
            for tf in tfs:
                limit = RECOMMENDED_LIMIT.get(tf, 500)
                df    = self.scanner.fetch_ohlcv(symbol, tf, limit=limit)
                if df is not None and len(df) > 0:
                    df_map[tf] = df

            if not df_map:
                logger.warning("[AutoOpt] Données insuffisantes pour l'optimisation planifiée.")
                return

            opt = AutoOptimizer(self.cfg, n_trials=40, method="bayesian",
                                on_apply_callback=self._on_opt_applied)
            opt.start_async(df_map, symbol, auto_apply=True)
            logger.info(f"[AutoOpt] Jobs lancés pour TFs : {list(df_map.keys())}")
        except Exception as e:
            logger.error(f"[AutoOpt] Erreur : {e}", exc_info=True)

    def _on_opt_applied(self, strategy_name: str, params: dict):
        """Callback appelé après application des params — recharge les stratégies actives."""
        # Mettre à jour les params en mémoire (merge safe)
        existing = dict(self.strat_params.get(strategy_name, {}))
        for k, v in params.items():
            if v is not None:
                existing[k] = v
        self.strat_params[strategy_name] = existing
        self.reload_active_strategies()
        logger.info(f"[LiveTrader] Stratégies rechargées après optimisation de {strategy_name}")

    # ── Hot-reload stratégies ─────────────────────────────────────────────
    def reload_strategies(self, enabled: list) -> dict:
        """Active/désactive des stratégies activées manuellement."""
        current = set(self._loaded_strategies.keys())
        target  = set(enabled)
        added, removed, errors = [], [], []

        for name in target - current:
            try:
                mod   = importlib.import_module(f"app.strategies.{name}")
                strat = mod.Strategy()
                self.engine.register(strat)
                self._loaded_strategies[name] = strat
                sp = self.strat_params.get(name, {})
                self._strat_thresholds[name] = float(sp.get("score_threshold", self.threshold))
                added.append(name)
            except Exception as e:
                errors.append(f"{name}: {e}")

        for name in current - target:
            self.engine.strategies = [s for s in self.engine.strategies if s.name != name]
            self._loaded_strategies.pop(name, None)
            self._strat_thresholds.pop(name, None)
            removed.append(name)

        self.cfg["strategies"]["enabled"] = list(target)
        self._build_active_per_tf()  # recalcule
        if hasattr(self, "allocator"):
            self.allocator.rebuild_slots(self._active_per_tf)
        if hasattr(self, "pipeline"):
            self.pipeline.update_strategies(self._loaded_strategies)
        return {"added": added, "removed": removed, "errors": errors,
                "active_per_tf": {tf: [s["name"] for s in v] for tf, v in self._active_per_tf.items()}}

    def set_auto_optimizer(self, enabled: bool, interval_h: int = 24):
        self._auto_opt_enabled  = enabled
        self._auto_opt_interval = interval_h * 3600
        if enabled and self._auto_opt_next_run == 0:
            self._auto_opt_next_run = time.time() + self._auto_opt_interval
        self.cfg.setdefault("optimizer", {})["enabled"] = enabled
        self.cfg["optimizer"]["auto_interval_h"] = interval_h

    @property
    def status(self) -> dict:
        risk      = self.risk.status_dict()
        positions = [self._serialize_position(p) for p in self.open_positions.values()]

        now = time.time()
        if (self._status_db_cache is None or
                now - self._status_db_cache_ts > self._status_db_cache_ttl):
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
            "total_fees":           round(db["total_fees"] + sum(p.get("fees",0) for p in self.open_positions.values()), 4),
            "win_rate":             db["win_rate"],
            "profit_factor":        db["profit_factor"],
            "best_trade":           db["best_trade"],
            "by_strategy":          db["by_strategy"],
            "last_scan_time":       self.last_scan_time.isoformat() if self.last_scan_time else None,
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
            # Nouveaux champs
            "capital_allocation":   self.allocator.get_status(),
            "circuit_breakers":     self.risk.get_circuit_breakers_status(),
            "slot_states":          self.risk.get_slot_states(),
            "volatility_brake":     self.risk.volatility_brake_active,
        })

    def _load_db_stats(self) -> dict:
        total_pnl = 0.0; total_trades = 0; wins = 0
        best_trade = 0.0; total_fees = 0.0
        gross_win = 0.0;  gross_loss = 0.0
        by_strategy: dict = {}
        try:
            _sess = self.SessionLocal()
            try:
                from app.core.database import get_trades as _gt
                all_trades = _gt(_sess, limit=10000)
                for t in all_trades:
                    p   = float(t.pnl or 0)
                    fee = float(t.fees or 0)
                    total_pnl    += p
                    total_fees   += fee
                    total_trades += 1
                    if p > 0:  wins += 1;  gross_win  += p
                    else:                  gross_loss += abs(p)
                    if p > best_trade: best_trade = p
                    sname = t.strategy or "unknown"
                    if sname not in by_strategy:
                        by_strategy[sname] = {"trades":0,"wins":0,"pnl":0.0,"fees":0.0,"pnls":[]}
                    by_strategy[sname]["trades"] += 1
                    by_strategy[sname]["pnl"]    += p
                    by_strategy[sname]["fees"]   += fee
                    by_strategy[sname]["pnls"].append(p)
                    if p > 0: by_strategy[sname]["wins"] += 1
            finally:
                _sess.close()
        except Exception:
            pass

        win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0
        pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

        import numpy as _np
        for sname, d in by_strategy.items():
            n    = d["trades"]
            pnls = d.pop("pnls", [])
            gw   = sum(p for p in pnls if p > 0)
            gl   = abs(sum(p for p in pnls if p < 0))
            d["win_rate"]      = round(d["wins"] / n * 100, 1) if n > 0 else 0.0
            d["total_pnl"]     = round(d["pnl"], 4)
            d["total_fees"]    = round(d["fees"], 4)
            d["total_trades"]  = n
            d["profit_factor"] = round(gw / gl, 3) if gl > 0 else (999.0 if gw > 0 else 0.0)
            if len(pnls) >= 3:
                arr = _np.array(pnls, dtype=float)
                std = float(_np.std(arr))
                raw_sharpe = float(_np.mean(arr)) / std * _np.sqrt(252) if std > 0 else 0.0
                d["sharpe"] = round(_safe_float(raw_sharpe, 0.0), 3)
            else:
                d["sharpe"] = 0.0
            if len(pnls) >= 2:
                eq   = _np.cumsum(pnls)
                peak = _np.maximum.accumulate(eq)
                raw_dd = float(_np.min((eq - peak) / (peak + 1e-9) * 100))
                d["max_drawdown"] = round(_safe_float(raw_dd, 0.0), 2)
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
