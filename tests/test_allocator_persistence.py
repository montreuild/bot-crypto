"""Persistance de l'allocateur (axe 2.4) — stats hebdo survivant au redémarrage."""
from app.core.database import init_db
from app.live.capital_allocator import CapitalAllocator

_ACTIVE = {"1h": [{"name": "trend"}], "4h": [{"name": "breakout"}]}
_CFG = {"capital_allocator": {"mode": "performance"}}


def test_weekly_stats_persist_and_restore(tmp_path):
    db = tmp_path / "alloc.db"
    _, SessionLocal = init_db(f"sqlite:///{db}")

    a1 = CapitalAllocator(capital=1000, active_per_tf=_ACTIVE, cfg=_CFG,
                          session_factory=SessionLocal)
    a1.register_close("trend::1h", notional=100, pnl=12.5)
    a1.register_close("trend::1h", notional=100, pnl=-4.0)

    # Nouvelle instance, même base → reprise des stats hebdo.
    a2 = CapitalAllocator(capital=1000, active_per_tf=_ACTIVE, cfg=_CFG,
                          session_factory=SessionLocal)
    s = a2._slots["trend::1h"]
    assert s.weekly_trades == 2
    assert s.weekly_wins == 1
    assert round(s.weekly_pnl, 4) == 8.5
    assert round(s.weekly_gross_win, 4) == 12.5
    assert round(s.weekly_gross_loss, 4) == 4.0
    # Slot sans trade : repart bien à zéro.
    assert a2._slots["breakout::4h"].weekly_trades == 0


def test_no_session_factory_is_noop():
    a = CapitalAllocator(capital=1000, active_per_tf={"1h": [{"name": "trend"}]},
                         cfg={})
    a.register_close("trend::1h", notional=100, pnl=5.0)  # ne doit pas lever
    assert a._slots["trend::1h"].weekly_trades == 1
