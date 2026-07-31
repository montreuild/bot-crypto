"""Parité de timing (axe 1.1) — élagage de la bougie en cours côté live."""
import time

import polars as pl

from app.live.ohlcv_cache import OHLCVCache


def _df(open_times_ms):
    n = len(open_times_ms)
    return pl.DataFrame({
        "time":   list(open_times_ms),
        "open":   [100.0] * n,
        "high":   [101.0] * n,
        "low":    [99.0] * n,
        "close":  [100.5] * n,
        "volume": [10.0] * n,
    })


def _cache():
    # Bypass __init__ : _drop_forming_candle n'utilise que _TF_MS + logger.
    return OHLCVCache.__new__(OHLCVCache)


def test_drops_forming_last_candle():
    now = int(time.time() * 1000)
    hour = 3_600_000
    # Dernière bougie ouverte à `now` → intervalle [now, now+1h) encore en cours.
    df = _df([now - 2 * hour, now - hour, now])
    out = _cache()._drop_forming_candle(df, "1h")
    assert out.height == 2
    assert int(out["time"][-1]) == now - hour


def test_keeps_closed_last_candle():
    now = int(time.time() * 1000)
    hour = 3_600_000
    # Dernière bougie ouverte à now-2h → [now-2h, now-1h) close (now-1h <= now).
    df = _df([now - 3 * hour, now - 2 * hour])
    out = _cache()._drop_forming_candle(df, "1h")
    assert out.height == 2


def test_unknown_tf_is_noop():
    now = int(time.time() * 1000)
    df = _df([now - 1000, now])
    out = _cache()._drop_forming_candle(df, "7h")
    assert out.height == 2
