"""OCO natif OKX : SL+TP côté exchange en un seul ordre algo lié.

Vérifie la construction des paramètres dans _place_exchange_stop :
- OKX + take_profit → OCO (stopLossPrice + takeProfitPrice, jambes limit).
- pas de TP, ou exchange non-OKX → stop simple (stopPrice), comportement initial.
- direction de l'offset correcte (long vend sous le trigger, short achète au-dessus).
- dégradation gracieuse si la pose échoue.
"""
import types

import pytest

from app.live.position_mixin import PositionMixin


class _RecExchange:
    """Exchange factice qui enregistre les appels create_order."""

    def __init__(self, name="okx", fail=False):
        self._name = name
        self.fail = fail
        self.calls = []

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        self.calls.append({"symbol": symbol, "type": order_type, "side": side,
                           "amount": amount, "price": price, "params": params or {}})
        if self.fail:
            raise RuntimeError("rejet exchange simulé")
        return {"id": f"stop-{len(self.calls)}"}


class _Trader(PositionMixin):
    """Stub minimal exposant juste ce dont _place_exchange_stop a besoin."""

    def __init__(self, exchange):
        self.exchange = exchange
        self.cfg = {"trading": {"paper_mode": False, "exchange_stop_orders": True,
                                "exchange_stop_limit_offset": 0.005}}
        self.notif = types.SimpleNamespace(send=lambda *a, **k: None)


def _pos(side="long", stop=100.0, tp=None):
    return {"symbol": "BTC/USDC", "side": side, "stop": stop,
            "size": 0.5, "take_profit": tp}


def test_okx_with_tp_places_oco_long():
    ex = _RecExchange(name="okx")
    pos = _pos("long", stop=100.0, tp=120.0)
    _Trader(ex)._place_exchange_stop(pos)

    assert len(ex.calls) == 1
    p = ex.calls[0]["params"]
    assert ex.calls[0]["side"] == "sell"
    assert p["stopLossPrice"] == pytest.approx(100.0)
    assert p["takeProfitPrice"] == pytest.approx(120.0)
    # jambes limit : long vend légèrement SOUS le trigger (×0.995)
    assert p["slOrdPx"] == pytest.approx(99.5)
    assert p["tpOrdPx"] == pytest.approx(119.4)
    assert "stopPrice" not in p
    assert pos["_exchange_oco"] is True
    assert pos["stop_order_id"] == "stop-1"


def test_okx_with_tp_places_oco_short():
    ex = _RecExchange(name="okx")
    pos = _pos("short", stop=100.0, tp=80.0)
    _Trader(ex)._place_exchange_stop(pos)

    p = ex.calls[0]["params"]
    assert ex.calls[0]["side"] == "buy"
    # short achète légèrement AU-DESSUS du trigger (×1.005)
    assert p["slOrdPx"] == pytest.approx(100.5)
    assert p["tpOrdPx"] == pytest.approx(80.4)
    assert pos["_exchange_oco"] is True


def test_okx_without_tp_places_simple_stop():
    ex = _RecExchange(name="okx")
    pos = _pos("long", stop=100.0, tp=None)
    _Trader(ex)._place_exchange_stop(pos)

    p = ex.calls[0]["params"]
    assert p == {"stopPrice": 100.0}            # stop simple, pas d'OCO
    assert ex.calls[0]["price"] == pytest.approx(99.5)   # limit price en arg
    assert "_exchange_oco" not in pos


def test_non_okx_with_tp_does_not_use_oco():
    # Un exchange non-OKX ne passe pas par l'OCO standalone → stop simple.
    ex = _RecExchange(name="bybit")
    pos = _pos("long", stop=100.0, tp=120.0)
    _Trader(ex)._place_exchange_stop(pos)

    p = ex.calls[0]["params"]
    assert "stopPrice" in p and "takeProfitPrice" not in p
    assert "_exchange_oco" not in pos


def test_placement_failure_is_graceful():
    ex = _RecExchange(name="okx", fail=True)
    sent = []
    tr = _Trader(ex)
    tr.notif = types.SimpleNamespace(send=lambda *a, **k: sent.append(a))
    pos = _pos("long", stop=100.0, tp=120.0)
    tr._place_exchange_stop(pos)            # ne lève pas

    assert "stop_order_id" not in pos       # pas d'id → stop logiciel seul
    assert "_exchange_oco" not in pos
    assert len(sent) == 1                   # notification envoyée
