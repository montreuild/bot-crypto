"""Phase 1 — le bot comme unité : identité/venue, sizing par bot, vetos shadow."""

import app.core.bot_identity as bi
from app.core.risk_gate import RiskManager


def _base_cfg(**over):
    cfg = {
        "trading": {
            "paper_mode": True, "max_trades_per_minute": 100,
            "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20,
            "margin_mode": "isolated",
        },
        "exchange": {"name": "okx", "margin": True},
        "risk": {"profile": "test", "profiles": {"test": 0.01},
                 "envelopes": {"margin-isolated": {
                     "capital": 1000, "max_symbol_exposure_pct": 1.0,
                     "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}}},
    }
    for k, v in over.items():
        cfg[k] = {**cfg.get(k, {}), **v}
    return cfg


# ── Identité / venue ────────────────────────────────────────────────────────
def test_params_hash_stable_and_order_insensitive():
    assert bi.params_hash({"a": 1, "b": 2}) == bi.params_hash({"b": 2, "a": 1})
    assert bi.params_hash({"a": 1}) != bi.params_hash({"a": 2})
    # None / NaN ignorés
    assert bi.params_hash({"a": 1, "c": None}) == bi.params_hash({"a": 1})


def test_generation_increments_only_on_param_change(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "_GEN_PATH", str(tmp_path / "gen.json"))
    cfg = _base_cfg()
    i1 = bi.register_identity("trend", "1h", {"x": 1}, cfg)
    assert i1.generation == 1
    # Mêmes params → génération inchangée
    i2 = bi.register_identity("trend", "1h", {"x": 1}, cfg)
    assert i2.generation == 1
    # Params changés → génération +1 (anti-collision)
    i3 = bi.register_identity("trend", "1h", {"x": 2}, cfg)
    assert i3.generation == 2
    assert i3.bot_id != i1.bot_id
    # peek ne modifie pas la génération persistée
    bi.peek_identity("trend", "1h", {"x": 3}, cfg)
    assert bi.register_identity("trend", "1h", {"x": 2}, cfg).generation == 2


def test_venue_default_from_globals_then_named():
    cfg = _base_cfg()
    v = bi.resolve_venue(cfg, "trend", "1h")
    assert v.market_type == "margin" and v.margin_mode == "isolated"
    # Venue nommée assignée à un bot précis
    cfg["venues"] = {
        "defs": {"perp-hedge-okx": {"market_type": "perp", "exchange": "okx",
                                     "hedge_mode": True, "max_leverage": 5}},
        "assign": {"trend::1h": "perp-hedge-okx"},
    }
    v2 = bi.resolve_venue(cfg, "trend", "1h")
    assert v2.market_type == "perp" and v2.exchange == "okx" and v2.hedge_mode
    assert v2.max_leverage == 5
    # Autre bot → venue par défaut
    assert bi.resolve_venue(cfg, "breakout", "4h").market_type == "margin"


# ── Sizing par enveloppe de slot (S12) ──────────────────────────────────────
def test_sizing_caps_on_slot_envelope_times_leverage():
    """`per_bot_sizing` disparaît : il n'existe plus qu'UNE base, l'enveloppe
    du slot (§2.2). Le plafond notionnel en dérive directement."""
    from app.core.risk_envelope import Envelope

    def _env(slot_envelope, leverage):
        return Envelope(
            venue="margin-isolated", symbol="BTC/USDC", slot_key="s::1h::BTC/USDC",
            currency="USDC", venue_envelope=1000.0, venue_risk_budget=50.0,
            symbol_envelope=1000.0, symbol_risk_budget=20.0,
            slot_envelope=slot_envelope, slot_risk_amount=slot_envelope * 0.01,
            max_leverage=leverage, min_notional=0.0,
            trade_risk_pct=0.01, weight=slot_envelope / 1000.0,
        )

    rm = RiskManager(_base_cfg())
    # Stop très serré → la taille butte sur le plafond, pas sur le risque.
    _, notional = rm.compute_size(entry=100.0, stop_dist=0.01, env=_env(50.0, 1.0))
    assert notional <= 50.0 + 1e-6
    _, notional_lev = rm.compute_size(entry=100.0, stop_dist=0.01, env=_env(50.0, 3.0))
    assert 50.0 < notional_lev <= 150.0 + 1e-6


# ── Vetos shadow ────────────────────────────────────────────────────────────
def test_veto_shadow_relaxes_anti_spam_but_keeps_killswitch():
    """S12 : les vetos de capacité (max_positions/longs/shorts) sont supprimés
    — le budget de risque venue les remplace. Il ne reste que l'anti-spam à
    pouvoir passer en shadow."""
    cfg = _base_cfg(trading={"max_trades_per_minute": 1},
                    risk={"veto_mode": "shadow"})
    rm = RiskManager(cfg)
    assert rm.can_trade("long")[0] is True      # consomme le quota de la minute
    ok, _ = rm.can_trade("long")
    assert ok is True                            # shadow : n'empêche plus
    assert rm.veto_shadow_blocks.get("anti_spam", 0) >= 1
    # Kill-switch global TOUJOURS appliqué, même en shadow
    rm.halted = True
    rm.halt_reason = "kill"
    ok2, reason = rm.can_trade("long")
    assert ok2 is False and reason == "kill"


def test_veto_shadow_ignored_when_live():
    # veto_mode shadow mais paper_mode False → enforce (sécurité)
    cfg = _base_cfg(trading={"paper_mode": False, "max_trades_per_minute": 1},
                    risk={"veto_mode": "shadow"})
    rm = RiskManager(cfg)
    assert rm.can_trade("long")[0] is True
    ok, reason = rm.can_trade("long")
    assert ok is False and "anti-spam" in reason
    assert rm.veto_shadow is False
