"""Tests unitaires — stratégie trend_rider (trend-follower long-biaisé
construit à partir d'indicateurs classiques)."""
import os
import sys

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.strategies.trend_rider import Strategy


def _trend_df(n=700, slope=0.5, noise=0.6, seed=0, direction=1):
    """OHLCV synthétique à tendance ONDULÉE (direction ±1) : une dérive + une
    oscillation qui fait re-basculer périodiquement le régime (fronts montants
    répartis sur toute la série, y compris après le warmup EMA200)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    base = 200.0 + (slope * n if direction < 0 else 0.0)   # évite le plancher
    drift = base + direction * slope * t
    wave = direction * 14.0 * np.sin(2 * np.pi * t / 90.0)
    close = np.maximum(drift + wave + rng.normal(0, noise, n).cumsum() * 0.3, 1.0)
    high = close + np.abs(rng.normal(0, 0.3, n))
    low = close - np.abs(rng.normal(0, 0.3, n))
    openp = np.concatenate([[close[0]], close[:-1]])
    vol = rng.uniform(1000, 3000, n)
    return pl.DataFrame({"open": openp, "high": high, "low": low,
                         "close": close, "volume": vol})


class TestTrendRider:
    def test_contract_insufficient(self):
        s = Strategy()
        out = s.score(_trend_df(50))
        assert out["side"] == "none" and out["name"] == "trend_rider"

    def test_long_signal_in_uptrend(self):
        s = Strategy()
        s._bt_params = None
        df = _trend_df(500, direction=1, seed=1)
        s.prepare_for_backtest(df)
        assert s._sig, "aucun signal en tendance haussière franche"
        for sig in s._sig.values():
            assert sig["side"] == "long"
            assert sig["disable_trailing"] is False
            assert sig["trail_override"]["trail_wide"] > 0
            assert sig["stop_hint"] > 0
            assert 0.5 <= sig["score"] <= 1.0

    def test_long_only_default(self):
        """long_only par défaut → aucun signal short même en tendance baissière."""
        s = Strategy()
        s._bt_params = None
        s.prepare_for_backtest(_trend_df(500, direction=-1, seed=2))
        assert all(v["side"] == "long" for v in (s._sig or {}).values())
        assert not any(v["side"] == "short" for v in (s._sig or {}).values())

    def test_shorts_when_enabled(self):
        s = Strategy()
        s._bt_params = {"trend_rider": {"long_only": False}}
        s.prepare_for_backtest(_trend_df(700, direction=-1, seed=3))
        assert any(v["side"] == "short" for v in (s._sig or {}).values())

    def test_edge_triggered_sparse(self):
        """Entrées au front montant du régime → clairsemées, pas chaque barre."""
        s = Strategy()
        s._bt_params = None
        df = _trend_df(500, direction=1, seed=4)
        s.prepare_for_backtest(df)
        assert 0 < len(s._sig) < df.height // 5

    def test_stop_below_entry_long(self):
        s = Strategy()
        s._bt_params = None
        df = _trend_df(500, direction=1, seed=5)
        s.prepare_for_backtest(df)
        c = df["close"].to_numpy()
        for i, sig in s._sig.items():
            assert sig["stop_hint"] < c[i], "stop long doit être sous la clôture"

    def test_cache_coherence_live_vs_backtest(self):
        """Le chemin live (score sur df tronqué) reproduit le signal du cache
        backtest à la même barre — mêmes côté/stop."""
        s = Strategy()
        s._bt_params = None
        df = _trend_df(500, direction=1, seed=6)
        s.prepare_for_backtest(df)
        checked = 0
        for i in list(s._sig)[:5]:
            live = Strategy()
            out = live.score(df[:i + 1])
            assert out["side"] == "long"
            assert abs(out["stop_hint"] - s._sig[i]["stop_hint"]) < 1e-6
            checked += 1
        assert checked > 0
