"""Tests TVR / Snowball : signaux, trailing chandelier, pyramidage (scale-in)."""
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from app.core.trailing import ChandelierTrailingStop, TrailingStopManager
from app.engine.backtest import Backtester
from app.engine.engine import Engine
from app.strategies.snowball_pyramid import Strategy as SnowballStrategy
from app.strategies.tvr_trend import Strategy as TVRStrategy


def _make_df(n=3000, seed=7, drift_up=0.0008, drift_down=-0.0006):
    """OHLCV 4h synthétique : tendance haussière puis baissière."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2023, 1, 1)
    times = [t0 + timedelta(hours=4 * i) for i in range(n)]
    drift = np.concatenate([np.full(n // 2, drift_up), np.full(n - n // 2, drift_down)])
    rets = drift + rng.normal(0, 0.008, n)
    close = 30000 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pl.DataFrame({
        "time": times, "open": open_, "high": high, "low": low,
        "close": close, "volume": rng.uniform(100, 1000, n),
    })


_CFG = {
    "trading": {"capital": 10000, "risk_per_trade": 0.02, "score_threshold": 0.55,
                "timeframe": "4h", "taker_fee": 0.0005, "maker_fee": 0.0004},
    "backtest": {},
    "strategy_params": {},
}


# ── Registre ────────────────────────────────────────────────────────────────

def test_strategies_in_registry():
    from app.engine.registry import get_param_spaces
    ps = get_param_spaces()
    assert "tvr_trend" in ps
    assert "snowball_pyramid" in ps
    assert "max_units" in ps["snowball_pyramid"]


# ── Trailing Chandelier ─────────────────────────────────────────────────────

def test_chandelier_ratchets_with_peak():
    ch = ChandelierTrailingStop(mult=3.0)
    stop = ch.init(100.0, atr=2.0, side="long")
    assert stop == pytest.approx(94.0)
    # Le prix monte → le stop suit l'extrême
    s1, _, _ = ch.update(110.0, stop, 2.0, "long")
    assert s1 == pytest.approx(104.0)
    # Le prix redescend → le stop ne recule jamais
    s2, _, _ = ch.update(105.0, s1, 2.0, "long")
    assert s2 >= s1


def test_chandelier_short_side():
    ch = ChandelierTrailingStop(mult=3.0)
    stop = ch.init(100.0, atr=2.0, side="short")
    assert stop == pytest.approx(106.0)
    s1, _, _ = ch.update(90.0, stop, 2.0, "short")
    assert s1 == pytest.approx(96.0)
    s2, _, _ = ch.update(95.0, s1, 2.0, "short")
    assert s2 <= s1


def test_trailing_manager_chandelier_mode():
    tm = TrailingStopManager(mult=3.0, mode="chandelier")
    stop = tm.initial_stop(100.0, 2.0, "long")
    assert stop == pytest.approx(94.0)
    new = tm.update_stop(110.0, stop, 2.0, "long", entry=100.0, bars_held=1)
    assert new == pytest.approx(104.0)
    assert tm.phase_name == "chandelier"


def test_trailing_manager_default_mode_unchanged():
    tm = TrailingStopManager(mult=2.5)
    assert tm.mode == "dynamic"
    stop = tm.initial_stop(100.0, 2.0, "long")
    assert stop == pytest.approx(95.0)


# ── Signaux TVR ─────────────────────────────────────────────────────────────

def test_tvr_signal_has_required_fields():
    df = _make_df()
    strat = TVRStrategy()
    strat.prepare_for_backtest(df)
    found = None
    for i in range(300, len(df)):
        sig = strat.score(df[: i + 1])
        if sig["side"] != "none":
            found = sig
            break
    assert found is not None, "Aucun signal TVR sur la tendance synthétique"
    assert found["side"] in ("long", "short")
    assert found["score"] >= 0.55
    assert found["sl_atr_mult"] > 0
    assert found["trail_override"]["mode"] == "chandelier"


def test_tvr_early_exit_on_regime_flip():
    df = _make_df()
    strat = TVRStrategy()
    strat.prepare_for_backtest(df)
    # En fin de série la tendance est baissière → un long doit être sorti
    reason = strat.check_early_exit(df, {"side": "long"})
    assert reason == "regime_flip"
    assert strat.check_early_exit(df, {"side": "short"}) is None


def test_tvr_backtest_cache_matches_live_path():
    """Le lookup backtest et le calcul fenêtre live donnent le même signal."""
    df = _make_df()
    cached = TVRStrategy()
    cached.prepare_for_backtest(df)
    fresh = TVRStrategy()
    for i in (1200, 1500, 2500):
        a = cached.score(df[: i + 1])
        b = fresh.score(df[: i + 1])
        assert a["side"] == b["side"]
        if a["side"] != "none":
            assert a["score"] == pytest.approx(b["score"], abs=1e-6)


# ── Backtest end-to-end ─────────────────────────────────────────────────────

def test_tvr_backtest_trades():
    df = _make_df()
    eng = Engine()
    eng.register(TVRStrategy(), silent=True)
    res = Backtester(eng, _CFG).run(df, "BTC/USDC", timeframe="4h").to_dict()
    assert res["total_trades"] > 0


def test_snowball_backtest_scale_ins():
    df = _make_df()
    eng = Engine()
    eng.register(SnowballStrategy(), silent=True)
    res = Backtester(eng, _CFG).run(df, "BTC/USDC", timeframe="4h").to_dict()
    assert res["total_trades"] > 0
    assert res["diagnostics"].get("scale_ins", 0) > 0, \
        "Snowball doit pyramider sur les tendances gagnantes"


def test_snowball_respects_max_units():
    df = _make_df()
    strat = SnowballStrategy()
    strat.prepare_for_backtest(df)
    pos = {"side": "long", "entry": float(df["close"][1000]), "stop": 0.0}
    params = {"snowball_pyramid": {"max_units": 2, "pyr_step": 0.1}}
    adds = 0
    for i in range(1000, 1400):
        if strat.check_scale_in(df[: i + 1], pos, params):
            adds += 1
    assert adds <= 1  # max_units=2 → 1 entrée + 1 ajout max
