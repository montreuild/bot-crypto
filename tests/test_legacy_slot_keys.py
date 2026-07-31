"""OPS-01 — compatibilité des clés de slot héritées à 2 parties.

Avant la refonte per-symbole, ``capital_allocator.slot_budgets``,
``capital_allocator.disabled_slots`` et ``lifecycle.manual_active``
persistaient des clés ``strategy::tf`` (sans symbole). Ces clés doivent
continuer de s'appliquer, par préfixe, à tous les slots ``strategy::tf::*``
— sans quoi budgets custom et forçages manuels deviennent orphelins en
silence. La clé exacte à 3 parties garde priorité.
"""
from app.live.capital_allocator import CapitalAllocator, _lookup_legacy
from app.live.slot_lifecycle import LifecycleState, SlotLifecycleManager


def _active_per_tf():
    return {"4h": [
        {"name": "trend_rider", "params": {"trend_rider": {}}, "score": 0.3,
         "tf": "4h", "symbol": "ETH/USDC"},
        {"name": "smart_money", "params": {"smart_money": {}}, "score": 0.3,
         "tf": "4h", "symbol": "BTC/USDC"},
    ]}


# ── _lookup_legacy (helper partagé) ──────────────────────────────────────────

def test_lookup_exact_key_has_priority():
    mapping = {"trend_rider::4h": "legacy", "trend_rider::4h::ETH/USDC": "exact"}
    assert _lookup_legacy(mapping, "trend_rider::4h::ETH/USDC") == "trend_rider::4h::ETH/USDC"


def test_lookup_falls_back_to_legacy_prefix():
    mapping = {"trend_rider::4h": 0.4}
    assert _lookup_legacy(mapping, "trend_rider::4h::ETH/USDC") == "trend_rider::4h"
    assert _lookup_legacy(mapping, "trend_rider::4h::BTC/USDC") == "trend_rider::4h"
    assert _lookup_legacy(mapping, "smart_money::4h::BTC/USDC") is None


# ── CapitalAllocator : disabled_slots + slot_budgets hérités ─────────────────

def test_allocator_legacy_disabled_slot_applies_to_symbol_slots():
    cfg = {"capital_allocator": {"disabled_slots": ["trend_rider::4h"]}}
    alloc = CapitalAllocator(capital=1000.0, active_per_tf=_active_per_tf(), cfg=cfg)
    assert alloc._slots["trend_rider::4h::ETH/USDC"].enabled is False
    assert alloc._slots["smart_money::4h::BTC/USDC"].enabled is True


def test_allocator_legacy_custom_budget_applies_by_prefix():
    cfg = {"capital_allocator": {"mode": "manual",
                                 "slot_budgets": {"trend_rider::4h": 0.40}}}
    alloc = CapitalAllocator(capital=1000.0, active_per_tf=_active_per_tf(), cfg=cfg)
    tr = alloc._slots["trend_rider::4h::ETH/USDC"].budget_pct
    sm = alloc._slots["smart_money::4h::BTC/USDC"].budget_pct
    # Le budget custom hérité est bien pris en compte pour le slot 3-parties
    # (après normalisation, il reste distinct du slot sans budget custom).
    assert tr > 0
    assert tr != sm


# ── SlotLifecycleManager : manual_active hérité ──────────────────────────────

def test_lifecycle_legacy_manual_active_forces_symbol_slot():
    mgr = SlotLifecycleManager({"lifecycle": {"manual_active": ["trend_rider::4h"]}})
    out = mgr.evaluate({
        "trend_rider::4h::ETH/USDC": {"budget_pct": 0.2, "live_trades": 0},
        "smart_money::4h::BTC/USDC": {"budget_pct": 0.2, "live_trades": 0},
    })
    states = out.get("states", out) if isinstance(out, dict) else out
    # Le forçage hérité s'applique au slot 3-parties correspondant…
    assert states["trend_rider::4h::ETH/USDC"] == LifecycleState.ACTIF
    # …et pas aux autres slots.
    assert states["smart_money::4h::BTC/USDC"] != LifecycleState.ACTIF


def test_lifecycle_disable_also_clears_legacy_key():
    mgr = SlotLifecycleManager({"lifecycle": {"manual_active": ["trend_rider::4h"]}})
    # Désactiver via la clé 3-parties doit aussi lever le forçage hérité,
    # sinon le repli par préfixe re-forcerait le slot au prochain evaluate.
    mgr.set_manual_active("trend_rider::4h::ETH/USDC", False)
    assert "trend_rider::4h" not in mgr._manual_active
