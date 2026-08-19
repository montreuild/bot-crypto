"""MLStrategyTrainer — gestion du cycle de vie des modèles ML (chargement, scheduling, réentraînement).

ML-02 : le chargement passe par le registre (``app.ml.model_registry`` —
dernière version PROMUE pour ce (symbole, TF, stratégie), au lieu du chemin
plat historique) et le réentraînement passe par la politique de gate
(``app.ml.policy.maybe_refresh`` — le nouveau modèle n'est promu que s'il ne
régresse pas par rapport au sortant, comparés sur le même holdout). Un modèle
qui régresse reste sur disque pour l'audit (``decisions.jsonl``) mais
n'écrase jamais silencieusement le modèle servi.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Au-delà de ce multiple de l'intervalle de réentraînement configuré, un
# modèle chargé au démarrage est considéré périmé (log de warning seul —
# rien n'empêche de continuer à le servir, cf. ``retrain_due`` qui le
# rafraîchira au prochain cycle si le scheduler tourne).
_STALE_FACTOR = 2.0


class MLStrategyTrainer:
    """Gère le cycle de vie des modèles BaseStrategyML (chargement, scheduling, réentraînement)."""

    def __init__(self, cfg: dict, ml_lock: threading.Lock | None = None):
        self.cfg = cfg
        self._ml_lock = ml_lock or threading.Lock()
        self._retrain_timeout = float(
            cfg.get("trading", {}).get("ml_retrain_timeout_secs", 300)
        )
        # Clé : "{name}@{tf}" — timer indépendant par (stratégie, timeframe)
        self._retrain_at: Dict[str, float] = {}

    # ── Démarrage ─────────────────────────────────────────────────────────
    def load_models(self, strategies: dict, timeframes) -> None:
        """Charge la dernière version PROMUE du registre pour chaque (stratégie,
        TF) et active ``managed_externally``.

        Aucun scanner requis : la clé du registre est ``(TF, recette)``. Ce
        paramètre existait pour dériver un symbole de résolution ; sans lui,
        un appelant qui ne connaissait pas encore de scanner résolvait
        ``symbol=None`` et ne trouvait jamais rien — un « pas de modèle »
        silencieux qui déclenchait un réentraînement inutile.
        """
        import app.ml.model_registry as ml_registry
        from app.engine.engine import BaseStrategyML
        if isinstance(timeframes, str):
            timeframes = [timeframes]

        strat_params = self.cfg.get("strategy_params", {})
        for name, strat in strategies.items():
            if not isinstance(strat, BaseStrategyML):
                continue
            strat.managed_externally = True
            sp         = strat_params.get(name, {})
            interval_h = float(sp.get("retrain_interval_h", strat.retrain_interval_h))
            base_dir   = getattr(strat, "model_dir", "models") or "models"

            for tf in self._supported_tfs(strat, timeframes):
                key = f"{name}@{tf}"
                art = None
                try:
                    from app.ml.scoring import resolve_recipe_name
                    art = ml_registry.resolve(tf, resolve_recipe_name(strat),
                                              base_dir=base_dir)
                except Exception as e:
                    logger.warning(f"[MLTrainer] {name}/{tf} : resolve() KO : {e}")

                if art is not None and strat.load_model(art.path_prefix):
                    auc = self._loaded_auc(strat, tf)
                    logger.info(
                        f"[MLTrainer] {name}/{tf} : modèle chargé "
                        f"(version={art.version_id}, AUC={auc:.4f})"
                    )
                    warn = self._freshness_warning(art, interval_h)
                    if warn:
                        logger.warning(f"[MLTrainer] {name}/{tf} : {warn}")
                    self._retrain_at[key] = time.time() + interval_h * 3600
                else:
                    logger.info(f"[MLTrainer] {name}/{tf} : pas de modèle — réentraînement immédiat planifié")
                    self._retrain_at[key] = 0  # déclenche dès le premier cycle

    # ── Interface pour le scheduler (live_trader._maybe_auto_optimize) ─────
    def any_due(self, strategies: dict) -> bool:
        """Retourne True si au moins une paire (stratégie, TF) doit être réentraînée."""
        from app.engine.engine import BaseStrategyML
        now = time.time()
        for name, strat in strategies.items():
            if not isinstance(strat, BaseStrategyML):
                continue
            for key, ts in self._retrain_at.items():
                if key.startswith(f"{name}@") and now >= ts:
                    return True
        return False

    # ── Réentraînement (appelé depuis _auto_opt_thread) ───────────────────
    def retrain_due(self, strategies: dict, scanner, timeframes) -> None:
        """
        Lance (en thread daemon) le réentraînement de chaque paire (stratégie, TF)
        dont l'intervalle est écoulé. Non-bloquant.

        timeframes : str (compat) ou List[str]
        """
        from app.engine.engine import BaseStrategyML
        if isinstance(timeframes, str):
            timeframes = [timeframes]

        now          = time.time()
        strat_params = self.cfg.get("strategy_params", {})
        for name, strat in strategies.items():
            if not isinstance(strat, BaseStrategyML):
                continue
            sp         = strat_params.get(name, {})
            interval_h = float(sp.get("retrain_interval_h", strat.retrain_interval_h))

            for tf in self._supported_tfs(strat, timeframes):
                key = f"{name}@{tf}"
                if now < self._retrain_at.get(key, 0):
                    continue
                self._retrain_at[key] = now + interval_h * 3600
                logger.info(f"[MLTrainer] Réentraînement planifié : {name}/{tf} (intervalle={interval_h}h)")
                timeout = self._retrain_timeout
                threading.Thread(
                    target=self._retrain_with_timeout,
                    args=(name, strat, strat_params, tf, scanner, timeout),
                    daemon=True,
                ).start()

    def _retrain_with_timeout(self, name: str, strat, strat_params: dict,
                               tf: str, scanner, timeout: float) -> None:
        """Lance _retrain_thread dans un executor et applique un timeout.

        S3-11 (audit V2) : en cas de timeout, le thread sous-jacent continue
        en arrière-plan (Python ne peut pas tuer un thread), MAIS il tient
        `_ml_lock` jusqu'à la fin de `maybe_refresh`. Pendant ce temps,
        l'inférence est bloquée → le live peut score sur des features
        périmées.

        Fix : **acquire `_ml_lock` AVEC un timeout** dans `_retrain_with_timeout`
        (pas dans `_retrain_thread`). Si le lock n'est pas obtenu dans le
        délai, on logge et on abandonne le réentraînement — l'inférence
        actuelle garde le lock exclusif.

        Le `_ml_lock` du `thread.Thread` sous-jacent est supprimé : c'est
        l'executor qui le détient, pas le thread interne. Quand le future
        timeout se déclenche, l'executor relâche le lock à la fin du
        context manager `with self._ml_lock`.
        """
        # S3-11 : acquire avec timeout. Si on ne peut pas prendre le lock
        # dans le délai, abandonner — un réentraînement concurrent est en
        # cours et on ne veut pas bloquer plus longtemps.
        if not self._ml_lock.acquire(timeout=5.0):
            logger.warning(
                f"[MLTrainer] Réentraînement {name}/{tf} SKIPPÉ — "
                f"_ml_lock indisponible après 5s (un autre retrain en cours ?)"
            )
            return

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._retrain_thread_no_lock,
                                     name, strat, strat_params, tf, scanner)
                try:
                    future.result(timeout=timeout)
                except FuturesTimeoutError:
                    logger.error(
                        f"[MLTrainer] Réentraînement {name}/{tf} timeout ({timeout}s) — "
                        f"le thread interne continue en arrière-plan, mais le "
                        f"_ml_lock sera relâché quand maybe_refresh terminera. "
                        f"L'inférence est temporairement bloquée — surveiller les "
                        f"latencies."
                    )
                except Exception as e:
                    logger.error(f"[MLTrainer] Réentraînement {name}/{tf} KO : {e}")
        finally:
            self._ml_lock.release()

    def _retrain_thread_no_lock(self, name: str, strat, strat_params: dict,
                                 tf: str, scanner) -> None:
        """Variante de _retrain_thread SANS acquire du _ml_lock — c'est
        l'appelant (_retrain_with_timeout) qui le détient.

        S3-11 : le verrou est pris au niveau de l'executor pour garantir
        qu'il sera relâché même en cas de timeout du future (le
        context manager `try/finally` dans _retrain_with_timeout le
        garantit). L'ancienne implémentation prenait le lock DANS
        _retrain_thread, ce qui le rendait inaccessible en cas de
        timeout du future (le thread continuait mais personne ne pouvait
        interrompre le lock).
        """
        logger.info(f"[MLTrainer] Début réentraînement {name}/{tf}…")
        try:
            import app.ml.policy as ml_policy

            symbol = self._resolve_symbol(scanner)
            if not symbol:
                logger.warning(f"[MLTrainer] {name}/{tf} : aucun symbole disponible")
                return

            sp = strat_params.get(name, {})
            gate_cfg = ml_policy.GateConfig.from_params(sp)
            need = int(strat.min_bars_required(strat_params))
            gate_min_n = gate_cfg.holdout_bars + gate_cfg.min_window_bars
            fetch_n = max(need + 200, 2 * need, gate_min_n, int(sp.get("live_fetch_bars", 0)), 1000)

            logger.info(
                f"[MLTrainer] {name}/{tf} : fetch {symbol} — demande {fetch_n} bougies "
                f"(min stratégie={need}, plancher gate={gate_min_n})"
            )
            df = scanner.fetch_ohlcv(symbol, tf, fetch_n)
            got = 0 if df is None else len(df)
            if df is None or got < gate_min_n:
                logger.warning(
                    f"[MLTrainer] {name}/{tf} : données insuffisantes pour {symbol} — "
                    f"reçu {got} bougies, requis ≥{gate_min_n} pour évaluer le gate "
                    f"(le store local a-t-il un historique suffisant ? "
                    f"sinon laissez tourner le live un moment avant de réentraîner)"
                )
                return
            logger.info(
                f"[MLTrainer] {name}/{tf} : {got} bougies dispo pour {symbol} — "
                f"entraînement + gate en cours…"
            )
            base_dir = getattr(strat, "model_dir", "models") or "models"
            # S3-11 : pas de acquire ici — _ml_lock est détenu par
            # _retrain_with_timeout via context manager try/finally.
            result = ml_policy.maybe_refresh(
                strat, symbol, tf, df, params=sp,
                gate_cfg=gate_cfg, source="live", base_dir=base_dir,
            )
            self._log_gate_result(name, tf, result)
        except Exception as e:
            logger.error(f"[MLTrainer] Réentraînement {name}/{tf} KO : {e}")

    # ── Thread interne ─────────────────────────────────────────────────────
    def _retrain_thread(self, name: str, strat, strat_params: dict,
                        tf: str, scanner) -> None:
        """Fetch OHLCV → entraîne un candidat → gate contre le sortant publié
        → promeut ou garde (``app.ml.policy.maybe_refresh``).

        Le nombre de bougies demandé à ``scanner.fetch_ohlcv`` couvre à la
        fois le minimum de la stratégie (``min_bars_required``) ET le
        plancher du gate (``holdout_bars + min_window_bars`` — sinon
        ``maybe_refresh`` refuserait systématiquement faute de données,
        cf. ``GateConfig``). Un plancher explicite plus large est possible
        par stratégie/TF via ``strategy_params.<name>.live_fetch_bars``
        (ML-02 volet « dimensionnement de la fenêtre » — l'edge mesuré du V4
        figé vient d'un entraînement sur ~40k barres, très supérieur au
        minimum technique). Les logs détaillent la fenêtre demandée vs reçue.
        """
        logger.info(f"[MLTrainer] Début réentraînement {name}/{tf}…")
        try:
            import app.ml.policy as ml_policy

            symbol = self._resolve_symbol(scanner)
            if not symbol:
                logger.warning(f"[MLTrainer] {name}/{tf} : aucun symbole disponible")
                return

            sp = strat_params.get(name, {})
            gate_cfg = ml_policy.GateConfig.from_params(sp)
            need = int(strat.min_bars_required(strat_params))
            gate_min_n = gate_cfg.holdout_bars + gate_cfg.min_window_bars
            fetch_n = max(need + 200, 2 * need, gate_min_n, int(sp.get("live_fetch_bars", 0)), 1000)

            logger.info(
                f"[MLTrainer] {name}/{tf} : fetch {symbol} — demande {fetch_n} bougies "
                f"(min stratégie={need}, plancher gate={gate_min_n})"
            )
            df = scanner.fetch_ohlcv(symbol, tf, fetch_n)
            got = 0 if df is None else len(df)
            if df is None or got < gate_min_n:
                logger.warning(
                    f"[MLTrainer] {name}/{tf} : données insuffisantes pour {symbol} — "
                    f"reçu {got} bougies, requis ≥{gate_min_n} pour évaluer le gate "
                    f"(le store local a-t-il un historique suffisant ? "
                    f"sinon laissez tourner le live un moment avant de réentraîner)"
                )
                return
            logger.info(
                f"[MLTrainer] {name}/{tf} : {got} bougies dispo pour {symbol} — "
                f"entraînement + gate en cours…"
            )
            base_dir = getattr(strat, "model_dir", "models") or "models"
            # Verrou tenu sur TOUT maybe_refresh (fit + score_holdout + éventuel
            # rollback) : l'inférence concurrente ne doit jamais voir un état
            # ML partiellement muté.
            with self._ml_lock:
                result = ml_policy.maybe_refresh(
                    strat, symbol, tf, df, params=sp,   # recipe dérivé de la liaison
                    gate_cfg=gate_cfg, source="live", base_dir=base_dir,
                )
            self._log_gate_result(name, tf, result)
        except Exception as e:
            logger.error(f"[MLTrainer] Réentraînement {name}/{tf} KO : {e}")

    @staticmethod
    def _log_gate_result(name: str, tf: str, result: dict) -> None:
        decision = result.get("decision")
        reason   = result.get("reason", "")
        if decision in ("promote", "initial"):
            logger.info(
                f"[MLTrainer] {name}/{tf} : {decision} — {reason} "
                f"(version={result.get('published_version')})"
            )
        elif decision == "keep":
            logger.info(f"[MLTrainer] {name}/{tf} : candidat rejeté — {reason}")
        elif decision == "skipped":
            logger.info(f"[MLTrainer] {name}/{tf} : {reason} (n_bars={result.get('n_bars')})")
        else:
            logger.warning(f"[MLTrainer] {name}/{tf} : {decision} — {reason}")

    # ── Helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_symbol(scanner) -> Optional[str]:
        """Symbole dont on prend les bougies pour ENTRAÎNER — préfère BTC.

        Ce n'est pas le symbole « du modèle » : l'artefact produit sert ensuite
        tous les symboles du scanner (``signal_pipeline.collect`` boucle sur
        ``get_symbols()`` avec la même instance de stratégie), et le registre
        ne range pas par symbole. Préférer BTC est un choix de **jeu
        d'entraînement**, mesuré : sur 18 cellules de la matrice de transfert
        (``scripts/measure_symbol_transfer.py``), aucune ne montre qu'un
        symbole gagnerait à son propre modèle, et BTC est celui dont
        l'historique est le plus profond et le plus dense.

        ``None`` si aucun scanner ou aucun symbole disponible.
        """
        if scanner is None:
            return None
        try:
            symbols = scanner.get_symbols()
        except Exception as e:
            logger.warning(f"[MLTrainer] get_symbols() KO : {e}")
            return None
        return next((s for s in symbols if "BTC" in s), symbols[0] if symbols else None)

    @staticmethod
    def _loaded_auc(strat, tf: str) -> float:
        """AUC à journaliser après ``load_model`` — jamais une exception.

        ``_best_auc_per_tf`` n'existe que sur les stratégies MLBackendMixin
        (et quelques presets historiques). ``smc_ml_edge`` et tout
        ``BaseStrategyML`` autonome n'ont pas cet attribut : y accéder
        cassait l'init de LiveTrader (serveur web seul, ``trader: false``).
        """
        auc_map = getattr(strat, "_best_auc_per_tf", None)
        if not isinstance(auc_map, dict):
            return 0.0
        try:
            return float(auc_map.get(tf, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _freshness_warning(art, interval_h: float, stale_factor: float = _STALE_FACTOR) -> Optional[str]:
        """Message si le modèle chargé est trop vieux par rapport à
        l'intervalle de réentraînement configuré — ``None`` si frais ou si la
        fraîcheur n'est pas mesurable (artefact sans date d'entraînement)."""
        if not art.train_end:
            return f"modèle sans provenance datée (version={art.version_id}) — fraîcheur non mesurable"
        try:
            train_end_dt = datetime.strptime(art.train_end, "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            return None
        age_hours = (datetime.utcnow() - train_end_dt).total_seconds() / 3600.0
        if interval_h > 0 and age_hours > stale_factor * interval_h:
            return (f"modèle vieux de {age_hours / 24:.1f}j "
                    f"(> {stale_factor:.0f}× l'intervalle de réentraînement {interval_h:.1f}h) "
                    f"— le scheduler a-t-il tourné récemment ?")
        return None

    @staticmethod
    def _supported_tfs(strat, requested_tfs: List[str]) -> List[str]:
        """
        Retourne les TFs à gérer pour cette stratégie :
        intersection entre les TFs demandés et ceux supportés par la stratégie.
        Si la stratégie ne déclare aucun TF, tous les TFs demandés sont acceptés.
        """
        supported = getattr(strat, "timeframes", None) or []
        if not supported:
            return list(requested_tfs)
        return [tf for tf in requested_tfs if tf in supported] or [supported[0]]
