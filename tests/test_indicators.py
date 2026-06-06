"""
Tests unitaires — Indicateurs techniques (RSI, ATR, ADX, EMA, MACD, SuperTrend, S/R, etc.)
Vérifie les calculs contre des valeurs de référence et les cas limites.
"""
import pytest
import sys
import os
import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.indicators import (
    ema, rsi, atr, atr_val, adx, adx_val,
    macd, bollinger, supertrend, stochastic,
    volume_ratio, vol_ratio, donchian, obv,
    market_structure, htf_trend, detect_regime, detect_regime_full,
    support_resistance_levels, nearest_support, nearest_resistance,
    precompute_df, pre_val, build_features,
    roc, green_ratio, rsi_divergence, trend_duration,
)


def _make_ohlcv(n=300, base=100.0, trend=0.05, seed=42):
    """Generates a synthetic OHLCV DataFrame."""
    rng = np.random.default_rng(seed)
    closes = [base]
    for _ in range(n - 1):
        step = rng.normal(0, 0.5) + trend
        closes.append(max(closes[-1] + step, 1.0))
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.normal(0, 0.005, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.005, n)))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    vols = rng.uniform(1000, 5000, n)
    return pl.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


class TestEMA:
    def test_length(self):
        df = _make_ohlcv(100)
        result = ema(df["close"], 20)
        assert len(result) == 100

    def test_values_finite(self):
        df = _make_ohlcv(100)
        result = ema(df["close"], 20)
        arr = result.to_numpy()
        assert np.all(np.isfinite(arr[20:]))

    def test_ema_smooths(self):
        """EMA should be smoother than raw close."""
        df = _make_ohlcv(200)
        close = df["close"].to_numpy()
        ema_arr = ema(df["close"], 20).to_numpy()
        assert np.std(np.diff(ema_arr[30:])) < np.std(np.diff(close[30:]))


class TestRSI:
    def test_range_0_100(self):
        df = _make_ohlcv(200)
        result = rsi(df["close"], 14)
        arr = result.drop_nulls().to_numpy()
        assert np.all(arr >= 0)
        assert np.all(arr <= 100)

    def test_length(self):
        df = _make_ohlcv(200)
        result = rsi(df["close"], 14)
        assert len(result) == 200

    def test_trending_up_high_rsi(self):
        """In a strong uptrend, RSI should be > 50 on average."""
        df = _make_ohlcv(200, trend=0.3)
        result = rsi(df["close"], 14)
        avg = result.drop_nulls().to_numpy()[-50:].mean()
        assert avg > 50


class TestATR:
    def test_positive(self):
        df = _make_ohlcv(100)
        result = atr(df, 14)
        arr = result.drop_nulls().to_numpy()
        assert np.all(arr >= 0)

    def test_atr_val_scalar(self):
        df = _make_ohlcv(100)
        val = atr_val(df, 14)
        assert isinstance(val, float)
        assert val > 0


class TestADX:
    def test_returns_tuple(self):
        df = _make_ohlcv(100)
        result = adx(df, 14)
        assert len(result) == 3  # adx, +DI, -DI

    def test_positive_values(self):
        df = _make_ohlcv(100)
        adx_s, pdi, ndi = adx(df, 14)
        assert adx_s.drop_nulls().to_numpy()[-1] >= 0
        assert pdi.drop_nulls().to_numpy()[-1] >= 0
        assert ndi.drop_nulls().to_numpy()[-1] >= 0

    def test_adx_val_scalar(self):
        df = _make_ohlcv(100)
        val = adx_val(df, 14)
        assert isinstance(val, float)
        assert 0 <= val <= 100


class TestMACD:
    def test_returns_three(self):
        df = _make_ohlcv(100)
        ml, ms, mh = macd(df["close"])
        assert len(ml) == 100
        assert len(ms) == 100
        assert len(mh) == 100

    def test_histogram_is_difference(self):
        df = _make_ohlcv(100)
        ml, ms, mh = macd(df["close"])
        # histogram = line - signal
        diff = (ml - ms).to_numpy()
        hist = mh.to_numpy()
        np.testing.assert_allclose(diff[30:], hist[30:], atol=1e-8)


class TestSuperTrend:
    def test_direction_values(self):
        df = _make_ohlcv(200)
        direction, st_line = supertrend(df)
        unique = set(direction.to_numpy())
        assert unique.issubset({-1.0, 1.0})

    def test_length(self):
        df = _make_ohlcv(200)
        direction, st_line = supertrend(df)
        assert len(direction) == 200
        assert len(st_line) == 200


class TestStochastic:
    def test_range(self):
        df = _make_ohlcv(100)
        k, d = stochastic(df)
        assert 0 <= k <= 100
        assert 0 <= d <= 100

    def test_insufficient_data(self):
        df = _make_ohlcv(5)
        k, d = stochastic(df)
        assert k == 50.0
        assert d == 50.0


class TestBollinger:
    def test_upper_above_lower(self):
        df = _make_ohlcv(100)
        upper, mid, lower = bollinger(df["close"])
        u = upper.drop_nulls().to_numpy()
        l = lower.drop_nulls().to_numpy()
        assert np.all(u >= l)


class TestVolumeRatio:
    def test_positive(self):
        df = _make_ohlcv(100)
        vr = volume_ratio(df, 20)
        arr = vr.drop_nulls().to_numpy()
        assert np.all(arr >= 0)

    def test_vol_ratio_scalar(self):
        df = _make_ohlcv(100)
        val = vol_ratio(df, 20)
        assert isinstance(val, float)
        assert val > 0


class TestMarketStructure:
    def test_uptrend(self):
        df = _make_ohlcv(200, trend=0.5)
        result = market_structure(df["high"], df["low"])
        assert result in (-1, 0, 1)

    def test_short_data(self):
        df = _make_ohlcv(5)
        result = market_structure(df["high"], df["low"])
        assert result == 0

    def test_configurable_window(self):
        df = _make_ohlcv(200, trend=0.5)
        result = market_structure(df["high"], df["low"], window=10)
        assert result in (-1, 0, 1)


class TestDetectRegime:
    def test_returns_string(self):
        df = _make_ohlcv(100)
        result = detect_regime(df)
        assert result in ("trend", "range", "unknown")

    def test_configurable_threshold(self):
        df = _make_ohlcv(100)
        result = detect_regime(df, adx_threshold=15.0)
        assert result in ("trend", "range", "unknown")

    def test_short_data(self):
        df = _make_ohlcv(10)
        result = detect_regime(df)
        assert result == "unknown"

    def test_full_returns_dict(self):
        df = _make_ohlcv(100)
        result = detect_regime_full(df)
        assert "regime" in result
        assert result["regime"] in ("trending", "ranging", "volatile", "unknown")

    def test_full_configurable_thresholds(self):
        df = _make_ohlcv(100)
        result = detect_regime_full(df, adx_trend_threshold=15.0, atr_volatile_threshold=1.0)
        assert "regime" in result


class TestSupportResistance:
    def test_returns_dict(self):
        df = _make_ohlcv(200)
        result = support_resistance_levels(df)
        assert "supports" in result
        assert "resistances" in result

    def test_supports_below_price(self):
        df = _make_ohlcv(200)
        result = support_resistance_levels(df)
        last_close = float(df["close"][-1])
        for s in result["supports"]:
            assert s["price"] < last_close

    def test_resistances_above_price(self):
        df = _make_ohlcv(200)
        result = support_resistance_levels(df)
        last_close = float(df["close"][-1])
        for r in result["resistances"]:
            assert r["price"] > last_close

    def test_short_data(self):
        df = _make_ohlcv(5)
        result = support_resistance_levels(df)
        assert result == {"supports": [], "resistances": []}


class TestNearestSR:
    def test_nearest_support(self):
        levels = [{"price": 90.0, "strength": 3}, {"price": 95.0, "strength": 2}]
        assert nearest_support(100.0, levels) == 95.0

    def test_nearest_resistance(self):
        levels = [{"price": 105.0, "strength": 3}, {"price": 110.0, "strength": 2}]
        assert nearest_resistance(100.0, levels) == 105.0

    def test_no_support(self):
        levels = [{"price": 105.0, "strength": 3}]
        assert nearest_support(100.0, levels) is None

    def test_no_resistance(self):
        levels = [{"price": 95.0, "strength": 3}]
        assert nearest_resistance(100.0, levels) is None


class TestPrecompute:
    def test_adds_columns(self):
        df = _make_ohlcv(300)
        result = precompute_df(df)
        expected_cols = [
            "_pre_rsi14", "_pre_atr14", "_pre_adx14",
            "_pre_pdi14", "_pre_ndi14",
            "_pre_macd_line", "_pre_macd_sig", "_pre_macd_hist",
            "_pre_volratio20",
            "_pre_ema20", "_pre_ema50", "_pre_ema200",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_preserves_length(self):
        df = _make_ohlcv(300)
        result = precompute_df(df)
        assert len(result) == 300


class TestPreVal:
    def test_returns_value_when_present(self):
        df = _make_ohlcv(100)
        df = precompute_df(df)
        val = pre_val(df, "_pre_rsi14")
        assert val is not None
        assert isinstance(val, float)

    def test_returns_none_when_missing(self):
        df = _make_ohlcv(100)
        val = pre_val(df, "_nonexistent_col")
        assert val is None

    def test_returns_none_for_empty_string(self):
        df = _make_ohlcv(100)
        val = pre_val(df, "")
        assert val is None


class TestBuildFeatures:
    def test_returns_dataframe(self):
        df = _make_ohlcv(200)
        result = build_features(df)
        assert isinstance(result, pl.DataFrame)
        assert len(result) > 0

    def test_columns_present(self):
        df = _make_ohlcv(200)
        result = build_features(df)
        for col in ("ret1", "rsi", "adx", "atr_pct", "vol_ratio", "st_dir"):
            assert col in result.columns, f"Missing feature: {col}"

    def test_insufficient_data(self):
        df = _make_ohlcv(10)
        result = build_features(df)
        assert len(result) == 0


class TestV4CatalogueIndicators:
    """Indicateurs repris du catalogue de features V4 (roc, green_ratio, …)."""

    def test_roc_length_and_value(self):
        df = _make_ohlcv(120)
        r = roc(df["close"], 14)
        assert len(r) == 120
        # ROC manuel sur la dernière barre
        c = df["close"].to_numpy()
        expected = (c[-1] / c[-15] - 1.0) * 100.0
        assert abs(float(r[-1]) - expected) < 1e-6

    def test_green_ratio_bounds(self):
        df = _make_ohlcv(120)
        g = green_ratio(df, 10)
        vals = g.to_numpy()[10:]
        assert np.all((vals >= 0.0) & (vals <= 1.0))

    def test_rsi_divergence_in_set(self):
        df = _make_ohlcv(120)
        d = rsi_divergence(df).to_numpy()
        assert set(np.unique(d)).issubset({-1, 0, 1})

    def test_trend_duration_monotonic_runs(self):
        df = _make_ohlcv(200, trend=0.6)  # forte tendance → ADX élevé
        td = trend_duration(df).to_numpy()
        assert td.min() >= 0
        # un run doit s'incrémenter de 1 au plus entre deux barres consécutives
        diffs = np.diff(td)
        assert diffs.max() <= 1
