"""app.ml.train_runner — chargement offline (cache Parquet local, ZÉRO appel
exchange), dry-run vs publication gatée, window sweep sur holdout commun
(ML-02 §3.3 / tâche E3 — reproductibilité de l'entraînement)."""
import datetime as dt

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

import app.core.candle_store as candle_store_mod
import app.ml.model_registry as registry
from app.ml.train_runner import load_offline_ohlcv, train_and_publish, window_sweep

# Les artefacts sont indexés par RECETTE, plus par nom de stratégie
# (fracture b) : opus_omnibus_v11 consomme omnibus_v4_multi.
_RECIPE = "omnibus_v4_multi"


def _make_ohlcv(n: int, seed: int = 1, start_price: float = 100.0,
                start: dt.datetime = dt.datetime(2020, 1, 1)) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    times = [start + dt.timedelta(hours=i) for i in range(n)]
    rets = rng.normal(0, 0.01, n)
    close = start_price * np.cumprod(1 + rets)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.002, n)))
    volume = rng.uniform(100, 1000, n)
    return pl.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close, "volume": volume})


@pytest.fixture
def seeded_store(tmp_path):
    """Injecte un CandleStore isolé (aucun appel exchange) via get_store.set,
    et le désarme après le test (ré-active la création paresseuse normale)."""
    store = candle_store_mod.CandleStore(base_dir=str(tmp_path / "ohlcv_cache"))
    candle_store_mod.get_store.set(store)
    yield store
    candle_store_mod.get_store.set(None)


def _seed(store, symbol, tf, df):
    store._save(store._path(symbol, tf), df.with_columns(pl.col("time").cast(pl.Datetime("ms"))))


# ─────────────────────────────────────────────────────────────────────────────
#  load_offline_ohlcv
# ─────────────────────────────────────────────────────────────────────────────
def test_load_offline_ohlcv_reads_cache(seeded_store):
    df = _make_ohlcv(300)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    got = load_offline_ohlcv("BTC/USDC", "1h")
    assert got is not None
    assert len(got) == 300


def test_load_offline_ohlcv_none_when_absent(seeded_store):
    assert load_offline_ohlcv("BTC/USDC", "1h") is None


def test_load_offline_ohlcv_as_of_filters_future_bars(seeded_store):
    df = _make_ohlcv(300)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    cutoff = df["time"][149]
    got = load_offline_ohlcv("BTC/USDC", "1h", as_of=str(cutoff))
    assert got is not None
    assert len(got) == 150
    assert got["time"][-1] <= cutoff


def test_load_offline_ohlcv_window_bars_tails_after_as_of(seeded_store):
    df = _make_ohlcv(300)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    cutoff = df["time"][199]
    got = load_offline_ohlcv("BTC/USDC", "1h", as_of=str(cutoff), window_bars=50)
    assert got is not None
    assert len(got) == 50
    assert got["time"][-1] <= cutoff


# ─────────────────────────────────────────────────────────────────────────────
#  train_and_publish — dry-run vs publication réelle
# ─────────────────────────────────────────────────────────────────────────────
_FAST_PARAMS = {"n_estimators": 20, "num_leaves": 7, "gate_holdout_bars": 250,
                "gate_min_window_bars": 700, "gate_auc_floor": 0.0}


@pytest.mark.slow
def test_train_and_publish_dry_run_writes_nothing(seeded_store, tmp_path):
    df = _make_ohlcv(1400, seed=11)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = train_and_publish("opus_omnibus_v11", "BTC/USDC", "1h",
                               params=_FAST_PARAMS, publish=False, base_dir=registry_base)

    assert result["decision"].startswith("dry_run_would_")
    assert "note" in result
    versions = registry.list_versions("BTC/USDC", "1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 0  # dry-run : rien n'est écrit


@pytest.mark.slow
def test_train_and_publish_publish_true_writes_registry(seeded_store, tmp_path):
    df = _make_ohlcv(1400, seed=12)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = train_and_publish("opus_omnibus_v11", "BTC/USDC", "1h",
                               params=_FAST_PARAMS, publish=True, base_dir=registry_base)

    assert result["decision"] in ("initial", "promote")
    versions = registry.list_versions("BTC/USDC", "1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 1


def test_train_and_publish_no_cached_data_fails_cleanly(seeded_store, tmp_path):
    result = train_and_publish("opus_omnibus_v11", "BTC/USDC", "1h",
                               params=_FAST_PARAMS, publish=False,
                               base_dir=str(tmp_path / "models"))
    assert result["decision"] == "failed"


# ─────────────────────────────────────────────────────────────────────────────
#  window_sweep
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_window_sweep_compares_without_publishing_by_default(seeded_store, tmp_path):
    df = _make_ohlcv(1800, seed=13)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = window_sweep("opus_omnibus_v11", "BTC/USDC", "1h", [800, 1200],
                          params=_FAST_PARAMS, publish_best=False, base_dir=registry_base)

    assert len(result["candidates"]) == 2
    assert result["best_window_bars"] in (800, 1200)
    assert "gate_decision" not in result
    versions = registry.list_versions("BTC/USDC", "1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 0  # comparaison seule, rien publié


@pytest.mark.slow
def test_window_sweep_publish_best_writes_only_one_version(seeded_store, tmp_path):
    df = _make_ohlcv(1800, seed=14)
    _seed(seeded_store, "BTC/USDC", "1h", df)
    registry_base = str(tmp_path / "models")

    result = window_sweep("opus_omnibus_v11", "BTC/USDC", "1h", [800, 1200],
                          params=dict(_FAST_PARAMS, gate_auc_floor=0.0),
                          publish_best=True, base_dir=registry_base)

    assert result["gate_decision"] in ("initial", "promote")
    versions = registry.list_versions("BTC/USDC", "1h", _RECIPE, base_dir=registry_base)
    assert len(versions) == 1  # seul le meilleur candidat est publié, pas les deux


def test_window_sweep_no_cached_data_returns_error(seeded_store, tmp_path):
    result = window_sweep("opus_omnibus_v11", "BTC/USDC", "1h", [800, 1200],
                          params=_FAST_PARAMS, base_dir=str(tmp_path / "models"))
    assert "error" in result
