"""Throttling des logs répétitifs (bruit d'entraînement ML)."""
import logging

from app.core.log_throttle import log_throttled, reset_throttle


def test_log_throttled_emits_once_per_window(caplog):
    reset_throttle()
    logger = logging.getLogger("test.throttle")
    with caplog.at_level(logging.DEBUG, logger="test.throttle"):
        for _ in range(50):
            log_throttled(logger, "k", "mono-classe", level=logging.WARNING, ttl=3600)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warns) == 1            # une seule alerte malgré 50 appels
    assert len(debugs) == 49          # les répétitions partent en DEBUG


def test_log_throttled_distinct_keys(caplog):
    reset_throttle()
    logger = logging.getLogger("test.throttle2")
    with caplog.at_level(logging.WARNING, logger="test.throttle2"):
        log_throttled(logger, "a", "x", ttl=3600)
        log_throttled(logger, "b", "y", ttl=3600)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_log_throttled_reemits_after_ttl(caplog):
    reset_throttle()
    logger = logging.getLogger("test.throttle3")
    with caplog.at_level(logging.WARNING, logger="test.throttle3"):
        log_throttled(logger, "k", "x", ttl=0)   # ttl=0 → toujours réémis
        log_throttled(logger, "k", "x", ttl=0)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
