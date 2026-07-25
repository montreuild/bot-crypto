"""Backtester.ml_mode (ML-02 §4.1) — frozen (résolution registre + garde
anti-chevauchement + repli visible), inline (comportement historique
inchangé), simulated_live (rafraîchissement périodique via app.ml.policy)."""
import datetime as dt

import numpy as np
import polars as pl
import pytest

pytest.importorskip("lightgbm")

import app.ml.model_registry as registry
from app.engine.backtest import Backtester, _resolve_frozen_ml_model
from app.engine.engine import Engine
from app.ml.backend.persistence import save_amp_dir_bundle


def _cfg(strategy_params=None):
    return {
        "trading": {"capital": 1000, "risk_per_trade": 0.01, "timeframe": "1h",
                    "paper_mode": True, "taker_fee": 0.001, "maker_fee": 0.0004,
                    "score_threshold": 0.5, "borrow_rate_daily": 0.0002},
        "backtest": {"spread_pct": 0.0005, "atr_stop_mult": 2.0,
                     "trail_wide": 2.5, "trail_normal": 2.0, "trail_lock": 1.5,
                     "trail_tight": 1.0, "grace_bars": 4, "breakeven_r": 1.2,
                     "lock_r": 2.5, "tight_r": 4.0, "lock_ratio": 0.6,
                     "use_swing": False, "max_notional_pct": 0.2},
        "strategies": {"enabled": ["opus_omnibus_v11"]},
        "strategy_params": strategy_params or {},
    }


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


def _v11():
    from app.strategies.opus_omnibus_v11 import Strategy
    return Strategy()


def _train_tiny_booster(seed: int):
    import lightgbm as lgb
    rng = np.random.RandomState(seed)
    X = rng.rand(200, 3).astype(np.float32)
    y = (X[:, 0] > 0.5).astype(np.int32)
    ds = lgb.Dataset(X, label=y, free_raw_data=False)
    return lgb.train({"objective": "binary", "verbosity": -1, "num_leaves": 4}, ds, num_boost_round=3)


def _publish_toy_model(tmp_path, symbol, tf, recipe, train_end, train_start=None):
    prefix = str(tmp_path / "toy_src")
    amp, dir_ = _train_tiny_booster(1), _train_tiny_booster(2)
    save_amp_dir_bundle(prefix, tf, amp, dir_, ["f0", "f1", "f2"], {}, 0.6, {})
    return registry.publish(symbol, tf, recipe, prefix, train_start=train_start,
                            train_end=train_end, decision="promote",
                            base_dir=str(tmp_path / "models"))


# ─────────────────────────────────────────────────────────────────────────────
#  Construction / validation
# ─────────────────────────────────────────────────────────────────────────────
def test_invalid_ml_mode_raises():
    eng = Engine()
    with pytest.raises(ValueError):
        Backtester(eng, _cfg(), ml_mode="bogus")


def test_ml_mode_is_the_only_lever(tmp_path):
    """``use_pretrained_ml`` a été retiré : un seul réglage pilote le mode."""
    df = _make_ohlcv(1400)
    strat = _v11()
    strat.model_dir = str(tmp_path / "models")  # isole du vrai models/ du dépôt
    eng = Engine()
    eng.register(strat, silent=True)

    bt = Backtester(eng, _cfg(), ml_mode="inline")
    result = bt.run(df, "BTC/USDC", "1h")
    assert result.ml_info["mode"] == "inline"


# ─────────────────────────────────────────────────────────────────────────────
#  frozen : résolution registre + anti-chevauchement + repli visible
# ─────────────────────────────────────────────────────────────────────────────
def test_resolve_frozen_ml_model_loads_and_reports_no_overlap(tmp_path):
    registry_base = str(tmp_path / "models")
    art = _publish_toy_model(tmp_path, "BTC/USDC", "1h", "opus_omnibus_v11",
                             train_end="2020-01-01T00:00:00")
    assert art is not None
    strat = _v11()
    strat.model_dir = registry_base

    entry = _resolve_frozen_ml_model(strat, "BTC/USDC", "1h",
                                     window_start="2020-06-01T00:00:00",
                                     window_end="2020-12-01T00:00:00")
    assert entry["resolved"] is True
    assert entry["fallback_to_inline"] is False
    assert entry["overlap_warning"] is False
    assert entry["version_id"] == art.version_id
    assert strat.is_trained


def test_resolve_frozen_ml_model_flags_overlap_at_boundary(tmp_path):
    """train_end == window_start (cas limite) : resolve(as_of=window_start)
    l'accepte, mais la fenêtre d'entraînement touche la fenêtre backtestée —
    doit être signalé, pas silencieusement ignoré (ML-02 §4.1)."""
    registry_base = str(tmp_path / "models")
    boundary = "2020-06-01T00:00:00"
    art = _publish_toy_model(tmp_path, "BTC/USDC", "1h", "opus_omnibus_v11",
                             train_start="2020-01-01T00:00:00", train_end=boundary)
    assert art is not None
    strat = _v11()
    strat.model_dir = registry_base

    entry = _resolve_frozen_ml_model(strat, "BTC/USDC", "1h",
                                     window_start=boundary, window_end="2020-12-01T00:00:00")
    assert entry["resolved"] is True
    assert entry["overlap_warning"] is True


def test_resolve_frozen_ml_model_no_artifact_is_visible_fallback(tmp_path):
    strat = _v11()
    strat.model_dir = str(tmp_path / "models_empty")
    entry = _resolve_frozen_ml_model(strat, "BTC/USDC", "1h",
                                     window_start="2020-01-01T00:00:00",
                                     window_end="2020-06-01T00:00:00")
    assert entry == {"resolved": False, "fallback_to_inline": True}


@pytest.mark.slow
def test_backtest_frozen_mode_falls_back_to_inline_when_no_model(tmp_path):
    df = _make_ohlcv(1400, seed=5)
    strat = _v11()
    strat.model_dir = str(tmp_path / "models")  # vide -> aucun artefact résoluble
    eng = Engine()
    eng.register(strat, silent=True)

    cfg = _cfg({"opus_omnibus_v11": {"n_estimators": 20, "num_leaves": 7}})
    bt = Backtester(eng, cfg, ml_mode="frozen")
    result = bt.run(df, "BTC/USDC", "1h")

    info = result.ml_info["models"]["opus_omnibus_v11"]
    assert info["requested_mode"] == "frozen"
    assert info["fallback_to_inline"] is True
    assert strat.is_trained  # l'entraînement inline de secours a bien eu lieu


# ─────────────────────────────────────────────────────────────────────────────
#  simulated_live : rafraîchissement périodique + publication registre
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_backtest_simulated_live_refreshes_and_publishes(tmp_path):
    df = _make_ohlcv(1000, seed=9)
    strat = _v11()
    registry_base = str(tmp_path / "models")
    strat.model_dir = registry_base
    eng = Engine()
    eng.register(strat, silent=True)

    cfg = _cfg({"opus_omnibus_v11": {
        "n_estimators": 20, "num_leaves": 7, "warmup_bars": 200,
        "retrain_every": 250, "gate_holdout_bars": 150,
        "gate_min_window_bars": 400, "gate_auc_floor": 0.0,
    }})
    bt = Backtester(eng, cfg, ml_mode="simulated_live")
    result = bt.run(df, "BTC/USDC", "1h")

    info = result.ml_info["models"]["opus_omnibus_v11"]
    assert result.ml_info["mode"] == "simulated_live"
    assert info["cadence_bars"] == 250
    assert info["n_refreshes"] >= 1
    # Au moins un rafraîchissement a fini par voir assez de données pour
    # entraîner réellement (skipped tant que la fenêtre est trop courte).
    assert any(d["decision"] in ("initial", "promote") for d in info["decisions"])

    versions = registry.list_versions("BTC/USDC", "1h", "opus_omnibus_v11", base_dir=registry_base)
    assert len(versions) >= 1


@pytest.mark.slow
def test_backtest_simulated_live_without_cadence_behaves_like_frozen(tmp_path):
    """Une stratégie sans retrain_every configuré (modèle figé pour toujours,
    ex. familles V4 pretrained) doit se comporter comme "frozen" même sous
    ml_mode="simulated_live" — pas de tentative de réentraînement périodique."""
    registry_base = str(tmp_path / "models")
    art = _publish_toy_model(tmp_path, "BTC/USDC", "1h", "opus_omnibus_v11",
                             train_end="2020-01-01T00:00:00")
    assert art is not None
    df = _make_ohlcv(1400, seed=3)
    strat = _v11()
    strat.model_dir = registry_base
    eng = Engine()
    eng.register(strat, silent=True)

    # retrain_every=0 -> pas de cadence -> traité comme frozen.
    cfg = _cfg({"opus_omnibus_v11": {"retrain_every": 0}})
    bt = Backtester(eng, cfg, ml_mode="simulated_live")
    result = bt.run(df, "BTC/USDC", "1h")

    info = result.ml_info["models"]["opus_omnibus_v11"]
    assert "cadence_bars" not in info  # pas passé par la branche simulated_live
    assert info["resolved"] is True
    assert info["version_id"] == art.version_id
