"""
LiveTrader — orchestrateur principal de la boucle de trading live.

Architecture :
  - LiveTrader(PositionMixin, BalanceSyncMixin) centralise la coordination
  - OHLCVCache  : cache multi-TF des DataFrames OHLCV (composé)
  - PositionMixin  : cycle de vie des positions (open/manage/close/restore)
  - BalanceSyncMixin : synchronisation du capital (paper/spot/margin)
  - SignalPipeline  : collecte et ranking des signaux
  - CapitalAllocator : allocation du capital par slot strategy::tf
"""
import importlib
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import polars as pl

from app.core.database           import init_db
from app.core.exchange           import RobustExchange
from app.core.indicators         import atr_val as _compute_atr
from app.core.notifications      import Notifier
from app.core.risk               import RiskManager
from app.engine.engine           import Engine
from app.engine.optimizer        import get_active_strategies_per_tf, RECOMMENDED_LIMIT
from app.engine.scanner          import MarketScanner
from app.live.balance_sync       import BalanceSyncMixin
from app.live.capital_allocator  import CapitalAllocator
from app.live.ohlcv_cache        import OHLCVCache
from app.live.position_mixin     import PositionMixin, _calc_unreal_pct
from app.live.signal_pipeline    import SignalPipeline
from app.live.utils              import _HTF_MAP, _sanitize, _safe_float

from app.core.bot_identity import build_slot_key, build_pos_key

logger = logging.getLogger(__name__)


class LiveTrader(PositionMixin, BalanceSyncMixin):
    """
    Orchestrateur de la boucle de trading live.

    Coordonne sans les implémenter directement :
      - la collecte de signaux multi-TF (via SignalPipeline)
      - la gestion des positions (via PositionMixin)
      - la synchronisation du capital (via BalanceSyncMixin)
      - le cache OHLCV (via OHLCVCache)
      - l'allocation du capital (via CapitalAllocator)
      - le réentraînement ML et l'auto-optimisation (threads daemon)
    """

    def __init__(self, cfg: dict, exchange: RobustExchange):
        self.cfg      = cfg
        self.exchange = exchange
        self.risk     = RiskManager(cfg)
        self.notif    = Notifier(cfg)
        self.risk.attach_notifier(self.notif)

        self._trailing_cfg = {
            "mult":             float(cfg.get("backtest", {}).get("trail_wide",     2.5)),
            "grace_bars":       int(cfg.get("backtest", {}).get("grace_bars",       4)),
            "breakeven_r":      float(cfg.get("backtest", {}).get("breakeven_r",    1.2)),
            "trail_tight_mult": float(cfg.get("backtest", {}).get("trail_tight",    1.0)),
            "lock_r":           float(cfg.get("backtest", {}).get("lock_r",         2.5)),
            "tight_r":          float(cfg.get("backtest", {}).get("tight_r",        4.0)),
            "lock_ratio":       float(cfg.get("backtest", {}).get("lock_ratio",     0.60)),
            "use_swing":        bool(cfg.get("backtest", {}).get("use_swing",       True)),
        }

        self.scanner  = MarketScanner(exchange, cfg)
        t             = cfg["trading"]
        self.timeframes  = t.get("timeframes") or [t.get("timeframe", "1h")]
        self.tf          = self.timeframes[0]
        self.interval    = t["scan_interval"]
        self.threshold   = t["score_threshold"]
        self.strat_params = cfg.get("strategy_params", {})

        _, self.SessionLocal = init_db(cfg["database"]["url"])

        # Phase 3 — reprise propre : restaure halt/kill-switch/pauses/compteurs.
        self.risk.attach_persistence(self.SessionLocal)

        # ── Moteur + stratégies ────────────────────────────────────────────
        self.engine = Engine()
        self._loaded_strategies: Dict[str, object] = {}
        self._load_all_strategies()

        # _ml_lock doit être initialisé avant MLStrategyTrainer
        self._ml_lock = threading.Lock()

        # Chargement des modèles ML persistés
        from app.ml.trainer import MLStrategyTrainer
        self._ml_trainer = MLStrategyTrainer(cfg, ml_lock=self._ml_lock)
        self._ml_trainer.load_models(self._loaded_strategies, self.timeframes)

        # Stratégies actives par TF (depuis optimizer_results)
        self._active_per_tf: Dict[str, List[dict]] = {}
        self._build_active_per_tf()

        # Seuils par stratégie (peut être surchargé dans strategy_params)
        self._strat_thresholds: Dict[str, float] = {}
        for name in self._loaded_strategies:
            sp = self.strat_params.get(name, {})
            self._strat_thresholds[name] = float(sp.get("score_threshold", self.threshold))

        # ── État interne ───────────────────────────────────────────────────
        self.open_positions: Dict[str, dict] = {}
        self._positions_lock  = threading.Lock()
        self._capital_lock    = threading.Lock()
        self.running          = False
        self.cycle_count      = 0
        self.capital_display  = cfg["trading"]["capital"]
        self._paper_base      = self._restore_paper_base(cfg["trading"]["capital"])
        self.last_scan_time   = None
        self.last_symbols_scanned: List[str] = []

        self._margin_enabled  = (cfg["exchange"].get("margin", False)
                                  or cfg["trading"].get("margin_mode") is not None)
        self._margin_level    = None
        self._margin_interest = 0.0
        self._margin_next_sync= 0
        self._balance_detail  = None   # {free, used, total, borrowed}

        self.signal_log: deque = deque(maxlen=100)

        self._auto_opt_enabled  = cfg.get("optimizer", {}).get("enabled", False)
        self._auto_opt_interval = int(cfg.get("optimizer", {}).get("auto_interval_h", 24)) * 3600
        self._auto_opt_next_run = 0

        # ── Forward-test glissant (Phase 0 — observationnel, zéro impact trading)
        # Re-backteste chaque jour les params figés des slots actifs sur données
        # fraîches et compare la réalisation live à une fourchette Monte-Carlo
        # glissante (cf. app/engine/forward_test.py). Tourne dans un thread dédié.
        _ft_cfg = cfg.get("forward_test", {}) or {}
        self._fwd_test_enabled       = bool(_ft_cfg.get("enabled", True))
        self._fwd_test_interval      = int(_ft_cfg.get("interval_h", 24)) * 3600
        self._fwd_test_lookback_days = int(_ft_cfg.get("lookback_days", 45))
        self._fwd_test_edge_lookback = int(_ft_cfg.get("edge_lookback_days", 100))
        self._fwd_test_symbol        = _ft_cfg.get("symbol", "BTC/USDC")
        # Premier passage différé pour laisser le cache OHLCV se réchauffer.
        self._fwd_test_next_run      = time.time() + int(_ft_cfg.get("initial_delay_s", 300))

        # ── Cycle de vie & allocation continue (Phase 2 — lecture/shadow)
        # Dérive l'état des bots (candidat/essai/actif/retiré) et calcule
        # l'allocation cible pilotée par le score, SANS l'appliquer (shadow).
        from app.live.slot_lifecycle import SlotLifecycleManager
        _lc_cfg = cfg.get("lifecycle", {}) or {}
        self._lifecycle = SlotLifecycleManager(cfg, session_factory=self.SessionLocal)
        self._lifecycle_enabled  = bool(_lc_cfg.get("enabled", True))
        self._lifecycle_interval = int(_lc_cfg.get("interval_h", 1)) * 3600
        self._lifecycle_next_run = time.time() + int(_lc_cfg.get("initial_delay_s", 600))
        # Re-optimisation automatique des bots retirés (opt-in). False = la file
        # de re-opt est seulement exposée (l'utilisateur garde la main).
        self._lifecycle_auto_reopt = bool(_lc_cfg.get("auto_reopt", False))
        self._lifecycle_snapshot: dict = {}
        self._shadow_alloc: dict = {}

        # Re-entry cooldown par symbole
        self._cooldown: Dict[str, float] = {}

        # Cache status DB (TTL court pour l'API)
        self._status_db_cache: Optional[dict] = None
        self._status_db_cache_ts: float = 0.0
        self._status_db_cache_ttl: float = 10.0

        self._loss_notified: set = set()
        self._purge_every_n = 200
        self._purge_counter = 0

        # ── Objets composés ────────────────────────────────────────────────
        self.ohlcv_cache = OHLCVCache(
            exchange=self.exchange, cfg=cfg,
            notif=self.notif, risk=self.risk
        )
        self.allocator = CapitalAllocator(
            capital=self.capital_display,
            active_per_tf=self._active_per_tf,
            cfg=cfg,
            session_factory=self.SessionLocal,
        )
        # Enregistrer le callback de persistance des budgets
        self.allocator.set_persist_callback(self._persist_allocator_budgets)
        self.pipeline = SignalPipeline(
            loaded_strategies=self._loaded_strategies,
            cfg=cfg,
        )

        self._last_day_key: str = ""

        logger.info(
            f"[LiveTrader] Démarré — Paper={cfg['trading']['paper_mode']} "
            f"| TFs configurés={self.timeframes}"
        )

    # ── Chargement des stratégies ──────────────────────────────────────────

    def _load_all_strategies(self) -> None:
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
                mod  = importlib.import_module(f"app.strategies.{name}")
                strat = mod.Strategy()
                self.engine.register(strat)
                self._loaded_strategies[name] = strat
                logger.info(f"[LiveTrader] Stratégie chargée : {name}")
            except Exception as e:
                logger.warning(f"[LiveTrader] Impossible de charger {name} : {e}")

    def _build_active_per_tf(self) -> None:
        """
        Construit self._active_per_tf depuis optimizer_results.
        Format : { "1h": [{"name": "trend", "params": {...}, "score": 0.82}, ...] }
        """
        self._active_per_tf = get_active_strategies_per_tf(self.cfg)
        self._bots_cache = None   # invalide le cache d'identités (set actif changé)
        for tf, strats in self._active_per_tf.items():
            names   = [s["name"] for s in strats]
            has_oos = any(s.get("score", 0) > 0 for s in strats)
            origin  = ("optimizer_results" if has_oos
                       else "fallback (stratégies activées manuellement)")
            logger.info(f"[LiveTrader] TF={tf} → {names} [{origin}]")

    def reload_active_strategies(self) -> None:
        """Rechargement à chaud après optimisation (appelé par l'API)."""
        self._build_active_per_tf()
        self.allocator.rebuild_slots(self._active_per_tf)
        self.pipeline.update_strategies(self._loaded_strategies)
        logger.info("[LiveTrader] Stratégies actives rechargées depuis optimizer_results")

    def reload_strategies(self, enabled: list) -> dict:
        """Active/désactive des stratégies activées manuellement (hot-reload API)."""
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
        self._build_active_per_tf()
        self.allocator.rebuild_slots(self._active_per_tf)
        self.pipeline.update_strategies(self._loaded_strategies)
        return {
            "added": added, "removed": removed, "errors": errors,
            "active_per_tf": {tf: [s["name"] for s in v]
                              for tf, v in self._active_per_tf.items()},
        }

    def set_auto_optimizer(self, enabled: bool, interval_h: int = 24) -> None:
        self._auto_opt_enabled  = enabled
        self._auto_opt_interval = interval_h * 3600
        if enabled and self._auto_opt_next_run == 0:
            self._auto_opt_next_run = time.time() + self._auto_opt_interval
        self.cfg.setdefault("optimizer", {})["enabled"] = enabled
        self.cfg["optimizer"]["auto_interval_h"] = interval_h

    # ── Boucle principale ──────────────────────────────────────────────────

    def start(self) -> None:
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
                        logger.warning(
                            f"[LiveTrader] Coupure réseau ({gap_secs/60:.1f} min) — reprise..."
                        )
                        self._recover_after_gap(gap_secs)
                        _last_successful_cycle = time.time()
                self._heartbeat()
                self._check_dead_man()
                # Maintenance planifiée isolée : une erreur ici (auto-opt,
                # forward-test, cycle de vie, purge) ne doit JAMAIS arrêter le
                # bot — sinon « arrêt complet ». Chaque tâche lourde tourne déjà
                # dans son propre thread ; ce garde-fou couvre la planification.
                try:
                    self._maybe_auto_optimize()
                    self._maybe_forward_test()
                    self._maybe_lifecycle()
                    self._purge_counter += 1
                    if self._purge_counter >= self._purge_every_n:
                        self._purge_counter = 0
                        self._purge_memory()
                except Exception as maint_err:
                    logger.exception(
                        f"[LiveTrader] Erreur de maintenance (non bloquante) : {maint_err}"
                    )
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

    def stop(self, close_positions: bool = False) -> None:
        self.running = False
        if close_positions and self.open_positions:
            logger.warning(
                f"[LiveTrader] Arrêt avec clôture de {len(self.open_positions)} position(s)…"
            )
            self.notif.send(
                f"🛑 *Bot arrêté — clôture des positions*\n"
                f"Clôture de `{len(self.open_positions)}` position(s) en cours…",
                async_=False
            )
            for pos_id in list(self.open_positions.keys()):
                for _attempt in range(3):
                    try:
                        pos    = self.open_positions.get(pos_id)
                        if not pos or pos.get("_reserved"):
                            break
                        ticker = self._safe_ticker(pos["symbol"])
                        price  = ticker.get("last", pos["entry"]) if ticker else pos["entry"]
                        self._close_position(pos_id, price)
                        break
                    except Exception as e:
                        wait = 2 ** _attempt
                        logger.error(
                            f"[Stop] Clôture {pos_id} KO (tentative {_attempt+1}/3) : {e}"
                            + (f" — retry dans {wait}s" if _attempt < 2 else "")
                        )
                        if _attempt < 2:
                            time.sleep(wait)
                        else:
                            logger.critical(
                                f"[Stop] ⚠️ Position {pos_id} "
                                f"({pos.get('symbol','?')}) NON clôturée après 3 tentatives "
                                f"— vérifiez manuellement sur l'exchange."
                            )
                            self.notif.send(
                                f"⚠️ *Clôture échouée au shutdown*\n"
                                f"Position `{pos_id}` non clôturée — vérifiez sur l'exchange.",
                                async_=False
                            )
        elif self.open_positions:
            n    = len(self.open_positions)
            syms = ", ".join(p["symbol"] for p in self.open_positions.values()
                             if not p.get("_reserved"))
            logger.info(
                f"[LiveTrader] Arrêt sans clôture — {n} position(s) conservée(s) : {syms}"
            )
            self.notif.send(
                f"⏸ *Bot arrêté — surveillance suspendue*\n"
                f"`{n}` position(s) conservée(s) sur l'exchange : `{syms}`\n"
                f"Elles seront reprises au prochain démarrage.",
                async_=False
            )
        self.notif.notify_stop("Arrêt normal")

    # ── Cycle principal ────────────────────────────────────────────────────

    def _cycle(self) -> None:
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
        self.ohlcv_cache.update_volatility_brake()

        # 3. Gestion des positions ouvertes
        for pos_id in list(self.open_positions.keys()):
            try:
                self._manage_position(pos_id)
            except Exception as e:
                logger.error(
                    f"[Cycle] Erreur gestion position {pos_id} : {e}", exc_info=True
                )

        # 4. Pipeline signaux : collecte + ranking
        _symbols_ttl = float(self.cfg.get("scanner", {}).get("symbols_cache_ttl", 300))
        try:
            symbols = self.scanner.get_symbols(ttl=_symbols_ttl)
        except Exception as _sc_err:
            logger.warning(f"[Cycle] get_symbols KO : {_sc_err}")
            return
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
            )
        except Exception as e:
            logger.error(f"[Cycle] Erreur pipeline signaux : {e}", exc_info=True)
            signals = []

        # 5. Exécution des signaux rankés
        for sig in signals:
            slot_key = sig.slot_key
            pos_key  = build_pos_key(sig.symbol, sig.strategy, sig.tf)
            if pos_key in self.open_positions:
                continue

            strat_threshold = self._strat_thresholds.get(sig.strategy, self.threshold)
            atr = sig.atr if sig.atr > 0 else (
                self.ohlcv_cache.get_cached_atr(sig.symbol) or 0.0
            )
            ticker = self._safe_ticker(sig.symbol)
            if ticker is None:
                continue
            price = ticker.get("last", 0)
            if price <= 0:
                continue

            self._try_open_from_signal(
                pos_key=pos_key,
                symbol=sig.symbol,
                strategy_name=sig.strategy,
                side=sig.side,
                score=sig.score,
                strat_threshold=strat_threshold,
                tf=sig.tf,
                slot_key=slot_key,
                signal_dict=sig.to_signal_dict(),
                atr=atr,
                price=price,
            )

        # 6. Synchro solde + rapport périodique
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

    # ── Accès OHLCV (wrappers vers ohlcv_cache) ───────────────────────────

    def _get_ohlcv(self, symbol: str, tf: str) -> Optional[pl.DataFrame]:
        """
        Wrapper public vers OHLCVCache.get — conservé pour compatibilité
        avec signal_pipeline (callback ohlcv_fn) et _scan_symbol_strategy.
        """
        return self.ohlcv_cache.get(symbol, tf, self.open_positions)

    def _get_cached_atr(self, symbol: str) -> Optional[float]:
        """Wrapper public vers OHLCVCache.get_cached_atr."""
        return self.ohlcv_cache.get_cached_atr(symbol)

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

    # ── Utilitaires ────────────────────────────────────────────────────────

    def _persist_allocator_budgets(self, budgets: dict) -> None:
        """
        Callback de persistance appelé par CapitalAllocator après chaque _apply_mode().
        Met à jour capital_allocator.slot_budgets dans state.cfg et config.yaml.
        """
        try:
            # V4-D : plus aucune dépendance à app.api — self.cfg EST l'objet
            # config partagé du process (posé par cli.py, lu par les routes),
            # et l'écriture disque passe par le verrou unique de core/yaml_io.
            self.cfg.setdefault("capital_allocator", {})["slot_budgets"] = budgets
            try:
                from app.core.yaml_io import update_config_yaml

                def _upd(d):
                    d.setdefault("capital_allocator", {})["slot_budgets"] = budgets
                update_config_yaml(_upd)
            except Exception as e:
                logger.warning(f"[LiveTrader] Persistance YAML budgets KO : {e}")
        except Exception as e:
            logger.debug(f"[LiveTrader] _persist_allocator_budgets : {e}")

    def persist_allocator_state(self) -> None:
        """
        Persiste manuellement l'état complet de l'allocateur (budgets + disabled_slots)
        dans config.yaml. Utile après des modifications batch ou un redémarrage.
        """
        budgets = {
            k: round(v.budget_pct, 4)
            for k, v in self.allocator._slots.items()
            if v.enabled
        }
        self._persist_allocator_budgets(budgets)

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
        Applique dans l'ordre : global risk → slot enabled → slot CB → corrélation →
        sizing → budget → pre_execution_check → ouverture.
        Retourne True si la position a été ouverte, False sinon.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        def _reject(tag: str, reason: str) -> bool:
            logger.debug(f"[Trade] {symbol}/{slot_key} rejeté ({tag}: {reason})")
            self.signal_log.append({
                "time": now_iso, "symbol": symbol, "strategy": strategy_name,
                "side": side, "score": round(score, 3),
                "threshold": round(strat_threshold, 3),
                "timeframe": tf, "status": "rejected",
                "reason": f"{tag}: {reason}",
            })
            return False

        # 1. Risque global
        ok_global, reason_global = self.risk.can_trade(side)
        if not ok_global:
            return _reject("risk", reason_global)

        # 2. Slot activé/désactivé (statut indépendant du budget)
        ok_enabled, reason_enabled = self.allocator.is_slot_enabled(slot_key)
        if not ok_enabled:
            return _reject("slot_disabled", reason_enabled)

        # 3. Slot circuit breaker (pause)
        ok_slot, reason_slot = self.risk.can_slot_trade(slot_key)
        if not ok_slot:
            return _reject("slot_cb", reason_slot)

        # 4. Corrélation/exposition
        ok_corr, reason_corr = self.allocator.check_correlation(
            side, self.open_positions, symbol=symbol
        )
        if not ok_corr:
            logger.debug(f"[Trade] {symbol}/{slot_key} rejeté (corrélation: {reason_corr})")
            return False

        # 5. Sizing — par bot (sur son budget) si activé, sinon sur l'équité globale.
        budget_usdc = None
        max_lev = None
        if getattr(self.allocator, "per_bot_sizing", False):
            b = self.allocator.slot_budget_usdc(slot_key)
            if b and b > 0:
                from app.core.bot_identity import resolve_venue
                budget_usdc = b
                max_lev = resolve_venue(self.cfg, strategy_name, tf, symbol).max_leverage
        # Distance au stop initial → sizing par le risque réel (parité backtest),
        # au lieu de l'ATR brut qui sur-risquait d'un facteur = multiple du stop.
        stop_dist = self._initial_stop_distance(side, price, atr, signal_dict)
        size, notional = self.risk.compute_size(
            price, atr, score=score, threshold=strat_threshold,
            size_factor=float(signal_dict.get("size_factor", 1.0)),
            budget=budget_usdc, max_leverage=max_lev,
            stop_dist=stop_dist,
        )
        leverage = self.risk.compute_leverage(notional)

        # 6. Budget
        ok_budget, reason_budget = self.allocator.can_allocate(slot_key, notional)
        if not ok_budget:
            return _reject("budget", reason_budget)

        # 7. Pre-execution check (ordres réels)
        if not self._pre_execution_check(symbol, side, size, price, notional):
            return False

        # 8. Vérification atomique + ouverture (protège contre les races concurrentes)
        with self._positions_lock:
            if pos_key in self.open_positions:
                return False
            max_pos = self.cfg["trading"].get("max_positions", 5)
            # En mode veto shadow (paper), max_positions ne bloque plus — on a déjà
            # compté l'écart dans risk.can_trade ; ce garde-fou atomique reste actif
            # uniquement en mode enforce.
            if not getattr(self.risk, "veto_shadow", False) and len(self.open_positions) >= max_pos:
                return _reject("risk", f"Max positions ({max_pos}) atteint")
            # Réserve le slot avant de relâcher le verrou
            self.open_positions[pos_key] = {"_reserved": True}

        try:
            self._open_position(pos_key, symbol, signal_dict, price, size, notional, atr, leverage, tf)
        except Exception:
            with self._positions_lock:
                self.open_positions.pop(pos_key, None)
            raise
        self.allocator.register_open(slot_key, notional)
        return True

    def _safe_ticker(self, symbol: str) -> Optional[dict]:
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.warning(f"[LiveTrader] fetch_ticker {symbol} : {e}")
            return None

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
            self._close_position(pos_id, price)

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

    # ── Auto-optimisation planifiée ───────────────────────────────────────

    def _maybe_auto_optimize(self) -> None:
        now     = time.time()
        opt_due = self._auto_opt_enabled and now >= self._auto_opt_next_run
        ml_due  = self._ml_trainer.any_due(self._loaded_strategies)
        if not opt_due and not ml_due:
            return
        if opt_due:
            self._auto_opt_next_run = now + self._auto_opt_interval
            logger.info("[AutoOpt] Démarrage optimisation planifiée…")
        threading.Thread(
            target=self._auto_opt_thread, args=(opt_due,), daemon=True
        ).start()

    def _auto_opt_thread(self, run_optimization: bool = True) -> None:
        try:
            # Réentraînement des stratégies ML dont l'intervalle est écoulé
            self._ml_trainer.retrain_due(
                self._loaded_strategies, self.scanner, self.timeframes
            )
            if not run_optimization:
                return

            from app.engine.auto_optimizer import AutoOptimizer

            # Ne ré-optimiser que les stratégies réellement actives/activées —
            # évite de lancer un job par stratégie × TF (jusqu'à 31×5) à chaque
            # cycle planifié. La concurrence est de toute façon bornée par le
            # sémaphore de auto_optimizer, mais on réduit ici le travail inutile.
            active_names = {
                s["name"] for strats in self._active_per_tf.values() for s in strats
            }
            active_names |= set(self.cfg.get("strategies", {}).get("enabled", []))
            strategies = sorted(active_names) or None

            # Config PAR SYMBOLE : on optimise chaque symbole configuré séparément
            # → chaque paire écrit sa propre optimizer_results[tf][symbol]. La
            # concurrence globale reste bornée par le sémaphore de l'optimiseur.
            symbols = (self.cfg.get("scanner") or {}).get("symbols") or ["BTC/USDC"]
            opt = AutoOptimizer(
                self.cfg, n_trials=40, method="bayesian",
                on_apply_callback=self._on_opt_applied
            )
            launched = 0
            for symbol in symbols:
                df_map = {}
                for tf in self.timeframes:
                    limit = RECOMMENDED_LIMIT.get(tf, 500)
                    df    = self.scanner.fetch_ohlcv(symbol, tf, limit=limit)
                    if df is not None and len(df) > 0:
                        df_map[tf] = df
                if not df_map:
                    logger.warning(f"[AutoOpt] Données insuffisantes pour {symbol}.")
                    continue
                opt.start_async(df_map, symbol, strategies=strategies,
                                timeframes=self.timeframes, auto_apply=True)
                launched += 1
                logger.info(
                    f"[AutoOpt] Jobs {symbol} lancés — "
                    f"stratégies={strategies or 'toutes'} | TFs={list(df_map.keys())}")
            if not launched:
                logger.warning("[AutoOpt] Aucune donnée — optimisation planifiée annulée.")
        except Exception as e:
            logger.error(f"[AutoOpt] Erreur : {e}", exc_info=True)

    # ── Forward-test glissant (Phase 0) ───────────────────────────────────
    def _maybe_forward_test(self) -> None:
        """Planifie le forward-test glissant quotidien (thread dédié, non bloquant)."""
        if not self._fwd_test_enabled:
            return
        now = time.time()
        if now < self._fwd_test_next_run:
            return
        # Ne pas surcharger pendant une optimisation (les backtests ML du
        # forward-test s'ajouteraient à la charge → risque d'OOM) : on diffère.
        if self._optimization_running():
            self._fwd_test_next_run = now + 600   # nouvel essai dans 10 min
            return
        self._fwd_test_next_run = now + self._fwd_test_interval
        logger.info("[ForwardTest] Démarrage forward-test glissant planifié…")
        threading.Thread(target=self._forward_test_thread, daemon=True).start()

    def _forward_test_thread(self) -> None:
        try:
            from app.engine.forward_test import run_forward_test
            run_forward_test(
                cfg=self.cfg,
                fetch_ohlcv=self.scanner.fetch_ohlcv,
                active_per_tf=self._active_per_tf,
                session_factory=self.SessionLocal,
                symbol=self._fwd_test_symbol,
                lookback_days=self._fwd_test_lookback_days,
                edge_lookback_days=self._fwd_test_edge_lookback,
            )
        except Exception as e:
            logger.error(f"[ForwardTest] Erreur : {e}", exc_info=True)

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

    # ── Cycle de vie & allocation continue (Phase 2) ───────────────────────
    def _maybe_lifecycle(self) -> None:
        """Planifie l'évaluation du cycle de vie + allocation shadow (thread dédié)."""
        if not self._lifecycle_enabled:
            return
        now = time.time()
        if now < self._lifecycle_next_run:
            return
        # Le thread lit les stats live + recalcule l'alloc ; on diffère pendant
        # une optimisation pour ne pas concurrencer la base/CPU.
        if self._optimization_running():
            self._lifecycle_next_run = now + 600
            return
        self._lifecycle_next_run = now + self._lifecycle_interval
        threading.Thread(target=self._lifecycle_thread, daemon=True).start()

    @staticmethod
    def _optimization_running() -> bool:
        """True si une optimisation est en cours (pour différer les tâches de fond)."""
        try:
            from app.engine.auto_optimizer import any_optimization_running
            return any_optimization_running()
        except Exception:
            return False

    def _lifecycle_thread(self) -> None:
        try:
            from app.core.oos_tracker import load_oos_tracker
            from app.core.database import get_slot_live_stats, session_scope
            oos = load_oos_tracker()
            days = self._fwd_test_lookback_days
            slots_data: dict = {}
            scores: dict = {}
            for tf, slots in self._active_per_tf.items():
                for slot in slots:
                    name = slot.get("name")
                    if not name:
                        continue
                    # V4-C : clé 3-parties (l'ancienne 2-parties ne matchait
                    # plus ni oos_tracker ni allocator._slots -> budget lu = 0
                    # pour TOUS les slots par le lifecycle).
                    key = build_slot_key(name, tf, slot.get("symbol", ""))
                    rec = oos.get(key, {})
                    contract = rec.get("contract", {}) or {}
                    sim = rec.get("sim", {}) or {}
                    edge = rec.get("edge", {}) or {}
                    with session_scope(self.SessionLocal) as sess:
                        stats = get_slot_live_stats(sess, name, tf, days=days,
                                                    symbol=slot.get("symbol") or None)
                    sb = self.allocator._slots.get(key)
                    # Score budget-indépendant : rendement simulé moyen par trade.
                    score = float(sim.get("avg_return_pct", 0.0) or 0.0)
                    scores[key] = score
                    slots_data[key] = {
                        "budget_pct":          sb.budget_pct if sb else 0.0,
                        "live_trades":         stats["n_trades"],
                        "live_in_band":        contract.get("in_band"),
                        "live_avg_return_pct": stats["avg_return_pct"],
                        "score":               score,
                        # Promotion par edge (cf. CONCEPTION_PROMOTION_PAR_EDGE).
                        "edge_ci_low":         edge.get("ci_low_pct"),
                        "edge_n":              edge.get("n"),
                        "worst_trade_pct":     edge.get("worst_trade_pct"),
                    }
            self._lifecycle_snapshot = self._lifecycle.evaluate(slots_data)
            # Allocation continue : appliquée si activée, sinon calculée en shadow.
            if getattr(self.allocator, "continuous_allocation", False):
                self._shadow_alloc = self.allocator.apply_continuous_allocation(scores)
            else:
                self._shadow_alloc = self.allocator.compute_shadow_allocation(scores)
            logger.info(
                f"[Lifecycle] états={self._lifecycle_snapshot.get('counts')} "
                f"| file re-opt={len(self._lifecycle_snapshot.get('reopt_queue', []))}"
            )
            # Re-optimisation des bots retirés (opt-in) : ferme la boucle
            # « retrait → re-optimisation » de la doc §2.
            if self._lifecycle_auto_reopt:
                queue = self._lifecycle.pop_reopt_queue()
                strategies = sorted({k.split("::", 1)[0] for k in queue})
                if strategies:
                    logger.info(f"[Lifecycle] Re-optimisation des bots retirés : {strategies}")
                    self._trigger_reopt(strategies)
        except Exception as e:
            logger.error(f"[Lifecycle] Erreur : {e}", exc_info=True)

    def _trigger_reopt(self, strategies: list) -> None:
        """Lance une optimisation ciblée pour les stratégies de bots retirés.

        L'application des nouveaux params (callback ``_on_opt_applied``) bumpe la
        génération du bot et recharge les stratégies : le bot « renaît » et
        repassera par candidat/essai au fil des trades live.
        """
        try:
            from app.engine.auto_optimizer import AutoOptimizer
            # Config par symbole : re-optimise sur chaque symbole configuré (chacun
            # réécrit sa propre optimizer_results[tf][symbol]).
            symbols = ((self.cfg.get("scanner") or {}).get("symbols")
                       or [self._fwd_test_symbol])
            opt = AutoOptimizer(self.cfg, n_trials=40, method="bayesian",
                                on_apply_callback=self._on_opt_applied)
            launched = 0
            for symbol in symbols:
                df_map = {}
                for tf in self.timeframes:
                    limit = RECOMMENDED_LIMIT.get(tf, 500)
                    df = self.scanner.fetch_ohlcv(symbol, tf, limit=limit)
                    if df is not None and len(df) > 0:
                        df_map[tf] = df
                if not df_map:
                    logger.warning(f"[Lifecycle] Re-opt {symbol} : données insuffisantes.")
                    continue
                opt.start_async(df_map, symbol, strategies=strategies,
                                timeframes=self.timeframes, auto_apply=True)
                launched += 1
            if not launched:
                logger.warning("[Lifecycle] Re-opt annulée : aucune donnée.")
        except Exception as e:
            logger.error(f"[Lifecycle] _trigger_reopt KO : {e}", exc_info=True)

    def _on_opt_applied(self, strategy_name: str, params: dict) -> None:
        """Callback après application des params optimisés — recharge les stratégies actives."""
        existing = dict(self.strat_params.get(strategy_name, {}))
        for k, v in params.items():
            if v is not None:
                existing[k] = v
        self.strat_params[strategy_name] = existing
        # Nouveaux params figés → nouveau bot : incrémente la génération monotone
        # (anti-collision) pour chaque slot de cette stratégie.
        try:
            from app.core.bot_identity import register_identity
            for tf, slots in self._active_per_tf.items():
                for slot in slots:
                    if slot.get("name") == strategy_name:
                        ident = register_identity(
                            strategy_name, tf,
                            slot.get("params", {}).get(strategy_name, existing), self.cfg,
                            symbol=slot.get("symbol", ""),
                        )
                        logger.info(f"[Bot] {ident.bot_id} ({ident.venue.describe()})")
        except Exception as e:
            logger.debug(f"[Bot] register_identity KO : {e}")
        self.reload_active_strategies()

    def get_bot_identities(self) -> list:
        """Identité (lecture seule) de chaque bot actif — pour l'API/UI.

        Mise en cache (le ``status`` est sollicité ~1×/s) : on ne recalcule —
        et on ne relit ``data/bot_generations.json`` — qu'à l'invalidation
        (changement du set actif / application d'une optimisation).
        """
        if getattr(self, "_bots_cache", None) is not None:
            return self._bots_cache
        from app.core.bot_identity import peek_identity, _load_generations
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
        """Agrège les statistiques de trading depuis la table Trade."""
        total_pnl = 0.0; total_trades = 0; wins = 0
        best_trade = 0.0; total_fees = 0.0
        gross_win = 0.0;  gross_loss = 0.0
        by_strategy: dict = {}
        try:
            from app.core.database import get_trades as _gt, session_scope
            with session_scope(self.SessionLocal) as _sess:
                for t in _gt(_sess, limit=10000):
                    p   = float(t.pnl or 0)
                    fee = float(t.fees or 0)
                    total_pnl    += p
                    total_fees   += fee
                    total_trades += 1
                    if p > 0:
                        wins += 1
                        gross_win += p
                    else:
                        gross_loss += abs(p)
                    if p > best_trade:
                        best_trade = p
                    sname = t.strategy or "unknown"
                    if sname not in by_strategy:
                        by_strategy[sname] = {
                            "trades": 0, "wins": 0,
                            "pnl": 0.0, "fees": 0.0, "pnls": [],
                        }
                    by_strategy[sname]["trades"] += 1
                    by_strategy[sname]["pnl"]    += p
                    by_strategy[sname]["fees"]   += fee
                    by_strategy[sname]["pnls"].append(p)
                    if p > 0:
                        by_strategy[sname]["wins"] += 1
        except Exception as e:
            logger.debug(f"[LiveTrader] agrégation trades : {e}")

        win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0
        pf = (round(gross_win / gross_loss, 3) if gross_loss > 0
              else (999.0 if gross_win > 0 else 0.0))

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
                raw = float(_np.mean(arr)) / std * _np.sqrt(252) if std > 0 else 0.0
                d["sharpe"] = round(_safe_float(raw, 0.0), 3)
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
