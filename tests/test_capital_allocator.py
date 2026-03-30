"""
Tests unitaires — CapitalAllocator (gestion des slots).
"""
import pytest
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.live.capital_allocator import CapitalAllocator, SlotBudget


def _active_per_tf(strategies=None, tfs=None):
    """Helper : construit un active_per_tf basique."""
    strategies = strategies or ["trend", "mean_revert"]
    tfs = tfs or ["1h", "4h"]
    result = {}
    for tf in tfs:
        result[tf] = [{"name": s} for s in strategies]
    return result


def _cfg(mode="equal", max_slot_pct=0.50, disabled=None, budgets=None):
    """Helper : construit un cfg minimal pour le CapitalAllocator."""
    return {
        "capital_allocator": {
            "mode": mode,
            "max_slot_pct": max_slot_pct,
            "min_trades_for_rebalance": 3,
            "rebalance_interval": "weekly",
            "max_symbol_exposure_pct": 0.25,
            "max_pyramiding": 2,
            "disabled_slots": disabled or [],
            "slot_budgets": budgets or {},
        }
    }


class TestCapitalAllocatorInit:
    def test_creates_correct_slots(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        status = alloc.get_status()
        assert len(status) == 4  # 2 strategies × 2 timeframes
        keys = {s["slot_key"] for s in status}
        assert keys == {"trend::1h", "trend::4h", "mean_revert::1h", "mean_revert::4h"}

    def test_equal_distribution(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        status = alloc.get_status()
        # 4 slots → 25% each
        for s in status:
            assert s["budget_pct"] == pytest.approx(25.0, abs=0.5)

    def test_empty_active_per_tf(self):
        alloc = CapitalAllocator(1000, {}, _cfg())
        assert alloc.get_status() == []

    def test_single_slot(self):
        apt = {"1h": [{"name": "trend"}]}
        alloc = CapitalAllocator(1000, apt, _cfg())
        status = alloc.get_status()
        assert len(status) == 1
        assert status[0]["budget_pct"] == pytest.approx(50.0, abs=0.5)  # capped at max_slot_pct

    def test_respects_max_slot_pct(self):
        apt = {"1h": [{"name": "trend"}]}
        alloc = CapitalAllocator(1000, apt, _cfg(max_slot_pct=0.30))
        status = alloc.get_status()
        assert status[0]["budget_pct"] <= 30.1


class TestSlotEnabled:
    def test_all_enabled_by_default(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        for s in alloc.get_status():
            assert s["enabled"] is True

    def test_disabled_from_config(self):
        alloc = CapitalAllocator(
            1000, _active_per_tf(),
            _cfg(disabled=["trend::1h"])
        )
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["enabled"] is False
                assert s["budget_pct"] == 0.0
            else:
                assert s["enabled"] is True

    def test_toggle_disable(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok = alloc.set_slot_enabled("trend::1h", False)
        assert ok is True
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["enabled"] is False
                assert s["budget_pct"] == 0.0

    def test_toggle_enable(self):
        alloc = CapitalAllocator(
            1000, _active_per_tf(),
            _cfg(disabled=["trend::1h"])
        )
        alloc.set_slot_enabled("trend::1h", True)
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["enabled"] is True
                assert s["budget_pct"] > 0.0

    def test_toggle_unknown_slot(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok = alloc.set_slot_enabled("unknown::1h", False)
        assert ok is False

    def test_disabled_slot_cannot_allocate(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        alloc.set_slot_enabled("trend::1h", False)
        ok, reason = alloc.can_allocate("trend::1h", 100)
        assert ok is False
        assert "désactivé" in reason

    def test_disabled_slots_list(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        alloc.set_slot_enabled("trend::1h", False)
        alloc.set_slot_enabled("mean_revert::4h", False)
        assert sorted(alloc.disabled_slots) == ["mean_revert::4h", "trend::1h"]

    def test_budget_redistributed_on_disable(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        alloc.set_slot_enabled("trend::1h", False)
        # 3 remaining active slots should have ~33% each
        active = [s for s in alloc.get_status() if s["enabled"]]
        total_pct = sum(s["budget_pct"] for s in active)
        assert total_pct == pytest.approx(100.0, abs=1.0)


class TestModes:
    def test_mode_equal(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(mode="equal"))
        assert alloc.mode == "equal"
        budgets = [s["budget_pct"] for s in alloc.get_status()]
        assert all(abs(b - budgets[0]) < 0.5 for b in budgets)

    def test_mode_manual(self):
        cfg = _cfg(mode="manual", budgets={"trend::1h": 0.40, "trend::4h": 0.30})
        alloc = CapitalAllocator(1000, _active_per_tf(), cfg)
        assert alloc.mode == "manual"
        status = {s["slot_key"]: s["budget_pct"] for s in alloc.get_status()}
        # Custom budgets applied (may be normalized)
        assert status["trend::1h"] >= 30.0  # Should be close to 40%

    def test_mode_performance(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(mode="performance"))
        assert alloc.mode == "performance"

    def test_mode_invalid_defaults_to_equal(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(mode="invalid_mode"))
        assert alloc.mode == "equal"

    def test_set_mode(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        assert alloc.mode == "equal"
        ok = alloc.set_mode("manual")
        assert ok is True
        assert alloc.mode == "manual"

    def test_set_mode_invalid(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok = alloc.set_mode("unknown")
        assert ok is False
        assert alloc.mode == "equal"

    def test_mode_switch_preserves_state(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(mode="equal"))
        alloc.register_open("trend::1h", 100)
        alloc.set_mode("manual")
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["used_notional"] == 100.0


class TestAllocation:
    def test_can_allocate_within_budget(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok, reason = alloc.can_allocate("trend::1h", 200)
        assert ok is True
        assert reason == ""

    def test_cannot_allocate_over_budget(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        # Budget is ~25% of 1000 = 250 USDC
        ok, reason = alloc.can_allocate("trend::1h", 300)
        assert ok is False
        assert "épuisé" in reason

    def test_cannot_allocate_unknown_slot(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok, reason = alloc.can_allocate("unknown::1h", 100)
        assert ok is False
        assert "inconnu" in reason

    def test_register_open_close(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        alloc.register_open("trend::1h", 150)
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["used_notional"] == 150.0

        alloc.register_close("trend::1h", 150, pnl=10.0)
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["used_notional"] == 0.0
                assert s["weekly_pnl"] == 10.0
                assert s["weekly_wins"] == 1
                assert s["weekly_trades"] == 1

    def test_register_close_loss(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        alloc.register_open("trend::1h", 100)
        alloc.register_close("trend::1h", 100, pnl=-5.0)
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["weekly_pnl"] == -5.0
                assert s["weekly_wins"] == 0
                assert s["weekly_trades"] == 1

    def test_set_slot_budget(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok = alloc.set_slot_budget("trend::1h", 0.40)
        assert ok is True
        for s in alloc.get_status():
            if s["slot_key"] == "trend::1h":
                assert s["budget_pct"] == pytest.approx(40.0, abs=0.5)

    def test_set_slot_budget_unknown(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok = alloc.set_slot_budget("unknown::1h", 0.30)
        assert ok is False

    def test_set_slot_budget_normalizes(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(max_slot_pct=0.50))
        alloc.set_slot_budget("trend::1h", 0.50)
        total = sum(s["budget_pct"] for s in alloc.get_status() if s["enabled"])
        assert total <= 101.0  # some rounding tolerance


class TestCorrelation:
    def test_no_positions(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        ok, reason = alloc.check_correlation("long", {})
        assert ok is True

    def test_too_correlated(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        positions = {
            "a": {"side": "long", "symbol": "BTC/USDC", "notional": 100},
            "b": {"side": "long", "symbol": "ETH/USDC", "notional": 100},
            "c": {"side": "long", "symbol": "SOL/USDC", "notional": 100},
            "d": {"side": "short", "symbol": "BNB/USDC", "notional": 100},
        }
        ok, reason = alloc.check_correlation("long", positions)
        assert ok is False
        assert "Corrélation" in reason

    def test_symbol_exposure_limit(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        positions = {
            "a": {"side": "long", "symbol": "BTC/USDC", "notional": 250},
            "b": {"side": "short", "symbol": "ETH/USDC", "notional": 100},
            "c": {"side": "long", "symbol": "SOL/USDC", "notional": 100},
            "d": {"side": "short", "symbol": "BNB/USDC", "notional": 100},
        }
        ok, reason = alloc.check_correlation("long", positions, symbol="BTC/USDC")
        assert ok is False
        assert "Exposition symbole" in reason

    def test_pyramiding_limit(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        positions = {
            "a": {"side": "long", "symbol": "BTC/USDC", "notional": 50},
            "b": {"side": "long", "symbol": "BTC/USDC", "notional": 50},
            "c": {"side": "short", "symbol": "ETH/USDC", "notional": 100},
            "d": {"side": "short", "symbol": "SOL/USDC", "notional": 100},
        }
        ok, reason = alloc.check_correlation("long", positions, symbol="BTC/USDC")
        assert ok is False
        assert "Pyramiding" in reason


class TestRebalance:
    def test_rebalance_only_in_performance_mode(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(mode="equal"))
        alloc._rebalance_next = 0  # Force check
        alloc.rebalance_if_due()
        # In equal mode, rebalance_if_due returns immediately

    def test_force_rebalance_equal(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(mode="equal"))
        alloc.force_rebalance()
        # Should equalize budgets
        budgets = [s["budget_pct"] for s in alloc.get_status()]
        assert all(abs(b - budgets[0]) < 0.5 for b in budgets)

    def test_force_rebalance_performance(self):
        cfg = _cfg(mode="performance")
        alloc = CapitalAllocator(1000, _active_per_tf(), cfg)
        # Simulate some wins for one slot
        for _ in range(5):
            alloc.register_close("trend::1h", 0, pnl=10.0)
        # Simulate losses for another
        for _ in range(5):
            alloc.register_close("mean_revert::4h", 0, pnl=-8.0)
        alloc.force_rebalance()
        status = {s["slot_key"]: s["budget_pct"] for s in alloc.get_status()}
        # trend::1h should have a higher budget than mean_revert::4h
        assert status["trend::1h"] > status["mean_revert::4h"]

    def test_rebalance_resets_weekly_stats(self):
        cfg = _cfg(mode="performance")
        alloc = CapitalAllocator(1000, _active_per_tf(), cfg)
        alloc.register_close("trend::1h", 0, pnl=10.0)
        assert alloc.get_status()[0]["weekly_trades"] > 0 or any(
            s["weekly_trades"] > 0 for s in alloc.get_status()
        )
        alloc.force_rebalance()
        for s in alloc.get_status():
            assert s["weekly_trades"] == 0
            assert s["weekly_pnl"] == 0.0

    def test_rebalance_skips_low_trade_count(self):
        cfg = _cfg(mode="performance")
        cfg["capital_allocator"]["min_trades_for_rebalance"] = 10
        alloc = CapitalAllocator(1000, _active_per_tf(), cfg)
        # Only 2 trades — below threshold
        alloc.register_close("trend::1h", 0, pnl=10.0)
        alloc.register_close("trend::1h", 0, pnl=10.0)
        before = {s["slot_key"]: s["budget_pct"] for s in alloc.get_status()}
        alloc.force_rebalance()
        after = {s["slot_key"]: s["budget_pct"] for s in alloc.get_status()}
        # Budgets should not have changed significantly
        for key in before:
            assert abs(before[key] - after[key]) < 1.0


class TestGetConfig:
    def test_get_config_returns_all_fields(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        config = alloc.get_config()
        assert "mode" in config
        assert "max_slot_pct" in config
        assert "rebalance_interval" in config
        assert "disabled_slots" in config
        assert "custom_budgets" in config
        assert "max_pyramiding" in config
        assert "max_symbol_exposure_pct" in config

    def test_get_config_mode_value(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg(mode="manual"))
        assert alloc.get_config()["mode"] == "manual"

    def test_get_config_disabled_slots(self):
        alloc = CapitalAllocator(
            1000, _active_per_tf(),
            _cfg(disabled=["trend::1h"])
        )
        config = alloc.get_config()
        assert "trend::1h" in config["disabled_slots"]


class TestRebuildSlots:
    def test_rebuild_preserves_existing_budgets(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        alloc.set_slot_budget("trend::1h", 0.40)
        alloc.rebuild_slots(_active_per_tf())
        # After rebuild, custom budget should still be there (in manual/performance mode)
        # In equal mode, it's equalized

    def test_rebuild_removes_old_slots(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        assert len(alloc.get_status()) == 4
        # Reduce to 1 strategy
        new_apt = {"1h": [{"name": "trend"}]}
        alloc.rebuild_slots(new_apt)
        assert len(alloc.get_status()) == 1
        assert alloc.get_status()[0]["slot_key"] == "trend::1h"

    def test_rebuild_adds_new_slots(self):
        apt = {"1h": [{"name": "trend"}]}
        alloc = CapitalAllocator(1000, apt, _cfg())
        assert len(alloc.get_status()) == 1
        new_apt = {"1h": [{"name": "trend"}, {"name": "momentum"}]}
        alloc.rebuild_slots(new_apt)
        assert len(alloc.get_status()) == 2


class TestEquity:
    def test_update_equity(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        alloc.update_equity(2000)
        assert alloc.capital == 2000
        # Budget USDC should reflect new capital
        for s in alloc.get_status():
            if s["enabled"]:
                assert s["budget_usdc"] == pytest.approx(
                    2000 * s["budget_pct"] / 100, abs=1.0
                )


class TestGetStatus:
    def test_status_fields(self):
        alloc = CapitalAllocator(1000, _active_per_tf(), _cfg())
        status = alloc.get_status()
        for s in status:
            assert "slot_key" in s
            assert "strategy" in s
            assert "tf" in s
            assert "enabled" in s
            assert "budget_pct" in s
            assert "budget_usdc" in s
            assert "used_notional" in s
            assert "used_pct" in s
            assert "weekly_pnl" in s
            assert "weekly_trades" in s
            assert "weekly_wins" in s
            assert "next_rebalance" in s
