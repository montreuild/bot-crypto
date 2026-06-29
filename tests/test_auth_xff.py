"""Sécurité (axe 7.1) — anti-spoofing de X-Forwarded-For et contrôle localhost.

Vérifie qu'un client distant ne peut pas se faire passer pour 127.0.0.1 via un
en-tête X-Forwarded-For falsifié quand aucune clé API n'est configurée.
"""
import asyncio
import types

import pytest
from fastapi import HTTPException

from app.api import state
from app.api.helpers import _extract_client_ip, verify_api_key


class _FakeReq:
    def __init__(self, peer, headers=None, cookies=None):
        self.client  = types.SimpleNamespace(host=peer)
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.method  = "GET"
        self.url     = types.SimpleNamespace(path="/api/test")


def test_xff_ignored_without_trusted_proxies(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    req = _FakeReq("203.0.113.5", headers={"x-forwarded-for": "127.0.0.1"})
    # Header falsifié ignoré → on retient l'IP réelle du pair TCP.
    assert _extract_client_ip(req) == "203.0.113.5"


def test_xff_honored_for_trusted_proxy(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1, 10.0.0.2")
    req = _FakeReq("10.0.0.1", headers={"x-forwarded-for": "198.51.100.9, 10.0.0.1"})
    # Connexion issue d'un proxy de confiance → on lit l'IP cliente du header.
    assert _extract_client_ip(req) == "198.51.100.9"


def test_verify_api_key_blocks_spoofed_localhost(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    monkeypatch.setattr(state, "cfg", {"web": {"api_key": ""}}, raising=False)
    req = _FakeReq("203.0.113.5", headers={"x-forwarded-for": "127.0.0.1"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(verify_api_key(req))
    assert exc.value.status_code == 403


def test_verify_api_key_allows_real_localhost(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    monkeypatch.setattr(state, "cfg", {"web": {"api_key": ""}}, raising=False)
    req = _FakeReq("127.0.0.1")
    # Pas d'exception attendue (accès local légitime, sans clé configurée).
    assert asyncio.run(verify_api_key(req)) is None
