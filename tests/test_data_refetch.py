"""Tests — robustesse du cache OHLCV (ordre des colonnes) + endpoints /api/data."""
import json
import os
import sys

import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.core.candle_store as cs
from app.api.routes import data as droute
from app.api import state


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
