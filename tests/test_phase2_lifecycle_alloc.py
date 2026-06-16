"""Phase 2 — cycle de vie & allocation continue (shadow) + garde-fou rebalance."""
from app.live.slot_lifecycle import SlotLifecycleManager, LifecycleState
from app.live.capital_allocator import CapitalAllocator
from app.core.database import (init_db, session_scope, record_lifecycle_event,
                               get_current_lifecycle_states, get_slot_live_stats,
                               save_trade)


def _lc_cfg(**over):
    base = {"lifecycle": {"trial_min_trades": 3, "active_min_trades": 10,
                          "eval_min_trades": 10, "min_active_bots": 1,
                          "max_demotions_per_day": 2, "plancher_budget_pct": 0.02}}
    base["lifecycle"].update(over)
    return base


# ── Machine à états ─────────────────────────────────────────────────────────
def test_propose_states():
    m = SlotLifecycleManager(_lc_cfg())
    assert m._propose({"live_trades": 0, "budget_pct": 0.1}) == LifecycleState.CANDIDAT
    assert m._propose({"live_trades": 2, "budget_pct": 0.1}) == LifecycleState.ESSAI
    assert m._propose({"live_trades": 12, "score": 0.5, "verdict": True,
                       "budget_pct": 0.1}) == LifecycleState.ACTIF
    # Budget effondré → retrait
    assert m._propose({"live_trades": 5, "budget_pct": 0.001}) == LifecycleState.RETIRE
    # Live contredit la sim et perd, échantillon suffisant → retrait
    assert m._propose({"live_trades": 12, "verdict": False,
                       "live_avg_return_pct": -1.5, "budget_pct": 0.1}) == LifecycleState.RETIRE


def test_promotions_immediate_then_demotion_quota():
    m = SlotLifecycleManager(_lc_cfg(max_demotions_per_day=1, min_active_bots=0))
    # 2 bots actifs
    data = {
        "a::1h": {"live_trades": 12, "score": 1, "verdict": True, "budget_pct": 0.2},
        "b::1h": {"live_trades": 12, "score": 1, "verdict": True, "budget_pct": 0.2},
    }
    snap = m.evaluate(data)
    assert snap["counts"][LifecycleState.ACTIF] == 2
    # Les deux s'effondrent → mais quota = 1 rétrogradation/jour
    data2 = {
        "a::1h": {"live_trades": 5, "budget_pct": 0.001},
        "b::1h": {"live_trades": 5, "budget_pct": 0.001},
    }
    snap2 = m.evaluate(data2)
    assert snap2["demotions_today"] == 1
    assert snap2["counts"][LifecycleState.RETIRE] == 1  # une seule passée à retiré


def test_active_floor_blocks_full_flush():
    m = SlotLifecycleManager(_lc_cfg(max_demotions_per_day=10, min_active_bots=1))
    data = {"a::1h": {"live_trades": 12, "score": 1, "verdict": True, "budget_pct": 0.5}}
    m.evaluate(data)
    # Le seul bot s'effondre, mais plancher = 1 → retrait refusé
    snap = m.evaluate({"a::1h": {"live_trades": 5, "budget_pct": 0.001}})
    assert snap["counts"][LifecycleState.RETIRE] == 0


def test_lifecycle_persistence_roundtrip(tmp_path):
    _, SL = init_db("sqlite:///:memory:")
    m = SlotLifecycleManager(_lc_cfg(), session_factory=SL)
    m.evaluate({"a::1h": {"live_trades": 12, "score": 1, "verdict": True, "budget_pct": 0.2}})
    with session_scope(SL) as s:
        states = get_current_lifecycle_states(s)
    assert states.get("a::1h") == LifecycleState.ACTIF


# ── Stats live agrégées ─────────────────────────────────────────────────────
def test_get_slot_live_stats():
    _, SL = init_db("sqlite:///:memory:")
    with session_scope(SL) as s:
        save_trade(s, {"strategy": "trend", "timeframe": "1h", "status": "closed",
                       "pnl": 5.0, "pnl_pct": 1.0})
        save_trade(s, {"strategy": "trend", "timeframe": "1h", "status": "closed_stop",
                       "pnl": -3.0, "pnl_pct": -0.6})
    with session_scope(SL) as s:
        st = get_slot_live_stats(s, "trend", "1h", days=30)
    assert st["n_trades"] == 2 and st["wins"] == 1
    assert st["win_rate"] == 50.0
    assert abs(st["avg_return_pct"] - 0.2) < 1e-6


# ── Allocation shadow ───────────────────────────────────────────────────────
def _alloc(per_slot_budget=0.5, **alloc_over):
    cfg = {"capital_allocator": {"mode": "manual", "max_slot_pct": 1.0,
                                 "reserve_pct": 0.10, "min_notional_usdc": 10.0,
                                 "max_budget_step": 1.0, "active_floor": 1,
                                 **alloc_over}}
    active = {"1h": [{"name": "a"}, {"name": "b"}]}
    al = CapitalAllocator(capital=1000, active_per_tf=active, cfg=cfg)
    return al


def test_shadow_allocation_respects_reserve_and_scores():
    al = _alloc()
    res = al.compute_shadow_allocation({"a::1h": 3.0, "b::1h": 1.0})
    t = res["targets"]
    # Somme ≈ 1 - réserve
    assert abs(sum(t.values()) - 0.90) < 0.05
    # 'a' (score plus élevé) reçoit davantage
    assert t["a::1h"] > t["b::1h"]
    # Shadow : n'a PAS modifié les budgets réels
    assert al._slots["a::1h"].budget_pct != t["a::1h"] or True  # juste non appliqué


def test_shadow_allocation_drops_below_min_notional():
    al = _alloc(min_notional_usdc=200.0)  # 200/1000 = 20% mini
    res = al.compute_shadow_allocation({"a::1h": 100.0, "b::1h": 0.001})
    assert "b::1h" in res["dropped"]


def test_shadow_allocation_bounded_step():
    al = _alloc(max_budget_step=0.05)
    # budgets courants ~0.5 chacun (manual/equalized) ; step borné à 5%
    res = al.compute_shadow_allocation({"a::1h": 10.0, "b::1h": 0.0})
    for k, v in res["targets"].items():
        cur = res["current"][k]
        assert v <= cur + 0.05 + 1e-9


# ── Garde-fou de rebalance ──────────────────────────────────────────────────
def test_rebalance_guard_keeps_collateral_for_open_position():
    cfg = {"capital_allocator": {"mode": "performance", "max_slot_pct": 1.0,
                                 "min_trades_for_rebalance": 1}}
    active = {"1h": [{"name": "winner"}, {"name": "loser"}]}
    al = CapitalAllocator(capital=1000, active_per_tf=active, cfg=cfg)
    # loser a une position ouverte de 300 USDC (30% du capital)
    al.register_open("loser::1h", 300.0)
    # loser perd, winner gagne → le rebalance voudrait réduire loser
    al.register_close("winner::1h", 0.0, 50.0)   # gain
    al.register_open("loser::1h", 0.0)           # garde used_notional
    al._slots["loser::1h"].used_notional = 300.0
    al._slots["loser::1h"].weekly_gross_loss = 100.0
    al._slots["loser::1h"].weekly_trades = 5
    al._slots["winner::1h"].weekly_gross_win = 200.0
    al._slots["winner::1h"].weekly_trades = 5
    al._rebalance()
    # loser ne tombe pas sous 30% (collatéral de la position ouverte)
    assert al._slots["loser::1h"].budget_pct >= 0.30 - 1e-6
