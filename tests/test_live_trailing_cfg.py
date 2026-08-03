"""S1-08 — live.trailing dédié, avec repli documenté sur backtest.*.

Avant ce correctif, _trailing_cfg lisait TOUJOURS cfg["backtest"] : changer
la config backtest (walk-forward, optimiseur) modifiait silencieusement le
trailing live. live.trailing est maintenant la source dédiée ; backtest.*
reste un repli rétro-compatible, avec un WARNING explicite.
"""
import logging

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


def _base_cfg(db_path: str) -> dict:
    return {
        "trading": {"capital": 1000.0, "timeframes": ["1h"], "scan_interval": 60,
                    "score_threshold": 0.5, "paper_mode": True},
        "database": {"url": f"sqlite:///{db_path}"},
        "exchange": {"name": "okx", "margin": False},
        "strategies": {"enabled": ["trend_rider"]},
        "strategy_params": {}, "optimizer_results": {}, "scanner": {}, "risk": {},
        "notifications": {}, "optimizer": {"enabled": False},
        "forward_test": {"enabled": False}, "lifecycle": {"enabled": False},
    }


def test_live_trailing_section_used_when_present(tmp_path):
    cfg = _base_cfg(str(tmp_path / "live.db"))
    cfg["live"] = {"trailing": {"trail_wide": 9.9, "grace_bars": 7}}
    cfg["backtest"] = {"trail_wide": 1.1, "grace_bars": 1}  # ne doit pas être utilisé
    trader = LiveTrader(cfg, MockExchange())
    assert trader._trailing_cfg["mult"] == 9.9
    assert trader._trailing_cfg["grace_bars"] == 7


def test_falls_back_to_backtest_with_warning(tmp_path, caplog):
    cfg = _base_cfg(str(tmp_path / "live.db"))
    cfg["backtest"] = {"trail_wide": 3.3, "grace_bars": 8}
    with caplog.at_level(logging.WARNING):
        trader = LiveTrader(cfg, MockExchange())
    assert trader._trailing_cfg["mult"] == 3.3
    assert trader._trailing_cfg["grace_bars"] == 8
    assert any("live.trailing" in r.message for r in caplog.records)


def test_no_warning_when_live_trailing_defined(tmp_path, caplog):
    cfg = _base_cfg(str(tmp_path / "live.db"))
    cfg["live"] = {"trailing": {"trail_wide": 2.5}}
    with caplog.at_level(logging.WARNING):
        LiveTrader(cfg, MockExchange())
    assert not any("live.trailing" in r.message for r in caplog.records)
