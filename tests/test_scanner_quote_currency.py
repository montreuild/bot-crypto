"""S2-03 — MarketScanner : devise de cotation résolue via la venue, plus de
littéral "/USDC" en dur. Comportement crypto par défaut INCHANGÉ (aucune
venue configurée → USDC, comme avant)."""
from app.engine.scanner import MarketScanner


class MockExchange:
    def __init__(self, tickers):
        self._tickers = tickers

    def fetch_tickers(self, symbols=None):
        return self._tickers


def _cfg(trading_extra=None, venues=None):
    cfg = {
        "trading": {"min_volume_usdc_24h": 1_000_000, **(trading_extra or {})},
        "scanner": {"dynamic_scan": True},
    }
    if venues is not None:
        cfg["venues"] = venues
    return cfg


def test_dynamic_symbols_defaults_to_usdc_quote():
    tickers = {
        "BTC/USDC": {"quoteVolume": 5_000_000},
        "ETH/USDC": {"quoteVolume": 3_000_000},
        "BTC/EUR":  {"quoteVolume": 9_000_000},  # devise différente, ignoré
    }
    scanner = MarketScanner(MockExchange(tickers), _cfg())
    symbols = scanner.get_symbols()
    assert symbols == ["BTC/USDC", "ETH/USDC"]


def test_dynamic_symbols_follows_configured_default_venue_quote_currency():
    tickers = {
        "AIR/EUR": {"quoteVolume": 5_000_000},
        "BTC/USDC": {"quoteVolume": 9_000_000},  # devise différente, ignoré
    }
    venues = {
        "default": "euronext",
        "defs": {"euronext": {"asset_class": "equity", "quote_currency": "EUR"}},
    }
    scanner = MarketScanner(MockExchange(tickers), _cfg(venues=venues))
    symbols = scanner.get_symbols()
    assert symbols == ["AIR/EUR"]


def test_min_volume_quote_alias_takes_priority_over_legacy_key():
    scanner = MarketScanner(
        MockExchange({}),
        _cfg(trading_extra={"min_volume_usdc_24h": 1_000_000, "min_volume_quote_24h": 7_000_000}),
    )
    assert scanner.min_vol == 7_000_000


def test_min_volume_falls_back_to_legacy_key_when_alias_absent():
    scanner = MarketScanner(MockExchange({}), _cfg(trading_extra={"min_volume_usdc_24h": 2_000_000}))
    assert scanner.min_vol == 2_000_000
