"""Throttling de logs répétitifs.

Certains messages d'entraînement ML (labels mono-classe, fold ignoré…) sont émis
à chaque ré-entraînement walk-forward × chaque essai d'optimisation, ce qui noie
les logs. ``log_throttled`` n'émet le message au niveau demandé qu'une fois par
fenêtre ``ttl`` et par ``key`` ; les répétitions partent en DEBUG (toujours
disponibles en cas de besoin, mais invisibles au niveau INFO/WARNING par défaut).
"""
import logging
import threading
import time

_lock = threading.Lock()
_last: dict = {}


def log_throttled(logger: logging.Logger, key: str, msg: str, *,
                  level: int = logging.WARNING, ttl: float = 1800.0) -> None:
    """Log ``msg`` au plus une fois / ``ttl`` s pour ``key`` ; sinon en DEBUG."""
    now = time.time()
    with _lock:
        emit = (now - _last.get(key, 0.0)) >= ttl
        if emit:
            _last[key] = now
    logger.log(level if emit else logging.DEBUG, msg)


def reset_throttle() -> None:
    """Vide l'état (tests)."""
    with _lock:
        _last.clear()
