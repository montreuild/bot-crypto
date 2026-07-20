"""Tests — app.core.derivatives (DerivativesStore) + stratégie derivatives_reversion.

Réseau mocké (l'environnement de CI/test ne joint pas les exchanges). On valide :
parsing, cache Parquet, alignement causal, z-scores, dégradation gracieuse, et
la logique de veto/boost dérivés de la stratégie.
"""
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.core.derivatives as dv
from app.core.derivatives import DerivativesStore, to_perp_symbol
from app.core.indicators import precompute_df
from app.strategies.derivatives_reversion import Strategy


class _FakeExchange:
    """Exchange ccxt simulé : renvoie funding/OI déterministes (pas de réseau)."""
    def __init__(self, t0_ms):
        self.t0 = t0_ms

    def fetch_funding_rate_history(self, symbol, limit=1000):
        return [{"timestamp": self.t0 + i * 3600_000, "fundingRate": 0.0001 * (i % 5 - 2)}
                for i in range(60)]

    def fetch_open_interest_history(self, symbol, period, limit=500):
        return [{"timestamp": self.t0 + i * 3600_000, "openInterestValue": 1e9 + i * 1e6}
                for i in range(60)]


def _ohlcv(n=80, t0=datetime(2024, 1, 1)):
    close = 30000 + np.cumsum(np.random.RandomState(0).randn(n) * 50)
    return pl.DataFrame({
        "time": [t0 + timedelta(hours=i) for i in range(n)],
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0)})


class TestDerivativesStore:
    def test_perp_symbol(self):
        assert to_perp_symbol("BTC/USDC") == "BTCUSDT"
        assert to_perp_symbol("ETH/USDT") == "ETHUSDT"

    def test_fetch_funding_parses_and_caches(self, tmp_path):
        st = DerivativesStore(base_dir=str(tmp_path))
        ex = _FakeExchange(int(datetime(2024, 1, 1).timestamp() * 1000))
        df = st.fetch_funding(ex, "BTC/USDC")
        assert len(df) == 60 and "value" in df.columns
        # persistance
        assert (tmp_path / "BTCUSDT__funding.parquet").exists()

    def test_http_endpoints_graceful_without_network(self, tmp_path, monkeypatch):
        """Sans réseau (GET → None), fetch renvoie un df vide, jamais d'exception."""
        monkeypatch.setattr(dv, "_http_get_json", lambda url, timeout=8.0: None)
        st = DerivativesStore(base_dir=str(tmp_path))
        assert len(st.fetch_long_short_ratio("BTC/USDC")) == 0
        assert len(st.fetch_taker_ratio("BTC/USDC")) == 0

    def test_http_parsing(self, tmp_path, monkeypatch):
        # Format OKX rubik : {"code":"0","data":[[ts, ratio], ...]}
        t0 = int(datetime(2024, 1, 1).timestamp() * 1000)
        fake = {"code": "0",
                "data": [[str(t0 + i * 3600_000), str(1.0 + 0.1 * i)] for i in range(40)]}
        monkeypatch.setattr(dv, "_http_get_json", lambda url, timeout=8.0: fake)
        st = DerivativesStore(base_dir=str(tmp_path))
        df = st.fetch_long_short_ratio("BTC/USDC")
        assert len(df) == 40 and df["value"][0] == pytest.approx(1.0)

    def test_taker_volume_buy_sell_ratio(self, tmp_path, monkeypatch):
        # taker-volume OKX : data[[ts, sellVol, buyVol]] → value = buyVol/sellVol
        t0 = int(datetime(2024, 1, 1).timestamp() * 1000)
        fake = {"code": "0", "data": [
            [str(t0),            "100", "150"],   # buy/sell = 1.5
            [str(t0 + 3600_000), "200", "100"],   # buy/sell = 0.5
        ]}
        monkeypatch.setattr(dv, "_http_get_json", lambda url, timeout=8.0: fake)
        st = DerivativesStore(base_dir=str(tmp_path))
        df = st.fetch_taker_ratio("BTC/USDC").sort("time")
        assert df["value"][0] == pytest.approx(1.5)
        assert df["value"][1] == pytest.approx(0.5)

    def test_align_adds_columns_and_zscores(self, tmp_path, monkeypatch):
        t0 = int(datetime(2024, 1, 1).timestamp() * 1000)
        ls = {"code": "0",
              "data": [[str(t0 + i * 3600_000), str(1.5 + 0.2 * np.sin(i / 5))] for i in range(80)]}
        # taker-volume : [ts, sellVol, buyVol] avec sellVol=1.0 → value = buyVol
        tk = {"code": "0",
              "data": [[str(t0 + i * 3600_000), "1.0", str(1.0 + 0.1 * np.cos(i / 5))] for i in range(80)]}
        calls = {"n": 0}
        def fake_get(url, timeout=8.0):
            calls["n"] += 1
            return ls if "long-short-account-ratio" in url else tk
        monkeypatch.setattr(dv, "_http_get_json", fake_get)
        st = DerivativesStore(base_dir=str(tmp_path))
        ex = _FakeExchange(t0)
        out = st.align_to_ohlcv(_ohlcv(80), "BTC/USDC", exchange=ex, refresh=True, z_window=20)
        # colonnes dérivées présentes + z-scores
        for col in ("funding_rate", "funding_z", "open_interest", "long_short_ratio", "lsr_z",
                    "taker_buy_sell_ratio", "taker_z"):
            assert col in out.columns, f"colonne {col} manquante"
        assert len(out) == 80          # alignement ne change pas la longueur OHLCV

    def test_align_graceful_when_no_data(self, tmp_path):
        """Sans cache ni réseau : align renvoie le df OHLCV inchangé (pas de crash)."""
        st = DerivativesStore(base_dir=str(tmp_path))
        df = _ohlcv(50)
        out = st.align_to_ohlcv(df, "BTC/USDC", exchange=None, refresh=False)
        assert len(out) == 50
        # aucune colonne dérivée fabriquée à partir de rien
        assert "funding_z" not in out.columns


def _ranging_df(n=620, seed=2):
    """Marché en range (oscillation) pour produire des extrêmes de range fadables.
    n>600 pour dépasser le warmup (SMA200) et couvrir plusieurs cycles."""
    rng = np.random.RandomState(seed)
    t = np.arange(n)
    base = 30000 + 600 * np.sin(t / 12.0) + rng.randn(n) * 60
    close = base
    return pl.DataFrame({
        "time": [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(n)],
        "open": close, "high": close * 1.002, "low": close * 0.998,
        "close": close, "volume": rng.uniform(100, 200, n)})


class TestReversionStrategy:
    def _params(self, **kw):
        base = {"use_derivatives": True}
        base.update(kw)
        return {"derivatives_reversion": base}

    def test_valid_dict_and_registry(self):
        from app.engine.registry import get_param_spaces, get_strategy_timeframes
        assert "derivatives_reversion" in get_param_spaces()
        assert get_strategy_timeframes()["derivatives_reversion"] == ["1h", "4h"]
        s = Strategy()
        r = s.score(precompute_df(_ranging_df()), self._params(), symbol="BTC/USDC")
        assert r["side"] in ("long", "short", "none") and 0 <= r["score"] <= 1

    def test_funding_veto_blocks_long(self):
        """funding_z extrême positif (longs surchargés) doit VETO un long de bas de range."""
        s = Strategy()
        df = precompute_df(_ranging_df())
        # force un contexte de bas de range sur la dernière barre + funding extrême
        n = len(df)
        df = df.with_columns(pl.Series("funding_z", [3.0] * n))   # longs surchargés partout
        emitted_long = False
        for end in range(260, n):
            r = s.score(df[:end], self._params(funding_z_extreme=2.0), symbol="X")
            if r["side"] == "long":
                emitted_long = True
                break
        assert not emitted_long, "Le veto funding (z=3 ≥ 2) aurait dû bloquer tous les longs"

    def test_ohlcv_only_path_runs(self):
        """Sans colonnes dérivées : fallback OHLCV pur, pas de crash, signal valide.
        adx_range_max élevé pour isoler la logique range-position (pas le gate ADX)."""
        s = Strategy()
        df = precompute_df(_ranging_df())
        got = [s.score(df[:e], self._params(use_derivatives=False, adx_range_max=99),
                       symbol="Y")["side"]
               for e in range(260, len(df), 4)]
        assert all(x in ("long", "short", "none") for x in got)
        assert any(x in ("long", "short") for x in got), "devrait fader des extrêmes de range"

    def test_no_ml_dependency(self):
        import app.strategies.derivatives_reversion as mod
        src = open(mod.__file__).read()
        assert "lightgbm" not in src and "sklearn" not in src
