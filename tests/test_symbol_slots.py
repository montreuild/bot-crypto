"""Phase 2 — slots par symbole : sélection active, identité de bot, allocation.

Vérifie que la dimension symbole (`strategy::tf::symbol`) traverse la sélection
des slots actifs, l'identité de bot et l'allocateur de capital.
"""
from app.core.bot_identity import BotIdentity, Venue, default_venue_from_cfg, resolve_venue
from app.engine.opt_persistence import get_active_strategies_per_tf


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


# ── Enveloppes par symbole ────────────────────────────────────────────────────

def test_envelopes_are_keyed_per_symbol():
    """S12 : les enveloppes portent la meme cle 3-parties que les slots — un
    meme bot sur deux symboles a deux enveloppes distinctes, jamais une seule
    partagee."""
    from app.core.risk_envelope import envelopes_for_active_slots
    cfg = _cfg()
    cfg["venues"] = {"default": "v", "defs": {"v": {"max_leverage": 1}}, "assign": {}}
    cfg["risk"] = {"profile": "p", "profiles": {"p": 0.02},
                   "envelopes": {"v": {"capital": 1000.0, "max_symbol_exposure_pct": 1.0,
                                      "symbol_risk_pct": 0.05, "venue_risk_pct": 0.05}}}
    envs = envelopes_for_active_slots(cfg, get_active_strategies_per_tf(cfg))
    keys = set(envs)
    assert "trend_rider::4h::ETH/USDC" in keys
    assert "trend_rider::4h::BTC/USDC" in keys
    assert "smart_money::4h::BTC/USDC" in keys
    assert "smart_money::4h::ETH/USDC" not in keys
    for k, env in envs.items():
        assert env.symbol in k


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

    sdir = tmp_path / "strategies"
    sdir.mkdir()
    spath = sdir / "trend_rider.yaml"
    yaml.safe_dump({"params": {"adx_min": 22}}, spath.open("w"))
    cfgpath = tmp_path / "config.yaml"
    cfgpath.write_text("trading: {}\n")

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

    sdir = tmp_path / "strategies"
    sdir.mkdir()
    spath = sdir / "trend_rider.yaml"
    yaml.safe_dump({
        "params": {},
        "optimizer_results": {"4h": {"run_date": "2026-07-08", "oos_score": 0.16,
                                     "params": {"chop_max": 55.0}}},
    }, spath.open("w"))
    cfgpath = tmp_path / "config.yaml"
    cfgpath.write_text("trading: {}\n")

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


# ── V4-C : helpers canoniques + validation des clés 3-parties ────────────────

def test_build_helpers():
    from app.core.bot_identity import build_pos_key, build_slot_key
    assert build_slot_key("trend_rider", "4h", "ETH/USDC") == "trend_rider::4h::ETH/USDC"
    assert build_slot_key("trend", "1h") == "trend::1h"
    assert build_pos_key("ETH/USDC", "trend_rider", "4h") == "ETH/USDC::trend_rider::4h"


def test_slot_key_route_regex_accepts_symbol():
    """La validation des routes slots accepte les clés 3-parties (l'ancienne
    regex 2-parties cassait budget/toggle/reset depuis l'écran Bots)."""
    from app.api.routes.trades import _SLOT_KEY_RE
    assert _SLOT_KEY_RE.match("trend_rider::4h::ETH/USDC")
    assert _SLOT_KEY_RE.match("smart_money::1h")
    assert not _SLOT_KEY_RE.match("../etc/passwd")
    assert not _SLOT_KEY_RE.match("bad key::4h")


# ── S2-02 : généralisation multi-actifs (Venue étendue) ─────────────────────

def test_venue_defaults_preserve_crypto_behavior():
    """Sans venues.defs configuré (config.yaml historique), les nouveaux
    champs restent alignés sur le comportement crypto (rétro-compat totale)."""
    v = default_venue_from_cfg({"trading": {}, "exchange": {"name": "okx"}})
    assert v.asset_class == "crypto"
    assert v.quote_currency == "USDC"
    assert v.fractional is True
    assert v.allow_short is True


def test_resolve_venue_symbol_alone_assigns_asset_class():
    """Un symbole peut être assigné à une venue actions indépendamment de
    la stratégie qui le trade (ex. AIR.PA → euronext-paper, quelle que soit
    la stratégie)."""
    cfg = {"venues": {"defs": {
        "euronext-paper": {
            "asset_class": "equity", "quote_currency": "EUR",
            "fractional": False, "allow_short": False, "lot_size": 1.0,
        },
    }, "assign": {"AIR.PA": "euronext-paper"}}}
    v = resolve_venue(cfg, "pullback_trend", "1d", "AIR.PA")
    assert v.name == "euronext-paper"
    assert v.asset_class == "equity"
    assert v.quote_currency == "EUR"
    assert v.fractional is False
    assert v.allow_short is False
    assert v.lot_size == 1.0
    # Un autre symbole, même stratégie/tf, reste sur le défaut crypto.
    v2 = resolve_venue(cfg, "pullback_trend", "1d", "BTC/USDC")
    assert v2.asset_class == "crypto"
    assert v2.quote_currency == "USDC"


def test_resolve_venue_strategy_tf_symbol_still_beats_symbol_alone():
    """La clé composée strategy::tf::symbol reste prioritaire sur symbol seul."""
    cfg = {"venues": {"defs": {
        "specific": {"asset_class": "equity", "max_leverage": 1},
        "generic": {"asset_class": "crypto", "max_leverage": 3},
    }, "assign": {
        "AIR.PA": "generic",
        "trend_rider::1d::AIR.PA": "specific",
    }}}
    v = resolve_venue(cfg, "trend_rider", "1d", "AIR.PA")
    assert v.name == "specific"
    assert v.asset_class == "equity"
