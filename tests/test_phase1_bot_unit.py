"""Phase 1 — le bot comme unité : identité/venue, sizing par bot, vetos shadow."""

import app.core.bot_identity as bi
from app.core.risk_gate import RiskManager


def _base_cfg(**over):
    cfg = {
        "trading": {
            "capital": 1000, "paper_mode": True, "risk_per_trade": 0.01,
            "max_positions": 2, "max_longs": 1, "max_shorts": 1,
            "max_trades_per_minute": 100, "max_leverage": 1,
            "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20,
            "margin_mode": "isolated",
        },
        "exchange": {"name": "okx", "margin": True},
        "backtest": {"max_notional_pct": 0.20},
        "risk": {},
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


# ── Sizing par bot ──────────────────────────────────────────────────────────
def test_per_bot_sizing_caps_on_budget_times_leverage():
    rm = RiskManager(_base_cfg())
    # Sans budget : cap sur équité × max_notional_pct (0.20 × 1000 = 200)
    _, notional_global = rm.compute_size(entry=100.0, atr=0.01, score=1.0, threshold=0.5)
    assert notional_global <= 200.0 + 1e-6
    # Avec budget de 50 USDC et levier 1 : notionnel plafonné à 50
    _, notional_bot = rm.compute_size(entry=100.0, atr=0.01, score=1.0, threshold=0.5,
                                      budget=50.0, max_leverage=1.0)
    assert notional_bot <= 50.0 + 1e-6
    # Levier 3 → cap à 150
    _, notional_lev = rm.compute_size(entry=100.0, atr=0.01, score=1.0, threshold=0.5,
                                      budget=50.0, max_leverage=3.0)
    assert 50.0 < notional_lev <= 150.0 + 1e-6


# ── Vetos shadow ────────────────────────────────────────────────────────────
def test_veto_shadow_relaxes_capacity_but_keeps_killswitch():
    cfg = _base_cfg(risk={"veto_mode": "shadow"})
    rm = RiskManager(cfg)
    # Sature max_positions (2) et max_longs (1)
    rm.open_positions = {
        "a": {"side": "long", "id": "a"},
        "b": {"side": "long", "id": "b"},
    }
    ok, _ = rm.can_trade("long")
    assert ok is True  # shadow : n'empêche plus
    assert rm.veto_shadow_blocks.get("max_positions", 0) >= 1
    assert rm.veto_shadow_blocks.get("max_longs", 0) >= 1
    # Kill-switch global TOUJOURS appliqué, même en shadow
    rm.halted = True
    rm.halt_reason = "kill"
    ok2, reason = rm.can_trade("long")
    assert ok2 is False and reason == "kill"


def test_veto_shadow_ignored_when_live():
    # veto_mode shadow mais paper_mode False → enforce (sécurité)
    cfg = _base_cfg(trading={"paper_mode": False}, risk={"veto_mode": "shadow"})
    rm = RiskManager(cfg)
    rm.open_positions = {"a": {"side": "long", "id": "a"},
                         "b": {"side": "long", "id": "b"}}
    ok, reason = rm.can_trade("long")
    assert ok is False and "Max positions" in reason
    assert rm.veto_shadow is False
