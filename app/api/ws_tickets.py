"""Jetons WebSocket éphémères — SEC-02.

Le navigateur n'envoie pas d'en-tête custom sur un ``WebSocket()``.
Le cookie HttpOnly reste le chemin nominal. Pour les clients qui ne
peuvent pas poser de cookie (script, sonde), un ``POST /api/ws/ticket``
authentifié délivre un jeton à usage unique, valable quelques secondes —
jamais la clé API permanente en query string.

⚠ SEC-01 — CONTRAINTE DE DÉPLOIEMENT : le registre ``_tickets`` vit en
mémoire, dans le processus. L'API doit donc tourner en **process unique**,
ce qui est le cas aujourd'hui (``cli.py --paper``, sans ``--workers`` ni
gunicorn — cf. ``Dockerfile`` et ``docker-compose.yml``).

Derrière plusieurs workers ou plusieurs conteneurs, un jeton émis par l'un
serait inconnu de l'autre : le handshake ``/ws`` répondrait 4403 de façon
**intermittente**, à une fréquence proportionnelle au nombre de workers — le
pire profil à diagnostiquer, d'autant que le frontend redemande un jeton à
chaque tentative de reconnexion. Avant de scaler horizontalement, déplacer ce
registre vers un support partagé (Redis, ou la base déjà présente).

``tests/test_sec_hardening.py`` vérifie que le lancement de l'API reste
mono-processus : c'est ce qui rend cette hypothèse tenable.
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Dict

_TTL_SEC = 30.0
#: Plafond du registre. À 30 s de TTL, un usage normal en tient quelques
#: dizaines ; ce plafond ne borne donc qu'un client emballé ou malveillant.
_MAX_TICKETS = 10_000
_lock = threading.Lock()
_tickets: Dict[str, float] = {}


def issue_ticket(ttl: float = _TTL_SEC) -> tuple[str, float]:
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        if len(_tickets) >= _MAX_TICKETS:
            _purge_locked(now)
        if len(_tickets) >= _MAX_TICKETS:
            # Tous encore valides : on évince le plus proche de l'expiration
            # plutôt que de refuser. Un jeton perdu coûte une reconnexion ;
            # un refus coupe le temps réel.
            _tickets.pop(min(_tickets, key=_tickets.__getitem__), None)
        _tickets[token] = now + float(ttl)
    return token, float(ttl)


def consume_ticket(token: str | None) -> bool:
    if not token or len(token) > 128:
        return False
    now = time.time()
    with _lock:
        # Pas de purge ici : elle parcourait tout le registre sous verrou à
        # CHAQUE handshake. Un jeton expiré est de toute façon refusé par le
        # test ci-dessous, et `issue_ticket` fait le ménage.
        exp = _tickets.pop(token, None)
    return exp is not None and exp >= now


def _purge_locked(now: float) -> None:
    dead = [k for k, exp in _tickets.items() if exp < now]
    for k in dead:
        _tickets.pop(k, None)


def _reset_for_tests() -> None:
    with _lock:
        _tickets.clear()
