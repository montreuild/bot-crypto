"""Jetons WebSocket éphémères — SEC-02.

Le navigateur n'envoie pas d'en-tête custom sur un ``WebSocket()``.
Le cookie HttpOnly reste le chemin nominal. Pour les clients qui ne
peuvent pas poser de cookie (script, sonde), un ``POST /api/ws/ticket``
authentifié délivre un jeton à usage unique, valable quelques secondes —
jamais la clé API permanente en query string.
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Dict

_TTL_SEC = 30.0
_lock = threading.Lock()
_tickets: Dict[str, float] = {}


def issue_ticket(ttl: float = _TTL_SEC) -> tuple[str, float]:
    token = secrets.token_urlsafe(24)
    exp = time.time() + float(ttl)
    with _lock:
        _purge_locked(time.time())
        _tickets[token] = exp
    return token, float(ttl)


def consume_ticket(token: str | None) -> bool:
    if not token or len(token) > 128:
        return False
    now = time.time()
    with _lock:
        _purge_locked(now)
        exp = _tickets.pop(token, None)
    return exp is not None and exp >= now


def _purge_locked(now: float) -> None:
    dead = [k for k, exp in _tickets.items() if exp < now]
    for k in dead:
        _tickets.pop(k, None)
