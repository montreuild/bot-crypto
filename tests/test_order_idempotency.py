"""Idempotence des ordres : pas de doublon après timeout réseau sur create_order.

Paramétré par exchange : OKX utilise ``clOrdId``, les autres le champ unifié
ccxt ``clientOrderId`` — l'idempotence doit tenir quel que soit l'exchange.
"""
import pytest

ccxt = pytest.importorskip("ccxt")

from app.core.exchange import RobustExchange

# (id ccxt, champ clientOrderId à la création, champ à la recherche d'ordre)
_EXCHANGES = [
    ("okx",   "clOrdId",        "clOrdId"),
    ("bybit", "clientOrderId",  "clientOrderId"),   # fallback ccxt unifié
]


class _FlakyExchange:
    """Exchange factice : create_order timeout, mais l'ordre a bien été créé."""

    def __init__(self, ex_id, create_field, fetch_field, order_received=True):
        self.id = ex_id
        self._create_field = create_field
        self._fetch_field = fetch_field
        self.create_calls = 0
        self.fetch_calls = []
        self.order_received = order_received

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_calls += 1
        self._last_client_id = params.get(self._create_field)
        if self.create_calls == 1:
            raise ccxt.RequestTimeout("timeout simulé")
        return {"id": "real-2", "clientOrderId": self._last_client_id,
                "status": "closed", "price": 100.0}

    def fetch_order(self, order_id, symbol, params=None):
        self.fetch_calls.append((order_id, (params or {}).get(self._fetch_field)))
        if self.order_received:
            return {"id": "real-1",
                    "clientOrderId": (params or {}).get(self._fetch_field),
                    "status": "closed", "price": 100.0}
        raise ccxt.OrderNotFound("inconnu")


def _wrap(fake):
    ex = RobustExchange(fake, paper=False, margin=False)
    ex._reconnect = lambda: None
    return ex


@pytest.mark.parametrize("ex_id,create_field,fetch_field", _EXCHANGES)
def test_timeout_with_order_received_returns_existing(monkeypatch, ex_id,
                                                      create_field, fetch_field):
    """Timeout mais ordre reçu côté exchange → réutilisé, pas de 2e create."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    fake = _FlakyExchange(ex_id, create_field, fetch_field, order_received=True)
    order = _wrap(fake).create_order("BTC/USDC", "market", "buy", 0.01)
    assert order["id"] == "real-1"
    assert fake.create_calls == 1          # pas de doublon
    # La vérification a bien utilisé le clientOrderId déterministe
    assert fake.fetch_calls[0][1] == fake._last_client_id


@pytest.mark.parametrize("ex_id,create_field,fetch_field", _EXCHANGES)
def test_timeout_without_order_retries_same_client_id(monkeypatch, ex_id,
                                                      create_field, fetch_field):
    """Timeout et ordre non reçu → retry avec le MÊME clientOrderId."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    fake = _FlakyExchange(ex_id, create_field, fetch_field, order_received=False)
    cids = []
    orig = fake.create_order

    def capture(symbol, order_type, side, amount, price, params):
        cids.append(params.get(create_field))
        return orig(symbol, order_type, side, amount, price, params)

    fake.create_order = capture
    order = _wrap(fake).create_order("BTC/USDC", "market", "buy", 0.01)
    assert order["id"] == "real-2"
    assert fake.create_calls == 2
    assert len(set(cids)) == 1             # clientOrderId stable entre tentatives
    assert all(c is not None for c in cids)   # le champ attendu est bien renseigné


def test_paper_mode_unchanged():
    fake = _FlakyExchange("okx", "clOrdId", "clOrdId")
    ex = RobustExchange(fake, paper=True)
    order = ex.create_order("BTC/USDC", "market", "buy", 0.01)
    assert order["id"].startswith("paper_")
    assert fake.create_calls == 0
