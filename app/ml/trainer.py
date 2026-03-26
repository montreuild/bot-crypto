"""MLStrategyTrainer — gestion du cycle de vie des modèles ML (chargement, scheduling, réentraînement)."""
import logging
import os
import threading
import time
from typing import Dict, List

logger = logging.getLogger(__name__)


class MLStrategyTrainer:
    """Gère le cycle de vie des modèles BaseStrategyML (chargement, scheduling, réentraînement)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        # Clé : "{name}@{tf}" — timer indépendant par (stratégie, timeframe)
        self._retrain_at: Dict[str, float] = {}

    # ── Démarrage ─────────────────────────────────────────────────────────
    def load_models(self, strategies: dict, timeframes) -> None:
        """Charge les modèles persistés et active managed_externally pour chaque (stratégie, TF)."""
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

            for tf in self._supported_tfs(strat, timeframes):
                key  = f"{name}@{tf}"
                path = self._model_path(strat, name, tf)
                if strat.load_model(path):
                    logger.info(f"[MLTrainer] {name}/{tf} : modèle chargé (AUC={strat._best_auc_per_tf.get(tf, 0):.4f})")
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
                threading.Thread(
                    target=self._retrain_thread,
                    args=(name, strat, strat_params, tf, scanner),
                    daemon=True,
                ).start()

    # ── Thread interne ─────────────────────────────────────────────────────
    def _retrain_thread(self, name: str, strat, strat_params: dict,
                        tf: str, scanner) -> None:
        """Fetch OHLCV → fit → save_model pour un TF donné (thread daemon)."""
        logger.info(f"[MLTrainer] Début réentraînement {name}/{tf}…")
        try:
            symbols = scanner.get_symbols()
            symbol  = next((s for s in symbols if "BTC" in s), symbols[0] if symbols else None)
            if not symbol:
                logger.warning(f"[MLTrainer] {name}/{tf} : aucun symbole disponible")
                return
            df = scanner.fetch_ohlcv(symbol, tf, 1000)
            if df is None or len(df) < strat.min_bars_required(strat_params):
                logger.warning(f"[MLTrainer] {name}/{tf} : données insuffisantes ({symbol}/{tf})")
                return
            strat.fit(df, strat_params)
            strat.save_model(self._model_path(strat, name, tf))
            logger.info(f"[MLTrainer] {name}/{tf} réentraîné — AUC={strat._best_auc_per_tf.get(tf, 0):.4f}")
        except Exception as e:
            logger.error(f"[MLTrainer] Réentraînement {name}/{tf} KO : {e}")

    # ── Helpers ────────────────────────────────────────────────────────────
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

    @staticmethod
    def _model_path(strat, name: str, tf: str) -> str:
        return os.path.join(strat.model_dir, f"{name}_{tf}.pkl")
