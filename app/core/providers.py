"""Protocoles MarketDataProvider / ExecutionProvider (S2-01).

Formalisent l'interface déjà implémentée par ``RobustExchange`` (ccxt) —
permettent de brancher un provider data-only (ex. yfinance pour les actions)
sans dépendre de ccxt. ``Protocol`` structurel (PEP 544) : aucune classe n'a
besoin d'hériter explicitement, seule la forme (les méthodes présentes)
compte — ``RobustExchange`` s'y conforme déjà de facto, sans modification.
"""
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class MarketDataProvider(Protocol):
    """Lecture de marché : bougies OHLCV, tickers, instruments disponibles."""

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100,
                     since: Optional[int] = None) -> list: ...

    def fetch_ticker(self, symbol: str) -> dict: ...

    def fetch_tickers(self, symbols: Optional[list] = None) -> dict: ...

    def load_markets(self) -> dict: ...


@runtime_checkable
class ExecutionProvider(Protocol):
    """Exécution d'ordres et consultation du solde."""

    def create_order(self, symbol: str, order_type: str, side: str,
                      amount: float, price: Optional[float] = None,
                      params: Optional[dict] = None) -> Any: ...

    def cancel_order(self, order_id: str, symbol: str) -> dict: ...

    def fetch_order(self, order_id: str, symbol: str) -> dict: ...

    def fetch_balance_detail(self) -> dict: ...
