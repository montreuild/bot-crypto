"""Tests du FeatureStore — correction de l'incrémental, du hash strict et du partage."""
import numpy as np
import polars as pl
import pytest

from app.core.feature_store import (
    FeatureStore, FeatureProvider, register_provider, get_feature_store,
    WARMUP_FULL,
)


def _make_ohlcv(n: int, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0, 1, n)
    low = close - rng.uniform(0, 1, n)
    open_ = close + rng.normal(0, 0.5, n)
    vol = rng.uniform(10, 100, n)
    t = pl.datetime_range(pl.datetime(2020, 1, 1), pl.datetime(2030, 1, 1),
                          interval="1h", eager=True).head(n)
    return pl.DataFrame({
        "time": t, "open": open_, "high": high, "low": low,
        "close": close, "volume": vol,
    })


def _recursive_build(df: pl.DataFrame) -> pl.DataFrame:
    """Provider de test : indicateurs récursifs (EMA) + glissants (rolling mean/std)."""
    c = df["close"]
    return pl.DataFrame({
        "ema200": c.ewm_mean(span=200, adjust=False),
        "ema20": c.ewm_mean(span=20, adjust=False),
        "sma50": c.rolling_mean(50),
        "std20": c.rolling_std(20),
    })


def _provider(version="1", warmup=1500):
    return FeatureProvider(name="testprov", build=_recursive_build,
                           warmup=warmup, version=version)


def test_full_build_matches_direct(tmp_path):
    store = FeatureStore(base_dir=str(tmp_path))
    df = _make_ohlcv(800)
    out = store.get_or_build("BTC/USDC", "1h", df, _provider())
    direct = _recursive_build(df)
    assert out.height == df.height
    for col in ("ema200", "ema20", "sma50", "std20"):
        a = out[col].to_numpy()
        b = direct[col].to_numpy()
        np.testing.assert_allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True)


def test_incremental_equals_full_rebuild(tmp_path):
    """Cœur du contrat : append incrémental ≈ recalcul complet (warmup convergence)."""
    store = FeatureStore(base_dir=str(tmp_path))
    df = _make_ohlcv(2000)

    # 1) Cache initial sur les 1800 premières bougies.
    store.get_or_build("BTC/USDC", "1h", df.head(1800), _provider())
    # 2) Ajout incrémental des 200 dernières.
    incr = store.get_or_build("BTC/USDC", "1h", df, _provider())

    # Référence : recalcul complet direct.
    direct = _recursive_build(df)
    assert incr.height == df.height
    for col in ("ema200", "ema20", "sma50", "std20"):
        np.testing.assert_allclose(
            incr[col].to_numpy(), direct[col].to_numpy(),
            rtol=1e-6, atol=1e-6, equal_nan=True,
            err_msg=f"divergence incrémental/complet sur {col}")


def test_incremental_path_dependent_with_full_warmup(tmp_path):
    """Features causales path-dépendantes (cumsum) : WARMUP_FULL garantit l'exactitude."""
    store = FeatureStore(base_dir=str(tmp_path))
    df = _make_ohlcv(1500)

    def _cumulative_build(d: pl.DataFrame) -> pl.DataFrame:
        # OBV-like : dépend de tout l'historique depuis l'index 0.
        return pl.DataFrame({
            "obv": (d["close"].diff().sign().fill_null(0) * d["volume"]).cum_sum(),
        })

    prov = lambda: FeatureProvider("cum", _cumulative_build,
                                   warmup=WARMUP_FULL, version="1")
    store.get_or_build("BTC/USDC", "1h", df.head(1200), prov())
    incr = store.get_or_build("BTC/USDC", "1h", df, prov())
    direct = _cumulative_build(df)
    np.testing.assert_allclose(incr["obv"].to_numpy(), direct["obv"].to_numpy(),
                               rtol=0, atol=0, equal_nan=True)


def test_reuse_returns_cached(tmp_path):
    store = FeatureStore(base_dir=str(tmp_path))
    df = _make_ohlcv(600)
    store.get_or_build("ETH/USDC", "4h", df, _provider())
    # Deuxième appel sur la même fenêtre → relecture cache, valeurs identiques.
    again = store.get_or_build("ETH/USDC", "4h", df, _provider())
    direct = _recursive_build(df)
    np.testing.assert_allclose(again["ema200"].to_numpy(),
                               direct["ema200"].to_numpy(),
                               rtol=1e-9, atol=1e-9, equal_nan=True)


def test_hash_invalidation_rebuilds(tmp_path):
    """Si une bougie historique change, le cache doit être recalculé, pas servi périmé."""
    store = FeatureStore(base_dir=str(tmp_path))
    df = _make_ohlcv(700, seed=1)
    store.get_or_build("BTC/USDC", "1h", df, _provider())

    # Corrompt une bougie au milieu (close différent).
    df2 = df.clone()
    idx = 400
    new_close = float(df2["close"][idx]) + 50.0
    df2 = df2.with_columns(
        pl.when(pl.arange(0, df2.height) == idx)
        .then(pl.lit(new_close)).otherwise(pl.col("close")).alias("close"))

    out = store.get_or_build("BTC/USDC", "1h", df2, _provider())
    direct = _recursive_build(df2)
    # Les valeurs doivent refléter df2 (recalculé), pas df d'origine.
    np.testing.assert_allclose(out["ema20"].to_numpy(), direct["ema20"].to_numpy(),
                               rtol=1e-6, atol=1e-6, equal_nan=True)


def test_two_providers_share_catalog(tmp_path):
    """Deux providers distincts coexistent dans le même fichier (catalogue)."""
    store = FeatureStore(base_dir=str(tmp_path))
    df = _make_ohlcv(500)

    p1 = FeatureProvider("provA", lambda d: pl.DataFrame({"x": d["close"] * 2}),
                         warmup=300, version="1")
    p2 = FeatureProvider("provB", lambda d: pl.DataFrame({"y": d["close"] + 1}),
                         warmup=300, version="1")
    out1 = store.get_or_build("BTC/USDC", "1h", df, p1)
    out2 = store.get_or_build("BTC/USDC", "1h", df, p2)

    assert "x" in out1.columns and "y" in out2.columns
    # Le fichier doit contenir les deux providers.
    st = store.stats("BTC/USDC", "1h")
    assert set(st["providers"]) == {"provA", "provB"}
    np.testing.assert_allclose(out1["x"].to_numpy(), df["close"].to_numpy() * 2)
    np.testing.assert_allclose(out2["y"].to_numpy(), df["close"].to_numpy() + 1)


def test_version_bump_invalidates(tmp_path):
    store = FeatureStore(base_dir=str(tmp_path))
    df = _make_ohlcv(400)
    store.get_or_build("BTC/USDC", "1h", df,
                       FeatureProvider("pv", lambda d: pl.DataFrame({"z": d["close"]}),
                                       warmup=200, version="1"))
    out = store.get_or_build("BTC/USDC", "1h", df,
                             FeatureProvider("pv", lambda d: pl.DataFrame({"z": d["close"] * 10}),
                                             warmup=200, version="2"))
    np.testing.assert_allclose(out["z"].to_numpy(), df["close"].to_numpy() * 10)


def test_registry_sharing():
    p = register_provider(FeatureProvider("shared_x", _recursive_build, version="1"))
    p2 = register_provider(FeatureProvider("shared_x", _recursive_build, version="1"))
    assert p is p2  # même version → instance partagée
