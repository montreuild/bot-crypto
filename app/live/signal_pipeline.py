"""SignalPipeline — collecte, scoring et ranking des signaux par slot strategy::tf."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import polars as pl

from app.core.timeframes import HTF_MAP as _HTF_MAP
from app.live.utils import _merge_params

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    side: str               # "long" | "short"
    score: float
    strategy: str
    tf: str
    slot_key: str           # "strategy::tf"
    params: dict
    atr: float
    price: float
    reason: str = ""
    name: str = ""          # alias strategy (pour compatibilité position_mixin)

    def __post_init__(self):
        self.name = self.strategy

    def to_signal_dict(self) -> dict:
        """Convertit en dict compatible avec l'API de position_mixin._open_position."""
        return {
            "side":   self.side,
            "score":  self.score,
            "name":   self.strategy,
            "reason": self.reason,
        }


class SignalPipeline:
    """
    Pipeline de collecte et de ranking des signaux.

    Usage :
        pipeline = SignalPipeline(loaded_strategies, cfg)
        signals = pipeline.collect(
            symbols, active_per_tf, ohlcv_fn, open_positions, cooldowns
        )
        # signals est trié par score décroissant, dédupliqué par (symbol, side)
    """

    def __init__(self, loaded_strategies: Dict[str, object], cfg: dict):
        self._strategies   = loaded_strategies
        self._cfg          = cfg
        self._threshold    = cfg["trading"]["score_threshold"]
        self._strat_thresholds: Dict[str, float] = {}
        self._score_timeout = float(cfg.get("trading", {}).get("signal_score_timeout", 5))
        # Executor partagé pour borner le temps de scoring d'une stratégie sans
        # recréer un ThreadPoolExecutor à chaque appel (réutilisé sur toute la
        # vie du process). Plusieurs workers pour qu'un scoring lent/figé
        # n'empêche pas immédiatement les suivants.
        self._score_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="sigscore"
        )
        sp = cfg.get("strategy_params", {})
        for name in loaded_strategies:
            self._strat_thresholds[name] = float(
                sp.get(name, {}).get("score_threshold", self._threshold)
            )

    def update_strategies(self, loaded_strategies: Dict[str, object]):
        """Mise à jour à chaud après hot-reload des stratégies."""
        self._strategies = loaded_strategies
        sp = self._cfg.get("strategy_params", {})
        for name in loaded_strategies:
            if name not in self._strat_thresholds:
                self._strat_thresholds[name] = float(
                    sp.get(name, {}).get("score_threshold", self._threshold)
                )

    def collect(self,
                symbols: List[str],
                active_per_tf: Dict[str, List[dict]],
                ohlcv_fn: Callable[[str, str], Optional[pl.DataFrame]],
                open_positions: dict,
                cooldowns: dict,
                signal_log) -> List[Signal]:
        """
        Collecte tous les signaux valides pour les (symbol, tf, strategy) actifs.
        Retourne les signaux rankés (tri décroissant par score).
        """
        raw: List[Signal] = []

        for tf, entries in active_per_tf.items():
            if not entries:
                continue
            htf = _HTF_MAP.get(tf)
            htf_same = htf == tf

            for symbol in symbols:
                # Cooldown après stop-loss
                if time.time() < cooldowns.get(symbol, 0):
                    continue

                df = ohlcv_fn(symbol, tf)
                if df is None:
                    continue

                df_htf = ohlcv_fn(symbol, htf) if htf and not htf_same else None

                for entry in entries:
                    # Config par symbole : une entrée ne s'applique qu'à SON
                    # symbole (rétro-compat : une entrée sans symbole s'applique
                    # à tous).
                    e_sym = entry.get("symbol", "")
                    if e_sym and e_sym != symbol:
                        continue
                    strat_name = entry.get("name", "")
                    strategy   = self._strategies.get(strat_name)
                    if strategy is None:
                        continue

                    slot_key = (f"{strat_name}::{tf}::{symbol}" if e_sym
                                else f"{strat_name}::{tf}")
                    pos_key  = f"{symbol}::{strat_name}::{tf}"
                    if pos_key in open_positions:
                        continue

                    sig = self._score_symbol_slot(
                        symbol, df, df_htf, strategy, entry, tf, slot_key, signal_log
                    )
                    if sig is not None:
                        raw.append(sig)

        return self.rank(raw)

    def _score_symbol_slot(self,
                           symbol: str,
                           df: pl.DataFrame,
                           df_htf: Optional[pl.DataFrame],
                           strategy,
                           entry: dict,
                           tf: str,
                           slot_key: str,
                           signal_log) -> Optional[Signal]:
        strat_name = entry.get("name", "")
        try:
            params = _merge_params(
                self._cfg.get("strategy_params", {}),
                entry.get("params", {})
            )
            future = self._score_executor.submit(
                strategy.score, df, params, df_htf=df_htf, symbol=symbol
            )
            try:
                signal = future.result(timeout=self._score_timeout)
            except FuturesTimeoutError:
                # Le worker continue jusqu'à sa fin naturelle (le pool le
                # réutilisera ensuite) ; le résultat est simplement ignoré
                # — aucune ressource partagée n'est modifiée.
                future.cancel()
                logger.error(
                    f"[Pipeline] {strat_name}/{symbol}/{tf} score timeout "
                    f"({self._score_timeout}s) — signal ignoré"
                )
                return None
        except Exception as e:
            logger.error(f"[Pipeline] {strat_name}/{symbol}/{tf} score KO : {e}")
            return None

        if signal.get("side") == "none":
            return None

        threshold = self._strat_thresholds.get(strat_name, self._threshold)
        score     = float(signal.get("score", 0))

        if score < threshold:
            signal_log.append({
                "time":      datetime.now(timezone.utc).isoformat(),
                "symbol":    symbol, "strategy": strat_name,
                "side":      signal.get("side", "?"),
                "score":     round(score, 3),
                "threshold": round(threshold, 3),
                "timeframe": tf, "status": "rejected",
                "reason":    f"score {score:.2f} < threshold {threshold:.2f}",
            })
            return None

        # Le prix d'exécution est (re)lu au moment de l'ouverture dans
        # LiveTrader._cycle (ticker frais) — inutile de fetch ici, ce qui
        # évitait un double appel fetch_ticker par signal.
        atr = float(df["_pre_atr14"][-1]) if "_pre_atr14" in df.columns else 0.0

        return Signal(
            symbol=symbol,
            side=signal["side"],
            score=float(signal.get("score", score)),
            strategy=strat_name,
            tf=tf,
            slot_key=slot_key,
            params=entry.get("params", {}),
            atr=float(atr) if atr else 0.0,
            price=0.0,
            reason=signal.get("reason", ""),
        )

    def rank(self, signals: List[Signal]) -> List[Signal]:
        """
        Trie les signaux par score décroissant.
        Déduplique par (symbol, side) : garde le meilleur score.
        """
        # Tri décroissant
        signals.sort(key=lambda s: s.score, reverse=True)

        # Déduplication par (symbol, side)
        seen: Dict[tuple, bool] = {}
        result = []
        for sig in signals:
            key = (sig.symbol, sig.side)
            if key not in seen:
                seen[key] = True
                result.append(sig)

        return result
