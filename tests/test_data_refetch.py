"""Tests — robustesse du cache OHLCV (ordre des colonnes) + endpoints /api/data."""
import json
import os
import sys

import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.core.candle_store as cs
from app.api import state
from app.api.routes import data as droute


def test_load_forces_canonical_column_order(tmp_path):
    """Régression : un Parquet aux colonnes désordonnées (time en dernier, 0
    ligne — cas produit par un fetch tiers) doit être rechargé en ordre canonique
    pour que pl.concat/vstack ne casse plus (« column names don't match »)."""
    store = cs.CandleStore(base_dir=str(tmp_path))
    path = store._path("ETH/USDC", "4h")
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(schema={"open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
                         "close": pl.Float64, "volume": pl.Float64,
                         "time": pl.Datetime("ms")}).write_parquet(path)
    loaded = store._load(path)
    assert loaded.columns == ["time", "open", "high", "low", "close", "volume"]
    # le concat qui plantait auparavant fonctionne désormais
    fresh = cs.CandleStore._raw_to_df([[1704067200000, 2200.0, 2210.0, 2190.0, 2205.0, 100.0]])
    assert pl.concat([loaded, fresh]).height == 1


def test_refetch_requires_symbol():
    state.cfg = {"scanner": {"symbols": [], "timeframes": ["4h"]}}
    r = droute.data_refetch(None, symbol=None, tf=None)
    assert r.status_code == 400
    assert "symbole" in json.loads(r.body)["error"].lower()


def test_data_status_shape():
    resp = droute.data_status()
    assert resp.status_code == 200
    assert "datasets" in json.loads(resp.body)


def test_all_stats_skips_nested_ohlcv_copy(tmp_path):
    """Régression UI /data : un rglob listait aussi ``ohlcv/data/<SYMBOL>/``,
    donc deux lignes (symbole, tf) et des clés React dupliquées."""
    from datetime import datetime, timezone

    store = cs.CandleStore(base_dir=str(tmp_path))
    df = pl.DataFrame({
        "time": [datetime(2024, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)],
        "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [10.0],
    }).with_columns(pl.col("time").cast(pl.Datetime("ms")))
    canon = tmp_path / "BTC_USDC" / "1h.parquet"
    nested = tmp_path / "data" / "BTC_USDC" / "1h.parquet"
    canon.parent.mkdir(parents=True)
    nested.parent.mkdir(parents=True)
    df.write_parquet(canon)
    df.write_parquet(nested)
    rows = store.all_stats()
    keys = [(r["symbol"], r["tf"]) for r in rows]
    assert keys.count(("BTC/USDC", "1h")) == 1
    assert len(rows) == 1
