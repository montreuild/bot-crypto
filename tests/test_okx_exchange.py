"""Migration OKX : câblage passphrase + quirks margin spécifiques OKX.

Vérifie que la couche RobustExchange traduit correctement le modèle de marge
Binance (sideEffectType/isIsolated, marginLevel, origClientOrderId) vers OKX
(tdMode, mgnRatio, clOrdId) — sans régression côté Binance.
"""
import pytest

ccxt = pytest.importorskip("ccxt")

from app.core.exchange import RobustExchange, create_exchange


# ── Câblage des credentials (passphrase OKX) ─────────────────────────────────

def test_create_exchange_okx_wires_passphrase():
    cfg = {
        "exchange": {"name": "okx", "api_key": "k", "api_secret": "s",
                     "api_password": "pass123", "margin": True},
        "trading": {"paper_mode": False, "margin_mode": "isolated"},
    }
    rex = create_exchange(cfg)
    assert rex._name == "okx"
    assert rex._ex.password == "pass123"
    assert rex._ex.apiKey == "k"


def test_create_exchange_okx_missing_passphrase_warns(caplog):
    cfg = {
        "exchange": {"name": "okx", "api_key": "k", "api_secret": "s"},
        "trading": {"paper_mode": False},
    }
    create_exchange(cfg)   # ne lève pas, mais journalise un warning
    assert any("passphrase" in r.message.lower() for r in caplog.records)


# ── Paramètres de marge par exchange ─────────────────────────────────────────

def test_margin_params_okx_uses_tdmode():
    ex = RobustExchange(ccxt.okx(), paper=False, margin=True, margin_mode="isolated")
    assert ex._margin_params() == {"tdMode": "isolated"}
    ex2 = RobustExchange(ccxt.okx(), paper=False, margin=True, margin_mode="cross")
    assert ex2._margin_params() == {"tdMode": "cross"}
    assert ex._client_id_field() == "clOrdId"


def test_margin_params_binance_unchanged():
    ex = RobustExchange(ccxt.binance(), paper=False, margin=True, margin_mode="isolated")
    assert ex._margin_params() == {
        "sideEffectType": "AUTO_BORROW_REPAY", "isIsolated": "TRUE"}
    assert ex._client_id_field() == "newClientOrderId"


def test_margin_params_spot_is_empty():
    ex = RobustExchange(ccxt.okx(), paper=False, margin=False)
    assert ex._margin_params() == {}


# ── Introspection margin OKX (mgnRatio + liab) ───────────────────────────────

class _FakeOkx:
    id = "okx"

    def __init__(self, mgn_ratio="3.5", liab="12.0", adj_eq=None, mmr=None):
        self._mgn = mgn_ratio
        self._liab = liab
        self._adj_eq = adj_eq
        self._mmr = mmr

    def fetch_balance(self, params=None):
        acct = {
            "mgnRatio": self._mgn,
            "details": [
                {"ccy": "USDC", "liab": self._liab, "availBal": "100"},
                {"ccy": "BTC", "liab": "0", "availBal": "0.1"},
            ],
        }
        if self._adj_eq is not None:
            acct["adjEq"] = self._adj_eq
        if self._mmr is not None:
            acct["mmr"] = self._mmr
        return {
            "USDC": {"free": 100.0, "used": 5.0, "total": 105.0},
            "info": {"data": [acct]},
        }


def test_fetch_margin_account_okx_computes_adjeq_over_mmr():
    # Chemin principal : marginLevel = adjEq / mmr (ratio décimal non ambigu).
    ex = RobustExchange(_FakeOkx(adj_eq="3000", mmr="1500"), paper=False,
                        margin=True, margin_mode="isolated")
    assert ex.fetch_margin_account()["marginLevel"] == pytest.approx(2.0)


def test_fetch_margin_account_okx_no_mmr_is_safe():
    # Pas de mmr (aucune position margin) ET mgnRatio vide → sûr (999).
    ex = RobustExchange(_FakeOkx(mgn_ratio="", adj_eq="500", mmr="0"),
                        paper=False, margin=True, margin_mode="isolated")
    assert ex.fetch_margin_account()["marginLevel"] == 999.0


def test_fetch_margin_account_okx_falls_back_to_mgn_ratio():
    # mmr indisponible mais mgnRatio présent → fallback sur le ratio brut.
    ex = RobustExchange(_FakeOkx(mgn_ratio="2.7"), paper=False,
                        margin=True, margin_mode="isolated")
    assert ex.fetch_margin_account()["marginLevel"] == pytest.approx(2.7)


def test_fetch_margin_account_okx_graceful_on_empty_ratio():
    ex = RobustExchange(_FakeOkx(mgn_ratio=""), paper=False,
                        margin=True, margin_mode="isolated")
    assert ex.fetch_margin_account()["marginLevel"] == 999.0


def test_fetch_balance_detail_okx_reads_liability():
    ex = RobustExchange(_FakeOkx(liab="42.0"), paper=False,
                        margin=True, margin_mode="isolated")
    detail = ex.fetch_balance_detail()
    assert detail["free"] == pytest.approx(100.0)
    assert detail["borrowed"] == pytest.approx(42.0)


# ── cancel_order : pas de param Binance sur OKX ──────────────────────────────

class _RecordingCancel:
    id = "okx"

    def __init__(self):
        self.last_params = None

    def cancel_order(self, order_id, symbol, params=None):
        self.last_params = params
        return {"id": order_id, "status": "canceled"}


def test_cancel_order_okx_no_isolated_param():
    fake = _RecordingCancel()
    ex = RobustExchange(fake, paper=False, margin=True, margin_mode="isolated")
    ex.cancel_order("oid-1", "BTC/USDC")
    assert "isIsolated" not in (fake.last_params or {})
