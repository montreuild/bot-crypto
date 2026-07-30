"""Tests — pré-calcul du spectre FFT de fft_spectral (prepare_for_backtest).

La FFT brute (détrending + Hanning + rfft) ne dépend pas des paramètres optimisés
(fft_min_period / fft_max_period / fft_top_n / min_confidence / cooldown / rr_min),
seulement de la fenêtre de prix. On la mémoïse par barre dans prepare_for_backtest
et score() la réutilise. Le résultat doit être STRICTEMENT identique au calcul live.
"""
import datetime as dt
import os
import sys

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.indicators import precompute_df
from app.strategies.fft_spectral import Strategy, _fft_direction, _fft_select, _fft_spectrum


def _ohlcv(n=700, seed=5):
    rng = np.random.default_rng(seed)
    t0 = dt.datetime(2024, 1, 1)
    times = [t0 + dt.timedelta(hours=4 * i) for i in range(n)]
    price = (100 + np.cumsum(rng.normal(0, 0.8, n))
             + 5 * np.sin(np.arange(n) * 2 * np.pi / 40))
    return precompute_df(pl.DataFrame({
        "time": times,
        "open": price,
        "high": price + 1,
        "low":  price - 1,
        "close": price,
        "volume": rng.uniform(100, 300, n),
    }))


def test_spectrum_split_matches_monolithic():
    """_fft_spectrum + _fft_select == ancien _fft_direction monolithique."""
    rng = np.random.default_rng(1)
    prices = 100 + np.cumsum(rng.normal(0, 1, 400))
    for mn, mx, tn in [(5, 150, 10), (7, 100, 7), (10, 200, 15)]:
        combined = _fft_select(_fft_spectrum(prices), mn, mx, tn)
        direct   = _fft_direction(prices, mn, mx, tn)
        assert combined == direct


def test_precompute_exact_vs_live():
    """score() avec cache == score() live, barre par barre (cooldown inclus)."""
    df = _ohlcv()
    A = Strategy()
    A._bt_symbol = ""
    A._bt_tf = "4h"
    A._bt_params = None
    A.prepare_for_backtest(df)
    B = Strategy()
    B._bt_symbol = ""  # pas de prepare → chemin live

    assert A._bt_spectra, "le cache de spectres doit être peuplé"
    mb = A.min_bars_required(None)
    signals = 0
    for i in range(mb, len(df)):
        w = df[: i + 1]
        ra = A.score(w, None)
        rb = B.score(w, None)
        assert ra["side"] == rb["side"]
        assert abs((ra.get("score") or 0) - (rb.get("score") or 0)) < 1e-9
        if ra["side"] != "none":
            signals += 1
    assert signals > 0, "le scénario doit produire au moins un signal FFT"


def test_reset_clears_spectra():
    df = _ohlcv()
    s = Strategy()
    s._bt_symbol = ""
    s._bt_params = None
    s.prepare_for_backtest(df)
    assert s._bt_spectra
    s.reset_model()
    assert s._bt_spectra == {}
