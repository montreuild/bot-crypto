"""Stratégie Signal Consensus — vote pondéré de toutes les stratégies rule-based."""


import importlib
import logging
from typing import Any, Dict, List, Tuple

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
        "min_consensus":   [1, 2, 3],
        "score_threshold": [0.45, 0.50, 0.55],
        "consensus_bonus": [0.01, 0.02, 0.03],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        # Chargement unique au démarrage pour éviter les imports répétés
        self._sub_strategies: List[Tuple[str, BaseStrategy]] = _load_sub_strategies()
        # ── Pré-calcul backtest (cf. prepare_for_backtest / _precompute_votes) ──
        # Cache des votes bruts des sous-stratégies indexé par timestamp de barre.
        # Préfixe ``_bt_`` : capturé par le snapshot des workers d'optimisation
        # (app.engine.optimizer._snap_state) → calculé une fois par worker puis
        # réutilisé par tous les trials, qui ne font varier que les paramètres de
        # consensus (sans impact sur les votes des sous-stratégies).
        self._bt_full_df = None
        self._bt_params: dict = None
        self._bt_votes: Dict[str, List[Tuple[str, float, str, float]]] = {}

    def min_bars_required(self, params: dict | None = None) -> int:
        # La plus contraignante : EMA200 + marge pour les stratégies sous-jacentes
        return 250

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Propage le hook aux sous-stratégies puis pré-calcule les votes.

        1. Propagation : le backtest n'appelle ``prepare_for_backtest`` que sur la
           stratégie enregistrée (ici le consensus). Sans propagation, les
           sous-stratégies instrumentées (ex: supertrend_macd et sa série
           SuperTrend pré-calculée) retomberaient sur un recalcul O(n) par barre
           × 8 stratégies. On transmet aussi symbol/tf pour le FeatureStore.

        2. Pré-calcul (``_precompute_votes``) : on évalue chaque sous-stratégie
           **une seule fois par barre** sur la fenêtre complète et on mémorise le
           vote brut (side, score). ``score()`` se réduit alors à un lookup O(1)
           + l'agrégation pondérée. Dans l'optimiseur, ce cache (préfixe ``_bt_``)
           est capturé par le snapshot du worker et partagé par tous les trials :
           les votes des sous-stratégies ne dépendent pas des paramètres de
           consensus optimisés (min_consensus / score_threshold / consensus_bonus),
           donc on évite de tout recalculer à chaque trial (gain ×n_trials).
        """
        for name, inst in self._sub_strategies:
            prep = getattr(inst, "prepare_for_backtest", None)
            if not callable(prep):
                continue
            try:
                inst._bt_symbol = getattr(self, "_bt_symbol", None)
                inst._bt_tf = getattr(self, "_bt_tf", None)
                prep(df)
            except Exception as exc:
                logger.debug(f"[signal_consensus] prepare_for_backtest({name}) KO: {exc}")

        self._bt_full_df = df
        try:
            self._precompute_votes(df)
        except Exception as exc:
            logger.warning(f"[signal_consensus] pré-calcul des votes KO (fallback live): {exc}")
            self._bt_votes = {}

    def reset_model(self) -> None:
        """Propage le reset aux sous-stratégies (utilisé entre IS/OOS par l'optimiseur)."""
        self._bt_votes = {}
        for name, inst in self._sub_strategies:
            r = getattr(inst, "reset_model", None)
            if callable(r):
                try:
                    r()
                except Exception:
                    pass

    # ── Pré-calcul / collecte des votes ──────────────────────────────────────
    def _gather_votes(self, df: pl.DataFrame, params: dict,
                      df_htf, symbol: str) -> List[Tuple[str, float, str, float]]:
        """Évalue chaque sous-stratégie sur la dernière barre de ``df``.

        Retourne une liste de tuples ``(strat_name, weight, side, score)`` —
        données brutes indépendantes des seuils de consensus, donc réutilisables
        quel que soit le paramétrage du consensus.
        """
        votes: List[Tuple[str, float, str, float]] = []
        for strat_name, inst in self._sub_strategies:
            weight = _STRATEGY_WEIGHTS.get(strat_name, 1.0)
            try:
                result = inst.score(df, params, df_htf=df_htf, symbol=symbol)
                side   = result.get("side", "none")
                sc     = float(result.get("score") or 0.0)
            except Exception as exc:
                logger.debug(f"[signal_consensus] Erreur {strat_name}: {exc}")
                side, sc = "error", 0.0
            votes.append((strat_name, weight, side, sc))
        return votes

    def _precompute_votes(self, df: pl.DataFrame) -> None:
        """Pré-calcule les votes bruts de chaque barre une seule fois.

        Évaluation par barre déterministe (état des sous-stratégies réinitialisé
        en amont) → le cache est indépendant du chemin de positions du backtest,
        ce qui le rend partageable entre tous les trials de l'optimiseur.
        """
        if df is None or "time" not in df.columns:
            self._bt_votes = {}
            return
        # Colonnes _pre_* (idempotent + mémoïsé) : les sous-stratégies lisent ces
        # indicateurs causaux en O(1) au lieu de les recalculer barre par barre.
        # ``prepare_for_backtest`` est invoqué avant le precompute de Backtester.run,
        # donc on l'applique ici pour ne pas évaluer sur un df brut (plus lent).
        try:
            from app.core.indicators import precompute_df as _precompute_df
            df = _precompute_df(df)
        except Exception as exc:
            logger.debug(f"[signal_consensus] precompute_df KO: {exc}")
        n = len(df)
        start = self.min_bars_required(self._bt_params) - 1
        if n <= start:
            self._bt_votes = {}
            return
        # État propre pour une évaluation par barre déterministe.
        self.reset_model()
        symbol = getattr(self, "_bt_symbol", None) or ""
        times  = df["time"]
        votes_map: Dict[str, List[Tuple[str, float, str, float]]] = {}
        for i in range(start, n):
            window = df[: i + 1]
            votes_map[str(times[i])] = self._gather_votes(
                window, self._bt_params, None, symbol)
        self._bt_votes = votes_map

    def _build_consensus(self, votes: List[Tuple[str, float, str, float]],
                         min_consensus: int, score_threshold: float,
                         consensus_bonus: float) -> Dict[str, Any]:
        """Agrège les votes bruts en un signal de consensus pondéré."""
        votes_long:  List[Tuple[float, float]] = []   # (weight, score)
        votes_short: List[Tuple[float, float]] = []
        detail_parts: List[str] = []

        for strat_name, weight, side, sc in votes:
            if side == "long" and sc >= score_threshold:
                votes_long.append((weight, sc))
                detail_parts.append(f"▲{strat_name}({sc:.2f})")
            elif side == "short" and sc >= score_threshold:
                votes_short.append((weight, sc))
                detail_parts.append(f"▼{strat_name}({sc:.2f})")
            elif side == "error":
                detail_parts.append(f"?{strat_name}")
            else:
                detail_parts.append(f"—{strat_name}")

        n_long  = len(votes_long)
        n_short = len(votes_short)
        n_total = len(votes)

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

    def score(self, df: pl.DataFrame, params: dict | None = None,
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
                "reason": (f"Données insuffisantes ({len(df) if df is not None else 0} barres "
                          f"< {self.min_bars_required(params)})"),
            }

        # ── Votes bruts : cache de pré-calcul si dispo, sinon collecte live ────
        # En backtest/optimiseur, ``prepare_for_backtest`` a rempli ``_bt_votes``
        # → lookup O(1). En live (pas de prepare), on évalue les sous-stratégies
        # à la volée avec le df_htf réel.
        votes = None
        if self._bt_votes and "time" in df.columns:
            votes = self._bt_votes.get(str(df["time"][-1]))
        if votes is None:
            votes = self._gather_votes(df, params, df_htf, symbol)

        return self._build_consensus(
            votes, min_consensus, score_threshold, consensus_bonus)
