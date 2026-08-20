"""CandleStore — backfill historique et boucle de redemande.

Symptôme qui a motivé ces tests, observé sur le SBF 120 :

    [CandleStore] STLAP.PA/15m — cache insuffisant (361/500) — tentative de
                  récupération de 139 bougies historiques
    [CandleStore] STLAP.PA/15m — aucune bougie historique supplémentaire
                  disponible sur l'exchange (cache : 361 bougies)

…répété à chaque cycle, pour 98 titres × 5 timeframes. Deux causes
indépendantes, testées séparément ci-dessous.
"""
import tempfile
import time
from datetime import datetime, timezone

import pytest

from app.core.candle_store import (
    _MIN_SINCE_MS,
    CandleStore,
    _bars_span_ms,
    _min_since,
    epoch_ms,
)
from app.core.provider_router import build_market_provider, register_provider

MIN_MS = 60_000


class _FakeExchange:
    """Exchange ouvert en continu, historique profond, format ccxt."""

    rateLimit = 0
    drop_zero_volume = True
    min_since_ms = 0

    def __init__(self, depth: int = 5_000, tf_ms: int = 15 * MIN_MS):
        self.depth = depth
        self.tf_ms = tf_ms
        self.calls = []
        self._now = 1_800_000_000_000

    def milliseconds(self):
        return self._now

    def parse_timeframe(self, tf):
        return self.tf_ms // 1000

    def _all(self):
        first = self._now - self.depth * self.tf_ms
        return [[first + i * self.tf_ms, 10.0, 11.0, 9.0, 10.5, 100.0]
                for i in range(self.depth)]

    def fetch_ohlcv(self, symbol, tf, since=None, limit=100):
        self.calls.append((symbol, tf, since, limit))
        rows = self._all()
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return rows[:limit]


class _SessionExchange(_FakeExchange):
    """Place à séances : déclare `bars_span_ms` comme YFinanceProvider."""

    def bars_span_ms(self, tf, count):
        return int(count * self.tf_ms / 0.25)


def _store(tmp):
    return CandleStore(base_dir=tmp)


def _spy_backfill(store):
    """Compte les backfills historiques.

    Compter les appels `fetch_ohlcv` ne suffit pas : le fetch incrémental
    pagine avec le même `limit=1000` et serait comptabilisé à tort. C'est
    l'appel à `_fetch_historical` qui nous intéresse — un par cycle, c'est
    exactement la boucle qu'on traque.
    """
    seen = []
    original = store._fetch_historical

    # ``**kw`` et non une signature figée : ce spy n'observe QUE la cadence
    # d'appel, il n'a pas à connaître les arguments que `_fetch_historical`
    # gagne au fil du temps (`known_ts` en est un). Une signature exacte le
    # transformait en test de signature, qui échoue sur une évolution
    # rétro-compatible.
    def spy(exchange, symbol, tf, before_ms, needed, **kw):
        seen.append((symbol, tf, before_ms, needed))
        return original(exchange, symbol, tf, before_ms, needed, **kw)

    store._fetch_historical = spy
    return seen


# ── Cause 1 : la fenêtre visée est en temps calendaire ─────────────────────

class TestTradingTimeSpan:
    def test_default_is_the_historical_crypto_behaviour(self):
        """Un exchange sans `bars_span_ms` (ccxt) ne doit RIEN voir changer."""
        ex = _FakeExchange()
        assert _bars_span_ms(ex, "AIR/EUR", "15m", 500, ex.tf_ms) == 500 * ex.tf_ms

    def test_a_provider_can_widen_the_window(self):
        ex = _SessionExchange()
        assert _bars_span_ms(ex, "AIR/EUR", "15m", 500, ex.tf_ms) == 500 * ex.tf_ms * 4

    def test_a_broken_provider_hook_falls_back_instead_of_crashing(self):
        class _Broken(_FakeExchange):
            def bars_span_ms(self, tf, count):
                raise RuntimeError("boom")
        ex = _Broken()
        assert _bars_span_ms(ex, "AIR/EUR", "15m", 500, ex.tf_ms) == 500 * ex.tf_ms

    def test_historical_backfill_reaches_far_enough_on_a_session_venue(self):
        """Le `since` du backfill doit tenir compte des heures de fermeture,
        sinon il ne remonte qu'au quart de la profondeur demandée."""
        ex = _SessionExchange()
        with tempfile.TemporaryDirectory() as d:
            _store(d)._fetch_historical(ex, "AIR/EUR", "15m",
                                        before_ms=ex.milliseconds(), needed=500)
        since = ex.calls[0][2]
        assert ex.milliseconds() - since == 500 * ex.tf_ms * 4


# ── Cause 2 : la question est reposée à chaque cycle ───────────────────────

class TestNoHistoryMemo:
    """Quand le provider a réellement atteint sa profondeur maximale (titre
    récemment introduit, plafond Yahoo), le compte visé est hors d'atteinte
    POUR TOUJOURS. Redemander à chaque cycle ne coûte que du quota."""

    class _Shallow(_FakeExchange):
        def __init__(self):
            super().__init__(depth=120)

    def test_the_lost_request_is_not_replayed_every_cycle(self):
        ex = self._Shallow()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            backfills = _spy_backfill(store)
            for _ in range(4):
                store.fetch(ex, "NEWCO/EUR", "15m", total=500)
        assert len(backfills) == 1, (
            f"un seul backfill perdant attendu, {len(backfills)} joués — "
            "c'est la boucle observée sur le SBF 120"
        )

    def test_the_message_is_logged_once_not_once_per_cycle(self, caplog):
        ex = self._Shallow()
        with tempfile.TemporaryDirectory() as d, caplog.at_level("INFO"):
            store = _store(d)
            for _ in range(4):
                store.fetch(ex, "NEWCO/EUR", "15m", total=500)
        noise = [r for r in caplog.records if "cache insuffisant" in r.message]
        assert len(noise) == 1

    def test_the_memo_never_blocks_the_incremental_fetch(self):
        """LE point à ne jamais casser : le mémo ne gèle QUE le backfill des
        bougies ANCIENNES. Le fetch incrémental — celui qui ramène les bougies
        qui viennent de se fermer — part à chaque cycle, mémo ou pas. Sans
        cette garantie, un mémo de 6 h sur du 15 min ferait manquer 24 bougies
        au live, ce qui serait bien pire que le bruit de log qu'il supprime.
        """
        ex = self._Shallow()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)
            assert store._no_history, "le mémo doit être posé"
            seen = []
            for _ in range(3):
                ex._now += ex.tf_ms       # une nouvelle bougie se ferme
                ex.depth += 1
                df = store.fetch(ex, "NEWCO/EUR", "15m", total=500)
                seen.append(len(df))
        assert seen == [121, 122, 123], (
            f"chaque nouvelle bougie doit arriver malgré le mémo, obtenu {seen}"
        )

    def test_data_is_still_served_while_the_memo_holds(self):
        """Le mémo coupe la REQUÊTE, pas le service : les bougies en cache
        doivent continuer d'être rendues."""
        ex = self._Shallow()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)
            df = store.fetch(ex, "NEWCO/EUR", "15m", total=500)
        assert df is not None and len(df) == 120

    def test_the_memo_expires_so_a_transient_failure_is_not_permanent(self,
                                                                     monkeypatch):
        """Un 429 pendant le backfill est indiscernable d'un historique
        épuisé. Le mémo doit donc être daté, sinon un incident réseau
        gèlerait l'historique du symbole définitivement."""
        import app.core.candle_store as cs
        monkeypatch.setattr(cs, "_NO_HISTORY_RETRY_S", 0.0)
        ex = self._Shallow()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            backfills = _spy_backfill(store)
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)
            time.sleep(0.01)
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)
        assert len(backfills) == 2

    def test_the_memo_lifts_when_older_bars_arrive_by_another_route(self):
        """Backfill hors ligne, autre timeframe : si la borne basse du cache
        recule, le mémo ne décrit plus la réalité et doit tomber."""
        ex = self._Shallow()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)
            assert store._history_exhausted(
                "NEWCO/EUR", "15m",
                epoch_ms(store.load_cached("NEWCO/EUR", "15m")["time"].min()))
            assert not store._history_exhausted("NEWCO/EUR", "15m", 1)

    def test_a_successful_backfill_clears_the_memo(self):
        ex = self._Shallow()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)
            assert store._no_history
            store._forget_exhausted("NEWCO/EUR", "15m")
            assert not store._no_history


# ── Amorçage profond (`period='max'` côté provider) ────────────────────────

class _DeepExchange(_SessionExchange):
    """Provider sachant rendre TOUTE sa profondeur en une requête."""

    def __init__(self, depth=5_000):
        super().__init__(depth=depth)
        self.max_calls = 0

    def fetch_ohlcv_max(self, symbol, tf):
        self.max_calls += 1
        return self._all()


class TestDeepBootstrap:
    def test_cold_start_takes_everything_in_one_request(self):
        ex = _DeepExchange()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(ex, "AIR/EUR", "15m", total=500)
            cached = store.load_cached("AIR/EUR", "15m")
        assert ex.max_calls == 1
        assert len(cached) == ex.depth, (
            "l'amorçage doit CONSERVER toute la profondeur obtenue, pas la "
            "retailler au nombre de bougies demandé"
        )

    def test_an_already_seeded_cache_catches_up_its_depth(self):
        """Cas réel : un cache peuplé par une version antérieure, resté court.
        Le backfill doit rattraper via le chemin profond, pas s'entêter sur
        une fenêtre calculée."""
        ex = _DeepExchange()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            shallow = ex._all()[-100:]
            store._save(store._path("AIR/EUR", "15m"), store._raw_to_df(shallow))
            store.fetch(ex, "AIR/EUR", "15m", total=500)
            assert len(store.load_cached("AIR/EUR", "15m")) == ex.depth

    def test_a_provider_without_the_hook_keeps_the_paginated_path(self):
        """La crypto (ccxt) n'expose pas `fetch_ohlcv_max` : rien ne change."""
        ex = _SessionExchange()
        assert not hasattr(ex, "fetch_ohlcv_max")
        with tempfile.TemporaryDirectory() as d:
            df = _store(d).fetch(ex, "AIR/EUR", "15m", total=500)
        assert df is not None and len(df) == 500

    def test_an_empty_deep_response_falls_back_instead_of_emptying_the_cache(self):
        """Quota atteint ou timeframe non servi : le chemin profond rend une
        liste vide. Retourner ça tel quel laisserait le cache vide alors que
        le chemin borné, lui, aurait pu servir."""
        class _Silent(_DeepExchange):
            def fetch_ohlcv_max(self, symbol, tf):
                self.max_calls += 1
                return []
        ex = _Silent()
        with tempfile.TemporaryDirectory() as d:
            df = _store(d).fetch(ex, "AIR/EUR", "15m", total=500)
        assert ex.max_calls >= 1
        assert df is not None and len(df) == 500

    def test_a_raising_deep_path_does_not_break_the_cycle(self):
        class _Boom(_DeepExchange):
            def fetch_ohlcv_max(self, symbol, tf):
                raise RuntimeError("429")
        ex = _Boom()
        with tempfile.TemporaryDirectory() as d:
            df = _store(d).fetch(ex, "AIR/EUR", "15m", total=500)
        assert df is not None and len(df) == 500

    def test_a_definitive_no_older_answer_skips_the_bounded_retry(self):
        """Le provider a répondu et n'a rien de plus ancien : c'est une
        réponse, pas un échec. Relancer une requête bornée derrière ne ferait
        que consommer du quota pour le même « non »."""
        ex = _DeepExchange(depth=120)
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)
            floor_ms = epoch_ms(store.load_cached("NEWCO/EUR", "15m")["time"].min())
            store._forget_exhausted("NEWCO/EUR", "15m")   # force un 2e backfill
            ex.calls.clear()
            store.fetch(ex, "NEWCO/EUR", "15m", total=500)

        assert ex.max_calls == 2, "le 2e backfill doit repasser par le chemin profond"
        reaching_back = [c for c in ex.calls
                         if c[2] is not None and c[2] < floor_ms]
        assert reaching_back == [], (
            f"aucune requête bornée ne doit repartir chercher avant "
            f"{floor_ms}, obtenu {reaching_back}"
        )


# ── Le cas nominal ne doit pas régresser ───────────────────────────────────

class TestDeepHistoryStillWorks:
    def test_a_deep_provider_fills_the_cache_on_the_first_cycle(self):
        ex = _SessionExchange()
        with tempfile.TemporaryDirectory() as d:
            df = _store(d).fetch(ex, "AIR/EUR", "15m", total=500)
        assert df is not None and len(df) == 500

    @pytest.mark.parametrize("total", [50, 500])
    def test_no_backfill_attempt_when_the_cache_suffices(self, total):
        ex = _SessionExchange()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            backfills = _spy_backfill(store)
            store.fetch(ex, "AIR/EUR", "15m", total=total)
            store.fetch(ex, "AIR/EUR", "15m", total=total)
        assert backfills == [], "le premier fetch doit déjà suffire"


# ── Le routeur multi-venues ne doit rien masquer ───────────────────────────
#
# Symptôme réel : `scripts/backfill_equities.py --tf 1d` plafonnait tous les
# titres du SBF 120 à 2447 barres depuis 2017-01-01 — la fondation d'OKX —
# alors que Yahoo sert AC.PA depuis 2000-01-03. En live, le store reçoit un
# `ProviderRouter` : il route `fetch_ohlcv` par symbole, mais son `__getattr__`
# renvoie tout le reste à l'exchange crypto par défaut. Les quatre points de
# contrat que le provider actions expose au store étaient donc invisibles.

class _CryptoDefault(_FakeExchange):
    """Exchange par défaut du routeur — valeurs volontairement distinctes de
    celles du provider actions, pour que toute confusion se voie."""

    drop_zero_volume = True
    min_since_ms = _MIN_SINCE_MS


class _RoutedEquityProvider:
    """Provider actions derrière le routeur — contrat de `YFinanceProvider`.

    Volume nul sur toutes les barres (légitime sur une valeur peu liquide) :
    si le store interroge le routeur au lieu du provider, il applique la règle
    crypto et le cache ressort **vide**.
    """

    instances: list = []
    drop_zero_volume = False
    min_since_ms = 0

    def __init__(self, cfg=None, depth: int = 5_000, tf_ms: int = 15 * MIN_MS):
        self.depth = depth
        self.tf_ms = tf_ms
        self.max_calls = 0
        self.bounded_calls: list = []
        self._now = 1_800_000_000_000
        _RoutedEquityProvider.instances.append(self)

    def _all(self):
        first = self._now - self.depth * self.tf_ms
        return [[first + i * self.tf_ms, 10.0, 11.0, 9.0, 10.5, 0.0]
                for i in range(self.depth)]

    def bars_span_ms(self, tf, count):
        return int(count * self.tf_ms / 0.25)

    def fetch_ohlcv_max(self, symbol, tf):
        self.max_calls += 1
        return self._all()

    def fetch_ohlcv(self, symbol, tf, since=None, limit=100):
        self.bounded_calls.append((symbol, tf, since, limit))
        rows = self._all()
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return rows[:limit]


register_provider("fake_routed_equity", f"{__name__}:_RoutedEquityProvider")

_ROUTED_CFG = {
    "exchange": {"name": "okx"},
    "trading": {},
    "venues": {
        "defs": {
            "spot": {"market_type": "spot"},
            "euronext-paper": {
                "asset_class": "equity", "quote_currency": "EUR",
                "data_provider": "fake_routed_equity", "can_execute": False,
                "calendar": "XPAR",
            },
        },
        "assign": {"AIR.PA": "euronext-paper"},
    },
}


class TestRoutedProviderContract:

    @pytest.fixture(autouse=True)
    def _reset(self):
        _RoutedEquityProvider.instances.clear()
        yield

    def _router(self):
        return build_market_provider(_ROUTED_CFG, _CryptoDefault())

    def test_the_history_floor_comes_from_the_symbols_provider(self):
        """LA cause du plafond 2017-01-01 : le plancher crypto s'appliquait
        aux actions, qui cotent bien avant la fondation d'OKX."""
        router = self._router()
        assert _min_since(router, "AIR.PA") == 0
        assert _min_since(router, "BTC/USDC") == _MIN_SINCE_MS

    def test_the_session_span_comes_from_the_symbols_provider(self):
        router = self._router()
        tf_ms = 15 * MIN_MS
        assert _bars_span_ms(router, "AIR.PA", "15m", 500, tf_ms) == 500 * tf_ms * 4
        assert _bars_span_ms(router, "BTC/USDC", "15m", 500, tf_ms) == 500 * tf_ms

    def test_the_deep_bootstrap_survives_the_router(self):
        router = self._router()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(router, "AIR.PA", "15m", total=500)
            cached = store.load_cached("AIR.PA", "15m")
        provider = _RoutedEquityProvider.instances[0]
        assert provider.max_calls == 1, (
            "`fetch_ohlcv_max` était invisible derrière le routeur : le store "
            "retombait sur une fenêtre estimée et bornée à 2017"
        )
        assert len(cached) == provider.depth

    def test_zero_volume_bars_survive_the_router(self):
        router = self._router()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.fetch(router, "AIR.PA", "15m", total=500)
            cached = store.load_cached("AIR.PA", "15m")
        assert len(cached) > 0, (
            "la règle crypto « volume nul = donnée cassée » ne doit pas "
            "s'appliquer à une action peu liquide"
        )

    def test_a_crypto_symbol_keeps_the_exchange_contract(self):
        """Le pendant à ne jamais casser : rien ne change côté crypto."""
        exchange = _CryptoDefault()
        router = build_market_provider(_ROUTED_CFG, exchange)
        with tempfile.TemporaryDirectory() as d:
            df = _store(d).fetch(router, "BTC/USDC", "15m", total=500)
        assert df is not None and len(df) == 500
        assert _RoutedEquityProvider.instances == []


# ── Les bornes du cache se lisent en UTC ───────────────────────────────────

class TestCacheBoundsAreUTC:
    """La colonne `time` est un `Datetime` NAÏF qui porte de l'UTC.

    `datetime.timestamp()` la relisait en heure locale : sur une machine à
    UTC+1, les bornes du cache repartaient une heure trop tôt. Conséquence
    concrète — et invisible sur un CI en UTC : le backfill s'arrêtait avant
    les bougies qui touchent le cache, laissant un trou permanent d'un fuseau
    à la jonction.
    """

    def test_epoch_ms_ignores_the_machine_timezone(self):
        naive = datetime(2027, 1, 15, 7, 45)
        assert epoch_ms(naive) == int(
            naive.replace(tzinfo=timezone.utc).timestamp() * 1000)

    def test_a_seeded_cache_is_backfilled_without_a_gap(self):
        """Le cas qui échouait : cache amorcé sur les 100 dernières bougies,
        backfill profond derrière — les deux blocs doivent se toucher."""
        ex = _DeepExchange()
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store._save(store._path("AIR/EUR", "15m"),
                        store._raw_to_df(ex._all()[-100:]))
            store.fetch(ex, "AIR/EUR", "15m", total=500)
            cached = store.load_cached("AIR/EUR", "15m")
        assert len(cached) == ex.depth
        stamps = [epoch_ms(t) for t in cached["time"].to_list()]
        gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b - a != ex.tf_ms]
        assert gaps == [], f"trou(s) à la jonction du backfill : {gaps}"


class TestInteriorGaps:
    """Les trous INTÉRIEURS — barres absentes du cache alors qu'elles tombent
    dans sa plage — n'étaient comblés par aucun chemin.

    Le fetch incrémental ne regarde qu'après la dernière barre connue. Le
    backfill historique ne gardait que ce qui précède la première. Entre les
    deux, un trou restait un trou pour toujours, même quand la source venait
    de publier les barres manquantes dans la même réponse.

    Constaté en production sur `AC.PA/4h` : la source annonçait 1522 bougies,
    le cache en stockait 1442, et l'écart de 80 ne bougeait plus.
    """

    def _holed_cache(self, store, ex, symbol="HOLE/EUR", tf="15m"):
        """Cache couvrant toute la plage, mais amputé en son milieu."""
        rows = ex._all()
        kept = rows[:50] + rows[80:]          # 30 barres manquantes au milieu
        store._save(store._path(symbol, tf), store._raw_to_df(kept))
        return len(kept), len(rows)

    def test_an_interior_gap_is_filled(self):
        ex = _DeepExchange(depth=400)
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            kept, total = self._holed_cache(store, ex)
            assert kept == total - 30, "le cache de départ doit bien être troué"
            store.fetch(ex, "HOLE/EUR", "15m", total=500)
            cached = store.load_cached("HOLE/EUR", "15m")
        assert len(cached) == total, (
            f"{total - len(cached)} barre(s) toujours manquante(s) — le chemin "
            f"profond avait la réponse et la jetait")
        stamps = [epoch_ms(t) for t in cached["time"].to_list()]
        gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b - a != ex.tf_ms]
        assert gaps == [], f"trou(s) résiduel(s) : {gaps}"

    def test_a_complete_cache_triggers_no_write(self):
        """Le pendant : sans trou ni barre plus ancienne, le chemin profond
        doit conclure « rien de neuf » et laisser le memo d'épuisement se
        poser — sinon on réécrirait le parquet à chaque cycle."""
        ex = _DeepExchange(depth=400)
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store._save(store._path("FULL/EUR", "15m"),
                        store._raw_to_df(ex._all()))
            store.fetch(ex, "FULL/EUR", "15m", total=500)
            store.fetch(ex, "FULL/EUR", "15m", total=500)
            cached = store.load_cached("FULL/EUR", "15m")
        assert len(cached) == ex.depth

    def test_interior_gap_filled_even_when_cache_already_longer_than_total(self):
        """Cas UI / live : 58 k barres en cache, refetch à 6 000 — sans ce
        passage les trous au milieu n'étaient jamais visés."""
        ex = _FakeExchange(depth=400)
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            kept, total = self._holed_cache(store, ex, symbol="BTC/USDC")
            assert kept == total - 30
            store.fetch(ex, "BTC/USDC", "15m", total=50)
            cached = store.load_cached("BTC/USDC", "15m")
        assert len(cached) == total, (
            f"{total - len(cached)} barre(s) toujours manquante(s) alors que "
            f"le cache dépassait déjà `total`")
        stamps = [epoch_ms(t) for t in cached["time"].to_list()]
        gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b - a != ex.tf_ms]
        assert gaps == [], f"trou(s) résiduel(s) : {gaps}"

    def test_prefer_cache_does_not_hit_exchange_to_fill_gaps(self):
        """Un backtest (prefer_cache) ne doit pas muter la série."""
        ex = _FakeExchange(depth=400)
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            kept, _total = self._holed_cache(store, ex, symbol="BTC/USDC")
            n_calls = len(ex.calls)
            store.fetch(ex, "BTC/USDC", "15m", total=50, prefer_cache=True)
            cached = store.load_cached("BTC/USDC", "15m")
        assert len(cached) == kept
        assert len(ex.calls) == n_calls

    def test_unfillable_gap_is_not_refetched_every_cycle(self):
        """Maintenance exchange : le trou n'existe pas chez le provider.
        Un mémo 6 h évite de rejouer la plage à chaque cycle live."""

        class _HoledSource(_FakeExchange):
            def _all(self):
                rows = super()._all()
                return rows[:50] + rows[80:]

        ex = _HoledSource(depth=200)
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store._save(store._path("BTC/USDC", "15m"),
                        store._raw_to_df(ex._all()))
            store.fetch(ex, "BTC/USDC", "15m", total=50)
            after_first = len(ex.calls)
            store.fetch(ex, "BTC/USDC", "15m", total=50)
            after_second = len(ex.calls)
        # 2e fetch : incrémental (1 appel) mais pas de re-pagination du trou.
        assert after_second - after_first <= 1, (
            f"le trou incombable a été redemandé "
            f"({after_second - after_first} appels)")

    def test_scattered_holes_filled_in_one_span(self):
        """Des trous dispersés se recousent en UNE pagination de plage,
        pas un fetch par trou."""
        ex = _FakeExchange(depth=400)
        rows = ex._all()
        kept = []
        drop = set()
        for start in range(40, 360, 20):
            drop.update(range(start, start + 3))
        kept = [r for i, r in enumerate(rows) if i not in drop]
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store._save(store._path("BTC/USDC", "15m"),
                        store._raw_to_df(kept))
            assert len(store.load_cached("BTC/USDC", "15m")) < 400
            n_before = len(ex.calls)
            store.fetch(ex, "BTC/USDC", "15m", total=50)
            cached = store.load_cached("BTC/USDC", "15m")
            n_span = len(ex.calls) - n_before
        assert len(cached) == 400
        # Une pagination de la plage : largement moins d'appels que de trous.
        n_holes = len(range(40, 360, 20))
        assert n_span < n_holes, (
            f"{n_span} appels pour {n_holes} trous — le fetch par trou est revenu")


def test_unique_keep_last_on_incremental_overlap():
    """D-02 : la barre de recouvrement prend la version fraîche (close à jour)."""
    import polars as pl

    old = pl.DataFrame({
        "time": [datetime(2024, 1, 1, 10), datetime(2024, 1, 1, 11)],
        "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
        "close": [10.0, 20.0], "volume": [1.0, 1.0],
    }).with_columns(pl.col("time").cast(pl.Datetime("ms")))
    new = pl.DataFrame({
        "time": [datetime(2024, 1, 1, 11), datetime(2024, 1, 1, 12)],
        "open": [2.0, 3.0], "high": [2.0, 3.0], "low": [2.0, 3.0],
        "close": [21.0, 30.0], "volume": [1.0, 1.0],
    }).with_columns(pl.col("time").cast(pl.Datetime("ms")))
    merged = pl.concat([old, new]).unique("time", keep="last").sort("time")
    eleven = merged.filter(pl.col("time") == datetime(2024, 1, 1, 11))
    assert eleven["close"][0] == 21.0


def test_unique_keep_first_on_historical_overlap():
    """D-02 : le backfill ne doit pas écraser une barre déjà en cache."""
    import polars as pl

    cached = pl.DataFrame({
        "time": [datetime(2024, 1, 1, 11)],
        "open": [2.0], "high": [2.0], "low": [2.0],
        "close": [21.0], "volume": [1.0],
    }).with_columns(pl.col("time").cast(pl.Datetime("ms")))
    older = pl.DataFrame({
        "time": [datetime(2024, 1, 1, 10), datetime(2024, 1, 1, 11)],
        "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
        "close": [10.0, 99.0], "volume": [1.0, 1.0],
    }).with_columns(pl.col("time").cast(pl.Datetime("ms")))
    merged = pl.concat([cached, older]).unique("time", keep="first").sort("time")
    eleven = merged.filter(pl.col("time") == datetime(2024, 1, 1, 11))
    assert eleven["close"][0] == 21.0


def test_fetch_range_approfondit_le_store_et_ne_rend_que_la_plage(tmp_path):
    """A-03 : le backfill vise la profondeur du store ; le backtest n'en voit
    que la fenêtre. Une plage de 3 jours ne doit pas empêcher d'aller chercher
    l'historique manquant — c'est persisté une fois."""
    from datetime import timedelta

    ex = _FakeExchange(depth=400, tf_ms=3_600_000)
    store = _store(str(tmp_path))
    # `_fetch_full(limit=200)` sans `since` ramène les 200 plus anciennes
    # barres de la source. La fenêtre doit recouvrir ce bloc, pas la queue.
    first = datetime.fromtimestamp(
        (ex._now - ex.depth * ex.tf_ms) / 1000, tz=timezone.utc
    ).replace(tzinfo=None)
    start, end = first, first + timedelta(hours=10)
    out = store.fetch_range(
        ex, "BTC/USDC", "1h", start=start, end=end,
        total=200, prefer_cache=False,
    )
    cached = store.load_cached("BTC/USDC", "1h")
    assert len(cached) >= 200, "le Parquet doit avoir été approfondi vers `total`"
    assert out is not None and 0 < len(out) < len(cached)
    assert out["time"].min() >= start
    assert out["time"].max() <= end


def test_load_range_ne_materialise_que_la_plage(tmp_path):
    """A-03 : scan Parquet filtré — pas les 50k bougies du fichier."""
    from datetime import timedelta

    import polars as pl

    n = 500
    start0 = datetime(2024, 1, 1)
    df = pl.DataFrame({
        "time": [start0 + timedelta(hours=i) for i in range(n)],
        "open":   [100.0] * n,
        "high":   [101.0] * n,
        "low":    [99.0] * n,
        "close":  [100.5] * n,
        "volume": [1.0] * n,
    }).with_columns(pl.col("time").cast(pl.Datetime("ms")))
    store = _store(str(tmp_path))
    store._save(store._path("BTC/USDC", "1h"), df)
    out = store.load_range(
        "BTC/USDC", "1h",
        start=datetime(2024, 1, 3),
        end=datetime(2024, 1, 5),
    )
    assert 0 < len(out) < n
    assert out["time"].min() >= datetime(2024, 1, 3)
    assert out["time"].max() <= datetime(2024, 1, 5)
