"""Non-régression : détection du timeframe dans ml_dynamic_threshold.

Garde-fou contre le bug d'unité où une TF longue stockée en microsecondes
(1 h = 3,6e9 µs) était divisée par 1e9 et détectée comme « custom_3s ».
"""
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

pytest.importorskip("lightgbm")  # le module importe lightgbm

from app.strategies.ml_dynamic_threshold import _detect_tf


def _df(delta_s: int, n: int = 100) -> pl.DataFrame:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pl.DataFrame({"time": [base + timedelta(seconds=delta_s * i) for i in range(n)]})


@pytest.mark.parametrize("tf,delta", [
    ("5m", 300), ("15m", 900), ("30m", 1800),
    ("1h", 3600), ("4h", 14400), ("1d", 86400),
])
def test_detect_tf_standard_timeframes(tf, delta):
    assert _detect_tf(_df(delta)) == tf


def test_detect_tf_handles_empty_and_short():
    assert _detect_tf(pl.DataFrame({"time": []})) == "unknown"
    assert _detect_tf(_df(3600, n=1)) == "unknown"


def test_detect_tf_custom_uses_seconds_not_microseconds():
    # 7 s n'est aucune TF standard → custom, mais en SECONDES (pas 7e6).
    assert _detect_tf(_df(7)) == "custom_7s"
