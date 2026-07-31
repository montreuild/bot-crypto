"""G2 — routage multi-provider par venue (app/core/provider_router.py).

Deux propriétés à garantir :
  * une configuration purement crypto n'emprunte AUCUN code de routage ;
  * un symbole assigné à une venue actions atteint bien son provider, et une
    venue sans exécution branchée ne transmet jamais d'ordre.
"""
import pytest

from app.core.provider_router import (
    ProviderRouter,
    build_market_provider,
    declared_providers,
    market_calendar_for,
    register_provider,
)


class FakeExchange:
    """Double d'exchange crypto (surface RobustExchange utilisée ici)."""
    rateLimit = 100
    paper = True

    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        self.calls.append(("ohlcv", symbol))
        return [[0, 1, 2, 0.5, 1.5, 10]]

    def fetch_ticker(self, symbol):
        self.calls.append(("ticker", symbol))
        return {"last": 100.0}

    def fetch_tickers(self, symbols=None):
        self.calls.append(("tickers", tuple(symbols or ())))
        return {s: {"last": 100.0} for s in (symbols or [])}

    def load_markets(self):
        return {"BTC/USDC": {}}

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        self.calls.append(("order", symbol, side, amount))
        return {"id": "real-1", "status": "closed", "filled": amount}

    def parse_timeframe(self, tf):
        return 3600

    def milliseconds(self):
        return 1_700_000_000_000


class FakeEquityProvider:
    """Provider actions data-only injecté via register_provider."""
    instances = []

    def __init__(self, cfg=None):
        self.cfg = cfg
        self.calls = []
        FakeEquityProvider.instances.append(self)

    def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        self.calls.append(("ohlcv", symbol))
        return [[0, 10, 11, 9, 10.5, 1000]]

    def fetch_ticker(self, symbol):
        self.calls.append(("ticker", symbol))
        return {"last": 42.0}

    def fetch_tickers(self, symbols=None):
        self.calls.append(("tickers", tuple(symbols or ())))
        return {s: {"last": 42.0} for s in (symbols or [])}


register_provider("fake_equity", f"{__name__}:FakeEquityProvider")


CRYPTO_CFG = {
    "exchange": {"name": "okx"},
    "trading": {},
    "venues": {"defs": {"spot": {"market_type": "spot"}}, "assign": {}},
}

EQUITY_CFG = {
    "exchange": {"name": "okx"},
    "trading": {},
    "venues": {
        "defs": {
            "spot": {"market_type": "spot"},
            "euronext-paper": {
                "asset_class": "equity", "quote_currency": "EUR",
                "data_provider": "fake_equity", "can_execute": False,
                "calendar": "XPAR", "fractional": False, "allow_short": False,
            },
        },
        "assign": {"AIR.PA": "euronext-paper"},
    },
}


@pytest.fixture(autouse=True)
def _reset_instances():
    FakeEquityProvider.instances.clear()
    yield


# ── Pas de routage quand rien ne le demande ────────────────────────────────

class TestNoOpForCrypto:
    def test_declared_providers_is_empty(self):
        assert declared_providers(CRYPTO_CFG) == set()

    def test_build_returns_the_exchange_untouched(self):
        exchange = FakeExchange()
        assert build_market_provider(CRYPTO_CFG, exchange) is exchange

    def test_missing_venues_section_is_safe(self):
        exchange = FakeExchange()
        assert build_market_provider({}, exchange) is exchange


# ── Routage ────────────────────────────────────────────────────────────────

class TestRouting:
    def test_build_wraps_when_a_venue_declares_a_provider(self):
        router = build_market_provider(EQUITY_CFG, FakeExchange())
        assert isinstance(router, ProviderRouter)

    def test_equity_symbol_reaches_its_provider(self):
        exchange = FakeExchange()
        router = build_market_provider(EQUITY_CFG, exchange)
        router.fetch_ohlcv("AIR.PA", "1h", limit=10)
        assert FakeEquityProvider.instances[0].calls == [("ohlcv", "AIR.PA")]
        assert exchange.calls == []

    def test_crypto_symbol_still_reaches_the_exchange(self):
        exchange = FakeExchange()
        router = build_market_provider(EQUITY_CFG, exchange)
        router.fetch_ticker("BTC/USDC")
        assert exchange.calls == [("ticker", "BTC/USDC")]

    def test_provider_is_instantiated_once(self):
        router = build_market_provider(EQUITY_CFG, FakeExchange())
        for _ in range(3):
            router.fetch_ticker("AIR.PA")
        assert len(FakeEquityProvider.instances) == 1

    def test_fetch_tickers_groups_by_provider(self):
        exchange = FakeExchange()
        router = build_market_provider(EQUITY_CFG, exchange)
        out = router.fetch_tickers(["BTC/USDC", "AIR.PA"])
        assert set(out) == {"BTC/USDC", "AIR.PA"}
        assert out["AIR.PA"]["last"] == 42.0
        assert out["BTC/USDC"]["last"] == 100.0

    def test_unknown_provider_falls_back_instead_of_crashing(self, caplog):
        cfg = {**EQUITY_CFG, "venues": {
            "defs": {"broken": {"data_provider": "n_existe_pas"}},
            "assign": {"AIR.PA": "broken"},
        }}
        exchange = FakeExchange()
        router = build_market_provider(cfg, exchange)
        with caplog.at_level("ERROR"):
            router.fetch_ticker("AIR.PA")
        assert exchange.calls == [("ticker", "AIR.PA")]

    def test_unknown_attributes_delegate_to_the_exchange(self):
        """parse_timeframe, milliseconds, rateLimit… ne dépendent pas du symbole."""
        router = build_market_provider(EQUITY_CFG, FakeExchange())
        assert router.parse_timeframe("1h") == 3600
        assert router.rateLimit == 100
        assert router.paper is True


# ── Exécution ──────────────────────────────────────────────────────────────

class TestExecutionGating:
    def test_data_only_venue_never_transmits_an_order(self, caplog):
        exchange = FakeExchange()
        router = build_market_provider(EQUITY_CFG, exchange)
        with caplog.at_level("WARNING"):
            order = router.create_order("AIR.PA", "market", "buy", 3)
        assert order["simulated"] is True
        assert order["venue"] == "euronext-paper"
        assert order["filled"] == 3
        assert exchange.calls == [], "aucun ordre ne doit atteindre l'exchange"
        assert FakeEquityProvider.instances == [], \
            "ni le provider de données, qui ne sait pas exécuter"

    def test_crypto_order_goes_through_untouched(self):
        exchange = FakeExchange()
        router = build_market_provider(EQUITY_CFG, exchange)
        order = router.create_order("BTC/USDC", "market", "buy", 0.5)
        assert order["id"] == "real-1"
        assert "simulated" not in order
        assert exchange.calls == [("order", "BTC/USDC", "buy", 0.5)]

    def test_cancel_and_fetch_are_simulated_too(self):
        router = build_market_provider(EQUITY_CFG, FakeExchange())
        assert router.cancel_order("x", "AIR.PA")["simulated"] is True
        assert router.fetch_order("x", "AIR.PA")["simulated"] is True


# ── Calendrier par symbole ─────────────────────────────────────────────────

class TestCalendarResolution:
    def test_equity_symbol_gets_its_venue_calendar(self):
        assert market_calendar_for(EQUITY_CFG, "AIR.PA").name == "XPAR"

    def test_crypto_symbol_is_always_open(self):
        cal = market_calendar_for(EQUITY_CFG, "BTC/USDC")
        from datetime import datetime, timezone
        assert cal.is_open(datetime(2026, 1, 1, 3, tzinfo=timezone.utc))
