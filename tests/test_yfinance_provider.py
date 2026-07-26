"""G2 — provider actions Yahoo (app/core/yfinance_provider.py).

L'essentiel des tests porte sur les **limitations de l'API** : ce sont elles
qui, non traitées, produisent des réponses vides inexpliquées, des trous
d'historique ou des HTTP 429 en rafale. Aucun test ne touche le réseau : le
backend HTTP est remplacé par un double.
"""
import time

import pytest

from app.core.providers import ExecutionProvider, MarketDataProvider
from app.core.yfinance_provider import (
    ExecutionNotSupported,
    YFinanceProvider,
    _aggregate,
)

HOUR_MS = 3_600_000


def _provider(**overrides):
    cfg = {"providers": {"yfinance": {"suffix": ".PA", "min_request_interval": 0.0,
                                      "cache_ttl": 0.0, "max_retries": 1,
                                      "prefer_yfinance": False, **overrides}}}
    return YFinanceProvider(cfg)


def _bars(n: int, start_ms: int = 1_700_000_000_000, step_ms: int = HOUR_MS):
    return [[start_ms + i * step_ms, 10.0 + i, 11.0 + i, 9.0 + i, 10.5 + i, 100.0]
            for i in range(n)]


# ── Contrat d'interface ────────────────────────────────────────────────────

class TestContract:
    def test_is_a_market_data_provider(self):
        assert isinstance(_provider(), MarketDataProvider)

    def test_execution_methods_exist_only_to_refuse_loudly(self):
        """Choix assumé : les méthodes d'exécution SONT définies (le provider
        satisfait donc structurellement ``ExecutionProvider``) mais lèvent
        ``ExecutionNotSupported``. Un ``AttributeError`` opaque en pleine
        ouverture de position serait un moins bon diagnostic qu'un message
        nommant la venue mal configurée."""
        p = _provider()
        assert isinstance(p, ExecutionProvider)
        with pytest.raises(ExecutionNotSupported, match="can_execute"):
            p.create_order("AIR.PA", "market", "buy", 1)

    def test_exposes_the_ccxt_surface_candlestore_needs(self):
        p = _provider()
        assert p.parse_timeframe("1h") == 3600
        assert p.parse_timeframe("15m") == 900
        assert p.milliseconds() > 1_600_000_000_000
        assert p.rateLimit >= 0

    def test_unknown_timeframe_raises_like_ccxt(self):
        with pytest.raises(ValueError):
            _provider().parse_timeframe("7s")

    def test_orders_are_refused_explicitly(self):
        p = _provider()
        for call in (lambda: p.create_order("AIR.PA", "market", "buy", 1),
                     lambda: p.cancel_order("1", "AIR.PA"),
                     lambda: p.fetch_order("1", "AIR.PA")):
            with pytest.raises(ExecutionNotSupported):
                call()

    def test_signals_candlestore_specifics(self):
        """Deux hypothèses crypto que le store doit relâcher pour les actions."""
        p = _provider()
        assert p.drop_zero_volume is False   # séance sans échange = barre légitime
        assert p.min_since_ms == 0           # une action cote avant 2017


# ── Symboles ───────────────────────────────────────────────────────────────

class TestSymbolMapping:
    def test_suffix_added_to_bare_ticker(self):
        assert _provider().to_provider_symbol("AIR") == "AIR.PA"

    def test_pair_form_uses_the_base(self):
        assert _provider().to_provider_symbol("AIR/EUR") == "AIR.PA"

    def test_already_suffixed_left_intact(self):
        assert _provider().to_provider_symbol("AIR.PA") == "AIR.PA"

    def test_explicit_map_wins(self):
        p = _provider(symbol_map={"AIR/EUR": "AIR.XX"})
        assert p.to_provider_symbol("AIR/EUR") == "AIR.XX"

    def test_no_suffix_configured_is_passthrough(self):
        p = _provider(suffix="")
        assert p.to_provider_symbol("AAPL") == "AAPL"


# ── Limitations de profondeur ──────────────────────────────────────────────

class TestDepthLimits:
    def _capture(self, provider):
        seen = {}

        def fake(ticker, interval, period1, period2):
            seen.update(ticker=ticker, interval=interval,
                        period1=period1, period2=period2)
            return _bars(5)
        provider._fetch_via_chart_api = fake
        return seen

    def test_one_minute_window_capped_at_seven_days(self, caplog):
        p = _provider()
        seen = self._capture(p)
        p.fetch_ohlcv("AIR.PA", "1m", limit=100_000)
        span_days = (seen["period2"] - seen["period1"]) / 86_400
        assert span_days <= 7.1, "Yahoo refuse au-delà de 7 jours en 1 minute"

    def test_hourly_window_capped_at_730_days(self):
        p = _provider()
        seen = self._capture(p)
        p.fetch_ohlcv("AIR.PA", "1h", limit=100_000)
        span_days = (seen["period2"] - seen["period1"]) / 86_400
        assert 700 <= span_days <= 731

    def test_daily_window_is_not_capped(self):
        p = _provider()
        seen = self._capture(p)
        p.fetch_ohlcv("AIR.PA", "1d", limit=5_000)
        span_days = (seen["period2"] - seen["period1"]) / 86_400
        assert span_days > 4_000, "le journalier n'a pas de plafond de profondeur"

    def test_truncation_is_warned_once_per_symbol_and_tf(self, caplog):
        p = _provider()
        self._capture(p)
        with caplog.at_level("WARNING"):
            p.fetch_ohlcv("AIR.PA", "1m", limit=100_000)
            p.fetch_ohlcv("AIR.PA", "1m", limit=100_000)
        warnings = [r for r in caplog.records if "plafonne" in r.message]
        assert len(warnings) == 1, "un avertissement, pas un par cycle"

    def test_unsupported_timeframe_returns_empty(self, caplog):
        p = _provider()
        with caplog.at_level("WARNING"):
            assert p.fetch_ohlcv("AIR.PA", "7s", limit=10) == []


# ── Intervalles absents chez Yahoo ─────────────────────────────────────────

class TestAggregation:
    def test_four_hour_is_built_from_hourly(self):
        p = _provider()
        captured = {}

        def fake(ticker, interval, period1, period2):
            captured["interval"] = interval
            return _bars(8, start_ms=0, step_ms=HOUR_MS)
        p._fetch_via_chart_api = fake

        rows = p.fetch_ohlcv("AIR.PA", "4h", limit=10)
        assert captured["interval"] == "1h", "Yahoo ne cote pas le 4 h"
        assert len(rows) == 2                       # 8 bougies 1 h → 2 bougies 4 h

    def test_aggregate_keeps_ohlcv_semantics(self):
        rows = [[0, 10, 12, 9, 11, 100],
                [HOUR_MS, 11, 15, 8, 14, 50],
                [2 * HOUR_MS, 14, 14, 13, 13, 20]]
        out = _aggregate(rows, 4 * HOUR_MS)
        assert len(out) == 1
        ts, o, h, lo, c, v = out[0]
        assert (o, h, lo, c, v) == (10, 15, 8, 13, 170)

    def test_aggregate_anchors_on_epoch_for_stable_buckets(self):
        """Deux fenêtres décalées doivent produire les MÊMES bornes, sinon le
        cache Parquet incrémental accumulerait des doublons décalés."""
        full = _aggregate(_bars(8, start_ms=0), 4 * HOUR_MS)
        tail = _aggregate(_bars(8, start_ms=0)[3:], 4 * HOUR_MS)
        assert full[-1][0] == tail[-1][0]
        assert all(ts % (4 * HOUR_MS) == 0 for ts, *_ in full)


# ── Robustesse & throttling ────────────────────────────────────────────────

class TestRobustness:
    def test_network_failure_degrades_to_empty_list(self, caplog):
        p = _provider(max_retries=2)

        def boom(*_a):
            raise RuntimeError("HTTP 429 — quota Yahoo atteint")
        p._fetch_via_chart_api = boom
        with caplog.at_level("WARNING"):
            assert p.fetch_ohlcv("AIR.PA", "1h", limit=10) == []

    def test_retries_then_succeeds(self):
        p = _provider(max_retries=3)
        calls = {"n": 0}

        def flaky(*_a):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("429")
            return _bars(3)
        p._fetch_via_chart_api = flaky
        assert len(p.fetch_ohlcv("AIR.PA", "1h", limit=10)) == 3
        assert calls["n"] == 2

    def test_cache_collapses_repeated_calls(self):
        p = _provider(cache_ttl=60.0)
        calls = {"n": 0}

        def counted(*_a):
            calls["n"] += 1
            return _bars(5)
        p._fetch_via_chart_api = counted
        p.fetch_ohlcv("AIR.PA", "1h", limit=5)
        p.fetch_ohlcv("AIR.PA", "1h", limit=5)
        assert calls["n"] == 1, "un cycle live rescanne le même symbole en boucle"

    def test_throttle_spaces_requests_process_wide(self):
        p = _provider(min_request_interval=0.05)
        p._fetch_via_chart_api = lambda *_a: _bars(2)
        started = time.time()
        p.fetch_ohlcv("AIR.PA", "1h", limit=2)
        p.fetch_ohlcv("MC.PA", "1h", limit=2)
        assert time.time() - started >= 0.05

    def test_limit_and_since_are_honoured(self):
        p = _provider()
        rows = _bars(20, start_ms=0)
        p._fetch_via_chart_api = lambda *_a: rows
        assert len(p.fetch_ohlcv("AIR.PA", "1h", limit=5)) == 5
        since = 10 * HOUR_MS
        assert all(r[0] >= since
                   for r in p.fetch_ohlcv("AIR.PA", "1h", limit=50, since=since))

    def test_ticker_falls_back_across_timeframes(self):
        p = _provider()
        p._fetch_via_chart_api = lambda ticker, interval, *_a: (
            _bars(2) if interval == "1d" else []
        )
        ticker = p.fetch_ticker("AIR.PA")
        assert ticker["last"] > 0
        assert ticker["quoteVolume"] == pytest.approx(ticker["baseVolume"]
                                                      * ticker["last"])

    def test_ticker_without_data_is_neutral_not_an_exception(self):
        p = _provider()
        p._fetch_via_chart_api = lambda *_a: []
        assert p.fetch_ticker("ZZZZ.PA")["last"] == 0.0

    def test_fetch_tickers_without_symbols_returns_empty(self):
        """Pas d'énumération de place : l'univers actions est statique."""
        assert _provider().fetch_tickers() == {}


# ── Parsing de la réponse chart ────────────────────────────────────────────

class TestChartApiParsing:
    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _session(self, payload):
        class _S:
            headers = {}

            def update(self, *_a):
                pass

            def get(_self, *_a, **_kw):
                return TestChartApiParsing._Resp(payload)
        s = _S()
        s.headers = type("H", (), {"update": lambda *_a: None})()
        return s

    def test_holes_in_the_quote_arrays_are_skipped(self):
        """Suspension de cotation : Yahoo renvoie des None, pas moins de points."""
        payload = {"chart": {"result": [{
            "timestamp": [1_700_000_000, 1_700_003_600, 1_700_007_200],
            "indicators": {"quote": [{
                "open":  [10.0, None, 12.0],
                "high":  [11.0, None, 13.0],
                "low":   [9.0,  None, 11.0],
                "close": [10.5, None, 12.5],
                "volume": [100, None, 200],
            }]},
        }]}}
        p = _provider()
        p._session = self._session(payload)
        rows = p._fetch_via_chart_api("AIR.PA", "1h", 0, 9_999_999_999)
        assert len(rows) == 2
        assert [r[0] for r in rows] == [1_700_000_000_000, 1_700_007_200_000]

    def test_yahoo_error_payload_raises_for_retry(self):
        p = _provider()
        p._session = self._session({"chart": {"error": {"code": "Not Found"}}})
        with pytest.raises(RuntimeError):
            p._fetch_via_chart_api("ZZZZ.PA", "1h", 0, 9_999_999_999)
