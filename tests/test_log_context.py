"""OBS-02 — logs JSON structurés et identifiants de corrélation.

Ce qui est verrouillé ici, et pourquoi :

1. **L'identifiant traverse un thread.** C'est LE point technique de l'item :
   un ``ContextVar`` ne franchit pas ``threading.Thread``, et le bot fait tout
   son travail long dans des threads. Sans le relais, on aurait tracé les
   requêtes HTTP — la partie qui dure trois millisecondes — et perdu les
   entraînements, les optimisations et les cycles de trading.
2. **Un log ne peut pas faire échouer ce qu'il journalise.** Un objet non
   sérialisable dans les extras devient son ``repr`` ; il ne lève pas.
3. **Le fichier de log n'est pas un canal d'injection.** L'identifiant peut
   venir d'un en-tête HTTP, donc d'un inconnu, et il est recopié dans chaque
   ligne.
4. **La console ne salit plus le fichier.** Régression réelle constatée dans
   les rotations archivées : ``_ColorFormatter`` mutait ``record.levelname``,
   donc les séquences ANSI finissaient dans ``bot.log``.
"""
import json
import logging
import threading

import pytest

from app.core.log_context import (
    CorrelationFilter,
    JsonFormatter,
    correlation_scope,
    get_correlation_id,
    new_correlation_id,
    run_with_correlation,
)


def _record(msg: str = "hello", level: int = logging.INFO, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("test.logger", level, __file__, 42, msg, (), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    CorrelationFilter().filter(rec)
    return rec


def _emit(**kw) -> dict:
    return json.loads(JsonFormatter().format(_record(**kw)))


# ─────────────────────────────────────────────────────────────────────────────
#  Portée de l'identifiant
# ─────────────────────────────────────────────────────────────────────────────
class TestCorrelationScope:
    def test_absent_outside_any_operation(self):
        """Un log de démarrage n'appartient à aucune requête — lui inventer un
        identifiant créerait un faux lien."""
        assert get_correlation_id() == ""
        assert "correlation_id" not in _emit()

    def test_scope_sets_then_restores(self):
        with correlation_scope("abc123"):
            assert get_correlation_id() == "abc123"
        assert get_correlation_id() == ""

    def test_scopes_nest(self):
        with correlation_scope("outer"):
            with correlation_scope("inner"):
                assert get_correlation_id() == "inner"
            assert get_correlation_id() == "outer"

    def test_generated_ids_are_short_and_distinct(self):
        ids = {new_correlation_id() for _ in range(200)}
        assert len(ids) == 200
        assert all(len(i) == 12 for i in ids)

    def test_it_crosses_a_thread_through_the_relay(self):
        """Le point technique de OBS-02. Un ContextVar seul ne franchit pas un
        thread — ce test échouerait si ``run_with_correlation`` disparaissait,
        et avec lui toute la traçabilité des jobs de fond."""
        seen = {}

        def work():
            seen["cid"] = get_correlation_id()

        t = threading.Thread(target=run_with_correlation, args=("job42", work))
        t.start()
        t.join()
        assert seen["cid"] == "job42"

    def test_a_plain_thread_does_not_inherit(self):
        """La limite qui JUSTIFIE le relais — si ce test se met à échouer,
        c'est que Python a changé et que ``_spawn`` peut être simplifié."""
        seen = {}
        with correlation_scope("parent"):
            t = threading.Thread(target=lambda: seen.setdefault("cid", get_correlation_id()))
            t.start()
            t.join()
        assert seen["cid"] == ""


# ─────────────────────────────────────────────────────────────────────────────
#  Format JSON
# ─────────────────────────────────────────────────────────────────────────────
class TestJsonFormatter:
    def test_one_line_one_object(self):
        out = JsonFormatter().format(_record("bonjour"))
        assert "\n" not in out
        assert json.loads(out)["message"] == "bonjour"

    def test_carries_the_core_fields(self):
        with correlation_scope("cid-1"):
            payload = _emit(msg="test")
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["correlation_id"] == "cid-1"
        assert payload["ts"].endswith("Z"), "horodatage UTC explicite"

    def test_location_only_from_warning_up(self):
        assert "line" not in _emit(level=logging.INFO)
        assert _emit(level=logging.WARNING)["line"] == 42

    def test_extras_are_promoted_to_fields(self):
        """La contrepartie des f-strings : un appelant qui VEUT du structuré
        passe par ``extra=`` et obtient de vrais champs."""
        payload = _emit(symbol="BTC/USDC", pnl=12.5)
        assert payload["symbol"] == "BTC/USDC"
        assert payload["pnl"] == 12.5

    def test_an_unserialisable_extra_degrades_to_repr(self):
        payload = _emit(obj=object())
        assert isinstance(payload["obj"], str)

    def test_accents_survive(self):
        assert _emit(msg="Entraînement démarré")["message"] == "Entraînement démarré"

    def test_exceptions_are_captured(self):
        try:
            raise ValueError("boum")
        except ValueError:
            import sys
            rec = _record("échec", level=logging.ERROR)
            rec.exc_info = sys.exc_info()
            payload = json.loads(JsonFormatter().format(rec))
        assert "ValueError: boum" in payload["exception"]


# ─────────────────────────────────────────────────────────────────────────────
#  Console — non-régression du fichier
# ─────────────────────────────────────────────────────────────────────────────
class TestColorFormatterDoesNotLeak:
    def test_console_colour_never_reaches_the_record(self):
        """Régression réelle : les rotations archivées de bot.log contiennent
        ``[[32mINFO[0m]`` parce que le formatter console mutait le record
        AVANT que le handler fichier ne le formate à son tour."""
        from app.core.logger import _ColorFormatter
        rec = _record("msg")
        _ColorFormatter("%(levelname)s %(message)s").format(rec)
        assert rec.levelname == "INFO", "le record a été muté par la console"
        assert "\033" not in JsonFormatter().format(rec)


# ─────────────────────────────────────────────────────────────────────────────
#  Middleware
# ─────────────────────────────────────────────────────────────────────────────
class TestMiddleware:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from app.api.main import app
        return TestClient(app)

    def test_response_carries_an_id(self, client):
        from app.api.middleware import CORRELATION_HEADER
        r = client.get("/health")
        assert len(r.headers[CORRELATION_HEADER]) == 12

    def test_a_client_supplied_id_is_reused(self, client):
        """Permet de suivre une trace à travers nginx et le bot."""
        from app.api.middleware import CORRELATION_HEADER
        r = client.get("/health", headers={CORRELATION_HEADER: "trace-abc.123"})
        assert r.headers[CORRELATION_HEADER] == "trace-abc.123"

    @pytest.mark.parametrize("hostile,expected_absent", [
        ("bad\nINJECTED line", "\n"),
        ("a" * 500, "a" * 100),
        ("../../etc/passwd", "/"),
    ])
    def test_a_hostile_id_is_sanitised(self, client, hostile, expected_absent):
        """La valeur atterrit dans chaque ligne de log : sans assainissement,
        un client écrit des entrées entières dans le fichier du serveur."""
        from app.api.middleware import CORRELATION_HEADER
        r = client.get("/health", headers={CORRELATION_HEADER: hostile})
        got = r.headers[CORRELATION_HEADER]
        assert expected_absent not in got
        assert len(got) <= 64
