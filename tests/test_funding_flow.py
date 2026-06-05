"""Tests — stratégie funding_flow (dérivés purs) + hook OHLCVCache (au fil de l'eau)."""
import os
import sys
import types
from datetime import datetime, timedelta

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.core.derivatives as dv
from app.core.derivatives import DerivativesStore
from app.strategies.funding_flow import Strategy
from app.core.indicators import precompute_df


def _ohlcv(n=260, t0=datetime(2024, 1, 1), seed=0):
    rng = np.random.RandomState(seed)
    close = 30000 + np.cumsum(rng.randn(n) * 40)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=i) for i in range(n)],
        "open": close, "high": close * 1.002, "low": close * 0.998,
        "close": close, "volume": rng.uniform(100, 200, n)})


def _with_derivs(df, funding_z=0.0, lsr_z=0.0, taker_z=0.0, oi_change=0.0):
    n = len(df)
    return df.with_columns([
        pl.Series("funding_z", [funding_z] * n),
        pl.Series("lsr_z", [lsr_z] * n),
        pl.Series("taker_z", [taker_z] * n),
        pl.Series("oi_change_pct", [oi_change] * n),
    ])


def _prep(seed=0, **derivs):
    """Reproduit l'ordre de PRODUCTION : precompute (OHLCV pur) PUIS enrichissement.
    Seed distinct par test → évite les collisions du cache mémoïsé de precompute_df."""
    df = precompute_df(_ohlcv(seed=seed))
    return _with_derivs(df, **derivs) if derivs else df


def _params(**kw):
    return {"funding_flow": kw}


class TestFundingFlow:
    def test_registry(self):
        from app.engine.registry import get_param_spaces, get_strategy_timeframes
        assert "funding_flow" in get_param_spaces()
        assert get_strategy_timeframes()["funding_flow"] == ["1h", "4h"]

    def test_abstains_without_derivatives(self):
        """Stratégie 100 % dérivés : sans colonnes → abstention (side none)."""
        s = Strategy()
        r = s.score(_prep(seed=1), _params(), symbol="BTC/USDC")
        assert r["side"] == "none"

    def test_short_on_crowded_longs(self):
        """funding/lsr/taker très positifs (foule longue) → SHORT (fade)."""
        s = Strategy()
        df = _prep(seed=2, funding_z=2.5, lsr_z=2.0, taker_z=2.0, oi_change=1.0)
        r = s.score(df, _params(enter_pressure=1.0), symbol="BTC/USDC")
        assert r["side"] == "short"
        assert r["setup"] == "FUNDING_REVERSION"
        assert r["sl_atr_mult"] > 0 and r["tp_atr_mult"] > 0

    def test_long_on_crowded_shorts(self):
        """funding/lsr/taker très négatifs (foule short) → LONG (fade)."""
        s = Strategy()
        df = _prep(seed=3, funding_z=-2.5, lsr_z=-2.0, taker_z=-1.5)
        r = s.score(df, _params(enter_pressure=1.0), symbol="BTC/USDC")
        assert r["side"] == "long"

    def test_neutral_pressure_abstains(self):
        s = Strategy()
        df = _prep(seed=4, funding_z=0.2, lsr_z=-0.1, taker_z=0.0)
        r = s.score(df, _params(enter_pressure=1.3), symbol="BTC/USDC")
        assert r["side"] == "none"

    def test_partial_derivatives_ok(self):
        """Fonctionne même si seul funding_z est présent (pondération normalisée)."""
        s = Strategy()
        df = precompute_df(_ohlcv(seed=5)).with_columns(pl.Series("funding_z", [3.0] * 260))
        r = s.score(df, _params(enter_pressure=1.0), symbol="BTC/USDC")
        assert r["side"] == "short"

    def test_no_ml_dependency(self):
        import app.strategies.funding_flow as mod
        src = open(mod.__file__).read()
        assert "lightgbm" not in src and "sklearn" not in src


class _FakeExchange:
    def __init__(self, t0_ms):
        self.t0 = t0_ms

    def fetch_funding_rate_history(self, symbol, limit=1000):
        return [{"timestamp": self.t0 + i * 3600_000, "fundingRate": 0.0002} for i in range(60)]

    def fetch_open_interest_history(self, symbol, period, limit=500):
        return [{"timestamp": self.t0 + i * 3600_000, "openInterestValue": 1e9 + i * 1e6} for i in range(60)]


class TestOHLCVCacheDerivativesHook:
    def _cache(self, tmp_path, enabled, monkeypatch):
        from app.live.ohlcv_cache import OHLCVCache
        t0 = int(datetime(2024, 1, 1).timestamp() * 1000)
        fake_http = [{"timestamp": t0 + i * 3600_000, "longShortRatio": str(1.5 + 0.01 * i)} for i in range(60)]
        monkeypatch.setattr(dv, "_http_get_json", lambda url, timeout=8.0: fake_http)
        cfg = {"trading": {"scan_interval": 60},
               "derivatives": {"enabled": enabled, "period": "1h", "refresh_interval": 0, "z_window": 20}}
        notif = types.SimpleNamespace(notify_exchange_error=lambda *a, **k: None)
        risk = types.SimpleNamespace(update_volatility=lambda *a, **k: None)
        cache = OHLCVCache(_FakeExchange(t0), cfg, notif, risk)
        if cache._deriv_store is not None:
            cache._deriv_store = DerivativesStore(base_dir=str(tmp_path))
        return cache

    def test_enrich_adds_columns_when_enabled(self, tmp_path, monkeypatch):
        cache = self._cache(tmp_path, enabled=True, monkeypatch=monkeypatch)
        df = precompute_df(_ohlcv(80))
        out = cache._enrich_derivatives("BTC/USDC", df)
        assert "funding_z" in out.columns
        assert "long_short_ratio" in out.columns
        assert len(out) == len(df)

    def test_disabled_is_noop(self, tmp_path, monkeypatch):
        cache = self._cache(tmp_path, enabled=False, monkeypatch=monkeypatch)
        df = precompute_df(_ohlcv(80))
        out = cache._enrich_derivatives("BTC/USDC", df)
        assert "funding_z" not in out.columns
        assert out is df  # strictement inchangé

    def test_enrich_graceful_on_error(self, tmp_path, monkeypatch):
        """Si le store lève, _enrich renvoie le df OHLCV inchangé (jamais d'exception)."""
        cache = self._cache(tmp_path, enabled=True, monkeypatch=monkeypatch)
        class _Boom:
            def refresh(self, *a, **k): raise RuntimeError("boom")
            def align_to_ohlcv(self, *a, **k): raise RuntimeError("boom")
        cache._deriv_store = _Boom()
        df = precompute_df(_ohlcv(80))
        out = cache._enrich_derivatives("BTC/USDC", df)
        assert out is df
