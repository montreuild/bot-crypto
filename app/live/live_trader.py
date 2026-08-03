"""
LiveTrader — orchestrateur principal de la boucle de trading live.

Architecture (V4-J / ARCH-06 : un fichier par responsabilité ;
               ARCH-003 : PositionMixin découpé en 4 mixins spécialisés) :
  - LiveTrader(PositionOpenMixin, PositionManageMixin, PositionCloseMixin,
               PositionRestoreMixin, BalanceSyncMixin, AutoOptMixin, HealthMixin)
    centralise la coordination (init, boucle, cycle)
  - PositionOpenMixin    : ouverture de positions + chemin unique
                          (_try_open_from_signal, _open_position, slippage)
  - PositionManageMixin  : suivi tick-by-tick (trailing, scale-in, exchange stops)
  - PositionCloseMixin   : clôture (ordre + PnL + BDD + notifications)
  - PositionRestoreMixin : restauration au démarrage depuis la BDD
  - MarketHoursMixin     : calendrier de marché (gating des entrées hors
                           séance, clôture avant fin de séance) — inerte en
                           crypto, où la venue par défaut est 24/7
  - BalanceSyncMixin     : synchronisation du capital (paper/spot/margin)
  - AutoOptMixin         : portefeuille de stratégies, auto-optimisation,
                           forward-test glissant, cycle de vie des bots
  - HealthMixin          : heartbeat/dead-man, reprise réseau, purge, status API
  - OHLCVCache           : cache multi-TF des DataFrames OHLCV (composé)
  - SignalPipeline       : collecte et ranking des signaux
  - CapitalAllocator     : allocation du capital par slot strategy::tf::symbol
"""
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import polars as pl

from app.core.bot_identity import build_pos_key
from app.core.database import init_db
from app.core.exchange import RobustExchange
from app.core.notifications import Notifier
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.core.rejections import RejectionCounter
from app.core.risk_gate import RiskManager, _default_venue_capital
from app.core.risk_ledger import RiskLedger
from app.engine.engine import Engine
from app.engine.scanner import MarketScanner
from app.live.auto_opt_mixin import AutoOptMixin
from app.live.balance_sync import BalanceSyncMixin
from app.live.capital_allocator import CapitalAllocator
from app.live.health_mixin import HealthMixin
from app.live.market_hours_mixin import MarketHoursMixin
from app.live.ohlcv_cache import OHLCVCache
from app.live.position_close_mixin import PositionCloseMixin
from app.live.position_manage_mixin import PositionManageMixin
from app.live.position_open_mixin import PositionOpenMixin
from app.live.position_restore_mixin import PositionRestoreMixin
from app.live.signal_pipeline import SignalPipeline

logger = logging.getLogger(__name__)


class LiveTrader(PositionOpenMixin, PositionManageMixin, PositionCloseMixin,
                 PositionRestoreMixin, BalanceSyncMixin, AutoOptMixin,
                 MarketHoursMixin, HealthMixin):
    """
    Orchestrateur de la boucle de trading live.

    Coordonne sans les implémenter directement :
      - la collecte de signaux multi-TF (via SignalPipeline)
      - la gestion des positions et l'ouverture (via PositionOpenMixin /
        PositionManageMixin / PositionCloseMixin / PositionRestoreMixin)
      - la synchronisation du capital (via BalanceSyncMixin)
      - le réentraînement ML, l'auto-optimisation, le forward-test et le
        cycle de vie des bots (via AutoOptMixin)
      - la santé du process et le reporting (via HealthMixin)
      - le cache OHLCV (via OHLCVCache)
      - l'allocation du capital (via CapitalAllocator)
    """

    def __init__(self, cfg: dict, exchange: RobustExchange):
        self.cfg      = cfg
        self.exchange = exchange
        self.risk     = RiskManager(cfg)
        self.notif    = Notifier(cfg)
        self.risk.attach_notifier(self.notif)

        # S1-08 : live.trailing est la source dédiée des paramètres de trailing
        # live — sans elle, un changement de config backtest (walk-forward,
        # optimiseur) modifiait silencieusement le trailing live. Repli sur
        # backtest.* pour compatibilité avec les config.yaml existants.
        _trailing_src = cfg.get("live", {}).get("trailing")
        if _trailing_src is None:
            _trailing_src = cfg.get("backtest", {})
            logger.warning(
                "[LiveTrader] live.trailing absent — repli sur backtest.* pour "
                "le trailing live (trail_wide/grace_bars/breakeven_r/...). "
                "Un changement de config backtest (walk-forward, optimiseur) "
                "modifie alors aussi le trailing live sans le vouloir — "
                "définissez live.trailing dans config.yaml pour découpler les deux."
            )
        self._trailing_cfg = {
            "mult":             float(_trailing_src.get("trail_wide",     2.5)),
            "grace_bars":       int(_trailing_src.get("grace_bars",       4)),
            "breakeven_r":      float(_trailing_src.get("breakeven_r",    1.2)),
            "trail_tight_mult": float(_trailing_src.get("trail_tight",    1.0)),
            "lock_r":           float(_trailing_src.get("lock_r",         2.5)),
            "tight_r":          float(_trailing_src.get("tight_r",        4.0)),
            "lock_ratio":       float(_trailing_src.get("lock_ratio",     0.60)),
            "use_swing":        bool(_trailing_src.get("use_swing",       True)),
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
        # S12 : le capital appartient à la venue par défaut, plus à `trading.*`.
        _venue_capital        = _default_venue_capital(cfg)
        self.capital_display  = _venue_capital
        self._paper_base      = self._restore_paper_base(_venue_capital)
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
        self._fwd_test_symbol        = _ft_cfg.get("symbol", DEFAULT_CONFIG_SYMBOL)
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
        # ── S12 : comptabilité du risque engagé ────────────────────────────
        # Le ledger remplace l'allocateur dans son rôle de GATING (réservation
        # sous enveloppe et budget de risque). L'allocateur reste instancié
        # tant que les routes API et le statut y renvoient — sa suppression est
        # la dernière étape de la refonte, quand plus rien ne le référence.
        self.ledger     = RiskLedger()
        self.rejections = RejectionCounter()
        # {slot_key: Envelope} — reconstruit par le thread de cycle de vie à
        # partir des slots réellement actifs et de leurs edges mesurées.
        self.envelopes: Dict[str, object] = {}
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

    # ── Boucle principale ──────────────────────────────────────────────────

    def start(self) -> None:
        self.running = True
        self._restore_open_positions()
        self.notif.notify_start(self.cfg)
        logger.info("=" * 60)
        logger.info("  BOT DÉMARRÉ (Multi-TF)")
        logger.info(f"  Timeframes actifs     : {self.timeframes}")
        logger.info("  → Pour ajouter des TFs : config.yaml › trading.timeframes")
        logger.info("  → Puis lancer une optimisation pour activer les stratégies")
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
                        self._close_position(pos_id, price, exit_reason="manual")
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
        # Marché fermé ne veut pas dire position oubliée : le trailing continue
        # de se recalculer et un stop touché au gap d'ouverture doit être
        # constaté. Seules les ENTRÉES sont soumises au calendrier (étapes 4-5).
        for pos_id in list(self.open_positions.keys()):
            try:
                self._manage_position(pos_id)
            except Exception as e:
                logger.error(
                    f"[Cycle] Erreur gestion position {pos_id} : {e}", exc_info=True
                )

        # 3bis. Clôture avant fin de séance (venues qui refusent l'overnight).
        try:
            self._close_positions_at_session_end()
        except Exception as e:
            logger.error(f"[Cycle] Clôture de séance KO : {e}", exc_info=True)

        # 4. Pipeline signaux : collecte + ranking
        _symbols_ttl = float(self.cfg.get("scanner", {}).get("symbols_cache_ttl", 300))
        try:
            symbols = self.scanner.get_symbols(ttl=_symbols_ttl)
        except Exception as _sc_err:
            logger.warning(f"[Cycle] get_symbols KO : {_sc_err}")
            return
        # G2 : ne scorer que les places ouvertes. No-op en crypto (24/7).
        # La synchro de solde et le rééquilibrage (étapes 6-7) restent joués
        # même toutes places fermées — l'équité doit continuer de vivre la nuit.
        symbols = self._tradable_symbols(symbols)
        self.last_scan_time       = datetime.now(timezone.utc)
        self.last_symbols_scanned = list(symbols)

        signals = []
        if symbols:
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
            # Garde-fou par signal : le filtre ci-dessus travaille sur la liste
            # du scanner, un signal peut venir d'ailleurs (API, scan direct).
            if not self._market_open(sig.symbol, sig.strategy, sig.tf):
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
        self._persist_allocator_budgets(self.allocator.enabled_budgets())

    def _safe_ticker(self, symbol: str) -> Optional[dict]:
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.warning(f"[LiveTrader] fetch_ticker {symbol} : {e}")
            return None
