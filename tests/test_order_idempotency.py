"""Idempotence des ordres : pas de doublon après timeout réseau sur create_order."""
import pytest

ccxt = pytest.importorskip("ccxt")

from app.core.exchange import RobustExchange


class _FlakyExchange:
    """Exchange factice : create_order timeout, mais l'ordre a bien été créé."""

    def __init__(self, order_received=True):
        self.create_calls = 0
        self.fetch_calls = []
        self.order_received = order_received

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_calls += 1
        self._last_client_id = params.get("newClientOrderId")
        if self.create_calls == 1:
            raise ccxt.RequestTimeout("timeout simulé")
        return {"id": "real-2", "clientOrderId": self._last_client_id,
                "status": "closed", "price": 100.0}

    def fetch_order(self, order_id, symbol, params=None):
        self.fetch_calls.append((order_id, (params or {}).get("origClientOrderId")))
        if self.order_received:
            return {"id": "real-1",
                    "clientOrderId": (params or {}).get("origClientOrderId"),
                    "status": "closed", "price": 100.0}
        raise ccxt.OrderNotFound("inconnu")


def _wrap(fake):
    ex = RobustExchange(fake, paper=False, margin=False)
    ex._reconnect = lambda: None
    return ex


def test_timeout_with_order_received_returns_existing(monkeypatch):
    """Timeout mais ordre reçu côté exchange → réutilisé, pas de 2e create."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    fake = _FlakyExchange(order_received=True)
    order = _wrap(fake).create_order("BTC/USDC", "market", "buy", 0.01)
    assert order["id"] == "real-1"
    assert fake.create_calls == 1          # pas de doublon
    # La vérification a bien utilisé le clientOrderId déterministe
    assert fake.fetch_calls[0][1] == fake._last_client_id


def test_timeout_without_order_retries_same_client_id(monkeypatch):
    """Timeout et ordre non reçu → retry avec le MÊME clientOrderId."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    fake = _FlakyExchange(order_received=False)
    cids = []
    orig = fake.create_order

    def capture(symbol, order_type, side, amount, price, params):
        cids.append(params.get("newClientOrderId"))
        return orig(symbol, order_type, side, amount, price, params)

    fake.create_order = capture
    order = _wrap(fake).create_order("BTC/USDC", "market", "buy", 0.01)
    assert order["id"] == "real-2"
    assert fake.create_calls == 2
    assert len(set(cids)) == 1             # clientOrderId stable entre tentatives


def test_paper_mode_unchanged():
    fake = _FlakyExchange()
    ex = RobustExchange(fake, paper=True)
    order = ex.create_order("BTC/USDC", "market", "buy", 0.01)
    assert order["id"].startswith("paper_")
    assert fake.create_calls == 0
