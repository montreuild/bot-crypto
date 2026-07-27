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

import pytest

from app.core.candle_store import CandleStore, _bars_span_ms

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

    def spy(exchange, symbol, tf, before_ms, needed):
        seen.append((symbol, tf, before_ms, needed))
        return original(exchange, symbol, tf, before_ms, needed)

    store._fetch_historical = spy
    return seen


# ── Cause 1 : la fenêtre visée est en temps calendaire ─────────────────────

class TestTradingTimeSpan:
    def test_default_is_the_historical_crypto_behaviour(self):
        """Un exchange sans `bars_span_ms` (ccxt) ne doit RIEN voir changer."""
        ex = _FakeExchange()
        assert _bars_span_ms(ex, "15m", 500, ex.tf_ms) == 500 * ex.tf_ms

    def test_a_provider_can_widen_the_window(self):
        ex = _SessionExchange()
        assert _bars_span_ms(ex, "15m", 500, ex.tf_ms) == 500 * ex.tf_ms * 4

    def test_a_broken_provider_hook_falls_back_instead_of_crashing(self):
        class _Broken(_FakeExchange):
            def bars_span_ms(self, tf, count):
                raise RuntimeError("boom")
        ex = _Broken()
        assert _bars_span_ms(ex, "15m", 500, ex.tf_ms) == 500 * ex.tf_ms

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
                int(store.load_cached("NEWCO/EUR", "15m")["time"]
                    .min().timestamp() * 1000))
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
            floor_ms = int(store.load_cached("NEWCO/EUR", "15m")["time"]
                           .min().timestamp() * 1000)
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
