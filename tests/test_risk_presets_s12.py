"""Presets UI S12 : n'écrivent plus trading.risk_per_trade / max_positions."""
from __future__ import annotations

from app.api.routes.portfolio import (
    _LEGACY_TRADING_KEYS,
    _RISK_PRESETS,
    _preset_public_view,
    _resolve_current_preset,
    _strip_legacy_trading_keys,
)


def test_presets_do_not_declare_legacy_trading_keys():
    for key, p in _RISK_PRESETS.items():
        for legacy in _LEGACY_TRADING_KEYS:
            assert legacy not in p["trading"], f"{key} déclare encore {legacy}"
        assert "profile" in p["risk"]
        assert p["risk"]["profile"] in ("prudent", "normal", "agressif")


def test_public_view_has_no_legacy_fields():
    for key, p in _RISK_PRESETS.items():
        view = _preset_public_view(key, p)
        assert "risk_per_trade" not in view
        assert "max_positions" not in view
        assert view["trade_risk_pct"] > 0
        assert view["profile"] == p["risk"]["profile"]


def test_strip_legacy_keys():
    trading = {
        "risk_per_trade": 0.02,
        "max_positions": 8,
        "daily_drawdown_limit": 0.08,
        "score_threshold": 0.55,
    }
    _strip_legacy_trading_keys(trading)
    assert "risk_per_trade" not in trading
    assert "max_positions" not in trading
    assert trading["daily_drawdown_limit"] == 0.08
    assert trading["score_threshold"] == 0.55


def test_resolve_current_from_profile():
    assert _resolve_current_preset({"risk": {"profile": "agressif"}}) == "agressif"
    assert _resolve_current_preset({"risk": {"profile": "normal"}}) == "equilibre"
    assert _resolve_current_preset({"ui": {"risk_preset": "prudent"}}) == "prudent"
