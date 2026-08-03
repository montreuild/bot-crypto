"""Phase 3 — sécurité & résilience : kill-switch persistant, reprise, watchdog."""
import importlib

import pytest

from app.core.database import init_db, load_risk_state, session_scope
from app.core.risk_gate import RiskManager


def _cfg(**risk_over):
    return {
        "trading": {"paper_mode": True, "max_trades_per_minute": 100,
                    "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20},
        "exchange": {"name": "okx"},
        "venues": {"default": "okx-test", "defs": {"okx-test": {"max_leverage": 1}},
                   "assign": {}},
        # S12 : l'équité suivie par les circuit breakers est celle de la venue
        # par défaut.
        "risk": {"equity_kill_switch_dd": 0.35,
                 "envelopes": {"okx-test": {
                     "capital": 1000, "max_symbol_exposure_pct": 1.0,
                     "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}},
                 **risk_over},
    }


# ── Kill-switch d'équité ────────────────────────────────────────────────────
def test_equity_kill_switch_trips_and_is_sticky():
    rm = RiskManager(_cfg())
    assert rm.kill_switch_equity == pytest.approx(650.0)
    rm.update_equity(640.0)               # sous le plancher → kill-switch
    assert rm.halted and rm._kill_switch_tripped
    # reset normal refusé tant que le kill-switch est actif
    rm.reset_halt()
    assert rm.halted
    # acquittement forcé requis
    rm.reset_halt(force=True)
    assert not rm.halted and not rm._kill_switch_tripped


def test_kill_switch_disabled_when_dd_zero():
    rm = RiskManager(_cfg(equity_kill_switch_dd=0.0))
    assert rm.kill_switch_equity == 0.0
    rm.update_equity(10.0)
    assert not rm._kill_switch_tripped    # pas de kill-switch (mais CB DD global peut halt)


# ── Persistance / reprise propre ────────────────────────────────────────────
def test_risk_state_persists_and_restores():
    _, SL = init_db("sqlite:///:memory:")
    rm = RiskManager(_cfg())
    rm.attach_persistence(SL)
    # déclenche le kill-switch + une pause de slot
    rm.update_equity(600.0)
    rm.update_slot_result("trend::1h", -5.0, won=False)
    with session_scope(SL) as s:
        blob = load_risk_state(s, "global")
    assert blob["kill_switch_tripped"] is True
    assert "trend::1h" in blob["slots"]

    # Nouveau RiskManager (simule un redémarrage) sur la même base → reprise
    rm2 = RiskManager(_cfg())
    rm2.attach_persistence(SL)
    assert rm2.halted and rm2._kill_switch_tripped
    assert rm2.halt_reason.startswith("KILL-SWITCH")


def test_slot_pause_survives_restart():
    _, SL = init_db("sqlite:///:memory:")
    cfg = _cfg(consecutive_loss_limit=2, consecutive_pause_secs=3600,
               equity_kill_switch_dd=0.0)
    rm = RiskManager(cfg)
    rm.attach_persistence(SL)
    rm.update_slot_result("a::1h", -1.0, won=False)
    rm.update_slot_result("a::1h", -1.0, won=False)   # 2 pertes → pause
    ok, _ = rm.can_slot_trade("a::1h")
    assert ok is False
    # redémarrage
    rm2 = RiskManager(cfg)
    rm2.attach_persistence(SL)
    ok2, reason = rm2.can_slot_trade("a::1h")
    assert ok2 is False and "pausé" in reason


# ── Watchdog dead-man ───────────────────────────────────────────────────────
def test_watchdog_heartbeat_and_kill(tmp_path, monkeypatch):
    wd = importlib.import_module("app.live.watchdog")
    monkeypatch.setattr(wd, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(wd, "HEARTBEAT_PATH", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(wd, "KILL_PATH", str(tmp_path / "KILL_SWITCH"))

    # Battement frais, bot actif → pas d'alerte
    wd.write_heartbeat({"running": True, "cycle": 1})
    res = wd.check_once(timeout_s=300)
    assert res["stale"] is False and res["armed"] is False

    # Battement périmé (simulate ancien ts) → arme le kill-switch
    res2 = wd.check_once(timeout_s=300, now=wd.read_heartbeat()["ts"] + 10_000)
    assert res2["stale"] is True and wd.kill_switch_armed() is True

    wd.clear_kill_switch()
    assert wd.kill_switch_armed() is False

    # Bot jamais démarré (running False) → pas d'alerte même si vieux
    wd.write_heartbeat({"running": False})
    res3 = wd.check_once(timeout_s=0, now=wd.read_heartbeat()["ts"] + 10_000)
    assert res3["stale"] is False
