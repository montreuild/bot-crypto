"""
MLStrategyTrainer — helper de cycle de vie des modèles ML en production.

Responsabilités :
  - load_models()   : charge les modèles persistés au démarrage, active
                      managed_externally sur chaque stratégie BaseStrategyML.
  - any_due()       : indique si au moins une stratégie ML doit être réentraînée
                      (utilisé par live_trader._maybe_auto_optimize pour décider
                      de lancer le thread de maintenance).
  - retrain_due()   : lance le réentraînement des stratégies dont l'intervalle
                      est écoulé (threads daemon, non-bloquant).

Ce module est un helper au service de l'auto_optimizer et de l'optimiseur.
Il ne contient aucune logique de trading et n'est pas couplé à LiveTrader.
"""
import logging
import os
import threading
import time
from typing import Dict

logger = logging.getLogger(__name__)


class MLStrategyTrainer:
    """
    Gère le cycle de vie des modèles BaseStrategyML entre les sessions.

    Instancié une fois par LiveTrader.__init__ ; partagé avec _auto_opt_thread.
    La logique de scheduling (quand lancer le thread) est dans live_trader ;
    la logique de QUOI entraîner et COMMENT est ici.

    Configurable via config.yaml :
      strategy_params.<name>.retrain_interval_h  (défaut : strat.retrain_interval_h)
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._retrain_at: Dict[str, float] = {}  # name → prochain timestamp

    # ── Démarrage ─────────────────────────────────────────────────────────
    def load_models(self, strategies: dict, default_tf: str) -> None:
        """
        Parcourt les stratégies BaseStrategyML, charge les modèles persistés
        et active managed_externally=True pour désactiver le réentraînement
        inline bloquant dans la boucle de trading.
        Appelé une seule fois depuis LiveTrader.__init__.
        """
        from app.engine.engine import BaseStrategyML
        strat_params = self.cfg.get("strategy_params", {})
        for name, strat in strategies.items():
            if not isinstance(strat, BaseStrategyML):
                continue
            strat.managed_externally = True
            tf   = self._tf(strat, default_tf)
            path = self._model_path(strat, name, tf)
            if strat.load_model(path):
                logger.info(f"[MLTrainer] {name} : modèle chargé (AUC={strat._best_auc:.4f})")
            else:
                logger.info(f"[MLTrainer] {name} : pas de modèle persisté — réentraînement au prochain cycle")
            # Initialise le timer pour que le 1er cycle déclenche bien l'entraînement
            sp         = strat_params.get(name, {})
            interval_h = float(sp.get("retrain_interval_h", strat.retrain_interval_h))
            if not strat.is_trained:
                self._retrain_at[name] = 0  # immédiatement
            else:
                self._retrain_at[name] = time.time() + interval_h * 3600

    # ── Interface pour le scheduler (live_trader._maybe_auto_optimize) ─────
    def any_due(self, strategies: dict) -> bool:
        """Retourne True si au moins une stratégie ML doit être réentraînée maintenant."""
        from app.engine.engine import BaseStrategyML
        now = time.time()
        for name, strat in strategies.items():
            if isinstance(strat, BaseStrategyML) and now >= self._retrain_at.get(name, 0):
                return True
        return False

    # ── Réentraînement (appelé depuis _auto_opt_thread) ───────────────────
    def retrain_due(self, strategies: dict, scanner, default_tf: str) -> None:
        """
        Lance le réentraînement (thread daemon) de chaque stratégie ML dont
        l'intervalle est écoulé. Non-bloquant : retourne immédiatement.
        Appelé depuis live_trader._auto_opt_thread, pas depuis la boucle principale.
        """
        from app.engine.engine import BaseStrategyML
        now          = time.time()
        strat_params = self.cfg.get("strategy_params", {})
        for name, strat in strategies.items():
            if not isinstance(strat, BaseStrategyML):
                continue
            if now < self._retrain_at.get(name, 0):
                continue
            sp         = strat_params.get(name, {})
            interval_h = float(sp.get("retrain_interval_h", strat.retrain_interval_h))
            self._retrain_at[name] = now + interval_h * 3600
            tf = self._tf(strat, default_tf)
            logger.info(f"[MLTrainer] Réentraînement planifié : {name}/{tf} (intervalle={interval_h}h)")
            threading.Thread(
                target=self._retrain_thread,
                args=(name, strat, strat_params, tf, scanner),
                daemon=True,
            ).start()

    # ── Thread interne ─────────────────────────────────────────────────────
    def _retrain_thread(self, name: str, strat, strat_params: dict,
                        tf: str, scanner) -> None:
        """Fetch OHLCV → fit → save_model (thread daemon)."""
        logger.info(f"[MLTrainer] Début réentraînement {name}/{tf}…")
        try:
            symbols = scanner.get_symbols()
            symbol  = next((s for s in symbols if "BTC" in s), symbols[0] if symbols else None)
            if not symbol:
                logger.warning(f"[MLTrainer] {name} : aucun symbole disponible")
                return
            df = scanner.fetch_ohlcv(symbol, tf, 1000)
            if df is None or len(df) < strat.min_bars_required(strat_params):
                logger.warning(f"[MLTrainer] {name} : données insuffisantes ({symbol}/{tf})")
                return
            strat.fit(df, strat_params)
            strat.save_model(self._model_path(strat, name, tf))
            logger.info(f"[MLTrainer] {name} réentraîné — AUC={strat._best_auc:.4f}")
        except Exception as e:
            logger.error(f"[MLTrainer] Réentraînement {name} KO : {e}")

    # ── Helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _tf(strat, default_tf: str) -> str:
        # Utilise le timeframe configuré (default_tf) en priorité.
        # Si la stratégie ne le supporte pas, repli sur son premier TF déclaré.
        supported = getattr(strat, "timeframes", None) or []
        if not supported or default_tf in supported:
            return default_tf
        return supported[0]

    @staticmethod
    def _model_path(strat, name: str, tf: str) -> str:
        return os.path.join(strat.model_dir, f"{name}_{tf}.pkl")
