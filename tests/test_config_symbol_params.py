"""UI-02 — édition des paramètres de stratégie PAR SYMBOLE depuis l'API config.

- GET /api/config/strategy-overrides : liste les overrides optimizer_results
  par (tf, symbole), une entrée héritée étant présentée comme BTC/USDC.
- POST /api/config/strategy-params : avec timeframe+symbol, écrit un override
  via apply_best_params (schéma per-symbole canonique) SANS toucher au bloc
  params: de base ; sans tf/symbol, comportement historique inchangé.
"""
import pytest
from fastapi import HTTPException

from app.api import state
from app.api.routes.config_strategies import strategy_overrides, update_strategy_params


@pytest.fixture
def fake_cfg(monkeypatch):
    cfg = {
        "scanner": {"symbols": ["BTC/USDC", "ETH/USDC"]},
        "strategy_params": {"trend_rider": {"adx_min": 22},
                            "smart_money": {"min_rr": 1.0}},
        "optimizer_results": {
            # trend_rider : schéma par symbole
            "trend_rider": {"4h": {
                "BTC/USDC": {"oos_score": 0.16, "params": {"chop_max": 55.0}},
                "ETH/USDC": {"oos_score": 0.32, "params": {"chop_max": 60.0}},
            }},
            # smart_money : entrée HÉRITÉE (sans symbole) = BTC/USDC
            "smart_money": {"4h": {"oos_score": 0.33,
                                   "params": {"chop_filter_max": 61.8}}},
        },
    }
    monkeypatch.setattr(state, "cfg", cfg)
    monkeypatch.setattr(state, "trader", None)
    return cfg


def test_overrides_listing_nested_and_legacy(fake_cfg):
    d = strategy_overrides("trend_rider")
    pairs = {(o["timeframe"], o["symbol"]) for o in d["overrides"]}
    assert ("4h", "BTC/USDC") in pairs and ("4h", "ETH/USDC") in pairs
    assert d["symbols"] == ["BTC/USDC", "ETH/USDC"]
    assert d["base_params"] == {"adx_min": 22}

    d2 = strategy_overrides("smart_money")
    assert len(d2["overrides"]) == 1
    o = d2["overrides"][0]
    # L'entrée héritée est présentée comme la config BTC/USDC
    assert o["symbol"] == "BTC/USDC" and o["legacy"] is True
    assert o["params"] == {"chop_filter_max": 61.8}


def test_update_requires_both_tf_and_symbol(fake_cfg):
    with pytest.raises(HTTPException) as e:
        update_strategy_params(None, "trend_rider", {"adx_min": 20}, timeframe="4h")
    assert e.value.status_code == 400


def test_override_write_goes_through_apply_best_params(fake_cfg, monkeypatch):
    captured = {}

    def _fake_apply(strategy, params, config_path, timeframe=None,
                    oos_score=0.0, symbol=None):
        captured.update(strategy=strategy, params=params, timeframe=timeframe,
                        oos_score=oos_score, symbol=symbol)
        return True

    import app.engine.opt_persistence as op
    monkeypatch.setattr(op, "apply_best_params", _fake_apply)

    res = update_strategy_params(None, "trend_rider", {"chop_max": 58.0},
                                 timeframe="4h", symbol="ETH/USDC")
    assert res["scope"] == "override" and res["symbol"] == "ETH/USDC"
    # Le symbole est transmis (BT-01 côté route config) et l'oos_score
    # existant de l'entrée ETH est préservé (édition ≠ nouvelle optimisation).
    assert captured["symbol"] == "ETH/USDC"
    assert captured["oos_score"] == pytest.approx(0.32)
    # La config en mémoire reflète l'override, sans toucher BTC ni la base.
    tf_entry = fake_cfg["optimizer_results"]["trend_rider"]["4h"]
    assert tf_entry["ETH/USDC"]["params"] == {"chop_max": 58.0}
    assert tf_entry["BTC/USDC"]["params"] == {"chop_max": 55.0}
    assert fake_cfg["strategy_params"]["trend_rider"] == {"adx_min": 22}


def test_base_write_unchanged_without_tf_symbol(fake_cfg, monkeypatch):
    saved = {}
    import app.api.routes.config_strategies as cfg_route
    monkeypatch.setattr(cfg_route, "_save_strategy_yaml",
                        lambda name, fn: saved.setdefault("name", name))
    res = update_strategy_params(None, "trend_rider", {"adx_min": 25})
    assert res["scope"] == "base"
    assert fake_cfg["strategy_params"]["trend_rider"] == {"adx_min": 25}
    assert saved["name"] == "trend_rider"
