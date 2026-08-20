"""S0-10 : Tests E2E pour les risques critiques identifiés par l'audit.

Couvre les 3 risques critiques qui ont motivé le Sprint 0 :
1. Sizing live = distance au stop (pas ATR brut).
2. Bypass auth via X-Forwarded-For bloqué.
3. Rate-limit effectif sur routes sensibles.

Ces tests sont les "garde-fous" qui empêchent une régression silencieuse
des fixes du Sprint 0.
"""
from unittest.mock import MagicMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# S0-01 : Sizing live = risk_amount / stop_dist (parité backtest)
# ─────────────────────────────────────────────────────────────────────────────

class TestSizingParity:
    """Le sizing live divise par la distance au stop, jamais par l'ATR brut.

    Bug d'origine : ``compute_size`` divisait par l'ATR alors que le stop était
    à mult×ATR — risque réel = risk% × mult (2,5× pour trail_wide=2,5). S12
    supprime la branche fautive au lieu de la corriger : sans distance au stop
    exploitable, il n'y a plus de repli, le trade est refusé.
    """

    @staticmethod
    def _rm_and_env():
        from app.core.risk_envelope import Envelope
        from app.core.risk_gate import RiskGate
        rm = RiskGate({
            "trading": {"max_trades_per_minute": 3,
                        "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20},
            "venues": {"default": "v", "defs": {"v": {"max_leverage": 1}}, "assign": {}},
            "risk": {"profile": "t", "profiles": {"t": 0.01},
                     "envelopes": {"v": {"capital": 1000, "max_symbol_exposure_pct": 1.0,
                                        "symbol_risk_pct": 0.02, "venue_risk_pct": 0.05}}},
        })
        # Enveloppe volontairement large : le plafond notionnel ne doit pas
        # mordre, sinon le test ne distingue plus le sizing correct du bug.
        env = Envelope(
            venue="v", symbol="BTC/USDC", slot_key="s::1h::BTC/USDC", currency="USDC",
            venue_envelope=1000.0, venue_risk_budget=50.0,
            symbol_envelope=1000.0, symbol_risk_budget=20.0,
            slot_envelope=1000.0, slot_risk_amount=10.0,
            max_leverage=1.0, min_notional=0.0, trade_risk_pct=0.01, weight=1.0,
        )
        return rm, env

    def test_size_is_exactly_the_risk_over_the_stop_distance(self):
        """risk_amount = 10, stop_dist = 5 → size = 2, sans facteur parasite."""
        rm, env = self._rm_and_env()
        size, _ = rm.compute_size(entry=100, stop_dist=5, env=env)
        assert size == pytest.approx(2.0)

    def test_no_stop_distance_is_refused_not_silently_over_risked(self):
        """L'ancien repli ATR brut donnait ici size=5 (2,5× trop gros) sans
        rien signaler. Il n'existe plus."""
        rm, env = self._rm_and_env()
        with pytest.raises(ValueError, match="stop_dist"):
            rm.compute_size(entry=100, stop_dist=0, env=env)

    def test_a_wider_stop_gives_a_proportionally_smaller_size(self):
        """Le risque engagé est invariant : seule la taille s'ajuste."""
        rm, env = self._rm_and_env()
        size_tight, _ = rm.compute_size(entry=100, stop_dist=2, env=env)
        size_wide, _ = rm.compute_size(entry=100, stop_dist=5, env=env)
        assert size_tight / size_wide == pytest.approx(2.5)
        assert size_tight * 2 == pytest.approx(size_wide * 5)


# ─────────────────────────────────────────────────────────────────────────────
# S0-02 : Bypass auth via X-Forwarded-For bloqué
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthXBypassBlocked:
    """X-Forwarded-For ne doit pas être honoré sauf si le peer IP est dans
    TRUSTED_PROXIES.

    Bug critique (avant fix) : `_extract_client_ip` faisait confiance au
    premier X-Forwarded-For sans validation → un attaquant distant pouvait
    déclarer `X-Forwarded-For: 127.0.0.1` et contourner l'auth localhost-only.
    """

    def test_xff_ignored_when_peer_not_in_trusted_proxies(self, monkeypatch):
        """Sans TRUSTED_PROXIES, X-Forwarded-For doit être ignoré."""
        from app.api.helpers import _extract_client_ip
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        request = MagicMock()
        request.client.host = "203.0.113.42"  # IP externe
        request.headers.get = lambda key, default="": {
            "x-forwarded-for": "127.0.0.1",  # spoofing attempt
        }.get(key, default)
        ip = _extract_client_ip(request)
        assert ip == "203.0.113.42", (
            f"X-Forwarded-For spoofable depuis IP externe : ip={ip} "
            f"(devrait être '203.0.113.42')"
        )

    def test_xff_honored_when_peer_in_trusted_proxies(self, monkeypatch):
        """Si TRUSTED_PROXIES contient le peer IP, X-Forwarded-For est honoré."""
        from app.api.helpers import _extract_client_ip
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1")
        request = MagicMock()
        request.client.host = "10.0.0.1"  # proxy de confiance
        request.headers.get = lambda key, default="": {
            "x-forwarded-for": "198.51.100.7",  # vrai client derrière proxy
        }.get(key, default)
        ip = _extract_client_ip(request)
        assert ip == "198.51.100.7", (
            f"X-Forwarded-For non honoré derrière proxy de confiance : ip={ip}"
        )

    def test_xff_first_value_taken_when_multiple(self, monkeypatch):
        """Si X-Forwarded-For a plusieurs valeurs, on prend la première."""
        from app.api.helpers import _extract_client_ip
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1")
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers.get = lambda key, default="": {
            "x-forwarded-for": "198.51.100.7, 10.0.0.2, 10.0.0.3",
        }.get(key, default)
        ip = _extract_client_ip(request)
        assert ip == "198.51.100.7"


# ─────────────────────────────────────────────────────────────────────────────
# S0-03 : Rate-limit effectif sur routes sensibles
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimitWired:
    """SlowAPIMiddleware doit être enregistré + décorateurs @limiter.limit
    présents sur les routes sensibles.

    Bug (avant fix) : `Limiter` configuré mais middleware jamais ajouté →
    rate-limit inerte.
    """

    def test_slowapi_middleware_registered(self):
        """SlowAPIMiddleware doit être enregistré sur l'app FastAPI."""
        from slowapi.middleware import SlowAPIMiddleware

        from app.api.main import app
        # Vérifie que le middleware est dans la pile
        middleware_types = [m.cls for m in app.user_middleware]
        assert SlowAPIMiddleware in middleware_types, (
            "SlowAPIMiddleware n'est pas enregistré — rate-limit inerte"
        )

    def test_limiter_attached_to_bot_start(self):
        """La route /api/bot/start doit avoir un @limiter.limit."""
        from app.api.routes.bot import bot_start
        # Les décorateurs SlowAPI posent un attribut sur la fonction
        assert hasattr(bot_start, "__wrapped__") or "limit" in str(bot_start.__dict__), (
            "/api/bot/start n'a pas de @limiter.limit"
        )

    def test_limiter_attached_to_bot_stop(self):
        from app.api.routes.bot import bot_stop
        assert hasattr(bot_stop, "__wrapped__") or "limit" in str(bot_stop.__dict__), (
            "/api/bot/stop n'a pas de @limiter.limit"
        )

    def test_limiter_attached_to_config_strategies(self):
        """La route /api/config/strategies (POST) doit avoir un @limiter.limit."""
        from app.api.routes import config_strategies
        routes_with_limit = []
        for route in config_strategies.router.routes:
            if hasattr(route.endpoint, "__wrapped__"):
                routes_with_limit.append(route.path)
        # Au moins une route POST doit être limitée
        post_routes = [r.path for r in config_strategies.router.routes
                       if r.methods and "POST" in r.methods]
        assert any(p in routes_with_limit for p in post_routes), (
            "Aucune route POST de config_strategies n'a de @limiter.limit"
        )


# ─────────────────────────────────────────────────────────────────────────────
# S0-04 : Démarrage live refusé si host=0.0.0.0 + pas de api_key
# ─────────────────────────────────────────────────────────────────────────────

class TestInsecureDefaultRejected:
    """Démarrage refusé si `web.host=0.0.0.0` sans `web.api_key`,
    sauf override explicite `ALLOW_INSECURE_WEB=1` (SEC-003 : YAML ignoré).
    """

    def test_load_config_raises_on_insecure_default(self, monkeypatch, tmp_path):
        """config.yaml avec host=0.0.0.0 sans api_key → ValueError."""
        from app.core.config import load_config
        cfg_file = tmp_path / "test_config.yaml"
        cfg_file.write_text(
            "exchange:\n"
            "  name: okx\n"
            "  api_key: ''\n"
            "  api_secret: ''\n"
            "  api_password: ''\n"
            "  margin: false\n"
            "trading:\n"
            "  capital: 1000\n"
            "  paper_mode: true\n"
            "  timeframe: 1h\n"
            "  scan_interval: 60\n"
            "  score_threshold: 0.55\n"
            "  risk_per_trade: 0.01\n"
            "  max_positions: 5\n"
            "  max_longs: 3\n"
            "  max_shorts: 3\n"
            "  max_leverage: 1\n"
            "  max_trades_per_minute: 3\n"
            "  daily_drawdown_limit: 0.05\n"
            "  max_drawdown_global: 0.20\n"
            "strategies:\n"
            "  enabled: [trend]\n"
            "web:\n"
            "  host: 0.0.0.0\n"
            "  port: 8000\n"
            "  api_key: ''\n"
            "  allow_insecure: false\n"
            "database:\n"
            # Quoté : non quoté, le `:` final rend le scalaire ambigu pour YAML
            # (« mapping values are not allowed here ») — la fixture n'était pas
            # parsable, donc ces deux tests n'avaient jamais pu s'exécuter.
            '  url: "sqlite:///:memory:"\n',
            encoding="utf-8",
        )
        monkeypatch.delenv("ALLOW_INSECURE_WEB", raising=False)
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        with pytest.raises(ValueError, match="API de trading serait exposée"):
            load_config(str(cfg_file))

    def test_load_config_allows_insecure_with_env_override(self, monkeypatch, tmp_path):
        """SEC-003 : seul ALLOW_INSECURE_WEB=1 autorise l'hôte ouvert sans clé."""
        from app.core.config import load_config
        cfg_file = tmp_path / "test_config_insecure.yaml"
        cfg_file.write_text(
            "exchange:\n"
            "  name: okx\n"
            "  api_key: ''\n"
            "  api_secret: ''\n"
            "  api_password: ''\n"
            "  margin: false\n"
            "trading:\n"
            "  capital: 1000\n"
            "  paper_mode: true\n"
            "  timeframe: 1h\n"
            "  scan_interval: 60\n"
            "  score_threshold: 0.55\n"
            "  risk_per_trade: 0.01\n"
            "  max_positions: 5\n"
            "  max_longs: 3\n"
            "  max_shorts: 3\n"
            "  max_leverage: 1\n"
            "  max_trades_per_minute: 3\n"
            "  daily_drawdown_limit: 0.05\n"
            "  max_drawdown_global: 0.20\n"
            "strategies:\n"
            "  enabled: [trend]\n"
            "web:\n"
            "  host: 0.0.0.0\n"
            "  port: 8000\n"
            "  api_key: ''\n"
            "database:\n"
            # Quoté : non quoté, le `:` final rend le scalaire ambigu pour YAML
            # (« mapping values are not allowed here ») — la fixture n'était pas
            # parsable, donc ces deux tests n'avaient jamais pu s'exécuter.
            '  url: "sqlite:///:memory:"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("ALLOW_INSECURE_WEB", "1")
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        cfg = load_config(str(cfg_file))
        assert cfg["web"]["host"] == "0.0.0.0"
        assert not cfg["web"].get("api_key")


# ─────────────────────────────────────────────────────────────────────────────
# S0-05 : Élagage de la bougie en cours côté live
# ─────────────────────────────────────────────────────────────────────────────

class TestFormingCandleDropped:
    """Le scoring live ne doit pas voir la bougie en cours de formation
    (close non définitif → repaint)."""

    def test_drop_forming_candle_removes_last_incomplete_bar(self):
        """Si la dernière bougie est encore en formation, elle est retirée."""
        from datetime import datetime, timedelta, timezone

        import polars as pl

        from app.live.ohlcv_cache import OHLCVCache
        # Construit un DF avec 10 bougies 1h, la dernière en formation
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 9 bougies clôturées (il y a 10h à 2h) + 1 bougie en cours (il y a 30min)
        times = [now - timedelta(hours=i) for i in range(10, 0, -1)]
        # La dernière bougie est "now - 30min", donc en formation (close dans 30min)
        times[-1] = now - timedelta(minutes=30)
        df = pl.DataFrame({
            "time": times,
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.5] * 10,
            "volume": [1000.0] * 10,
        })
        cache = OHLCVCache.__new__(OHLCVCache)  # bypass __init__
        df_dropped = cache._drop_forming_candle(df, "1h")
        # La dernière bougie (en formation) doit être retirée
        assert df_dropped.height == 9, (
            f"Bougie en formation non retirée : height={df_dropped.height} "
            f"(devrait être 9)"
        )

    def test_drop_forming_candle_noop_when_last_closed(self):
        """Si la dernière bougie est clôturée, aucune action."""
        from datetime import datetime, timedelta, timezone

        import polars as pl

        from app.live.ohlcv_cache import OHLCVCache
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 10 bougies toutes clôturées (la dernière il y a 1h+1s)
        times = [now - timedelta(hours=i) for i in range(11, 1, -1)]
        df = pl.DataFrame({
            "time": times,
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.5] * 10,
            "volume": [1000.0] * 10,
        })
        cache = OHLCVCache.__new__(OHLCVCache)
        df_dropped = cache._drop_forming_candle(df, "1h")
        assert df_dropped.height == 10, (
            f"Bougie clôturée incorrectement retirée : height={df_dropped.height}"
        )
