"""G2 — routage d'un instrument actions vers sa venue et son provider.

``data/universe/*.yaml`` déclarait déjà sa clé ``venue:`` et ``universe_venue()``
savait la lire, mais ``resolve_venue`` ne la consultait pas : activer
``scanner.universe`` ajoutait bien les 120 instruments au scanner, tous résolus
sur la venue CRYPTO par défaut — donc cherchés chez OKX et jamais chez le
provider actions. La seule alternative documentée était d'écrire une ligne
d'``assign`` par titre.

Ces tests verrouillent l'échelon « univers » de la précédence, et le fait qu'un
``assign`` explicite continue de primer.
"""
import polars as pl
import pytest
import yaml

import app.core.universe as universe_mod
from app.core.bot_identity import resolve_venue
from app.core.config import _load_and_merge
from app.core.universe import clear_cache, symbol_venue_index


@pytest.fixture
def universe_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(universe_mod, "UNIVERSE_DIR", str(tmp_path))
    (tmp_path / "acts.yaml").write_text(
        "name: Actions\nvenue: euronext-paper\nmembers:\n  - AIR.PA\n  - MC.PA\n",
        encoding="utf-8")
    (tmp_path / "sansvenue.yaml").write_text(
        "name: Sans venue\nmembers:\n  - XX.PA\n", encoding="utf-8")
    clear_cache()
    yield tmp_path
    clear_cache()


def _cfg(**scanner):
    return {
        "venues": {
            "default": "spot",
            "defs": {
                "spot": {"market_type": "spot", "exchange": "okx",
                         "asset_class": "crypto", "quote_currency": "USDC"},
                "euronext-paper": {"market_type": "spot", "exchange": "euronext",
                                   "asset_class": "equity", "quote_currency": "EUR",
                                   "calendar": "XPAR", "data_provider": "yfinance",
                                   "can_execute": False},
            },
            "assign": {},
        },
        "scanner": scanner,
    }


class TestUniverseVenuePrecedence:
    def test_universe_member_resolves_to_its_declared_venue(self, universe_dir):
        v = resolve_venue(_cfg(universe=["acts"]), symbol="AIR.PA")
        assert v.name == "euronext-paper"
        assert v.asset_class == "equity"
        assert v.data_provider == "yfinance"
        assert v.calendar == "XPAR"
        assert v.can_execute is False

    def test_non_member_still_resolves_to_the_crypto_default(self, universe_dir):
        assert resolve_venue(_cfg(universe=["acts"]), symbol="BTC/USDC").name == "spot"

    def test_universe_not_activated_routes_nothing(self, universe_dir):
        """Un fichier présent dans data/universe/ mais non référencé par
        ``scanner.universe`` ne doit router aucun symbole — sinon déposer un
        fichier changerait le routage sans rien activer."""
        assert resolve_venue(_cfg(), symbol="AIR.PA").name == "spot"

    def test_explicit_assign_wins_over_the_universe_declaration(self, universe_dir):
        """Le déclaratif ne reprend jamais la main sur ce qu'un opérateur a
        écrit à la main."""
        cfg = _cfg(universe=["acts"])
        cfg["venues"]["assign"] = {"AIR.PA": "spot"}
        assert resolve_venue(cfg, symbol="AIR.PA").name == "spot"
        # …et le reste de l'univers n'est pas affecté.
        assert resolve_venue(cfg, symbol="MC.PA").name == "euronext-paper"

    def test_universe_wins_over_a_strategy_level_assign(self, universe_dir):
        """Un instrument porte sa venue indépendamment de la stratégie qui le
        trade : une action ne peut pas se traiter sur une venue crypto."""
        cfg = _cfg(universe=["acts"])
        cfg["venues"]["assign"] = {"trend": "spot"}
        assert resolve_venue(cfg, strategy="trend", symbol="AIR.PA").name == "euronext-paper"
        assert resolve_venue(cfg, strategy="trend", symbol="BTC/USDC").name == "spot"

    def test_slot_level_assign_still_wins(self, universe_dir):
        cfg = _cfg(universe=["acts"])
        cfg["venues"]["assign"] = {"trend::1h::AIR.PA": "spot"}
        assert resolve_venue(cfg, "trend", "1h", "AIR.PA").name == "spot"

    def test_universe_without_venue_key_routes_nothing(self, universe_dir):
        assert resolve_venue(_cfg(universe=["sansvenue"]), symbol="XX.PA").name == "spot"


class TestSymbolVenueIndex:
    def test_first_universe_declaring_a_symbol_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(universe_mod, "UNIVERSE_DIR", str(tmp_path))
        (tmp_path / "a.yaml").write_text("venue: va\nmembers: [AIR.PA]\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("venue: vb\nmembers: [AIR.PA]\n", encoding="utf-8")
        clear_cache()
        assert symbol_venue_index(["a", "b"])["AIR.PA"] == "va"
        assert symbol_venue_index(["b", "a"])["AIR.PA"] == "vb"

    def test_accepts_a_bare_string_like_the_config_does(self, universe_dir):
        assert symbol_venue_index("acts") == {"AIR.PA": "euronext-paper",
                                              "MC.PA": "euronext-paper"}

    def test_empty_input_is_empty(self, universe_dir):
        assert symbol_venue_index([]) == {} and symbol_venue_index(None) == {}


class TestShippedConfig:
    """Le config.yaml livré doit réellement router le SBF 120 — c'est ce qui
    fait la différence entre « le code sait le faire » et « c'est activé »."""

    def test_sbf120_is_activated_and_routed(self):
        # S11 : la config est decoupee (config.yaml = sommaire `include:`).
        # Lire le seul fichier racine ne verrait plus ni `scanner` ni `venues`.
        cfg = _load_and_merge("config.yaml")
        clear_cache()
        assert "sbf120" in (cfg["scanner"].get("universe") or [])
        v = resolve_venue(cfg, symbol="AIR.PA")
        assert v.asset_class == "equity"
        assert v.data_provider == "yfinance"
        assert v.can_execute is False, "G3 non livré : aucun ordre ne doit partir"
        # La crypto ne doit rien emprunter de nouveau.
        assert resolve_venue(cfg, symbol="BTC/USDC").asset_class == "crypto"

    def test_yfinance_provider_block_is_declared(self):
        cfg = _load_and_merge("config.yaml")
        from app.core.provider_router import declared_providers
        assert "yfinance" in declared_providers(cfg)
        assert (cfg.get("providers") or {}).get("yfinance") is not None


_BARS = [[1_600_000_000_000 + i * 86_400_000, 10.0, 11.0, 9.0, 10.5, 0.0]
         for i in range(300)]


class EquityStub:
    """Provider actions de test. Volume nul assumé (``drop_zero_volume``)."""

    drop_zero_volume = False
    rateLimit = 0

    def __init__(self, cfg=None):
        pass

    def parse_timeframe(self, tf):
        return 86400

    def milliseconds(self):
        return _BARS[-1][0] + 86_400_000

    def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        return _BARS[-limit:] if since is None else []


class _CryptoOnly(EquityStub):
    """Exchange par défaut : lève si on lui demande une action. Sans lui, le
    test passerait même si le routage était cassé — le fallback servirait les
    mêmes bougies."""

    def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        raise AssertionError(
            f"{symbol} a été demandé à l'exchange crypto par défaut : "
            f"le routage vers la venue actions n'a pas fonctionné")


@pytest.fixture
def stub_provider():
    """Enregistre un provider 'stub' et RESTAURE la table des fabriques —
    ``_FACTORIES`` est un état global du module."""
    import app.core.provider_router as pr
    saved = dict(pr._FACTORIES)
    pr.register_provider("stub", f"{__name__}:EquityStub")
    yield
    pr._FACTORIES.clear()
    pr._FACTORIES.update(saved)


class TestBackfillPath:
    """Le chemin cache ← provider, avec un provider qui RÉPOND (Yahoo est
    injoignable en CI comme en bac à sable)."""

    def test_store_fetch_populates_the_cache_through_the_router(
            self, universe_dir, stub_provider, tmp_path):
        import app.core.candle_store as cs
        from app.core.provider_router import build_market_provider

        cfg = _cfg(universe=["acts"])
        cfg["venues"]["defs"]["euronext-paper"]["data_provider"] = "stub"
        store = cs.CandleStore(base_dir=str(tmp_path / "ohlcv"))
        router = build_market_provider(cfg, _CryptoOnly())

        df = store.fetch(router, "AIR.PA", "1d", 300)
        assert df is not None and len(df) == 300
        # Relance : incrémental et réentrant, pas de doublon.
        again = store.fetch(router, "AIR.PA", "1d", 300)
        assert len(again) == 300
        assert store.stats("AIR.PA", "1d")["bars"] == 300
        assert isinstance(store.load_cached("AIR.PA", "1d"), pl.DataFrame)

    def test_zero_volume_bars_are_kept_for_equities(
            self, universe_dir, stub_provider, tmp_path):
        """Sur actions peu liquides une barre à volume nul est légitime ; la
        règle crypto (volume nul = données cassées) viderait le cache."""
        import app.core.candle_store as cs
        from app.core.provider_router import build_market_provider

        cfg = _cfg(universe=["acts"])
        cfg["venues"]["defs"]["euronext-paper"]["data_provider"] = "stub"
        store = cs.CandleStore(base_dir=str(tmp_path / "ohlcv"))
        router = build_market_provider(cfg, _CryptoOnly())

        df = store.fetch(router, "AIR.PA", "1d", 300)
        assert len(df) == 300, "les barres à volume nul ont été éliminées"
        assert float(df["volume"].sum()) == 0.0


class TestRateLimitCircuitBreaker:
    """Un quota Yahoo atteint coûtait ``max_retries`` requêtes PAR symbole et
    PAR timeframe. Sur 98 titres × 5 TF, un cycle de scan passait de quelques
    secondes à une demi-heure — à ne rien rapporter."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        import app.core.yfinance_provider as yp
        yp._RATE_STATE.update({"consecutive": 0.0, "until": 0.0})
        yield
        yp._RATE_STATE.update({"consecutive": 0.0, "until": 0.0})

    def _provider(self):
        from app.core.yfinance_provider import YFinanceProvider
        return YFinanceProvider({"providers": {"yfinance": {"min_request_interval": 0.0,
                                                            "max_retries": 1}}})

    def test_repeated_429_opens_the_breaker_and_stops_calling(self, monkeypatch):
        import app.core.yfinance_provider as yp
        p = self._provider()
        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            raise RuntimeError("HTTP 429 — quota Yahoo atteint, backoff")

        monkeypatch.setattr(p, "_fetch_bars", _boom)
        for _ in range(yp._RATE_TRIP_AFTER):
            p._fetch_raw("X.PA", "1d", 0, 1)
        assert p._cooldown_remaining() > 0, "le disjoncteur devait s'ouvrir"

        before = calls["n"]
        for _ in range(20):
            assert p._fetch_raw("Y.PA", "1d", 0, 1) == []
        assert calls["n"] == before, "aucun appel ne doit partir disjoncteur ouvert"

    def test_a_success_resets_the_counter(self, monkeypatch):
        import app.core.yfinance_provider as yp
        p = self._provider()
        monkeypatch.setattr(p, "_fetch_bars",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("HTTP 429 — quota")))
        p._fetch_raw("X.PA", "1d", 0, 1)
        assert yp._RATE_STATE["consecutive"] == 1
        monkeypatch.setattr(p, "_fetch_bars",
                            lambda *a, **k: [[0, 1.0, 1.0, 1.0, 1.0, 0.0]])
        assert p._fetch_raw("X.PA", "1d", 0, 1)
        assert yp._RATE_STATE["consecutive"] == 0

    def test_a_non_429_error_does_not_trip_the_breaker(self, monkeypatch):
        p = self._provider()
        monkeypatch.setattr(p, "_fetch_bars",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("timeout réseau")))
        for _ in range(10):
            p._fetch_raw("X.PA", "1d", 0, 1)
        assert p._cooldown_remaining() == 0
