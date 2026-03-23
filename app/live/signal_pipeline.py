"""
SignalPipeline — Collecte, scoring et ranking des signaux de trading.

Extrait la logique de scan de LiveTrader pour une meilleure séparation des responsabilités.
Chaque signal est associé à un slot "strategy::tf".
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import polars as pl

from app.core.indicators import atr_val as _compute_atr

logger = logging.getLogger(__name__)

# Timeframe supérieur (HTF) pour le filtre de tendance
_HTF_MAP = {
    "1m": "15m", "3m": "30m", "5m": "1h", "15m": "4h",
    "30m": "4h", "1h": "4h",  "2h": "1d", "4h": "1d", "1d": "1d",
}


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
        pipeline = SignalPipeline(loaded_strategies, cfg, scanner)
        signals = pipeline.collect(
            symbols, active_per_tf, ohlcv_fn, open_positions, cooldowns, ml
        )
        # signals est trié par score décroissant, dédupliqué par (symbol, side)
    """

    def __init__(self, loaded_strategies: Dict[str, object], cfg: dict, scanner):
        self._strategies   = loaded_strategies
        self._cfg          = cfg
        self._scanner      = scanner
        self._threshold    = cfg["trading"]["score_threshold"]
        self._strat_thresholds: Dict[str, float] = {}
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
                signal_log,
                ml=None) -> List[Signal]:
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
                    strat_name = entry.get("name", "")
                    strategy   = self._strategies.get(strat_name)
                    if strategy is None:
                        continue

                    slot_key = f"{strat_name}::{tf}"
                    pos_key  = f"{symbol}::{strat_name}::{tf}"
                    if pos_key in open_positions:
                        continue

                    sig = self._score_symbol_slot(
                        symbol, df, df_htf, strategy, entry, tf, slot_key, signal_log, ml
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
                           signal_log,
                           ml=None) -> Optional[Signal]:
        strat_name = entry.get("name", "")
        try:
            from app.live.live_trader import _merge_params
            params = _merge_params(
                self._cfg.get("strategy_params", {}),
                entry.get("params", {})
            )
            signal = strategy.score(df, params, df_htf=df_htf, symbol=symbol)
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

        # ML blending
        if ml and ml.is_ready:
            try:
                regime = self._scanner.detect_regime(df)
                score, side = ml.blend_signal(score, signal["side"], df, regime)
                if score < threshold:
                    return None
                signal["score"] = score
                signal["side"]  = side
            except Exception as e:
                logger.debug(f"[Pipeline] ML blend KO {strat_name}/{symbol} : {e}")

        atr   = _compute_atr(df)
        price = 0.0
        try:
            ticker = self._scanner.exchange.fetch_ticker(symbol) if hasattr(self._scanner, "exchange") else None
            if ticker:
                price = float(ticker.get("last", 0))
        except Exception:
            pass

        return Signal(
            symbol=symbol,
            side=signal["side"],
            score=float(signal.get("score", score)),
            strategy=strat_name,
            tf=tf,
            slot_key=slot_key,
            params=entry.get("params", {}),
            atr=float(atr) if atr else 0.0,
            price=price,
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
