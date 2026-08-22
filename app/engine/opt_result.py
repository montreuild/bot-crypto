"""Agrégation du meilleur essai d'une recherche (DETTE-04c).

Extrait d'`optimizer_search.py`. Le modèle de coûts est recalculé depuis la
config et la venue résolue plutôt que capturé sur un trial : tous partagent le
même contexte, et un trial peut avoir échoué.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class OptimizerResultMixin:
    """Contrat d'hôte : `OptimizerSearchEngine` porte les essais et le contexte."""

    cfg: dict
    strategy_name: str
    symbol: str
    timeframe: Any
    results: list
    _penalized_score: Any

    def _best_result(self) -> dict:
        if not self.results:
            return {
                "error": ("Aucun trial complété (workers tous KO — ex: OOM LightGBM). "
                          "Réduisez --jobs / n_jobs, ou diminuez le nombre de bougies."),
                "failed": True,
                "completed_trials": 0,
            }
        best = max(self.results, key=self._penalized_score)
        # Top 5 par score final
        sorted_results = sorted(self.results, key=self._penalized_score, reverse=True)
        top5 = [
            {
                "is_score":    round(r["is_score"], 4),
                "oos_score":   round(r["oos_score"], 4),
                "final_score": round(self._penalized_score(r), 4),
                "oos_pnl":     round(r["oos_pnl"], 2),
                "oos_wr":      round(r.get("oos_wr", 0.0), 1),
                "oos_dd":      round(r.get("oos_dd", 0.0), 2),
                "overfit":     round(r.get("overfit", 1.0), 2),
            }
            for r in sorted_results[:5]
        ]
        return {
            "strategy":       self.strategy_name,
            "timeframe":      self.timeframe,
            "symbol":         self.symbol,
            "best_params":    best["params"],
            "best_is_score":  best["is_score"],
            "best_oos_score": self._penalized_score(best),
            "best_is_pnl":    best["is_pnl"],
            "best_oos_pnl":   best["oos_pnl"],
            "best_is_sharpe": best["is_sharpe"],
            "best_oos_sharpe":best["oos_sharpe"],
            "best_is_trades": best["is_trades"],
            "best_oos_trades":best["oos_trades"],
            "best_oos_wr":    round(best.get("oos_wr", 0.0), 1),
            # OPT-01 : discriminants de qualité du gate d'application.
            "best_oos_pf":         best.get("oos_pf"),
            "best_oos_expectancy": best.get("oos_expectancy"),
            # O-01 : alias honnêtes — cette tranche a servi à sélectionner.
            "best_val_score": self._penalized_score(best),
            "best_val_pnl":   best["oos_pnl"],
            "best_val_sharpe":best["oos_sharpe"],
            "best_val_trades":best["oos_trades"],
            "best_val_wr":    round(best.get("oos_wr", 0.0), 1),
            "best_is_wr":     round(best.get("is_wr", 0.0), 1),
            "best_oos_dd":    round(best.get("oos_dd", 0.0), 2),
            "best_oos_alpha": round(best["oos_alpha"], 4) if best.get("oos_alpha") is not None else None,
            "overfit":        best.get("overfit", 1.0),
            "stop_reason":    getattr(self, "stop_reason", None),
            "trials_failed":  getattr(self, "trials_failed", 0),
            "trials_done":    len(self.results),
            "n_trials":       len(self.results),
            "top5":           top5,
            # S11 : contexte d'exécution facturé pendant toute l'optimisation
            # (venue, spot/margin, levier, détail des frais, emprunt). Sans lui,
            # un `oos_score` n'est pas comparable d'un run à l'autre : deux
            # scores très différents peuvent ne différer que par la venue.
            "cost_model":     self._cost_model(),
        }

    def _cost_model(self) -> dict:
        """Modèle de coûts de CE couple (symbole, timeframe).

        Recalculé depuis la config et la venue résolue plutôt que capturé sur un
        trial : tous les trials partagent le même contexte (seuls les params de
        stratégie varient), et un trial peut avoir échoué.
        """
        from app.core.bot_identity import resolve_venue
        from app.core.execution import cost_model
        try:
            venue = resolve_venue(self.cfg, tf=self.timeframe, symbol=self.symbol)
            return cost_model(self.cfg, venue)
        except Exception as e:      # pragma: no cover — jamais bloquant
            logger.debug(f"[Optimizer] modèle de coûts indisponible : {e}")
            return {}

    # ── Dispatch & two-phase ML (#6) ──────────────────────────────────────────
