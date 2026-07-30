"""Stratégie TVR — Trend-Volatility-Regime (breakout Donchian filtré par régime).

Portage de research strategy_tvr.py vers l'interface BaseStrategy du bot :
  1. RÉGIME (1D)  : close > EMA200 ET pente EMA50 > 0 → BULL (long only) ;
                    close < EMA200 ET pente EMA50 < 0 → BEAR (short only).
  2. SIGNAL (TF)  : breakout Donchian N barres (canal sur les N barres
                    précédentes, bougie courante exclue) dans le sens du régime.
  3. ANTI-CHOP    : Kaufman Efficiency Ratio >= er_min ET ATR >= atr_expansion
                    × médiane(ATR, 100).
  4. SORTIES      : SL initial k_sl × ATR (sl_atr_mult), trailing Chandelier
                    k_trail × ATR (trail_override mode=chandelier), sortie sur
                    perte de régime (check_early_exit).

Le régime journalier est résolu dans cet ordre :
  a. df_htf (1D) fourni par le live (signal_pipeline, _HTF_MAP 4h→1d) ;
  b. resampling 1D du df d'exécution si l'historique couvre ema_slow_d jours
     (cas backtest longue période) ;
  c. fallback : EMA(ema_slow_d) et pente EMA(ema_fast_d) sur le TF d'exécution
     (cohérent avec les autres stratégies du bot qui utilisent l'EMA200 du TF).

En backtest, ``prepare_for_backtest`` pré-calcule toutes les séries causales en
une passe (Donchian shift(1), ER, ATR, régime décalé d'un jour) — ``score()``
fait ensuite un lookup O(1) par barre au lieu de recalculer sur la fenêtre.
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


# ── Séries causales (polars) ────────────────────────────────────────────────

def _atr_series(df: pl.DataFrame, n: int) -> pl.Series:
    """ATR Wilder (ewm alpha=1/n) — strictement causal."""
    h, lo, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pl.DataFrame({
        "a": h - lo,
        "b": (h - pc).abs(),
        "c": (lo - pc).abs(),
    }).select(pl.max_horizontal("a", "b", "c").alias("tr"))["tr"]
    return tr.ewm_mean(alpha=1.0 / n, adjust=False)


def _er_series(close: pl.Series, n: int) -> pl.Series:
    """Kaufman Efficiency Ratio ∈ [0,1] — déplacement net / somme des déplacements."""
    direction = (close - close.shift(n)).abs()
    volatility = close.diff().abs().rolling_sum(n)
    return (direction / (volatility + 1e-12)).fill_null(0.0).fill_nan(0.0)


def _daily_regime_series(d_close: pl.Series, ema_slow: int, ema_fast: int,
                         slope_lb: int) -> pl.Series:
    """Régime sur clôtures journalières : +1 BULL / -1 BEAR / 0 NEUTRE (non décalé)."""
    es = d_close.ewm_mean(span=ema_slow, adjust=False)
    ef = d_close.ewm_mean(span=ema_fast, adjust=False)
    slope = ef - ef.shift(slope_lb)
    bull = ((d_close > es) & (slope > 0)).fill_null(False).to_numpy().astype(bool)
    bear = ((d_close < es) & (slope < 0)).fill_null(False).to_numpy().astype(bool)
    return pl.Series(np.select([bull, bear], [1, -1], 0))


class Strategy(BaseStrategy):
    name = "tvr_trend"

    timeframes: List[str] = ["4h", "1d"]

    param_space: Dict[str, List] = {
        "donchian_n":    [40, 55, 70, 90],
        "er_period":     [20, 30, 40],
        "er_min":        [0.15, 0.20, 0.25, 0.30],
        "atr_expansion": [0.8, 0.9, 1.0, 1.1],
        "k_sl":          [2.0, 2.5, 3.0],
        "k_trail":       [2.5, 3.0, 3.5],
    }

    fixed_params: Dict[str, Any] = {
        "atr_period": 14, "ema_slow_d": 200, "ema_fast_d": 50, "slope_lookback_d": 10,
    }

    # Défauts validés par le backtest research (2019→2026)
    _DEFAULTS = dict(
        donchian_n=55, er_period=30, er_min=0.20, atr_period=14,
        atr_expansion=1.0, k_sl=2.5, k_trail=3.0,
        ema_slow_d=200, ema_fast_d=50, slope_lookback_d=10,
    )

    def __init__(self):
        self._bt_full_df: Optional[pl.DataFrame] = None
        # Cache backtest : clé = tuple des params pertinents → dict d'arrays numpy
        self._bt_cache: Dict[tuple, Dict[str, np.ndarray]] = {}

    # ── Paramètres ─────────────────────────────────────────────────────────

    def _params(self, params: dict = None) -> dict:
        p = dict(self._DEFAULTS)
        p.update((params or {}).get(self.name, {}) or {})
        return p

    def min_bars_required(self, params: dict = None) -> int:
        p = self._params(params)
        return max(int(p["donchian_n"]) + 5, 110, int(p["er_period"]) + 5)

    # ── Régime ─────────────────────────────────────────────────────────────

    def _regime_now(self, df: pl.DataFrame, p: dict, df_htf=None) -> int:
        """Régime du dernier jour clos (+1/-1/0). Voir docstring module (a/b/c)."""
        need_days = int(p["ema_slow_d"]) + int(p["slope_lookback_d"]) + 5
        # a. df_htf journalier fourni par le live
        if df_htf is not None and len(df_htf) >= need_days and "close" in df_htf.columns:
            reg = _daily_regime_series(
                df_htf["close"], int(p["ema_slow_d"]),
                int(p["ema_fast_d"]), int(p["slope_lookback_d"]))
            # Dernière bougie 1D potentiellement en cours → on prend l'avant-dernière
            return int(reg[-2]) if len(reg) >= 2 else int(reg[-1])
        # b. resampling 1D du TF d'exécution
        if "time" in df.columns:
            try:
                d = (df.sort("time")
                       .group_by_dynamic("time", every="1d")
                       .agg(pl.col("close").last()))
                if len(d) >= need_days:
                    reg = _daily_regime_series(
                        d["close"], int(p["ema_slow_d"]),
                        int(p["ema_fast_d"]), int(p["slope_lookback_d"]))
                    return int(reg[-2]) if len(reg) >= 2 else int(reg[-1])
            except Exception:
                pass
        # c. fallback : EMA sur le TF d'exécution (≥ ema_slow_d barres requis)
        if len(df) < int(p["ema_slow_d"]) + int(p["slope_lookback_d"]) + 2:
            return 0
        reg = _daily_regime_series(
            df["close"], int(p["ema_slow_d"]),
            int(p["ema_fast_d"]), int(p["slope_lookback_d"]))
        return int(reg[-1])

    # ── Pré-calcul backtest ────────────────────────────────────────────────

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        self._bt_full_df = df
        self._bt_cache.clear()

    def _cache_key(self, p: dict) -> tuple:
        return (int(p["donchian_n"]), int(p["er_period"]), int(p["atr_period"]),
                int(p["ema_slow_d"]), int(p["ema_fast_d"]), int(p["slope_lookback_d"]))

    def _bt_features(self, p: dict) -> Optional[Dict[str, np.ndarray]]:
        """Construit (une fois) les séries causales sur le df complet du backtest."""
        if self._bt_full_df is None or len(self._bt_full_df) < self.min_bars_required():
            return None
        key = self._cache_key(p)
        feats = self._bt_cache.get(key)
        if feats is not None:
            return feats
        df = self._bt_full_df
        n_don = int(p["donchian_n"])
        atr = _atr_series(df, int(p["atr_period"]))
        feats = {
            "close":   df["close"].to_numpy().astype(float),
            "atr":     atr.to_numpy().astype(float),
            "atr_med": atr.rolling_median(100).to_numpy().astype(float),
            "er":      _er_series(df["close"], int(p["er_period"])).to_numpy().astype(float),
            "don_hi":  df["high"].rolling_max(n_don).shift(1).to_numpy().astype(float),
            "don_lo":  df["low"].rolling_min(n_don).shift(1).to_numpy().astype(float),
        }
        # Régime journalier décalé d'1 jour, projeté sur le TF d'exécution
        regime = np.zeros(len(df))
        if "time" in df.columns:
            try:
                d = (df.sort("time")
                       .group_by_dynamic("time", every="1d")
                       .agg(pl.col("close").last()))
                reg_d = _daily_regime_series(
                    d["close"], int(p["ema_slow_d"]),
                    int(p["ema_fast_d"]), int(p["slope_lookback_d"]))
                d = d.with_columns(pl.Series("regime", reg_d).shift(1).fill_null(0))
                joined = (df.select("time")
                            .with_columns(pl.col("time").cast(pl.Datetime("ms")))
                            .sort("time")
                            .join_asof(
                                d.select(
                                    pl.col("time").cast(pl.Datetime("ms")), "regime"
                                ).sort("time"),
                                on="time", strategy="backward"))
                regime = joined["regime"].fill_null(0).to_numpy().astype(float)
            except Exception as e:
                logger.debug(f"[{self.name}] régime backtest KO : {e}")
        feats["regime"] = regime
        # Limite le cache aux 4 derniers jeux de params (optimiseur)
        if len(self._bt_cache) >= 4:
            self._bt_cache.pop(next(iter(self._bt_cache)))
        self._bt_cache[key] = feats
        return feats

    def _bt_lookup(self, df: pl.DataFrame, p: dict) -> Optional[Dict[str, float]]:
        """Lookup O(1) des features de la barre courante dans le cache backtest."""
        if self._bt_full_df is None:
            return None
        i = len(df) - 1
        if i >= len(self._bt_full_df):
            return None
        # Vérifie que la fenêtre correspond bien au df pré-calculé
        try:
            if "time" in df.columns and df["time"][-1] != self._bt_full_df["time"][i]:
                return None
        except Exception:
            return None
        feats = self._bt_features(p)
        if feats is None:
            return None
        return {k: float(v[i]) for k, v in feats.items()}

    # ── Features live (calcul sur la fenêtre courante) ─────────────────────

    def _live_features(self, df: pl.DataFrame, p: dict, df_htf=None) -> Dict[str, float]:
        n_don = int(p["donchian_n"])
        atr = _atr_series(df, int(p["atr_period"]))
        atr_med = atr.rolling_median(100)
        er = _er_series(df["close"], int(p["er_period"]))
        return {
            "close":   float(df["close"][-1]),
            "atr":     float(atr[-1]),
            "atr_med": float(atr_med[-1]) if atr_med[-1] is not None else float("nan"),
            "er":      float(er[-1]),
            "don_hi":  float(df["high"][-n_don - 1:-1].max()),
            "don_lo":  float(df["low"][-n_don - 1:-1].min()),
            "regime":  float(self._regime_now(df, p, df_htf)),
        }

    # ── Scoring ────────────────────────────────────────────────────────────

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = self._params(params)
        if len(df) < self.min_bars_required(params):
            return self._none("warmup")

        f = self._bt_lookup(df, p)
        if f is None:
            f = self._live_features(df, p, df_htf)

        atr = f["atr"]
        if not np.isfinite(atr) or atr <= 0 or not np.isfinite(f["atr_med"]) \
                or not np.isfinite(f["don_hi"]):
            return self._none("warmup indicateurs")

        regime = int(f["regime"])
        if regime == 0:
            return self._none("régime neutre")

        er_min = float(p["er_min"])
        clean_trend = f["er"] >= er_min
        vol_ok = atr >= float(p["atr_expansion"]) * f["atr_med"]
        if not clean_trend:
            return self._none(f"ER {f['er']:.2f} < {er_min} (chop)")
        if not vol_ok:
            return self._none(f"ATR {atr:.2f} < {p['atr_expansion']}× médiane")

        long_sig  = regime == 1 and f["close"] > f["don_hi"]
        short_sig = regime == -1 and f["close"] < f["don_lo"]
        if not (long_sig or short_sig):
            return self._none(
                f"pas de breakout (régime={'bull' if regime == 1 else 'bear'})")

        side = "long" if long_sig else "short"
        # Score : base 0.70 + qualité de tendance (ER) + expansion de volatilité
        er_bonus  = min((f["er"] - er_min) * 0.5, 0.15)
        vol_bonus = 0.05 if f["atr_med"] > 0 and atr / f["atr_med"] >= 1.2 else 0.0
        score = round(min(0.70 + er_bonus + vol_bonus, 0.92), 3)

        return {
            "score":       score,
            "side":        side,
            "name":        self.name,
            "atr":         atr,
            # SL initial = k_sl × ATR calé sur le prix d'exécution réel
            "sl_atr_mult": float(p["k_sl"]),
            # Trailing Chandelier pur (mécanisme dédié, cf. app/core/trailing.py)
            "trail_override": {"mode": "chandelier", "trail_wide": float(p["k_trail"])},
            "indicators": {
                "regime":  regime,
                "er":      round(f["er"], 3),
                "atr":     round(atr, 4),
                "atr_med": round(f["atr_med"], 4),
                "don_hi":  round(f["don_hi"], 2),
                "don_lo":  round(f["don_lo"], 2),
            },
            "conditions": [
                f"Régime 1D {'BULL' if regime == 1 else 'BEAR'} ✓",
                f"Breakout Donchian({int(p['donchian_n'])}) {side} ✓",
                f"ER {f['er']:.2f} >= {er_min} ✓",
                f"ATR {atr:.2f} >= {p['atr_expansion']}× médiane ✓",
            ],
            "reason": (
                f"TVR {side.upper()} — Donchian{int(p['donchian_n'])} breakout, "
                f"régime={'bull' if regime == 1 else 'bear'}, ER={f['er']:.2f}"
            ),
        }

    # ── Sortie sur perte de régime ─────────────────────────────────────────

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        p = self._params(params)
        side = 1 if position.get("side") == "long" else -1
        f = self._bt_lookup(df, p)
        regime = int(f["regime"]) if f is not None else self._regime_now(df, p)
        if regime == -side:
            return "regime_flip"
        return None

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
