"""S4-06 — get_trade_global_aggregates : compteurs globaux calculés par SQL
(COUNT/SUM/MAX) plutôt qu'en itérant les lignes Trade en Python."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import (
    Trade,
    get_trade_global_aggregates,
    get_trades,
    init_db,
    session_scope,
)


def _insert(session, *, pnl, fees=0.0, days_ago=0):
    # session_scope() ne commit PAS automatiquement (elle garantit seulement
    # la fermeture) — les appelants gèrent commit/rollback eux-mêmes.
    session.add(Trade(
        time=datetime.now(timezone.utc) - timedelta(days=days_ago),
        symbol="BTC/USDC", side="long", strategy="trend_rider", entry=100.0,
        exit_price=101.0, size=1.0, notional=100.0, pnl=pnl, fees=fees,
        status="closed", timeframe="1h",
    ))
    session.commit()


def test_empty_table_returns_zeros(tmp_path):
    _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with session_scope(SessionLocal) as sess:
        agg = get_trade_global_aggregates(sess)
    assert agg == {
        "total_trades": 0, "total_pnl": 0.0, "total_fees": 0.0,
        "best_trade": 0.0, "wins": 0, "gross_win": 0.0, "gross_loss": 0.0,
    }


def test_aggregates_match_manual_python_sum(tmp_path):
    _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
    pnls_fees = [(50.0, 0.5), (-20.0, 0.3), (30.0, 0.4), (-5.0, 0.1)]
    with session_scope(SessionLocal) as sess:
        for pnl, fee in pnls_fees:
            _insert(sess, pnl=pnl, fees=fee)

    with session_scope(SessionLocal) as sess:
        agg = get_trade_global_aggregates(sess)

    assert agg["total_trades"] == 4
    assert agg["total_pnl"] == pytest.approx(sum(p for p, _ in pnls_fees))
    assert agg["total_fees"] == pytest.approx(sum(f for _, f in pnls_fees))
    assert agg["best_trade"] == 50.0
    assert agg["wins"] == 2
    assert agg["gross_win"] == 80.0
    assert agg["gross_loss"] == 25.0


def test_since_filter_excludes_older_trades(tmp_path):
    _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with session_scope(SessionLocal) as sess:
        _insert(sess, pnl=100.0, days_ago=60)   # hors fenêtre
        _insert(sess, pnl=10.0, days_ago=1)     # dans la fenêtre

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    with session_scope(SessionLocal) as sess:
        agg = get_trade_global_aggregates(sess, since=cutoff)
    assert agg["total_trades"] == 1
    assert agg["total_pnl"] == 10.0


def test_get_trades_since_filter():
    """get_trades() supporte aussi `since` (S4-06)."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _, SessionLocal = init_db(f"sqlite:///{os.path.join(d, 'db.sqlite')}")
        with session_scope(SessionLocal) as sess:
            _insert(sess, pnl=1.0, days_ago=60)
            _insert(sess, pnl=2.0, days_ago=1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        with session_scope(SessionLocal) as sess:
            rows = get_trades(sess, since=cutoff)
        assert len(rows) == 1
        assert rows[0].pnl == 2.0
