"""rolling_slope / rolling_hurst : vectorisation (PERF) — non-régression contre
les implémentations scalaires (boucle Python par fenêtre) qu'elles remplacent.

rolling_slope : pente = cov(x,y)/var(x) avec x=arange(window) fixe et centré
→ se réduit à une corrélation par un noyau fixe (np.correlate), exacte.

rolling_hurst : hurst_exponent (méthode R/S) n'est pas linéaire, donc pas
réductible à une simple corrélation — vectorisé par batch sur toutes les
fenêtres (sliding_window_view) avec repli scalaire (hurst_exponent d'origine)
pour toute fenêtre contenant un NaN, dont la troncature ``arr[~isnan]`` casse
l'alignement entre positions et empêche une vectorisation exacte.
"""
import numpy as np
import polars as pl
import pytest

from app.core.indicators_market import hurst_exponent, rolling_hurst, rolling_slope


# ── Références scalaires (comportement AVANT vectorisation, préservé pour
#    non-régression — copie fidèle des anciennes implémentations) ───────────
def _rolling_slope_ref(s: pl.Series, window: int) -> np.ndarray:
    arr = s.to_numpy()
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if window <= 1 or n < window:
        return out
    x = np.arange(window, dtype=np.float64)
    x_dev = x - x.mean()
    denom = float((x_dev * x_dev).sum())
    if denom == 0:
        return out
    for i in range(window - 1, n):
        y = arr[i - window + 1: i + 1]
        if np.any(np.isnan(y)):
            continue
        out[i] = float(((y - y.mean()) * x_dev).sum()) / denom
    return out


def _rolling_hurst_ref(s: pl.Series, window: int = 100, max_lag: int = 20) -> np.ndarray:
    arr = s.to_numpy()
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        out[i] = hurst_exponent(arr[i - window + 1: i + 1], max_lag=max_lag)
    return out


def _series_from(arr: np.ndarray) -> pl.Series:
    return pl.Series(arr.astype(np.float64))


def _assert_series_match(ref: np.ndarray, new: np.ndarray, atol: float = 1e-6):
    assert len(ref) == len(new)
    nan_ref, nan_new = np.isnan(ref), np.isnan(new)
    assert (nan_ref == nan_new).all(), (
        f"NaN pattern mismatch at indices {np.nonzero(nan_ref != nan_new)[0][:10]}")
    ok = ~nan_ref
    if ok.any():
        assert np.allclose(ref[ok], new[ok], atol=atol), (
            f"max abs diff={np.max(np.abs(ref[ok] - new[ok])):.3e}")


@pytest.fixture
def trending_series() -> pl.Series:
    rng = np.random.default_rng(42)
    n = 400
    walk = rng.standard_normal(n).cumsum() + 100.0
    return _series_from(walk)


class TestRollingSlope:
    @pytest.mark.parametrize("window", [2, 5, 10, 20, 50])
    def test_matches_reference(self, trending_series, window):
        ref = _rolling_slope_ref(trending_series, window)
        new = rolling_slope(trending_series, window).to_numpy()
        _assert_series_match(ref, new, atol=1e-9)

    def test_constant_series_zero_slope(self):
        s = _series_from(np.full(50, 7.0))
        out = rolling_slope(s, 10).to_numpy()
        assert np.allclose(out[9:], 0.0, atol=1e-12)

    def test_perfect_linear_series_exact_slope(self):
        s = _series_from(np.arange(100, dtype=np.float64) * 3.0 + 5.0)
        out = rolling_slope(s, 10).to_numpy()
        assert np.allclose(out[9:], 3.0, atol=1e-9)

    def test_scattered_nan_and_constant_segment(self):
        rng = np.random.default_rng(1)
        arr = rng.standard_normal(300).cumsum() + 50.0
        arr[40:45] = np.nan
        arr[150:180] = 10.0
        s = _series_from(arr)
        ref = _rolling_slope_ref(s, 15)
        new = rolling_slope(s, 15).to_numpy()
        _assert_series_match(ref, new, atol=1e-9)

    def test_edge_cases_short_series(self):
        for n, window in [(5, 10), (10, 10), (1, 1), (0, 5)]:
            s = _series_from(np.arange(max(n, 0), dtype=np.float64))
            ref = _rolling_slope_ref(s, window)
            new = rolling_slope(s, window).to_numpy()
            _assert_series_match(ref, new, atol=1e-9)


class TestRollingHurst:
    @pytest.mark.parametrize("window", [30, 60, 100])
    def test_matches_reference(self, trending_series, window):
        ref = _rolling_hurst_ref(trending_series, window)
        new = rolling_hurst(trending_series, window).to_numpy()
        _assert_series_match(ref, new, atol=1e-6)

    def test_scattered_nan_and_constant_segment(self):
        rng = np.random.default_rng(2)
        arr = rng.standard_normal(300).cumsum() + 50.0
        arr[40:45] = np.nan            # bloc NaN mi-série -> repli scalaire
        arr[150:180] = 42.0            # segment constant -> std(d)==0 pour certains lags
        s = _series_from(arr)
        ref = _rolling_hurst_ref(s, 60)
        new = rolling_hurst(s, 60).to_numpy()
        _assert_series_match(ref, new, atol=1e-6)

    def test_edge_cases_short_series(self):
        for n, window in [(5, 10), (10, 10), (0, 5)]:
            s = _series_from(np.arange(max(n, 0), dtype=np.float64))
            ref = _rolling_hurst_ref(s, window)
            new = rolling_hurst(s, window).to_numpy()
            _assert_series_match(ref, new, atol=1e-9)

    def test_no_runtime_warnings(self, trending_series):
        """Les fenêtres à 0 lag valide ne doivent pas émettre de warning
        (nanmean sur slice vide) — filtré explicitement dans l'implémentation."""
        import warnings
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            rolling_hurst(trending_series, 30)
        runtime_warnings = [w for w in record if issubclass(w.category, RuntimeWarning)]
        assert not runtime_warnings, [str(w.message) for w in runtime_warnings]
