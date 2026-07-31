"""SMC-09/SMC-10 — famille SMC + grille de frais/périodes dans fast_analysis."""
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from app.core.fast_analysis import analyze, build_signals


def _df(n: int = 600, seed: int = 5) -> pl.DataFrame:
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    times = [start + timedelta(hours=k) for k in range(n)]
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.6, n))
    high = close + rng.uniform(0.1, 1.2, n)
    low = close - rng.uniform(0.1, 1.2, n)
    open_ = close + rng.normal(0, 0.25, n)
    vol = rng.uniform(10, 100, n)
    return pl.DataFrame({
        "time": pl.Series(times).cast(pl.Datetime("ms")),
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
    })


def test_smc_family_present_and_legacy_unchanged():
    df = _df()
    sigs = build_signals(df)
    legacy = [k for k in sigs if not k.startswith("SMC ")]
    assert len(legacy) == 9                      # les 9 signaux historiques
    smc_rows = [k for k in sigs if k.startswith("SMC ")]
    assert set(smc_rows) == {"SMC sweep rejeté", "SMC OB retest"}
    for name in smc_rows:
        s, kind, tp = sigs[name]
        assert kind == "smc" and tp is not None
        # TP défini partout où un signal est posé
        idx = np.flatnonzero(s != 0)
        assert not np.isnan(tp[idx]).any()


def test_analyze_default_api_shape():
    out = analyze(_df())
    names = [r["signal"] for r in out["rows"]]
    assert "SMC sweep rejeté" in names
    for r in out["rows"]:
        assert "taker" in r and "maker" in r and "fee_grid" not in r


def test_fee_grid_and_period_scales():
    df = _df()
    out = analyze(df, fee_grid=[(0.0005, 0.0002)], period_scales=(0.5,))
    names = [r["signal"] for r in out["rows"]]
    assert any(n.endswith("×0.5") for n in names)
    assert all(not n.startswith("SMC ") or "×" not in n for n in names)
    row = next(r for r in out["rows"] if "fee_grid" in r)
    g = row["fee_grid"][0]
    assert g["taker"] == 0.0005 and "oos_maker" in g and "n" in g["oos_maker"]


def test_bt10_slippage_model_off_is_default():
    """BT-10 : défaut "static" ; modèle "size" → coût d'impact > 0 croissant."""
    from types import SimpleNamespace

    from app.engine.backtest import Backtester
    from app.engine.engine import Engine
    cfg = {"trading": {"capital": 1000, "risk_per_trade": 0.01,
                       "timeframe": "1h"},
           "backtest": {}, "strategy_params": {}}
    bt = Backtester(Engine(), cfg)
    assert bt.slippage_model == "static"
    ctx = SimpleNamespace(qvol_arr=np.array([1000.0] * 5))
    assert bt._impact_cost(ctx, 2, 500.0) == 0.0        # off → 0
    bt2 = Backtester(Engine(), {**cfg, "backtest": {"slippage_model": "size"}})
    c1 = bt2._impact_cost(ctx, 2, 100.0)   # 10 % de participation
    c2 = bt2._impact_cost(ctx, 2, 500.0)   # 50 % de participation
    assert 0 < c1 < c2
    # quadratique en notionnel : ×5 de taille → ×25 de coût
    assert abs(c2 / c1 - 25.0) < 1e-9
    assert bt2._impact_cost(SimpleNamespace(qvol_arr=None), 2, 500.0) == 0.0
