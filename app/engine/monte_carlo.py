"""Monte-Carlo — risques de séquence et d'échantillonnage sur les trades.

Extrait de ``app/engine/backtest.py`` (ARCH-010) pour réduire la taille du
module de backtest. La classe ``MonteCarlo`` calcule deux familles de
statistiques avec la méthode adaptée (BT-02) :

- **Risque de SÉQUENCE** (``max_dd_p95``, ``prob_ruin_10pct``) : permutation
  des PnL (sans remise). La somme est invariante — seul l'ORDRE change —
  c'est exactement ce qu'on veut pour la distribution du drawdown.
- **Risque d'ÉCHANTILLONNAGE** (``final_equity_mean/p5/p95``,
  ``prob_profit``) : bootstrap AVEC remise (rng.choice, replace=True).
  La permutation donnait ici une distribution dégénérée : équité finale
  identique à chaque run (p5 = p95, prob_profit ∈ {0, 100}).

Ré-exportée depuis ``app.engine.backtest`` pour compatibilité ascendante.
"""
from typing import List

import numpy as np

from app.core.sanitize import safe_float as _sf


# ── Monte-Carlo ──
class MonteCarlo:
    """Deux familles de statistiques, calculées avec la méthode adaptée (BT-02) :

    - **Risque de SÉQUENCE** (``max_dd_p95``, ``prob_ruin_10pct``) : permutation
      des PnL (sans remise). La somme est invariante — seul l'ORDRE change —
      c'est exactement ce qu'on veut pour la distribution du drawdown.
    - **Risque d'ÉCHANTILLONNAGE** (``final_equity_mean/p5/p95``,
      ``prob_profit``) : bootstrap AVEC remise (rng.choice, replace=True).
      La permutation donnait ici une distribution dégénérée : équité finale
      identique à chaque run (p5 = p95, prob_profit ∈ {0, 100}).
    """

    def __init__(self, n_runs: int = 200, confidence: float = 0.95):
        self.n_runs    = n_runs
        self.confidence = confidence

    def run(self, trades: List[dict], initial_capital: float) -> dict:
        closed = [t for t in trades if t.get("status", "").startswith("closed")]
        if not closed:
            return {"error": "Aucun trade fermé"}

        pnls   = np.array([t["pnl"] for t in closed])
        finals = []
        max_dds= []
        rng    = np.random.default_rng(42)

        for _ in range(self.n_runs):
            # Séquence (permutation) → drawdown/ruine.
            shuffled = rng.permutation(pnls)
            equity   = np.concatenate([[initial_capital], initial_capital + np.cumsum(shuffled)])
            peak = np.maximum.accumulate(equity)
            dd   = (equity - peak) / np.where(peak > 0, peak, 1) * 100
            max_dds.append(float(dd.min()))
            # Échantillonnage (bootstrap avec remise) → équité finale.
            resampled = rng.choice(pnls, size=len(pnls), replace=True)
            finals.append(float(initial_capital + resampled.sum()))

        return {
            "runs":               self.n_runs,
            "confidence":         self.confidence,
            "final_equity_mean":  round(_sf(float(np.mean(finals)),  0.0), 2),
            "final_equity_p5":    round(_sf(float(np.percentile(finals,  5)),  0.0), 2),
            "final_equity_p95":   round(_sf(float(np.percentile(finals, 95)),  0.0), 2),
            # max_dds sont des drawdowns NÉGATIFS (dd.min()). Le 95ᵉ
            # percentile d'une série négative est le *meilleur* cas (le moins
            # négatif). Le quantile de risque à 95 % est la queue basse :
            # percentile 5, puis valeur absolue pour l'affichage.
            "max_dd_p95":         round(_sf(abs(float(np.percentile(max_dds, 5))), 0.0), 2),
            "prob_profit":        round(sum(1 for f in finals if f > initial_capital) / self.n_runs * 100, 1),
            "prob_ruin_10pct":    round(sum(1 for d in max_dds if d < -10) / self.n_runs * 100, 1),
            "mc_version":         2,
        }
