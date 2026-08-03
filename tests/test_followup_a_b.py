"""Suivi A (régressions) + B (file re-opt & allocation appliquée)."""
import app.core.bot_identity as bi
from app.live.slot_lifecycle import LifecycleState, SlotLifecycleManager


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
