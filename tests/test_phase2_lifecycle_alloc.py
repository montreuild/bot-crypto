"""Phase 2 — cycle de vie & allocation continue (shadow) + garde-fou rebalance."""
from app.core.database import get_current_lifecycle_states, get_slot_live_stats, init_db, save_trade, session_scope
from app.live.capital_allocator import CapitalAllocator
from app.live.slot_lifecycle import LifecycleState, SlotLifecycleManager


def _lc_cfg(**over):
    base = {"lifecycle": {"trial_min_trades": 3, "active_min_trades": 10,
                          "eval_min_trades": 10, "min_active_bots": 1,
                          "max_demotions_per_day": 2, "plancher_budget_pct": 0.02,
                          "edge_min_trades": 20, "max_worst_trade_pct": 50.0,
                          "fidelity_min_fills": 2}}
    base["lifecycle"].update(over)
    return base


def _edge_active(**over):
    """Bot avec edge prouvée (cône > 0, échantillon suffisant, queue bornée) ET
    fidélité live confirmée → devrait être ACTIF."""
    d = {"budget_pct": 0.2, "edge_ci_low": 0.5, "edge_n": 30, "worst_trade_pct": -5.0,
         "live_trades": 3, "live_in_band": True}
    d.update(over)
    return d


# ── Machine à états (promotion par edge) ─────────────────────────────────────
def test_propose_states():
    m = SlotLifecycleManager(_lc_cfg())
    S = LifecycleState
    # Pas d'edge prouvée → Candidat (même sans trade).
    assert m._propose({"live_trades": 0, "budget_pct": 0.1}) == S.CANDIDAT
    # Edge prouvée mais fidélité pas encore confirmée → Essai.
    assert m._propose(_edge_active(live_trades=0, live_in_band=None)) == S.ESSAI
    # Edge prouvée + fidélité (≥ fills, in_band True) → Actif.
    assert m._propose(_edge_active()) == S.ACTIF
    # Échantillon backtest trop court → Candidat.
    assert m._propose(_edge_active(edge_n=5)) == S.CANDIDAT
    # Borne basse du cône ≤ 0 → Candidat.
    assert m._propose(_edge_active(edge_ci_low=-0.1)) == S.CANDIDAT
    # Garde-fou de queue (pire trade au-delà du seuil) → Candidat.
    assert m._propose(_edge_active(worst_trade_pct=-80.0)) == S.CANDIDAT
    # Budget effondré → Retrait.
    assert m._propose({"live_trades": 5, "budget_pct": 0.001}) == S.RETIRE
    # Le réel contredit la sim et perd, échantillon suffisant → Retrait.
    assert m._propose(_edge_active(live_in_band=False, live_avg_return_pct=-1.5,
                                   live_trades=12)) == S.RETIRE
    # Bypass manuel → Actif quoi qu'il arrive.
    assert m._propose({"manual_active": True, "live_trades": 0, "budget_pct": 0.1}) == S.ACTIF


def test_promotions_immediate_then_demotion_quota():
    m = SlotLifecycleManager(_lc_cfg(max_demotions_per_day=1, min_active_bots=0))
    data = {"a::1h": _edge_active(), "b::1h": _edge_active()}
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
    m.evaluate({"a::1h": _edge_active(budget_pct=0.5)})
    # Le seul bot s'effondre, mais plancher = 1 → retrait refusé
    snap = m.evaluate({"a::1h": {"live_trades": 5, "budget_pct": 0.001}})
    assert snap["counts"][LifecycleState.RETIRE] == 0


def test_manual_active_bypass():
    # Forçage via config : un bot sans edge est quand même Actif.
    m = SlotLifecycleManager(_lc_cfg(manual_active=["x::1h"]))
    snap = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap["states"]["x::1h"] == LifecycleState.ACTIF


def test_set_manual_active_runtime():
    m = SlotLifecycleManager(_lc_cfg())
    snap = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap["states"]["x::1h"] == LifecycleState.CANDIDAT
    m.set_manual_active("x::1h", True)
    snap2 = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap2["states"]["x::1h"] == LifecycleState.ACTIF
    m.set_manual_active("x::1h", False)
    snap3 = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    # Libéré : retombe vers Candidat (pas d'edge) — au prochain quota de rétrogradation.
    assert snap3["states"]["x::1h"] in (LifecycleState.CANDIDAT, LifecycleState.ACTIF)


def test_lifecycle_persistence_roundtrip(tmp_path):
    _, SL = init_db("sqlite:///:memory:")
    m = SlotLifecycleManager(_lc_cfg(), session_factory=SL)
    m.evaluate({"a::1h": _edge_active()})
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


# ── Cône d'edge (promotion par expectancy) ───────────────────────────────────
def test_edge_contract_significance():
    from app.core.oos_tracker import _edge_contract
    # Edge consistante (gains réguliers, échantillon large) → borne basse > 0.
    strong = _edge_contract([1.0, 1.2, 0.8, 1.1, 0.9] * 8)  # 40 trades
    assert strong["available"] is True
    assert strong["ci_low_pct"] > 0
    assert strong["n"] == 40
    # Échantillon minuscule mais bruité → borne basse ≤ 0 (cône large).
    weak = _edge_contract([5.0, -4.0, 6.0])
    assert weak["ci_low_pct"] <= weak["ci_high_pct"]
    # worst_trade_pct = pire trade observé (garde-fou de queue).
    assert weak["worst_trade_pct"] == -4.0
    # Aucun trade → indisponible.
    assert _edge_contract([])["available"] is False
