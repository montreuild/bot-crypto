"""app.ml.trainer.MLStrategyTrainer — chargement via le registre ML, alerte
de fraîcheur, réentraînement gaté (ML-02). Aucun test préexistant sur ce
composant avant ce chantier."""
import datetime as dt

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

import app.ml.model_registry as registry
from app.ml.backend.persistence import save_amp_dir_bundle
from app.ml.trainer import MLStrategyTrainer


def _train_tiny_booster(seed: int):
    import lightgbm as lgb
    rng = np.random.RandomState(seed)
    X = rng.rand(200, 3).astype(np.float32)
    y = (X[:, 0] > 0.5).astype(np.int32)
    ds = lgb.Dataset(X, label=y, free_raw_data=False)
    return lgb.train({"objective": "binary", "verbosity": -1, "num_leaves": 4}, ds, num_boost_round=3)


def _publish_toy_model(tmp_path, symbol, tf, recipe, train_end):
    prefix = str(tmp_path / "toy_src")
    amp, dir_ = _train_tiny_booster(1), _train_tiny_booster(2)
    save_amp_dir_bundle(prefix, tf, amp, dir_, ["f0", "f1", "f2"], {}, 0.6, {})
    return registry.publish(symbol, tf, recipe, prefix, train_end=train_end,
                            decision="promote", base_dir=str(tmp_path / "models"))


def _v11():
    from app.strategies.opus_omnibus_v11 import Strategy
    return Strategy()


def _make_ohlcv(n: int, seed: int = 1, start_price: float = 100.0) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    start = dt.datetime(2020, 1, 1)
    times = [start + dt.timedelta(hours=i) for i in range(n)]
    rets = rng.normal(0, 0.01, n)
    close = start_price * np.cumprod(1 + rets)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.002, n)))
    volume = rng.uniform(100, 1000, n)
    return pl.DataFrame({"time": times, "open": open_, "high": high,
                         "low": low, "close": close, "volume": volume})


class _FakeScanner:
    def __init__(self, symbols, df=None):
        self._symbols = symbols
        self._df = df

    def get_symbols(self):
        return self._symbols

    def fetch_ohlcv(self, symbol, tf, limit):
        return self._df


# ─────────────────────────────────────────────────────────────────────────────
#  _resolve_symbol
# ─────────────────────────────────────────────────────────────────────────────
def test_resolve_symbol_prefers_btc():
    scanner = _FakeScanner(["ETH/USDC", "BTC/USDC", "XRP/USDC"])
    assert MLStrategyTrainer._resolve_symbol(scanner) == "BTC/USDC"


def test_resolve_symbol_falls_back_to_first_when_no_btc():
    scanner = _FakeScanner(["ETH/USDC", "XRP/USDC"])
    assert MLStrategyTrainer._resolve_symbol(scanner) == "ETH/USDC"


def test_resolve_symbol_none_scanner_returns_none():
    assert MLStrategyTrainer._resolve_symbol(None) is None


def test_resolve_symbol_empty_symbols_returns_none():
    assert MLStrategyTrainer._resolve_symbol(_FakeScanner([])) is None


# ─────────────────────────────────────────────────────────────────────────────
#  _freshness_warning
# ─────────────────────────────────────────────────────────────────────────────
def _art(train_end=None, version_id="v1"):
    return registry.ArtifactRef(path_prefix="x", symbol="BTC/USDC", tf="1h",
                                recipe="r", version_id=version_id,
                                train_end=train_end)


def test_freshness_warning_none_for_fresh_model():
    train_end = (dt.datetime.utcnow() - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    warn = MLStrategyTrainer._freshness_warning(_art(train_end=train_end), interval_h=6.0)
    assert warn is None


def test_freshness_warning_flags_stale_model():
    train_end = (dt.datetime.utcnow() - dt.timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%S")
    warn = MLStrategyTrainer._freshness_warning(_art(train_end=train_end), interval_h=6.0)
    assert warn is not None
    assert "vieux" in warn


def test_freshness_warning_undated_artifact_is_non_measurable():
    """Sans ``train_end``, la fraîcheur n'est pas mesurable — il faut le DIRE,
    pas retourner None (qui signifierait « modèle frais »)."""
    warn = MLStrategyTrainer._freshness_warning(_art(train_end=None), interval_h=6.0)
    assert warn is not None
    assert "non mesurable" in warn


# ─────────────────────────────────────────────────────────────────────────────
#  load_models
# ─────────────────────────────────────────────────────────────────────────────
def test_load_models_resolves_from_registry_and_schedules_future_retrain(tmp_path):
    registry_base = str(tmp_path / "models")
    art = _publish_toy_model(tmp_path, "BTC/USDC", "1h", "opus_omnibus_v11",
                             train_end="2020-01-01T00:00:00")
    assert art is not None

    strat = _v11()
    strat.model_dir = registry_base
    trainer = MLStrategyTrainer({"strategy_params": {}})
    scanner = _FakeScanner(["BTC/USDC"])

    trainer.load_models({"opus_omnibus_v11": strat}, ["1h"], scanner=scanner)

    assert strat.is_trained
    key = "opus_omnibus_v11@1h"
    assert trainer._retrain_at[key] > __import__("time").time()  # planifié dans le futur, pas immédiat
    assert strat.managed_externally is True


def test_load_models_no_model_schedules_immediate_retrain(tmp_path):
    strat = _v11()
    strat.model_dir = str(tmp_path / "models_empty")
    trainer = MLStrategyTrainer({"strategy_params": {}})
    scanner = _FakeScanner(["BTC/USDC"])

    trainer.load_models({"opus_omnibus_v11": strat}, ["1h"], scanner=scanner)

    assert trainer._retrain_at["opus_omnibus_v11@1h"] == 0
    assert not strat.is_trained


def test_load_models_without_scanner_does_not_crash(tmp_path):
    strat = _v11()
    strat.model_dir = str(tmp_path / "models_empty")
    trainer = MLStrategyTrainer({"strategy_params": {}})
    trainer.load_models({"opus_omnibus_v11": strat}, ["1h"], scanner=None)
    assert trainer._retrain_at["opus_omnibus_v11@1h"] == 0


# ─────────────────────────────────────────────────────────────────────────────
#  _retrain_thread — bout en bout avec le gate
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_retrain_thread_trains_and_publishes_when_enough_data(tmp_path):
    registry_base = str(tmp_path / "models")
    strat = _v11()
    strat.model_dir = registry_base
    df = _make_ohlcv(1400, seed=7)
    scanner = _FakeScanner(["BTC/USDC"], df=df)
    trainer = MLStrategyTrainer({"strategy_params": {}})

    strat_params = {"opus_omnibus_v11": {
        "n_estimators": 20, "num_leaves": 7,
        "gate_holdout_bars": 250, "gate_min_window_bars": 700, "gate_auc_floor": 0.0,
    }}
    trainer._retrain_thread("opus_omnibus_v11", strat, strat_params, "1h", scanner)

    assert strat.is_trained
    versions = registry.list_versions("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=registry_base)
    assert len(versions) >= 1


def test_retrain_thread_insufficient_data_does_not_crash_or_publish(tmp_path):
    registry_base = str(tmp_path / "models")
    strat = _v11()
    strat.model_dir = registry_base
    df = _make_ohlcv(500, seed=8)  # < plancher du gate
    scanner = _FakeScanner(["BTC/USDC"], df=df)
    trainer = MLStrategyTrainer({"strategy_params": {}})

    strat_params = {"opus_omnibus_v11": {
        "gate_holdout_bars": 250, "gate_min_window_bars": 700,
    }}
    trainer._retrain_thread("opus_omnibus_v11", strat, strat_params, "1h", scanner)

    assert not strat.is_trained
    versions = registry.list_versions("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=registry_base)
    assert len(versions) == 0


def test_retrain_thread_no_symbol_returns_without_crash():
    strat = _v11()
    trainer = MLStrategyTrainer({"strategy_params": {}})
    scanner = _FakeScanner([])  # aucun symbole
    trainer._retrain_thread("opus_omnibus_v11", strat, {}, "1h", scanner)
    assert not strat.is_trained
