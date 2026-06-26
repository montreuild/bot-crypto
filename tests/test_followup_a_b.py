"""Suivi A (régressions) + B (file re-opt & allocation appliquée)."""
import app.core.bot_identity as bi
from app.live.capital_allocator import CapitalAllocator
from app.live.slot_lifecycle import SlotLifecycleManager, LifecycleState


# ── A2 : peek_identity accepte un dict de générations préchargé (0 lecture disque)
def test_peek_identity_uses_preloaded_gens(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "_GEN_PATH", str(tmp_path / "gen.json"))
    cfg = {"trading": {"margin_mode": None, "max_leverage": 1},
           "exchange": {"name": "okx"}}
    bi.register_identity("trend", "1h", {"x": 1}, cfg)   # génération 1 persistée
    gens = bi._load_generations()

    calls = {"n": 0}
    real_load = bi._load_generations
    def _counting():
        calls["n"] += 1
        return real_load()
    monkeypatch.setattr(bi, "_load_generations", _counting)

    ident = bi.peek_identity("trend", "1h", {"x": 1}, cfg, gens=gens)
    assert ident.generation == 1
    assert calls["n"] == 0   # aucune lecture disque quand gens est fourni


# ── B : allocation continue réellement appliquée + garde-fou collatéral ──────
def _alloc(**over):
    cfg = {"capital_allocator": {"mode": "manual", "max_slot_pct": 1.0,
                                 "reserve_pct": 0.10, "min_notional_usdc": 1.0,
                                 "max_budget_step": 1.0, "active_floor": 1,
                                 "continuous_allocation": True, **over}}
    active = {"1h": [{"name": "a"}, {"name": "b"}]}
    return CapitalAllocator(capital=1000, active_per_tf=active, cfg=cfg)


def test_apply_continuous_allocation_writes_budgets():
    al = _alloc()
    assert al.continuous_allocation is True
    res = al.apply_continuous_allocation({"a::1h": 3.0, "b::1h": 1.0})
    assert res.get("applied") is True
    # Les budgets RÉELS suivent les cibles (a > b car meilleur score).
    assert al._slots["a::1h"].budget_pct == res["targets"]["a::1h"]
    assert al._slots["a::1h"].budget_pct > al._slots["b::1h"].budget_pct


def test_apply_respects_open_position_collateral():
    al = _alloc()
    al._slots["b::1h"].used_notional = 400.0   # 40% du capital engagé
    al.apply_continuous_allocation({"a::1h": 100.0, "b::1h": 0.0})  # voudrait défonder b
    assert al._slots["b::1h"].budget_pct >= 0.40 - 1e-9   # collatéral préservé


# ── B : la file de re-opt est consommable (pop vide la file) ─────────────────
def test_reopt_queue_pop_consumes():
    m = SlotLifecycleManager({"lifecycle": {"max_demotions_per_day": 10,
                                            "min_active_bots": 0,
                                            "plancher_budget_pct": 0.02}})
    m.evaluate({"a::1h": {"live_trades": 12, "score": 1, "verdict": True, "budget_pct": 0.2}})
    snap = m.evaluate({"a::1h": {"live_trades": 5, "budget_pct": 0.001}})  # → retiré
    assert snap["counts"][LifecycleState.RETIRE] == 1
    assert "a::1h" in snap["reopt_queue"]
    q = m.pop_reopt_queue()
    assert q == ["a::1h"]
    assert m.pop_reopt_queue() == []   # vidée
