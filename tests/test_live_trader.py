"""Tests unitaires de LiveTrader (TEST-02).

LiveTrader n'était jamais instancié dans tests/ (seulement exercé via cli.py
en e2e) : toute régression sur l'init, le chargement des stratégies actives
ou la restauration des positions passait inaperçue. Ces tests construisent
une instance réelle avec un exchange mocké (aucun appel réseau) et une base
sqlite temporaire, et exercent les chemins clés listés par l'audit :
instanciation, _build_active_per_tf, reload_active_strategies, status,
_restore_open_positions.
"""
import time

import pytest

from app.core.database import init_db, persist_open_position, session_scope
from app.live.live_trader import LiveTrader


class MockExchange:
    """Exchange minimal en mémoire — aucun appel réseau/ccxt réel."""

    def fetch_ticker(self, symbol):
        return {"last": 100.0, "bid": 99.9, "ask": 100.1}

    def fetch_tickers(self, symbols=None):
        return {}

    def fetch_balance(self):
        return {"free": {"USDC": 1000.0}, "total": {"USDC": 1000.0}}

    def fetch_positions(self):
        return []

    def fetch_balance_detail(self):
        return {"free": 1000.0, "used": 0.0, "total": 1000.0, "borrowed": 0.0}


def _make_cfg(db_path: str) -> dict:
    return {
        "trading": {
            "capital": 1000.0,
            "timeframes": ["1h"],
            "scan_interval": 60,
            "score_threshold": 0.5,
            "paper_mode": True,
        },
        "database": {"url": f"sqlite:///{db_path}"},
        "exchange": {"name": "okx", "margin": False},
        "strategies": {"enabled": ["trend_rider"]},
        "strategy_params": {},
        "optimizer_results": {},
        "scanner": {},
        "risk": {},
        "notifications": {},
        "optimizer": {"enabled": False},
        "forward_test": {"enabled": False},
        "lifecycle": {"enabled": False},
        "capital_allocator": {},
    }


@pytest.fixture
def trader(tmp_path):
    """LiveTrader avec exchange mocké + DB sqlite jetable."""
    return LiveTrader(_make_cfg(str(tmp_path / "live.db")), MockExchange())


# ── Instanciation ────────────────────────────────────────────────────────

class TestInstantiation:
    def test_builds_without_network_access(self, trader):
        assert trader.running is False
        assert trader.cycle_count == 0
        assert trader.capital_display == 1000.0
        assert trader.open_positions == {}

    def test_load_all_strategies_unions_param_spaces_and_enabled(self, trader):
        # _load_all_strategies() charge PARAM_SPACES ∪ strategies.enabled — pas
        # seulement les stratégies explicitement activées (cf. docstring de
        # AutoOptMixin._load_all_strategies).
        from app.engine.optimizer import PARAM_SPACES
        assert "trend_rider" in trader._loaded_strategies
        # -1 : 'breakout_filtreHor' est rejeté par le validateur de nom (camelCase).
        assert len(trader._loaded_strategies) >= len(PARAM_SPACES) - 1


# ── _build_active_per_tf ─────────────────────────────────────────────────

class TestBuildActivePerTf:
    def test_fallback_to_enabled_strategies_when_no_optimizer_results(self, trader):
        active = trader._active_per_tf
        assert "1h" in active
        names = [s["name"] for s in active["1h"]]
        assert names == ["trend_rider"]
        # Sans résultat d'optimisation, le score de fallback est 0.0 (convention
        # de get_active_strategies_per_tf).
        assert active["1h"][0]["score"] == 0.0

    def test_uses_optimizer_results_when_present(self, tmp_path):
        cfg = _make_cfg(str(tmp_path / "live.db"))
        cfg["optimizer_results"] = {
            "trend_rider": {"1h": {"oos_score": 0.42, "params": {}}},
        }
        t = LiveTrader(cfg, MockExchange())
        active = t._active_per_tf["1h"]
        assert any(s["name"] == "trend_rider" and s["score"] == 0.42 for s in active)


# ── S2-04 : filtre asset_classes ─────────────────────────────────────────

class TestAssetClassFilter:
    def test_crypto_only_strategy_kept_by_default(self, tmp_path):
        """Sans venue actions configurée, tout symbole reste 'crypto' — une
        stratégie crypto-only (funding_flow) n'est jamais filtrée."""
        cfg = _make_cfg(str(tmp_path / "live.db"))
        cfg["strategies"]["enabled"] = ["funding_flow"]
        t = LiveTrader(cfg, MockExchange())
        names = [s["name"] for s in t._active_per_tf["1h"]]
        assert names == ["funding_flow"]

    def test_crypto_only_strategy_excluded_on_equity_symbol(self, tmp_path):
        """funding_flow (asset_classes={'crypto'}) est exclue pour un
        symbole résolu en venue actions."""
        cfg = _make_cfg(str(tmp_path / "live.db"))
        cfg["strategies"]["enabled"] = ["funding_flow"]
        # Le fallback (pas d'optimizer_results) assigne DEFAULT_CONFIG_SYMBOL
        # (BTC/USDC) aux entrées actives — on le fait résoudre en venue actions.
        cfg["venues"] = {
            "defs": {"euronext": {"asset_class": "equity", "quote_currency": "EUR"}},
            "assign": {"BTC/USDC": "euronext"},
        }
        t = LiveTrader(cfg, MockExchange())
        names = [s["name"] for s in t._active_per_tf["1h"]]
        assert names == []

    def test_generic_strategy_kept_on_equity_symbol(self, tmp_path):
        """trend_rider (asset_classes par défaut = crypto ET equity) reste
        actif même sur un symbole résolu en venue actions."""
        cfg = _make_cfg(str(tmp_path / "live.db"))
        cfg["venues"] = {
            "defs": {"euronext": {"asset_class": "equity", "quote_currency": "EUR"}},
            "assign": {"BTC/USDC": "euronext"},
        }
        t = LiveTrader(cfg, MockExchange())
        names = [s["name"] for s in t._active_per_tf["1h"]]
        assert names == ["trend_rider"]


# ── reload_active_strategies / reload_strategies ────────────────────────

class TestReloadActiveStrategies:
    def test_reload_active_strategies_picks_up_new_optimizer_results(self, trader):
        trader.cfg["optimizer_results"] = {
            "trend_rider": {"1h": {"oos_score": 0.9, "params": {}}},
        }
        trader.reload_active_strategies()
        scores = {s["name"]: s["score"] for s in trader._active_per_tf["1h"]}
        assert scores.get("trend_rider") == 0.9

    def test_reload_strategies_narrows_loaded_set_to_enabled_list(self, trader):
        # Au départ, _load_all_strategies() a chargé tout PARAM_SPACES (~45).
        assert len(trader._loaded_strategies) > 1
        result = trader.reload_strategies(["trend_rider"])
        assert result["added"] == []
        assert len(result["removed"]) > 0
        assert trader._loaded_strategies.keys() == {"trend_rider"}
        # active_per_tf ne référence plus que la stratégie encore chargée.
        names = [s["name"] for s in trader._active_per_tf["1h"]]
        assert names == ["trend_rider"]


# ── status ────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_shape(self, trader):
        s = trader.status
        assert s["running"] is False
        assert s["cycle"] == 0
        assert s["capital"] == 1000.0
        assert s["positions"] == []
        assert s["active_per_tf"]["1h"] == ["trend_rider"]
        assert s["paper_mode"] is True
        assert "circuit_breaker_active" in s
        assert isinstance(s["bots"], list)

    def test_status_is_a_property_not_a_method(self, trader):
        # Garde-fou : status est exposé en @property côté API (routes le lisent
        # sans parenthèses) — une régression vers une méthode casserait l'API.
        assert not callable(type(trader).status)
        assert isinstance(trader.status, dict)


# ── _restore_open_positions ─────────────────────────────────────────────

class TestRestoreOpenPositions:
    def test_restores_persisted_position(self, tmp_path):
        db = tmp_path / "live.db"
        _, SessionLocal = init_db(f"sqlite:///{db}")
        with session_scope(SessionLocal) as sess:
            persist_open_position(sess, {
                "id": "pos1", "symbol": "BTC/USDC", "side": "long",
                "entry": 100.0, "stop": 95.0, "size": 0.5,
                "strategy": "trend_rider", "open_time": time.time(),
            })
        t = LiveTrader(_make_cfg(str(db)), MockExchange())
        assert t.open_positions == {}
        t._restore_open_positions()
        assert "pos1" in t.open_positions
        assert t.open_positions["pos1"]["symbol"] == "BTC/USDC"
        assert t.open_positions["pos1"]["side"] == "long"

    def test_drops_position_with_invalid_entry_price(self, tmp_path):
        db = tmp_path / "live.db"
        _, SessionLocal = init_db(f"sqlite:///{db}")
        with session_scope(SessionLocal) as sess:
            persist_open_position(sess, {
                "id": "badpos", "symbol": "ETH/USDC", "side": "short",
                "entry": 0.0, "stop": 10.0, "size": 1.0,
            })
        t = LiveTrader(_make_cfg(str(db)), MockExchange())
        t._restore_open_positions()
        assert "badpos" not in t.open_positions

    def test_noop_when_db_empty(self, trader):
        trader._restore_open_positions()
        assert trader.open_positions == {}


# ── stop() : chemin de robustesse (pas de crash sans positions) ─────────

def test_stop_without_open_positions_is_safe(trader):
    trader.stop(close_positions=False)
    assert trader.running is False
