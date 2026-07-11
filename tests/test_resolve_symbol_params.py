"""Résolution des paramètres par (timeframe, symbole).

``resolve_strategy_params(cfg, tf, symbol)`` : dimension symbole dans
``optimizer_results[strat][tf][symbol]`` avec rétro-compatibilité — une entrée
HÉRITÉE (sans dimension symbole) est réputée calibrée pour BTC/USDC et ne
s'applique PAS aux autres symboles.
"""
from app.live.utils import (
    resolve_strategy_params, _select_symbol_entry, DEFAULT_CONFIG_SYMBOL,
)


def _cfg(opt_results):
    return {
        "strategy_params": {"smart_money": {"min_rr": 1.0, "chop_filter_max": 0.0}},
        "optimizer_results": {"smart_money": {"4h": opt_results}},
    }


LEGACY = {"run_date": "2026-07-08", "oos_score": 0.33,
          "params": {"chop_filter_max": 61.8}}
NESTED = {
    "BTC/USDC": {"oos_score": 0.33, "params": {"chop_filter_max": 61.8}},
    "ETH/USDC": {"oos_score": 0.20, "params": {"min_rr": 2.0}},
}


# ── _select_symbol_entry ──────────────────────────────────────────────────────

def test_legacy_entry_is_btc_default():
    assert _select_symbol_entry(LEGACY, None) is LEGACY
    assert _select_symbol_entry(LEGACY, "BTC/USDC") is LEGACY
    # Une config héritée (BTC) ne déteint PAS sur un autre symbole
    assert _select_symbol_entry(LEGACY, "ETH/USDC") is None


def test_nested_entry_selects_symbol():
    assert _select_symbol_entry(NESTED, "ETH/USDC")["params"] == {"min_rr": 2.0}
    assert _select_symbol_entry(NESTED, "BTC/USDC")["params"] == {"chop_filter_max": 61.8}
    # symbole absent du mapping → aucune entrée (params de base)
    assert _select_symbol_entry(NESTED, "XRP/USDC") is None
    # symbol None → défaut BTC/USDC
    assert _select_symbol_entry(NESTED, None)["params"] == {"chop_filter_max": 61.8}


# ── resolve_strategy_params ───────────────────────────────────────────────────

def test_legacy_byte_identical_for_none_and_btc():
    cfg = _cfg(LEGACY)
    base = resolve_strategy_params(cfg, "4h")            # appelant historique
    btc  = resolve_strategy_params(cfg, "4h", "BTC/USDC")
    assert base["smart_money"]["chop_filter_max"] == 61.8
    assert btc == base


def test_legacy_not_applied_to_eth():
    cfg = _cfg(LEGACY)
    eth = resolve_strategy_params(cfg, "4h", "ETH/USDC")
    # config BTC héritée ignorée pour ETH → retombe sur la base YAML
    assert eth["smart_money"]["chop_filter_max"] == 0.0


def test_nested_per_symbol_coexist():
    cfg = _cfg(NESTED)
    btc = resolve_strategy_params(cfg, "4h", "BTC/USDC")
    eth = resolve_strategy_params(cfg, "4h", "ETH/USDC")
    assert btc["smart_money"]["chop_filter_max"] == 61.8
    assert btc["smart_money"]["min_rr"] == 1.0          # base (non surchargé)
    assert eth["smart_money"]["min_rr"] == 2.0          # config ETH dédiée
    assert eth["smart_money"]["chop_filter_max"] == 0.0  # base (non surchargé)
    # les deux coexistent : l'une n'écrase pas l'autre
    assert btc["smart_money"] != eth["smart_money"]


def test_default_symbol_constant():
    assert DEFAULT_CONFIG_SYMBOL == "BTC/USDC"


# ── ARCH-01 : _merge_params filtre les clés globales comme le backtest ────────

def test_merge_params_filters_global_keys():
    """Une clé globale (score_threshold, risk_per_trade…) glissée dans un
    entry optimisé ne doit PAS écraser la config côté live — même sémantique
    que resolve_strategy_params (parité live/backtest)."""
    from app.live.utils import _merge_params
    base = {"trend_rider": {"adx_min": 22, "score_threshold": 0.55}}
    optimized = {"trend_rider": {"adx_min": 25, "score_threshold": 0.10,
                                 "risk_per_trade": 0.5, "capital": 1}}
    merged = _merge_params(base, optimized)
    assert merged["trend_rider"]["adx_min"] == 25            # param normal appliqué
    assert merged["trend_rider"]["score_threshold"] == 0.55  # clé globale protégée
    assert "risk_per_trade" not in merged["trend_rider"]
    assert "capital" not in merged["trend_rider"]
