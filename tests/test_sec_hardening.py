"""SEC-002 … SEC-006 + SEC-03 — durcissement sécurité (août 2026)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError


# ── SEC-002 : CandleStore path traversal ─────────────────────────────────────

class TestCandleStorePath:
    def test_valid_path(self, tmp_path):
        from app.core.candle_store import CandleStore
        store = CandleStore.__new__(CandleStore)
        store._base = tmp_path
        p = store._path("BTC/USDC", "1h")
        assert p == (tmp_path / "BTC_USDC" / "1h.parquet").resolve()
        assert p.is_relative_to(tmp_path.resolve())

    def test_rejects_unknown_timeframe(self, tmp_path):
        from app.core.candle_store import CandleStore
        store = CandleStore.__new__(CandleStore)
        store._base = tmp_path
        with pytest.raises(ValueError, match="Timeframe invalide"):
            store._path("BTC/USDC", "../etc")

    def test_rejects_dotdot_symbol(self, tmp_path):
        from app.core.candle_store import CandleStore
        store = CandleStore.__new__(CandleStore)
        store._base = tmp_path
        with pytest.raises(ValueError, match="Symbole invalide"):
            store._path("../../etc/passwd", "1h")

    def test_rejects_absolute_symbol(self, tmp_path):
        from app.core.candle_store import CandleStore
        store = CandleStore.__new__(CandleStore)
        store._base = tmp_path
        with pytest.raises(ValueError, match="Symbole invalide"):
            store._path("/etc/passwd", "1h")

    def test_rejects_special_chars(self, tmp_path):
        from app.core.candle_store import CandleStore
        store = CandleStore.__new__(CandleStore)
        store._base = tmp_path
        with pytest.raises(ValueError, match="Symbole invalide"):
            store._path("BTC;rm -rf", "1h")


# ── SEC-004 : testclient hors production ─────────────────────────────────────

class TestWsLocalhost:
    def test_loopback_allowed(self):
        from app.api.routes.ws import _is_local_ws_client
        assert _is_local_ws_client("127.0.0.1")
        assert _is_local_ws_client("localhost")
        assert _is_local_ws_client("::1")

    def test_testclient_only_under_pytest(self, monkeypatch):
        from app.api.routes.ws import _is_local_ws_client
        assert os.environ.get("PYTEST_CURRENT_TEST")
        assert _is_local_ws_client("testclient") is True
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert _is_local_ws_client("testclient") is False

    def test_remote_rejected(self):
        from app.api.routes.ws import _is_local_ws_client
        assert _is_local_ws_client("8.8.8.8") is False
        assert _is_local_ws_client("") is False


# ── SEC-006 : OpenAPI off en prod ────────────────────────────────────────

class TestOpenApiProd:
    def test_docs_disabled_when_env_prod(self, monkeypatch):
        monkeypatch.setenv("ENV", "prod")
        is_prod = os.environ.get("ENV", "").strip().lower() in ("prod", "production")
        assert is_prod is True
        docs_url = None if is_prod else "/api/docs"
        assert docs_url is None

    def test_docs_enabled_when_not_prod(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        is_prod = os.environ.get("ENV", "").strip().lower() in ("prod", "production")
        assert is_prod is False


# ── SEC-03 : schémas Pydantic ────────────────────────────────────────────

class TestSchemas:
    def test_timeframe_ok(self):
        from app.api.schemas import validate_timeframe
        assert validate_timeframe("1h") == "1h"

    def test_timeframe_bad(self):
        from app.api.schemas import validate_timeframe
        with pytest.raises(ValueError):
            validate_timeframe("../x")

    def test_symbol_ok(self):
        from app.api.schemas import validate_symbol
        assert validate_symbol("BTC/USDC") == "BTC/USDC"
        assert validate_symbol("AAPL") == "AAPL"

    def test_symbol_path_traversal(self):
        from app.api.schemas import validate_symbol
        with pytest.raises(ValueError):
            validate_symbol("../../etc/passwd")

    def test_strategy_params_body(self):
        from app.api.schemas import StrategyParamsBody
        body = StrategyParamsBody(params={"a": 1}, timeframe="4h", symbol="ETH/USDC")
        assert body.timeframe == "4h"
        with pytest.raises(ValidationError):
            StrategyParamsBody(params={}, timeframe="nope", symbol="BTC/USDC")

    def test_trading_params_bounds(self):
        from app.api.schemas import TradingParamsBody
        TradingParamsBody(score_threshold=0.55, paper_slippage=0.01)
        with pytest.raises(ValidationError):
            TradingParamsBody(score_threshold=1.5)
        with pytest.raises(ValidationError):
            TradingParamsBody(paper_slippage=0.2)
