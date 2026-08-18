"""S4-01 — Sharpe live aligné sur BacktestResult._compute_metrics().

Avant ce correctif, le Sharpe live était calculé sur les PnL BRUTS (dollars)
et annualisé par un sqrt(252) fixe, indépendant du timeframe réel — un
Sharpe live de 2.0 n'avait pas la même signification qu'un Sharpe backtest
de 2.0. Ce test verrouille la nouvelle formule (retours sur une courbe
d'équité synthétique par trade, annualisation par bars_per_year(tf)) et
l'ordre chronologique des trades utilisé pour la construire.
"""
import numpy as np
import pytest

from app.core.database import Trade, session_scope
from app.core.timeframes import bars_per_year
from app.live.live_trader import LiveTrader


class MockExchange:
    def fetch_ticker(self, symbol):
        return {"last": 100.0}

    def fetch_balance(self):
        return {"free": {"USDC": 1000.0}, "total": {"USDC": 1000.0}}

    def fetch_positions(self):
        return []

    def fetch_balance_detail(self):
        return {"free": 1000.0, "used": 0.0, "total": 1000.0, "borrowed": 0.0}


def _make_cfg(db_path: str) -> dict:
    return {
        "trading": {"timeframes": ["1h"], "scan_interval": 60,
                    "score_threshold": 0.5, "paper_mode": True},
        "database": {"url": f"sqlite:///{db_path}"},
        "exchange": {"name": "okx", "margin": False},
        "strategies": {"enabled": ["trend_rider"]},
        "strategy_params": {}, "optimizer_results": {}, "scanner": {},
        # S12 : capital porté par la venue.
        "venues": {"default": "okx-test", "defs": {"okx-test": {"max_leverage": 1}},
                   "assign": {}},
        "risk": {"envelopes": {"okx-test": {
            "capital": 1000.0, "max_symbol_exposure_pct": 1.0,
            "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05,
        }}},
        "notifications": {}, "optimizer": {"enabled": False},
        "forward_test": {"enabled": False}, "lifecycle": {"enabled": False},
    }


def _insert_trade(session, *, pnl, fees, strategy, timeframe, seconds_offset):
    from datetime import datetime, timedelta, timezone
    rec = Trade(
        time=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds_offset),
        symbol="BTC/USDC", side="long", strategy=strategy, entry=100.0,
        exit_price=101.0, size=1.0, notional=100.0, pnl=pnl, fees=fees,
        status="closed", timeframe=timeframe,
    )
    session.add(rec)
    session.commit()


def _expected_sharpe(initial_capital: float, pnls_in_order: list, tf: str) -> float:
    """Réimplémentation indépendante de la formule attendue (même méthode
    que BacktestResult._compute_metrics), pour vérifier l'alignement."""
    eq = [initial_capital]
    cap = initial_capital
    for p in pnls_in_order:
        cap += p
        eq.append(cap)
    eq_arr = np.array(eq, dtype=float)
    denom = np.where(eq_arr[:-1] > 0, eq_arr[:-1], 1.0)
    rets = np.diff(eq_arr) / denom
    std = float(rets.std())
    if std == 0:
        return 0.0
    ann = float(np.sqrt(bars_per_year(tf)))
    return round(float(rets.mean() / std * ann), 3)


@pytest.fixture
def trader(tmp_path):
    return LiveTrader(_make_cfg(str(tmp_path / "live.db")), MockExchange())


def test_sharpe_matches_backtest_style_formula(trader):
    # R-01 : F-02 exige MIN_SIGNIFICANT_TRADES (10), plus 3.
    pnls_in_order = [50.0, -20.0, 30.0, 10.0, -8.0, 12.0, -5.0, 7.0, -3.0, 9.0]
    with session_scope(trader.SessionLocal) as sess:
        for i, p in enumerate(pnls_in_order):
            _insert_trade(sess, pnl=p, fees=0.1, strategy="trend_rider",
                          timeframe="1h", seconds_offset=i * 3600)

    stats = trader._load_db_stats()
    got = stats["by_strategy"]["trend_rider"]["sharpe"]
    expected = _expected_sharpe(trader.risk.initial_capital, pnls_in_order, "1h")
    assert got == expected


def test_sharpe_uses_chronological_order_not_db_desc_order(trader):
    """get_trades() renvoie du plus récent au plus ancien — un bug d'ordre
    inverserait la courbe d'équité synthétique et fausserait complètement
    le Sharpe (asymétrique par construction ici : gain suivi de deux pertes
    vs. deux pertes suivies d'un gain donnent des Sharpe différents)."""
    pnls_chronological = [80.0, -10.0, -10.0, 5.0, -4.0, 6.0, -3.0, 8.0, -2.0, 4.0]
    with session_scope(trader.SessionLocal) as sess:
        for i, p in enumerate(pnls_chronological):
            _insert_trade(sess, pnl=p, fees=0.0, strategy="trend_rider",
                          timeframe="1h", seconds_offset=i * 3600)

    stats = trader._load_db_stats()
    got = stats["by_strategy"]["trend_rider"]["sharpe"]
    expected_chrono = _expected_sharpe(trader.risk.initial_capital, pnls_chronological, "1h")
    expected_reversed = _expected_sharpe(
        trader.risk.initial_capital, list(reversed(pnls_chronological)), "1h"
    )
    assert got == expected_chrono
    assert got != expected_reversed


def test_sharpe_uses_dominant_timeframe_annualization(trader):
    """La stratégie trade sur deux TF mélangés dans la DB — l'annualisation
    doit suivre le TF majoritaire (4h ici, 2 trades contre 1)."""
    with session_scope(trader.SessionLocal) as sess:
        seq = [
            (40.0, "4h"), (-15.0, "1h"), (25.0, "4h"), (8.0, "4h"),
            (-6.0, "4h"), (11.0, "4h"), (-4.0, "1h"), (7.0, "4h"),
            (-3.0, "4h"), (9.0, "4h"),
        ]
        for i, (p, tf) in enumerate(seq):
            _insert_trade(sess, pnl=p, fees=0.0, strategy="trend_rider",
                          timeframe=tf, seconds_offset=i * 3600)

    stats = trader._load_db_stats()
    got = stats["by_strategy"]["trend_rider"]["sharpe"]
    expected = _expected_sharpe(
        trader.risk.initial_capital, [p for p, _ in seq], "4h")
    assert got == expected


def test_sharpe_none_under_min_significant_trades(trader):
    """R-01 : sous 10 trades le live publie None, pas 0.0."""
    with session_scope(trader.SessionLocal) as sess:
        _insert_trade(sess, pnl=10.0, fees=0.0, strategy="trend_rider",
                      timeframe="1h", seconds_offset=0)
        _insert_trade(sess, pnl=-5.0, fees=0.0, strategy="trend_rider",
                      timeframe="1h", seconds_offset=3600)

    stats = trader._load_db_stats()
    assert stats["by_strategy"]["trend_rider"]["sharpe"] is None
