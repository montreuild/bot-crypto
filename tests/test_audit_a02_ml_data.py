"""Constats A-02 / M-01–M-06 / D-03–D-05 / L-16 / A-11."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl


def test_compute_jobs_runs_inline(monkeypatch):
    from app.engine.compute_jobs import run_strategy_job

    n = 80
    start = datetime(2024, 1, 1)
    times = [start + timedelta(hours=i) for i in range(n)]
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 0.2, n))
    df = pl.DataFrame({
        "time": times, "open": close, "high": close + 0.5,
        "low": close - 0.5, "close": close, "volume": np.ones(n) * 10,
    })
    cfg = {
        "trading": {"timeframe": "1h", "score_threshold": 0.55},
        "backtest": {},
        "strategies": {"enabled": []},
        "risk": {},
    }
    # DummyLong peut ne pas exister — on teste le contrat d'erreur propre.
    name, entry = run_strategy_job({
        "name": "__missing_strategy__",
        "cfg": cfg, "df": df, "symbol": "BTC/USDC", "timeframe": "1h",
    })
    assert name == "__missing_strategy__"
    assert "error" in entry
    assert entry.get("trades") == []


def test_detect_ohlcv_gaps_crypto_flags_hole():
    from app.core.ohlcv_gaps import detect_ohlcv_gaps
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    times = [t0, t0 + timedelta(hours=1), t0 + timedelta(hours=5)]
    df = pl.DataFrame({
        "time": times, "open": [1, 1, 1], "high": [1, 1, 1],
        "low": [1, 1, 1], "close": [1, 1, 1], "volume": [1, 1, 1],
    })
    gaps = detect_ohlcv_gaps(df, "1h")
    assert len(gaps) == 1
    assert gaps[0]["gap_bars"] >= 2


def test_detect_ohlcv_gaps_xpar_weekend_is_not_a_hole():
    from app.core.market_calendar import get_calendar
    from app.core.ohlcv_gaps import detect_ohlcv_gaps
    cal = get_calendar("XPAR")
    # vendredi 17:00 → lundi 09:00
    fri = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)  # 17:00 Paris
    mon = datetime(2026, 8, 17, 7, 0, tzinfo=timezone.utc)   # 09:00 Paris
    df = pl.DataFrame({
        "time": [fri, mon],
        "open": [1.0, 1.0], "high": [1.0, 1.0],
        "low": [1.0, 1.0], "close": [1.0, 1.0], "volume": [1.0, 1.0],
    })
    gaps = detect_ohlcv_gaps(df, "1h", calendar=cal)
    assert gaps == []


def test_oos_tracker_atomic_write(tmp_path, monkeypatch):
    import app.core.oos_tracker as ot
    monkeypatch.setattr(ot, "_TRACKER_PATH", str(tmp_path / "oos_tracker.json"))
    ot.save_records({"slot-a": {"n": 1}})
    ot.save_records({"slot-b": {"n": 2}})
    data = ot.load_oos_tracker()
    assert data["slot-a"]["n"] == 1
    assert data["slot-b"]["n"] == 2
    # pas de .tmp orphelin
    assert not (tmp_path / "oos_tracker.json.tmp").exists()


def test_feature_store_catalog_hash_changes_path(tmp_path):
    from app.core import feature_store as fs
    store = fs.FeatureStore(base_dir=str(tmp_path))
    p1 = store._path("BTC/USDC", "1h")
    assert p1.suffix == ".parquet"
    assert "_" in p1.stem
    # enregistrer un provider change le hash → autre fichier
    fs.register_provider(fs.FeatureProvider(
        name="hashprobe", build=lambda df: df, version="9"))
    p2 = store._path("BTC/USDC", "1h")
    assert p1.name != p2.name


def test_feature_store_evicts_oldest(tmp_path, monkeypatch):
    from app.core import feature_store as fs
    monkeypatch.setattr(fs, "FEATURE_STORE_MAX_FILES", 2)
    monkeypatch.setattr(fs, "FEATURE_STORE_MAX_BYTES", 10 ** 12)
    store = fs.FeatureStore(base_dir=str(tmp_path))
    for i, name in enumerate(("a", "b", "c")):
        p = tmp_path / f"{name}.parquet"
        pl.DataFrame({"x": [i]}).write_parquet(p)
        os.utime(p, (i, i))
    keep = tmp_path / "c.parquet"
    store._evict_if_needed(keep=keep)
    left = {p.name for p in tmp_path.glob("*.parquet")}
    assert "a.parquet" not in left
    assert "c.parquet" in left


def test_bars_held_counts_session_bars_not_wall_clock():
    from app.live.position_manage_mixin import bars_held_from_ohlcv
    # vendredi 16:00 → lundi 10:00 : horloge ≈ 66 h, mais 3 barres 1h seulement
    fri = datetime(2026, 8, 14, 16, 0)
    times = [
        fri,
        fri + timedelta(hours=1),
        datetime(2026, 8, 17, 9, 0),
        datetime(2026, 8, 17, 10, 0),
    ]
    df = pl.DataFrame({
        "time": times, "open": [1] * 4, "high": [1] * 4,
        "low": [1] * 4, "close": [1] * 4, "volume": [1] * 4,
    })
    open_time = fri.timestamp()
    held = bars_held_from_ohlcv(df, open_time, 3600)
    assert held == 4
    # sans df : horloge murale >> 4
    wall = bars_held_from_ohlcv(None, open_time, 3600)
    assert wall > 50


def test_auc_floor_is_auc_weak():
    from app.ml.overfitting_gate import AUC_WEAK
    from app.ml.policy import GateConfig, decide_gate
    assert GateConfig().auc_floor == AUC_WEAK
    r = decide_gate({"auc_amp": 0.56}, None)
    assert r.decision in ("initial", "promote")
    r2 = decide_gate({"auc_amp": 0.50}, None)
    assert r2.decision == "keep"


def test_html_redirects_have_a_sunset():
    from app.api.main import HTML_LEGACY_ALIASES, HTML_REDIRECTS_SUNSET
    assert HTML_REDIRECTS_SUNSET >= "2026-12-31"
    assert "/backtest" in HTML_LEGACY_ALIASES
    assert "/bots-v2" in HTML_LEGACY_ALIASES
