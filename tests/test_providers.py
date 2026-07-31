"""S2-01 — Protocoles MarketDataProvider/ExecutionProvider.

RobustExchange (ccxt) doit s'y conformer SANS modification : les protocoles
formalisent une interface déjà implémentée, préparant le terrain pour un
provider data-only (actions) sans dépendance ccxt.
"""
import ccxt

from app.core.exchange import RobustExchange
from app.core.providers import ExecutionProvider, MarketDataProvider


def _make_exchange():
    return RobustExchange(ccxt.okx({"enableRateLimit": True}), paper=True)


def test_robust_exchange_satisfies_market_data_provider():
    assert isinstance(_make_exchange(), MarketDataProvider)


def test_robust_exchange_satisfies_execution_provider():
    assert isinstance(_make_exchange(), ExecutionProvider)


def test_object_missing_methods_does_not_satisfy_protocols():
    class Empty:
        pass

    assert not isinstance(Empty(), MarketDataProvider)
    assert not isinstance(Empty(), ExecutionProvider)


def test_data_only_provider_satisfies_market_data_but_not_execution():
    """Un provider actions data-only (ex. yfinance, phase G2) n'a pas besoin
    d'implémenter ExecutionProvider — le paper trading reste simulé
    localement (cf. exchange.py RobustExchange.create_order paper mode)."""
    class DataOnlyProvider:
        def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return []

        def fetch_ticker(self, symbol):
            return {}

        def fetch_tickers(self, symbols=None):
            return {}

        def load_markets(self):
            return {}

    provider = DataOnlyProvider()
    assert isinstance(provider, MarketDataProvider)
    assert not isinstance(provider, ExecutionProvider)
