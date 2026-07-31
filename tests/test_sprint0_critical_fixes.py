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
    """Le sizing live doit diviser par la distance au stop, pas par l'ATR brut.

    Bug critique (avant fix) : risk.compute_size divisait par ATR, mais le
    stop était à mult×ATR → risque réel = risk% × mult (2,5× pour trail_wide=2,5).
    Fix : passage du paramètre `stop_dist` (distance absolue entry→stop).
    """

    def test_sizing_uses_stop_dist_when_provided(self):
        """Si stop_dist est fourni, size = risk_amount / stop_dist."""
        from app.core.risk_gate import RiskGate
        cfg = {
            "trading": {
                "capital": 1000, "risk_per_trade": 0.01,
                "max_positions": 5, "max_longs": 3, "max_shorts": 3,
                "max_leverage": 1, "max_trades_per_minute": 3,
                "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20,
            },
            "risk": {},
            # ⚠ Indispensable : `max_notional_pct` vaut 0.20 par défaut, soit un
            # notionnel plafonné à 200 pour 1000 de capital — donc size ≤ 2 à
            # entry=100. Ce plafond mordait dans les DEUX branches (avec et sans
            # stop_dist), les ramenait toutes deux à 2.0 et rendait ces tests
            # vacants : ils ne pouvaient plus distinguer le sizing correct du
            # bug de sur-risque 2,5× qu'ils sont censés verrouiller.
            "backtest": {"max_notional_pct": 1.0},
        }
        rm = RiskGate(cfg)
        # entry=100, atr=2, stop_dist=5 (2,5×ATR)
        # risk_amount = 1000 × 0.01 = 10
        # size = 10 / 5 = 2 unités
        size, notional = rm.compute_size(
            entry=100, atr=2, score=1.0, threshold=0.6,
            size_factor=1.0, stop_dist=5,
        )
        # size devrait être ~2 (avec ajustements score_internal × vol_brake × sf)
        # Au minimum, size < 4 (= 10/2 = size ATR brut) — prouve qu'on a divisé
        # par stop_dist (plus grand que ATR) et non ATR brut.
        assert size < 4, f"Sizing ne divise pas par stop_dist : size={size} > 4"
        assert size > 1.5, f"Sizing trop petit : size={size}"

    def test_sizing_fallback_atr_when_no_stop_dist(self):
        """Sans stop_dist (rétro-compat), on retombe sur ATR brut — à éviter en live."""
        from app.core.risk_gate import RiskGate
        cfg = {
            "trading": {
                "capital": 1000, "risk_per_trade": 0.01,
                "max_positions": 5, "max_longs": 3, "max_shorts": 3,
                "max_leverage": 1, "max_trades_per_minute": 3,
                "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20,
            },
            "risk": {},
            # ⚠ Indispensable : `max_notional_pct` vaut 0.20 par défaut, soit un
            # notionnel plafonné à 200 pour 1000 de capital — donc size ≤ 2 à
            # entry=100. Ce plafond mordait dans les DEUX branches (avec et sans
            # stop_dist), les ramenait toutes deux à 2.0 et rendait ces tests
            # vacants : ils ne pouvaient plus distinguer le sizing correct du
            # bug de sur-risque 2,5× qu'ils sont censés verrouiller.
            "backtest": {"max_notional_pct": 1.0},
        }
        rm = RiskGate(cfg)
        # Sans stop_dist : size = 10 / 2 = 5 (ATR brut)
        size, notional = rm.compute_size(
            entry=100, atr=2, score=1.0, threshold=0.6, size_factor=1.0,
        )
        # Sans facteurs de réduction, size ~ 5 ; avec score_internal_factor=1.0
        # quand score=1.0 (max), on devrait être près de 5
        assert size > 2, f"Sizing par ATR brut cassé : size={size}"

    def test_sizing_with_stop_dist_2_5x_atr_gives_2_5x_smaller_size(self):
        """Test clé : stop_dist = 2,5×ATR doit donner size 2,5× plus petit que ATR brut.

        C'est exactement le bug critique : sans stop_dist, le sizing ignorait
        le 2,5×ATR du stop → sur-risque 2,5×. Avec stop_dist, on respecte risk%.
        """
        from app.core.risk_gate import RiskGate
        cfg = {
            "trading": {
                "capital": 1000, "risk_per_trade": 0.01,
                "max_positions": 5, "max_longs": 3, "max_shorts": 3,
                "max_leverage": 1, "max_trades_per_minute": 3,
                "daily_drawdown_limit": 0.05, "max_drawdown_global": 0.20,
            },
            "risk": {},
            # ⚠ Indispensable : `max_notional_pct` vaut 0.20 par défaut, soit un
            # notionnel plafonné à 200 pour 1000 de capital — donc size ≤ 2 à
            # entry=100. Ce plafond mordait dans les DEUX branches (avec et sans
            # stop_dist), les ramenait toutes deux à 2.0 et rendait ces tests
            # vacants : ils ne pouvaient plus distinguer le sizing correct du
            # bug de sur-risque 2,5× qu'ils sont censés verrouiller.
            "backtest": {"max_notional_pct": 1.0},
        }
        rm = RiskGate(cfg)
        # Sans stop_dist (ATR brut)
        size_atr, _ = rm.compute_size(
            entry=100, atr=2, score=1.0, threshold=0.6, size_factor=1.0,
        )
        # Avec stop_dist = 2,5 × ATR
        size_stop, _ = rm.compute_size(
            entry=100, atr=2, score=1.0, threshold=0.6, size_factor=1.0,
            stop_dist=5,  # 2,5 × 2
        )
        # Ratio attendu ~2,5 (au lieu de l'ancien 1,0 = bug)
        ratio = size_atr / size_stop
        assert 2.0 < ratio < 3.5, (
            f"Sizing ne respecte pas stop_dist : ratio ATR/stop = {ratio} "
            f"(devrait être ~2,5)"
        )


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
    sauf override explicite `allow_insecure: true` ou `ALLOW_INSECURE_WEB=1`.
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

    def test_load_config_allows_insecure_with_override(self, monkeypatch, tmp_path):
        """config.yaml avec allow_insecure: true → warning mais pas d'erreur."""
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
            "  allow_insecure: true\n"
            "database:\n"
            # Quoté : non quoté, le `:` final rend le scalaire ambigu pour YAML
            # (« mapping values are not allowed here ») — la fixture n'était pas
            # parsable, donc ces deux tests n'avaient jamais pu s'exécuter.
            '  url: "sqlite:///:memory:"\n',
            encoding="utf-8",
        )
        # Doit passer (warning mais pas raise)
        cfg = load_config(str(cfg_file))
        assert cfg["web"]["allow_insecure"] is True


# ─────────────────────────────────────────────────────────────────────────────
# S0-05 : Élagage de la bougie en cours côté live
# ─────────────────────────────────────────────────────────────────────────────

class TestFormingCandleDropped:
    """Le scoring live ne doit pas voir la bougie en cours de formation
    (close non définitif → repaint)."""

    def test_drop_forming_candle_removes_last_incomplete_bar(self):
        """Si la dernière bougie est encore en formation, elle est retirée."""
        from datetime import datetime, timedelta

        import polars as pl

        from app.live.ohlcv_cache import OHLCVCache
        # Construit un DF avec 10 bougies 1h, la dernière en formation
        now = datetime.utcnow()
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
        from datetime import datetime, timedelta

        import polars as pl

        from app.live.ohlcv_cache import OHLCVCache
        now = datetime.utcnow()
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
