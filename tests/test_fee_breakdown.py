"""FIN-06 — compteur de frais par catégorie (taker/maker/borrow/stop).

- ``Trade.fee_taker``/``fee_maker``/``exit_reason`` : nouvelles colonnes,
  auto-migrées (cf. tests/test_db_migration.py).
- ``save_trade`` : replie l'agrégat ``fees`` sur ``fee_taker`` par défaut
  (réalité actuelle du chemin live — aucune distinction maker/taker à
  l'exécution) si l'appelant ne fournit pas déjà la répartition.
- ``get_fee_breakdown`` : agrégats SQL taker/maker/borrow/stop, même
  convention que ``get_trade_global_aggregates`` (S4-06).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import Trade, get_fee_breakdown, init_db, save_trade, session_scope


def _insert(session, *, fee_taker=0.0, fee_maker=0.0, borrow_cost=0.0,
           exit_reason=None, days_ago=0):
    session.add(Trade(
        time=datetime.now(timezone.utc) - timedelta(days=days_ago),
        symbol="BTC/USDC", side="long", strategy="trend_rider", entry=100.0,
        exit_price=101.0, size=1.0, notional=100.0, pnl=1.0,
        fees=round(fee_taker + fee_maker, 6), fee_taker=fee_taker, fee_maker=fee_maker,
        borrow_cost=borrow_cost, exit_reason=exit_reason,
        status="closed", timeframe="1h",
    ))
    session.commit()


class TestGetFeeBreakdown:
    def test_empty_table_returns_zeros(self, tmp_path):
        _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
        with session_scope(SessionLocal) as sess:
            b = get_fee_breakdown(sess)
        assert b == {"taker": 0.0, "maker": 0.0, "borrow": 0.0, "stop": 0.0}

    def test_taker_maker_borrow_split(self, tmp_path):
        _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
        with session_scope(SessionLocal) as sess:
            _insert(sess, fee_taker=1.0, fee_maker=0.0, borrow_cost=0.2, exit_reason="take_profit")
            _insert(sess, fee_taker=0.0, fee_maker=0.5, borrow_cost=0.1, exit_reason="exit_after_bars")
        with session_scope(SessionLocal) as sess:
            b = get_fee_breakdown(sess)
        assert b["taker"]  == pytest.approx(1.0)
        assert b["maker"]  == pytest.approx(0.5)
        assert b["borrow"] == pytest.approx(0.3)

    def test_stop_category_only_counts_stop_exits(self, tmp_path):
        _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
        with session_scope(SessionLocal) as sess:
            _insert(sess, fee_taker=1.0, exit_reason="stop_loss")
            _insert(sess, fee_taker=2.0, exit_reason="trailing_stop")
            _insert(sess, fee_taker=3.0, exit_reason="take_profit")  # exclu
            _insert(sess, fee_taker=4.0, exit_reason=None)           # exclu
        with session_scope(SessionLocal) as sess:
            b = get_fee_breakdown(sess)
        assert b["stop"] == pytest.approx(3.0)   # 1.0 (stop_loss) + 2.0 (trailing_stop)
        assert b["taker"] == pytest.approx(10.0)  # tous comptent côté taker/maker

    def test_since_filter(self, tmp_path):
        _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
        with session_scope(SessionLocal) as sess:
            _insert(sess, fee_taker=5.0, days_ago=60)
            _insert(sess, fee_taker=1.0, days_ago=1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        with session_scope(SessionLocal) as sess:
            b = get_fee_breakdown(sess, since=cutoff)
        assert b["taker"] == pytest.approx(1.0)


class TestSaveTradeFeeSplit:
    def test_defaults_fee_taker_to_full_fees_when_unspecified(self, tmp_path):
        """Le chemin live actuel n'a aucune distinction maker/taker à
        l'exécution : save_trade doit refléter ça (100% taker) plutôt que de
        laisser fee_taker/fee_maker vides de sens."""
        _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
        with session_scope(SessionLocal) as sess:
            rec = save_trade(sess, {
                "symbol": "BTC/USDC", "side": "long", "strategy": "t",
                "timeframe": "1h", "status": "closed", "entry": 100.0,
                "exit": 101.0, "size": 1.0, "notional": 100.0,
                "pnl": 1.0, "pnl_pct": 1.0, "fees": 0.42,
                "exit_reason": "take_profit",
            })
            assert rec.fee_taker == pytest.approx(0.42)
            assert rec.fee_maker == pytest.approx(0.0)
            assert rec.exit_reason == "take_profit"

    def test_honours_explicit_fee_split(self, tmp_path):
        _, SessionLocal = init_db(f"sqlite:///{tmp_path / 'db.sqlite'}")
        with session_scope(SessionLocal) as sess:
            rec = save_trade(sess, {
                "symbol": "BTC/USDC", "side": "long", "strategy": "t",
                "timeframe": "1h", "status": "closed", "entry": 100.0,
                "exit": 101.0, "size": 1.0, "notional": 100.0,
                "pnl": 1.0, "pnl_pct": 1.0, "fees": 0.42,
                "fee_taker": 0.10, "fee_maker": 0.32,
            })
            assert rec.fee_taker == pytest.approx(0.10)
            assert rec.fee_maker == pytest.approx(0.32)


class TestSaveTradeEntryTime:
    def test_open_time_unix_prime_sur_la_reconstruction(self, tmp_path):
        """A-08 : un short tenu pendant le week-end ne doit pas reculer
        l'entrée de 48 h fantômes via duration_bars × TF."""
        from app.core.database import _resolve_entry_time
        opened = datetime(2026, 8, 14, 16, 0, 0, tzinfo=timezone.utc)  # vendredi
        closed = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)  # lundi
        weekend_hours = (closed - opened).total_seconds() / 3600
        got = _resolve_entry_time({
            "open_time": opened.timestamp(),
            "time": closed,
            "duration_bars": int(weekend_hours),
            "timeframe": "1h",
        })
        assert got == opened.replace(tzinfo=None)

    def test_entry_time_iso_backtest_reste_prioritaire(self, tmp_path):
        from app.core.database import _resolve_entry_time
        got = _resolve_entry_time({
            "entry_time": "2024-02-01T12:00:00",
            "open_time": 1_700_000_000,
        })
        assert got == datetime(2024, 2, 1, 12, 0, 0)

    def test_repli_duration_si_aucune_horloge(self):
        from app.core.database import _resolve_entry_time
        got = _resolve_entry_time({
            "time": datetime(2024, 2, 2, 0, 0, 0),
            "duration_bars": 24,
            "timeframe": "1h",
        })
        assert got == datetime(2024, 2, 1, 0, 0, 0)
