"""Phase 2 — cycle de vie & allocation continue (shadow) + garde-fou rebalance."""
from app.core.database import get_current_lifecycle_states, get_slot_live_stats, init_db, save_trade, session_scope
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


def test_force_active_bypass():
    # Forçage via config : un bot sans edge est quand même Actif.
    m = SlotLifecycleManager(_lc_cfg(force_active=["x::1h"]))
    snap = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap["states"]["x::1h"] == LifecycleState.ACTIF


def test_manual_active_is_still_read_as_force_active():
    """D6 — rétro-compat : une config non migrée continue de forcer ses slots."""
    m = SlotLifecycleManager(_lc_cfg(manual_active=["x::1h"]))
    snap = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap["states"]["x::1h"] == LifecycleState.ACTIF


def test_force_active_wins_over_the_legacy_key():
    """Si les deux clés coexistent, `force_active` fait foi — sinon la liste
    dépréciée ressusciterait des forçages que l'utilisateur a levés."""
    m = SlotLifecycleManager(_lc_cfg(force_active=[], manual_active=["x::1h"]))
    snap = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap["states"]["x::1h"] == LifecycleState.CANDIDAT


def test_force_active_also_blocks_the_retrait():
    """Ce que le forçage court-circuite VRAIMENT, au-delà de la promotion.

    Un slot forcé dont le budget s'est effondré reste ACTIF : ni retrait, ni
    entrée dans la file de ré-optimisation. C'est la raison de fond du retrait
    des 15 slots de config.yaml (D6) — un bot forcé perdant ne sort jamais.
    """
    healthy   = _edge_active()
    collapsed = {"live_trades": 5, "budget_pct": 0.001}

    # Le slot existe déjà (sinon la chute passe par le chemin « création », qui
    # ne compte pas comme une rétrogradation et n'alimente pas la file).
    m = SlotLifecycleManager(_lc_cfg(force_active=["x::1h"], min_active_bots=0))
    m.evaluate({"x::1h": healthy})
    snap = m.evaluate({"x::1h": collapsed})
    assert snap["states"]["x::1h"] == LifecycleState.ACTIF
    assert "x::1h" not in snap["reopt_queue"]

    # Même trajectoire sans forçage → retrait + file de ré-optimisation.
    auto = SlotLifecycleManager(_lc_cfg(min_active_bots=0))
    auto.evaluate({"x::1h": healthy})
    snap_auto = auto.evaluate({"x::1h": collapsed})
    assert snap_auto["states"]["x::1h"] == LifecycleState.RETIRE
    assert "x::1h" in snap_auto["reopt_queue"]


def test_set_force_active_runtime():
    m = SlotLifecycleManager(_lc_cfg())
    snap = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap["states"]["x::1h"] == LifecycleState.CANDIDAT
    m.set_force_active("x::1h", True)
    snap2 = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap2["states"]["x::1h"] == LifecycleState.ACTIF
    m.set_force_active("x::1h", False)
    snap3 = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    # Libéré : retombe vers Candidat (pas d'edge) — au prochain quota de rétrogradation.
    assert snap3["states"]["x::1h"] in (LifecycleState.CANDIDAT, LifecycleState.ACTIF)


def test_set_manual_active_alias_still_works():
    """L'alias déprécié reste appelable (appelants historiques, route API)."""
    m = SlotLifecycleManager(_lc_cfg())
    m.set_manual_active("x::1h", True)
    snap = m.evaluate({"x::1h": {"live_trades": 0, "budget_pct": 0.1}})
    assert snap["states"]["x::1h"] == LifecycleState.ACTIF


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
