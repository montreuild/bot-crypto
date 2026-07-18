"""FIN-07 — slippage paper trading proportionnel à la taille.

- ``OHLCVCache.get_avg_quote_volume`` : lecture seule du cache déjà rempli
  (pas de fetch réseau), même fenêtre (20 barres) que ``ctx.qvol_arr`` côté
  backtest (BT-10).
- ``PositionMixin._paper_slippage_fraction`` : "static" (défaut, inchangé)
  vs "size" (repli sur static si volume absent, sinon coût d'impact croissant
  avec la taille — même formule que ``Backtester._impact_cost``).
"""
import threading
import time

import polars as pl
import pytest

from app.live.ohlcv_cache import OHLCVCache
from app.live.position_mixin import PositionMixin
from app.core.execution import size_impact_cost


def _df(n, base_volume=1000.0):
    return pl.DataFrame({
        "time":   list(range(n)),
        "open":   [100.0] * n,
        "high":   [101.0] * n,
        "low":    [99.0] * n,
        "close":  [100.0] * n,
        "volume": [base_volume] * n,
    })


def _cache():
    c = OHLCVCache.__new__(OHLCVCache)
    c._lock = threading.RLock()
    c._ohlcv_cache = {}
    return c


class TestGetAvgQuoteVolume:
    def test_none_when_not_cached(self):
        c = _cache()
        assert c.get_avg_quote_volume("BTC/USDC", "1h") is None

    def test_none_when_too_short(self):
        c = _cache()
        c._ohlcv_cache[("BTC/USDC", "1h")] = (time.time(), _df(10))
        assert c.get_avg_quote_volume("BTC/USDC", "1h", lookback=20) is None

    def test_computes_rolling_mean_over_lookback(self):
        c = _cache()
        # 30 barres à volume=1000, close=100 -> quote volume = 100_000/barre
        c._ohlcv_cache[("BTC/USDC", "1h")] = (time.time(), _df(30, base_volume=1000.0))
        avg = c.get_avg_quote_volume("BTC/USDC", "1h", lookback=20)
        assert avg == pytest.approx(100_000.0)


class Harness(PositionMixin):
    """Instance minimale de PositionMixin pour tester _paper_slippage_fraction
    isolément (même pattern que test_execution_parity.py::_live_close_pnl)."""

    def __init__(self, model="static", ohlcv_cache=None):
        self.cfg = {"trading": {
            "paper_slippage": 0.001,
            "paper_slippage_model": model,
            "slippage_k": 1.0,
        }}
        self.ohlcv_cache = ohlcv_cache
        self.tf = "1h"


class TestPaperSlippageFraction:
    def test_static_model_returns_base(self):
        h = Harness(model="static")
        assert h._paper_slippage_fraction("BTC/USDC", "1h", 5000.0) == pytest.approx(0.001)

    def test_size_model_falls_back_when_no_cache(self):
        h = Harness(model="size", ohlcv_cache=_cache())
        assert h._paper_slippage_fraction("BTC/USDC", "1h", 5000.0) == pytest.approx(0.001)

    def test_size_model_falls_back_without_ohlcv_cache_attr(self):
        h = Harness(model="size", ohlcv_cache=None)
        assert h._paper_slippage_fraction("BTC/USDC", "1h", 5000.0) == pytest.approx(0.001)

    def test_size_model_adds_impact_matching_shared_formula(self):
        cache = _cache()
        cache._ohlcv_cache[("BTC/USDC", "1h")] = (time.time(), _df(30, base_volume=1000.0))
        h = Harness(model="size", ohlcv_cache=cache)
        notional = 5000.0
        avg_qv = cache.get_avg_quote_volume("BTC/USDC", "1h")
        expected = 0.001 + size_impact_cost(notional, 0.001, 1.0, avg_qv) / notional
        assert h._paper_slippage_fraction("BTC/USDC", "1h", notional) == pytest.approx(expected)

    def test_size_model_grows_with_notional(self):
        cache = _cache()
        cache._ohlcv_cache[("BTC/USDC", "1h")] = (time.time(), _df(30, base_volume=1000.0))
        h = Harness(model="size", ohlcv_cache=cache)
        small = h._paper_slippage_fraction("BTC/USDC", "1h", 1000.0)
        large = h._paper_slippage_fraction("BTC/USDC", "1h", 50_000.0)
        assert large > small > 0.001
