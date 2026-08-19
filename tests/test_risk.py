"""
Tests unitaires — RiskManager
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.risk_envelope import Envelope
from app.core.risk_gate import RiskManager


def _envelope(slot_envelope=200.0, risk_pct=0.01, leverage=1.0) -> Envelope:
    """Enveloppe de slot minimale pour exercer le sizing."""
    return Envelope(
        venue="okx-test", symbol="BTC/USDC", slot_key="s::1h::BTC/USDC",
        currency="USDC", venue_envelope=1000.0, venue_risk_budget=50.0,
        symbol_envelope=1000.0, symbol_risk_budget=20.0,
        slot_envelope=slot_envelope, slot_risk_amount=slot_envelope * risk_pct,
        max_leverage=leverage, min_notional=0.0,
        trade_risk_pct=risk_pct, weight=slot_envelope / 1000.0,
    )


def _cfg(capital=1000, paper=True, dd_daily=0.05, dd_global=0.20, risk=0.01):
    """S12 : le capital et le taux de risque ne sont plus des globales de
    `trading` — ils viennent de l'enveloppe de la venue par défaut et du
    profil de risque."""
    return {
        "trading": {
            "paper_mode": paper,
            "daily_drawdown_limit": dd_daily,
            "max_drawdown_global": dd_global,
            "max_trades_per_minute": 10,
        },
        "venues": {"default": "okx-test", "defs": {"okx-test": {"max_leverage": 1}},
                   "assign": {}},
        "risk": {
            "profile": "test", "profiles": {"test": risk},
            "envelopes": {"okx-test": {
                "capital": capital, "max_symbol_exposure_pct": 1.0,
                "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05,
            }},
        },
        "notifications": {"dd_warning_ratio": 0.80},
    }


class TestRateTokenIsNotConsumedOnReject:
    def test_failed_can_trade_does_not_eat_a_token(self):
        """F-11 : un refus plus loin (ici on ne consomme plus dans can_trade)
        ne doit pas épuiser le budget anti-spam."""
        cfg = _cfg()
        cfg["trading"]["max_trades_per_minute"] = 3
        rm = RiskManager(cfg)
        for _ in range(10):
            ok, _reason = rm.can_trade("long")
            assert ok
        assert len(rm._trade_times) == 0
        rm.consume_rate_token()
        rm.consume_rate_token()
        rm.consume_rate_token()
        ok, reason = rm.can_trade("long")
        assert not ok
        assert "anti-spam" in reason.lower() or "minute" in reason.lower()


class TestRiskManager:
    def test_initial_state(self):
        rm = RiskManager(_cfg())
        assert rm.equity == 1000
        assert rm.peak_equity == 1000
        assert not rm.halted

    def test_compute_risk_no_drawdown(self):
        rm = RiskManager(_cfg(risk=0.01))
        assert rm.compute_risk() == pytest.approx(0.01)

    def test_compute_risk_reduced_above_5pct_dd(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.equity = 940      # 6 % de drawdown → rampe FIN-11 : ×0,95
        rm.peak_equity = 1000
        assert rm.compute_risk() == pytest.approx(0.0095)

    def test_compute_risk_halved_above_10pct_dd(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.equity = 880      # 12 % de drawdown → rampe : ×0,65
        rm.peak_equity = 1000
        assert rm.compute_risk() == pytest.approx(0.0065)

    def test_compute_risk_floor_at_15pct_dd(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.equity = 850      # 15 % → plancher ×0,5
        rm.peak_equity = 1000
        assert rm.compute_risk() == pytest.approx(0.005)

    def test_circuit_breaker_daily(self):
        rm = RiskManager(_cfg(dd_daily=0.05))
        rm.daily_start = 1000
        rm.update_equity(940)   # perte de 6% → dépasse 5%
        assert rm.halted
        assert "journalier" in rm.halt_reason

    def test_circuit_breaker_global(self):
        rm = RiskManager(_cfg(dd_global=0.20))
        rm.peak_equity = 1000
        rm.daily_start = 800
        rm.update_equity(790)   # drawdown global 21%
        assert rm.halted
        assert "global" in rm.halt_reason

    def test_reset_halt(self):
        rm = RiskManager(_cfg())
        rm.halted = True
        rm.halt_reason = "test"
        rm.reset_halt()
        assert not rm.halted
        assert rm.halt_reason == ""

    def test_open_positions_no_longer_gate_entries(self):
        """S12 : `max_positions` est supprimé — compter les positions était un
        proxy grossier du budget de risque de la venue, qui borne désormais la
        perte directement (§2.2). Le refus, s'il a lieu, vient du RiskLedger."""
        rm = RiskManager(_cfg())
        rm.open_positions = {f"p{i}": {"side": "long"} for i in range(20)}
        can, reason = rm.can_trade("long")
        assert can
        assert reason == ""

    def test_can_trade_ok(self):
        rm = RiskManager(_cfg())
        can, reason = rm.can_trade("long")
        assert can
        assert reason == ""

    def test_can_trade_halted(self):
        rm = RiskManager(_cfg())
        rm.halted = True
        rm.halt_reason = "Test halt"
        can, reason = rm.can_trade("long")
        assert not can
        assert reason == "Test halt"

    def test_compute_size_risks_exactly_the_slot_amount(self):
        """S12 : la base est l'enveloppe du SLOT, plus l'équité globale."""
        rm = RiskManager(_cfg(capital=1000, risk=0.01))
        env = _envelope(slot_envelope=200.0, risk_pct=0.01)
        size, notional = rm.compute_size(entry=100, stop_dist=5, env=env)
        assert size * 5 == pytest.approx(env.slot_risk_amount)
        assert notional <= env.max_notional

    def test_compute_size_notional_capped_by_the_slot_envelope(self):
        rm = RiskManager(_cfg(capital=1000))
        env = _envelope(slot_envelope=200.0, risk_pct=0.01)
        # Stop très serré → taille énorme, doit être plafonnée par l'enveloppe.
        _, notional = rm.compute_size(entry=100, stop_dist=0.001, env=env)
        assert notional <= env.max_notional + 1e-6

    def test_compute_size_refuses_an_unusable_stop(self):
        """Le repli sur l'ATR brut est supprimé : il faisait risquer un
        multiple du risque annoncé."""
        rm = RiskManager(_cfg())
        with pytest.raises(ValueError, match="stop_dist"):
            rm.compute_size(entry=100, stop_dist=0.0, env=_envelope())

    def test_daily_pnl_pct_positive(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.daily_start = 1000
        rm.equity = 1050
        assert rm.daily_pnl_pct == pytest.approx(0.05)

    def test_daily_pnl_pct_negative(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.daily_start = 1000
        rm.equity = 950
        assert rm.daily_pnl_pct == pytest.approx(-0.05)

    def test_global_dd_pct(self):
        rm = RiskManager(_cfg(capital=1000))
        rm.peak_equity = 1200
        rm.equity = 1000
        assert rm.global_dd_pct == pytest.approx(1/6, rel=1e-3)

    def test_register_and_close_position(self):
        rm = RiskManager(_cfg())
        pos = {"id": "abc", "symbol": "BTC/USDC", "side": "long"}
        rm.register_open(pos)
        assert "abc" in rm.open_positions
        rm.register_close("abc")
        assert "abc" not in rm.open_positions

    def test_has_hedge(self):
        rm = RiskManager(_cfg())
        rm.open_positions = {
            "a": {"symbol": "BTC/USDC", "side": "long"},
            "b": {"symbol": "BTC/USDC", "side": "short"},
        }
        assert rm.has_hedge("BTC/USDC")
        assert not rm.has_hedge("ETH/USDC")

    def test_status_dict_keys(self):
        rm = RiskManager(_cfg())
        d = rm.status_dict()
        for key in ("equity", "peak_equity", "daily_pnl_pct", "global_dd_pct",
                    "open_positions", "halted", "halt_reason", "current_risk"):
            assert key in d
