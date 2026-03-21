"""
Stratégie Signal Consensus — Meta-stratégie combinant toutes les stratégies non-ML

Principe :
  Instancie chaque stratégie individuelle (rule-based), récupère leurs signaux,
  puis agrège via un vote pondéré pour produire un signal de consensus.

Pondération par stratégie :
  composite_score  : 1.5  (agrège déjà plusieurs indicateurs — signal de référence)
  trend            : 1.2  (trend following éprouvé, fiable sur tendances claires)
  fear_momentum    : 1.2  (setups haute conviction — rebond sur capitulation)
  multi_tf_sr      : 1.1  (confluence multi-timeframe Support/Résistance)
  supertrend_macd  : 1.0  (confirmation tendance via SuperTrend + MACD)
  breakout         : 1.0  (rupture Donchian + expansion ATR)
  pullback_trend   : 1.0  (entrée en pullback dans la tendance)
  fft_spectral     : 0.8  (cycles spectraux — expérimental, poids réduit)

Agrégation :
  - Chaque stratégie vote long ou short avec son score × son poids
  - Votes long et short sont séparés
  - Minimum de consensus requis (défaut : 2 stratégies dans la même direction)
  - Score final = moyenne pondérée + bonus de convergence (+0.02 par strat supplémentaire)
  - Toit du score : 0.95

Raison : la raison détaille le vote de chaque sous-stratégie pour la transparence.
"""

import importlib
import logging
from typing import Dict, Any, List, Tuple

import polars as pl

from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


# ── Pondérations des sous-stratégies ─────────────────────────────────────────
_STRATEGY_WEIGHTS: Dict[str, float] = {
    "composite_score": 1.5,
    "trend":           1.2,
    "fear_momentum":   1.2,
    "multi_tf_sr":     1.1,
    "supertrend_macd": 1.0,
    "breakout":        1.0,
    "pullback_trend":  1.0,
    "fft_spectral":    0.8,
}


def _load_sub_strategies() -> List[Tuple[str, BaseStrategy]]:
    """Charge dynamiquement toutes les sous-stratégies source."""
    strategies = []
    for name in _STRATEGY_WEIGHTS:
        try:
            mod = importlib.import_module(f"app.strategies.{name}")
            cls = getattr(mod, "Strategy", None)
            if cls is not None:
                strategies.append((name, cls()))
        except Exception as exc:
            logger.warning(f"[signal_consensus] Impossible de charger '{name}': {exc}")
    return strategies


class Strategy(BaseStrategy):
    """
    Meta-stratégie qui agrège les signaux de toutes les stratégies non-ML
    via un vote pondéré et retourne un signal de consensus.
    """

    name = "signal_consensus"

    timeframes: List[str] = ["5m", "15m", "1h", "4h", "1d"]

    param_space: Dict[str, List] = {
        "min_consensus":   [2, 3, 4],
        "score_threshold": [0.50, 0.55, 0.60],
        "consensus_bonus": [0.01, 0.02, 0.03],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        # Chargement unique au démarrage pour éviter les imports répétés
        self._sub_strategies: List[Tuple[str, BaseStrategy]] = _load_sub_strategies()

    def min_bars_required(self, params: dict = None) -> int:
        # La plus contraignante : EMA200 + marge pour les stratégies sous-jacentes
        return 250

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("signal_consensus", {})
        min_consensus   = int(p.get("min_consensus",   2))
        score_threshold = float(p.get("score_threshold", 0.55))
        consensus_bonus = float(p.get("consensus_bonus", 0.02))

        if df is None or len(df) < self.min_bars_required(params):
            return {
                "score":  0.0,
                "side":   "none",
                "name":   self.name,
                "reason": f"Données insuffisantes ({len(df) if df is not None else 0} barres < {self.min_bars_required(params)})",
            }

        # ── Collecte des votes ────────────────────────────────────────────────
        votes_long:  List[Tuple[float, float]] = []   # (weight, score)
        votes_short: List[Tuple[float, float]] = []
        detail_parts: List[str] = []

        for strat_name, inst in self._sub_strategies:
            weight = _STRATEGY_WEIGHTS.get(strat_name, 1.0)
            try:
                result = inst.score(df, params, df_htf=df_htf, symbol=symbol)
                side   = result.get("side", "none")
                sc     = float(result.get("score") or 0.0)

                if side == "long" and sc >= score_threshold:
                    votes_long.append((weight, sc))
                    detail_parts.append(f"▲{strat_name}({sc:.2f})")
                elif side == "short" and sc >= score_threshold:
                    votes_short.append((weight, sc))
                    detail_parts.append(f"▼{strat_name}({sc:.2f})")
                else:
                    detail_parts.append(f"—{strat_name}")
            except Exception as exc:
                logger.debug(f"[signal_consensus] Erreur {strat_name}: {exc}")
                detail_parts.append(f"?{strat_name}")

        n_long  = len(votes_long)
        n_short = len(votes_short)
        n_total = len(self._sub_strategies)

        # ── Seuil minimum de consensus ────────────────────────────────────────
        if n_long < min_consensus and n_short < min_consensus:
            reason = (
                f"Consensus insuffisant ({n_long}L/{n_short}S sur {n_total}) — "
                + " · ".join(detail_parts)
            )
            return {"score": 0.0, "side": "none", "name": self.name, "reason": reason}

        # ── Signal LONG ───────────────────────────────────────────────────────
        if n_long >= n_short and n_long >= min_consensus:
            total_w = sum(w for w, _ in votes_long)
            avg_sc  = sum(w * s for w, s in votes_long) / total_w
            # Bonus de convergence : +consensus_bonus par strat au-delà du minimum
            bonus       = min((n_long - min_consensus) * consensus_bonus, 0.12)
            final_score = min(round(avg_sc + bonus, 3), 0.95)
            reason = (
                f"Consensus LONG {n_long}/{n_total} strat · "
                + " · ".join(detail_parts)
            )
            return {"score": final_score, "side": "long",  "name": self.name, "reason": reason}

        # ── Signal SHORT ──────────────────────────────────────────────────────
        if n_short >= min_consensus:
            total_w = sum(w for w, _ in votes_short)
            avg_sc  = sum(w * s for w, s in votes_short) / total_w
            bonus       = min((n_short - min_consensus) * consensus_bonus, 0.12)
            final_score = min(round(avg_sc + bonus, 3), 0.95)
            reason = (
                f"Consensus SHORT {n_short}/{n_total} strat · "
                + " · ".join(detail_parts)
            )
            return {"score": final_score, "side": "short", "name": self.name, "reason": reason}

        # ── Aucun consensus net ───────────────────────────────────────────────
        reason = (
            f"Signal partagé ({n_long}L/{n_short}S) — "
            + " · ".join(detail_parts)
        )
        return {"score": 0.0, "side": "none", "name": self.name, "reason": reason}
