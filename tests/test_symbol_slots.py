"""Phase 2 — slots par symbole : sélection active, identité de bot, allocation.

Vérifie que la dimension symbole (`strategy::tf::symbol`) traverse la sélection
des slots actifs, l'identité de bot et l'allocateur de capital.
"""
from app.core.bot_identity import BotIdentity, resolve_venue, Venue
from app.engine.opt_persistence import get_active_strategies_per_tf
from app.live.capital_allocator import CapitalAllocator


def _cfg():
    return {
        "trading": {"timeframes": ["4h"], "top_strategies_per_tf": 2, "capital": 1000},
        "scanner": {"symbols": ["BTC/USDC", "ETH/USDC"]},
        "strategies": {"enabled": ["smart_money", "trend_rider"]},
        "strategy_params": {"smart_money": {}, "trend_rider": {}},
        "optimizer_results": {
            # smart_money : entrée HÉRITÉE = BTC/USDC seulement
            "smart_money": {"4h": {"oos_score": 0.30, "params": {"min_rr": 1.2}}},
            # trend_rider : schéma par symbole (BTC + ETH coexistent)
            "trend_rider": {"4h": {
                "BTC/USDC": {"oos_score": 0.16, "params": {"chop_max": 55.0}},
                "ETH/USDC": {"oos_score": 0.32, "params": {"chop_max": 60.0}},
            }},
        },
    }


# ── Sélection active par (tf, symbole) ────────────────────────────────────────

def test_active_slots_per_symbol():
    act = get_active_strategies_per_tf(_cfg())
    slots = {(e["name"], e["symbol"]) for e in act["4h"]}
    # smart_money : BTC uniquement (config héritée ne déteint pas sur ETH)
    assert ("smart_money", "BTC/USDC") in slots
    assert ("smart_money", "ETH/USDC") not in slots
    # trend_rider : BTC ET ETH, chacun sa config
    assert ("trend_rider", "BTC/USDC") in slots
    assert ("trend_rider", "ETH/USDC") in slots


def test_active_slot_carries_symbol_params():
    act = get_active_strategies_per_tf(_cfg())
    eth = next(e for e in act["4h"]
               if e["name"] == "trend_rider" and e["symbol"] == "ETH/USDC")
    btc = next(e for e in act["4h"]
               if e["name"] == "trend_rider" and e["symbol"] == "BTC/USDC")
    assert eth["params"]["trend_rider"]["chop_max"] == 60.0
    assert btc["params"]["trend_rider"]["chop_max"] == 55.0


# ── Identité de bot ───────────────────────────────────────────────────────────

def test_bot_identity_slot_key_includes_symbol():
    v = Venue(name="spot", market_type="spot", exchange="okx")
    idn = BotIdentity("trend_rider", "4h", "abcd1234", 1, v, symbol="ETH/USDC")
    assert idn.slot_key == "trend_rider::4h::ETH/USDC"
    assert "ETH/USDC" in idn.bot_id
    assert idn.to_dict()["symbol"] == "ETH/USDC"


def test_bot_identity_no_symbol_is_legacy_key():
    v = Venue(name="spot", market_type="spot", exchange="okx")
    idn = BotIdentity("trend_rider", "4h", "abcd1234", 1, v)
    assert idn.slot_key == "trend_rider::4h"


def test_resolve_venue_symbol_precedence():
    cfg = {"venues": {"defs": {"perp": {"market_type": "perp", "max_leverage": 5}},
                      "assign": {"trend_rider::4h::ETH/USDC": "perp"}}}
    v = resolve_venue(cfg, "trend_rider", "4h", "ETH/USDC")
    assert v.name == "perp" and v.max_leverage == 5.0
    # Sans le symbole, l'assignation spécifique ne matche pas
    v2 = resolve_venue(cfg, "trend_rider", "4h", "BTC/USDC")
    assert v2.name != "perp"


# ── Allocateur de capital ─────────────────────────────────────────────────────

def test_allocator_per_symbol_slot_keys():
    act = get_active_strategies_per_tf(_cfg())
    alloc = CapitalAllocator(capital=1000.0, active_per_tf=act, cfg=_cfg())
    keys = set(alloc._slots.keys())
    assert "trend_rider::4h::ETH/USDC" in keys
    assert "trend_rider::4h::BTC/USDC" in keys
    assert "smart_money::4h::BTC/USDC" in keys
    assert "smart_money::4h::ETH/USDC" not in keys
    for k, slot in alloc._slots.items():
        assert slot.symbol in k


# ── parse_slot_key (UI / routes) ──────────────────────────────────────────────

def test_parse_slot_key():
    from app.core.bot_identity import parse_slot_key
    assert parse_slot_key("trend_rider::4h::ETH/USDC") == ("trend_rider", "4h", "ETH/USDC")
    assert parse_slot_key("smart_money::1h") == ("smart_money", "1h", "")
    assert parse_slot_key("") == ("", "", "")


# ── BT-01 : apply_best_params par symbole (coexistence, pas d'écrasement) ─────

def test_apply_best_params_per_symbol_coexist(tmp_path):
    """Deux applies successifs (BTC puis ETH) sur le même strat/tf doivent
    coexister dans optimizer_results[tf] — la route /api/optimize/apply doit
    transmettre le symbole du job pour emprunter ce chemin (cf. BT-01)."""
    import yaml
    from app.engine.opt_persistence import apply_best_params

    sdir = tmp_path / "strategies"; sdir.mkdir()
    spath = sdir / "trend_rider.yaml"
    yaml.safe_dump({"params": {"adx_min": 22}}, spath.open("w"))
    cfgpath = tmp_path / "config.yaml"; cfgpath.write_text("trading: {}\n")

    assert apply_best_params("trend_rider", {"chop_max": 55.0}, str(cfgpath),
                             timeframe="4h", oos_score=0.16, symbol="BTC/USDC")
    assert apply_best_params("trend_rider", {"chop_max": 60.0}, str(cfgpath),
                             timeframe="4h", oos_score=0.32, symbol="ETH/USDC")

    data = yaml.safe_load(spath.open())
    tf_entry = data["optimizer_results"]["4h"]
    assert tf_entry["BTC/USDC"]["params"] == {"chop_max": 55.0}
    assert tf_entry["ETH/USDC"]["params"] == {"chop_max": 60.0}
    assert data["params"] == {"adx_min": 22}          # base intacte


def test_apply_best_params_migrates_legacy_entry(tmp_path):
    """Une entrée héritée (sans symbole) est migrée vers BTC/USDC lors de la
    première écriture per-symbole — rien n'est perdu."""
    import yaml
    from app.engine.opt_persistence import apply_best_params

    sdir = tmp_path / "strategies"; sdir.mkdir()
    spath = sdir / "trend_rider.yaml"
    yaml.safe_dump({
        "params": {},
        "optimizer_results": {"4h": {"run_date": "2026-07-08", "oos_score": 0.16,
                                     "params": {"chop_max": 55.0}}},
    }, spath.open("w"))
    cfgpath = tmp_path / "config.yaml"; cfgpath.write_text("trading: {}\n")

    assert apply_best_params("trend_rider", {"chop_max": 60.0}, str(cfgpath),
                             timeframe="4h", oos_score=0.32, symbol="ETH/USDC")
    tf_entry = yaml.safe_load(spath.open())["optimizer_results"]["4h"]
    assert tf_entry["BTC/USDC"]["params"] == {"chop_max": 55.0}   # migrée
    assert tf_entry["ETH/USDC"]["params"] == {"chop_max": 60.0}


def test_optimizer_apply_route_passes_symbol():
    """Garde-fou de non-régression BT-01 : la route lit bien job['symbol'] et
    le transmet à apply_best_params (vérification statique du source)."""
    import inspect
    from app.api.routes import optimizer as opt_route
    src = inspect.getsource(opt_route.optimizer_apply)
    assert 'job.get("symbol")' in src
    assert "symbol=symbol" in src
