"""TEST-05 / PERF-03 : débit du backtest sur une série *à trous*.

PERF-01 a montré qu'un gain ×120 validé sur une grille synthétique régulière
était inerte sur les données réelles (5 pas irréguliers, dont un trou de
164 jours). Ce module :

1. fournit une fixture « réaliste » (trous) pour tout test causal ;
2. mesure le débit de ``trend`` (qui appelle ``htf_trend``) et échoue si
   on retombe dans un O(n²) barre à barre.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.core.indicators_causal import htf_trend_ema_series
from app.engine.backtest import Backtester
from app.engine.engine import Engine
from tests.test_backtest import _cfg

REAL_PARQUET = Path("data/ohlcv/BTC_USDC/1h.parquet")

# Sous ce débit, le chemin mémoïsé de htf_trend est cassé (O(n²) mesuré
# ~50 barres/s sur 3 k barres irrégulières avant PERF-01).
MIN_BARS_PER_SEC = 150


def gapped_ohlcv(n: int = 3000, seed: int = 7) -> pl.DataFrame:
    """Grille 1h avec les trous typiques du cache réel (pas une grille parfaite)."""
    rng = np.random.default_rng(seed)
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    t0 = np.datetime64("2022-01-01T00:00:00", "ms")
    times = t0 + np.arange(n) * np.timedelta64(3_600_000, "ms")
    # Trou long (week-end boursier / panne) + quelques barres sautées.
    drop = {400, 401, 402, 1200, 1201, 1800}
    keep = [i for i in range(n) if i not in drop]
    # Décale le 2e tiers d'une semaine pour un trou de 164 h ~ une semaine.
    times = times.copy()
    times[n // 2:] = times[n // 2:] + np.timedelta64(164, "D")
    return pl.DataFrame({
        "time": times[keep],
        "open": open_[keep],
        "high": high[keep],
        "low": low[keep],
        "close": close[keep],
        "volume": np.abs(rng.normal(500, 120, len(keep))),
    })


def load_real_or_gapped(n: int = 3000) -> tuple[pl.DataFrame, str]:
    if REAL_PARQUET.exists():
        df = pl.read_parquet(REAL_PARQUET)
        if "time" not in df.columns:
            pytest.skip("parquet OHLCV sans colonne time")
        if len(df) > n:
            df = df.tail(n)
        return df, "real"
    return gapped_ohlcv(n), "gapped"


def test_htf_trend_vectorise_refuse_une_grille_a_trous():
    """Le chemin rapide doit s'abstenir — sinon on rejoue PERF-01."""
    df = gapped_ohlcv(800)
    assert htf_trend_ema_series(df, 50, 4) is None


@pytest.mark.slow
def test_debit_backtest_trend_sur_serie_reelle_ou_trouee():
    df, src = load_real_or_gapped(2800)
    from app.strategies.trend import Strategy

    eng = Engine()
    eng.register(Strategy(), silent=True)
    cfg = _cfg()
    cfg["strategies"] = {"enabled": ["trend"]}
    t0 = time.perf_counter()
    result = Backtester(eng, cfg, ml_mode="inline").run(df, "BTC/USDC", "1h")
    elapsed = max(time.perf_counter() - t0, 1e-6)
    n_bars = int(getattr(result, "n_bars", 0) or max(len(df) - 250, 1))
    rate = n_bars / elapsed
    assert rate >= MIN_BARS_PER_SEC, (
        f"débit {rate:.1f} barres/s ({src}, {n_bars} barres en {elapsed:.2f}s) "
        f"< {MIN_BARS_PER_SEC} — repli HTF probablement redevenu O(n²)"
    )
