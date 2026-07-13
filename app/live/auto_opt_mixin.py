"""
AutoOptMixin — gestion du portefeuille de stratégies et de son optimisation.

Extrait de LiveTrader (V4-J / ARCH-06). Regroupe :
  - chargement/rechargement des stratégies (registre, actives par TF)
  - auto-optimisation planifiée (threads AutoOptimizer par symbole)
  - forward-test glissant (Phase 0, observationnel)
  - cycle de vie des bots + allocation continue (Phase 2) et re-optimisation
    des bots retirés

Requiert que l'instance possède (fournis par LiveTrader.__init__) :
  self.cfg, self.engine, self.scanner, self.timeframes, self.threshold,
  self.strat_params, self._loaded_strategies, self._strat_thresholds,
  self._active_per_tf, self._ml_trainer, self.allocator, self.pipeline,
  self.SessionLocal, self._lifecycle, self._auto_opt_*, self._fwd_test_*,
  self._lifecycle_*
"""
import importlib
import logging
import threading
import time

from app.core.bot_identity import build_slot_key
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.engine.optimizer import get_active_strategies_per_tf, RECOMMENDED_LIMIT

logger = logging.getLogger(__name__)


class AutoOptMixin:
    """Portefeuille de stratégies : chargement, planification auto-opt,
    forward-test, cycle de vie (voir docstring module)."""

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
            symbols = (self.cfg.get("scanner") or {}).get("symbols") or [DEFAULT_CONFIG_SYMBOL]
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
                    # Score budget-indépendant : rendement simulé moyen par trade.
                    score = float(sim.get("avg_return_pct", 0.0) or 0.0)
                    scores[key] = score
                    slots_data[key] = {
                        "budget_pct":          self.allocator.budget_pct(key),
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
