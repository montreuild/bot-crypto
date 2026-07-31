"""Tests de BalanceSyncMixin (TEST-07) : sync paper/spot/margin + pré-exécution.

Aucun unittest isolé n'existait pour la réconciliation d'équité — seulement
les tests e2e/phase*. Construit une vraie instance LiveTrader (MockExchange
en mémoire, DB sqlite jetable) et exerce directement _sync_paper_balance,
_sync_spot_balance, _sync_margin_account et _pre_execution_check.
"""
import pytest

from app.live.live_trader import LiveTrader


class MockExchange:
    """Exchange minimal en mémoire — soldes/margin contrôlables par le test."""

    def __init__(self):
        self.ticker_price = 100.0
        self.balance_free = 1000.0
        self.balance_borrowed = 0.0
        self.margin_level = 5.0

    def fetch_ticker(self, symbol):
        return {"last": self.ticker_price}

    def fetch_positions(self):
        return []

    def fetch_balance(self):
        return {"free": {"USDC": self.balance_free}, "total": {"USDC": self.balance_free}}

    def fetch_balance_detail(self):
        return {
            "free": self.balance_free, "used": 0.0,
            "total": self.balance_free, "borrowed": self.balance_borrowed,
        }

    def fetch_margin_account(self):
        return {"marginLevel": self.margin_level}


def _make_cfg(db_path: str, paper: bool = True) -> dict:
    return {
        "trading": {
            "capital": 1000.0, "timeframes": ["1h"], "scan_interval": 60,
            "score_threshold": 0.5, "paper_mode": paper,
            "margin_level_critical": 1.5, "margin_level_alert": 3.0,
        },
        "database": {"url": f"sqlite:///{db_path}"},
        "exchange": {"name": "okx", "margin": True},
        "strategies": {"enabled": ["trend_rider"]},
        "strategy_params": {}, "optimizer_results": {}, "scanner": {}, "risk": {},
        "notifications": {}, "optimizer": {"enabled": False},
        "forward_test": {"enabled": False}, "lifecycle": {"enabled": False},
        "capital_allocator": {},
    }


@pytest.fixture
def trader_exchange(tmp_path):
    exchange = MockExchange()
    trader = LiveTrader(_make_cfg(str(tmp_path / "live.db")), exchange)
    return trader, exchange


# ── Paper ─────────────────────────────────────────────────────────────────

class TestSyncPaperBalance:
    def test_no_open_positions_keeps_base_capital(self, trader_exchange):
        trader, _ = trader_exchange
        trader._sync_paper_balance()
        assert trader.capital_display == trader._paper_base

    def test_unrealized_pnl_on_long_position_is_added(self, trader_exchange):
        trader, exchange = trader_exchange
        base = trader._paper_base
        trader.open_positions["p1"] = {
            "symbol": "BTC/USDC", "side": "long", "entry": 100.0, "size": 2.0,
        }
        exchange.ticker_price = 110.0  # +10/unit non réalisé
        trader._sync_paper_balance()
        assert trader.capital_display == round(base + 20.0, 4)

    def test_reserved_position_is_ignored(self, trader_exchange):
        trader, exchange = trader_exchange
        base = trader._paper_base
        trader.open_positions["p1"] = {"_reserved": True}
        exchange.ticker_price = 500.0  # ne doit pas être pris en compte
        trader._sync_paper_balance()
        assert trader.capital_display == base


# ── Spot réel ────────────────────────────────────────────────────────────

class TestSyncSpotBalance:
    def test_capital_from_free_minus_borrowed_no_positions(self, tmp_path):
        exchange = MockExchange()
        exchange.balance_free = 980.0
        exchange.balance_borrowed = 20.0
        trader = LiveTrader(_make_cfg(str(tmp_path / "live.db"), paper=False), exchange)
        trader._sync_spot_balance()
        assert trader.capital_display == 960.0

    def test_open_long_position_market_value_included(self, tmp_path):
        exchange = MockExchange()
        exchange.balance_free = 800.0
        exchange.balance_borrowed = 0.0
        exchange.ticker_price = 110.0
        trader = LiveTrader(_make_cfg(str(tmp_path / "live.db"), paper=False), exchange)
        trader.open_positions["p1"] = {
            "symbol": "BTC/USDC", "side": "long", "entry": 100.0, "size": 2.0,
        }
        trader._sync_spot_balance()
        # 800 (cash) + 2*110 (valeur de marché) - 0 (emprunt)
        assert trader.capital_display == 1020.0

    def test_equity_propagated_to_allocator(self, tmp_path):
        """S0-01 : le sizing par slot doit voir la même équité que le risk manager."""
        exchange = MockExchange()
        exchange.balance_free = 950.0
        exchange.balance_borrowed = 0.0
        trader = LiveTrader(_make_cfg(str(tmp_path / "live.db"), paper=False), exchange)
        trader._sync_spot_balance()
        assert trader.allocator.capital == trader.capital_display == 950.0


# ── Margin ────────────────────────────────────────────────────────────────

class TestSyncMarginAccount:
    def test_updates_margin_level(self, tmp_path):
        exchange = MockExchange()
        exchange.margin_level = 4.2
        trader = LiveTrader(_make_cfg(str(tmp_path / "live.db"), paper=False), exchange)
        trader._sync_margin_account()
        assert trader._margin_level == 4.2
        assert trader.risk.halted is False

    def test_critical_margin_level_halts_trading(self, tmp_path):
        exchange = MockExchange()
        exchange.margin_level = 1.2  # < margin_level_critical (1.5)
        trader = LiveTrader(_make_cfg(str(tmp_path / "live.db"), paper=False), exchange)
        assert trader.risk.halted is False
        trader._sync_margin_account()
        assert trader.risk.halted is True
        assert "margin" in trader.risk.halt_reason.lower()

    def test_equity_propagated_to_allocator(self, tmp_path):
        """S0-01 : même correctif que le mode spot, appliqué au mode margin."""
        exchange = MockExchange()
        exchange.balance_free = 900.0
        exchange.balance_borrowed = 0.0
        exchange.margin_level = 5.0
        trader = LiveTrader(_make_cfg(str(tmp_path / "live.db"), paper=False), exchange)
        trader._sync_margin_account()
        assert trader.allocator.capital == trader.capital_display == 900.0


# ── Pré-exécution ────────────────────────────────────────────────────────

class TestPreExecutionCheck:
    def test_paper_mode_sufficient_capital_passes(self, trader_exchange):
        trader, _ = trader_exchange
        assert trader._pre_execution_check("BTC/USDC", "long", 1.0, 100.0, 100.0) is True

    def test_paper_mode_insufficient_capital_blocks(self, trader_exchange):
        trader, _ = trader_exchange
        notional = trader._paper_base * 2  # dépasse largement le capital simulé
        assert trader._pre_execution_check("BTC/USDC", "long", 10.0, 100.0, notional) is False

    def test_paper_mode_accounts_for_already_locked_notional(self, trader_exchange):
        trader, _ = trader_exchange
        trader.open_positions["p1"] = {"notional": trader._paper_base * 0.9}
        # 20% du capital restant dispo (10%) ne suffit pas pour un nouveau 20%.
        assert trader._pre_execution_check(
            "ETH/USDC", "long", 1.0, 100.0, trader._paper_base * 0.2
        ) is False

    def test_live_spot_blocks_when_free_balance_insufficient(self, tmp_path):
        """S1-07 : capital_display seul (agrégé, resynchronisé 1×/cycle) ne
        suffit pas — le solde spot RÉEL (_balance_detail.free) doit couvrir
        le notionnel, sans quoi plusieurs ouvertures dans le même tour
        pourraient sur-engager un cash déjà consommé par un trade précédent."""
        cfg = _make_cfg(str(tmp_path / "live.db"), paper=False)
        cfg["exchange"]["margin"] = False  # spot pur : pas de levier
        exchange = MockExchange()
        trader = LiveTrader(cfg, exchange)
        trader.capital_display = 1000.0       # valeur agrégée encore "fraîche"
        trader._balance_detail = {"free": 50.0, "used": 950.0, "total": 1000.0, "borrowed": 0.0}
        # 25% de capital_display (250) passerait l'ancien garde-fou, mais le
        # cash libre réel (50) ne couvre pas ce notionnel.
        assert trader._pre_execution_check("BTC/USDC", "long", 1.0, 100.0, 200.0) is False

    def test_live_spot_passes_when_free_balance_sufficient(self, tmp_path):
        cfg = _make_cfg(str(tmp_path / "live.db"), paper=False)
        cfg["exchange"]["margin"] = False
        exchange = MockExchange()
        trader = LiveTrader(cfg, exchange)
        trader.capital_display = 1000.0
        trader._balance_detail = {"free": 500.0, "used": 500.0, "total": 1000.0, "borrowed": 0.0}
        assert trader._pre_execution_check("BTC/USDC", "long", 1.0, 100.0, 200.0) is True

    def test_live_margin_blocks_when_margin_level_critical(self, tmp_path):
        cfg = _make_cfg(str(tmp_path / "live.db"), paper=False)
        cfg["exchange"]["margin"] = True
        cfg["trading"]["margin_level_critical"] = 1.5
        exchange = MockExchange()
        trader = LiveTrader(cfg, exchange)
        trader.capital_display = 1000.0
        trader._margin_level = 1.2  # < margin_level_critical
        assert trader._pre_execution_check("BTC/USDC", "long", 1.0, 100.0, 200.0) is False

    def test_live_margin_passes_when_margin_level_safe(self, tmp_path):
        cfg = _make_cfg(str(tmp_path / "live.db"), paper=False)
        cfg["exchange"]["margin"] = True
        cfg["trading"]["margin_level_critical"] = 1.5
        exchange = MockExchange()
        trader = LiveTrader(cfg, exchange)
        trader.capital_display = 1000.0
        trader._margin_level = 4.0
        assert trader._pre_execution_check("BTC/USDC", "long", 1.0, 100.0, 200.0) is True
