"""Cohérence entre les redirections héritées de FastAPI et celles de Next.

`app/api/main.py` (HTML_ROUTES_TO_REDIRECT) et `frontend/next.config.mjs`
(bloc `redirects()`) décrivent la même bascule strangler fig depuis deux
fichiers, dans deux langages. Rien n'empêchait jusqu'ici qu'ils divergent :
une redirection posée côté Next sans être répercutée ici produisait un double
308 silencieux (FastAPI → ancien chemin Next → cible réelle), invisible en
test comme à l'œil.

Ce module lit `next.config.mjs` et vérifie que le backend vise directement la
cible finale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.main import HTML_ROUTES_TO_REDIRECT

NEXT_CONFIG = Path(__file__).resolve().parents[1] / "frontend" / "next.config.mjs"

# `{ source: '/x', destination: '/y', permanent: true }` — les clés peuvent
# être dans n'importe quel ordre, on capture les deux chaînes séparément.
_REDIRECT_RE = re.compile(
    r"\{\s*source:\s*'(?P<source>[^']+)'\s*,\s*destination:\s*'(?P<destination>[^']+)'",
)


def _next_redirects() -> dict[str, str]:
    """Extrait le mapping source → destination de `next.config.mjs`."""
    raw = NEXT_CONFIG.read_text(encoding="utf-8")
    # On ne lit que le corps de `redirects()`, pour ne pas confondre avec les
    # `rewrites()` (mêmes clés, sémantique différente : pas de 308). La borne
    # de fin est le `];` qui ferme le tableau — surtout pas le premier `},`
    # venu, qui ne ferme que la première entrée.
    start = raw.index("return [", raw.index("async redirects()"))
    end = raw.index("];", start)
    return {m["source"]: m["destination"] for m in _REDIRECT_RE.finditer(raw[start:end])}


def test_next_config_is_parseable():
    """Garde-fou : si le format du bloc change, les tests suivants deviennent
    vides et ne testent plus rien en silence."""
    redirects = _next_redirects()
    assert len(redirects) >= 14, (
        f"Seulement {len(redirects)} redirections extraites de next.config.mjs — "
        "le format du bloc a probablement changé, la regex est à mettre à jour."
    )


@pytest.mark.parametrize("source", sorted(_next_redirects()))
def test_backend_targets_final_destination(source: str):
    """Toute route redirigée côté Next et servie par FastAPI doit viser
    directement la destination finale, pas l'ancien chemin."""
    if source not in HTML_ROUTES_TO_REDIRECT:
        pytest.skip(f"{source} n'est pas une route HTML héritée servie par FastAPI")

    expected = _next_redirects()[source]
    actual = HTML_ROUTES_TO_REDIRECT[source]
    assert actual == expected, (
        f"{source} : le backend redirige vers {actual!r} alors que Next redirige "
        f"vers {expected!r}. Sans correction, un GET {source} coûte deux 308."
    )


V2_ALIASES = {"/portfolio-v2": "/portfolio",
              "/bots-v2": "/bots",
              "/settings-v2": "/settings"}


@pytest.mark.parametrize("alias,cible", sorted(V2_ALIASES.items()))
def test_v2_aliases_redirect_to_the_canonical_page(alias, cible):
    """S11 — le suffixe `-v2` datait de la coexistence avec les pages Jinja2 ;
    la migration finie, il ne veut plus rien dire. Les anciennes URLs ont vécu
    en prod (favoris, 308 déjà en cache navigateur) : elles doivent survivre en
    redirection, dans les DEUX tables."""
    assert _next_redirects().get(alias) == cible
    assert HTML_ROUTES_TO_REDIRECT.get(alias) == cible


def test_canonical_pages_are_not_redirected_away():
    """Le sens de la redirection est bien inversé : `/bots` EST la page, pas un
    alias. L'oublier créerait une boucle `/bots → /bots-v2 → /bots`."""
    next_redirects = _next_redirects()
    for cible in V2_ALIASES.values():
        assert cible not in next_redirects, (
            f"{cible} est redirigée alors qu'elle porte la page : boucle 308")
        assert HTML_ROUTES_TO_REDIRECT.get(cible) == cible


def test_no_v2_path_remains_in_the_frontend_sources():
    """Aucun lien interne ne doit encore pointer sur une URL `-v2` : ça
    fonctionnerait (308) mais ferait payer un aller-retour à chaque navigation,
    et la migration resterait visiblement inachevée."""
    src = NEXT_CONFIG.parent / "src"
    coupables = []
    for f in list(src.rglob("*.ts")) + list(src.rglob("*.tsx")):
        for i, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "-v2" in ligne:
                coupables.append(f"{f.relative_to(src)}:{i}")
    assert not coupables, f"URLs -v2 encore référencées : {coupables}"


def test_no_backend_target_is_itself_redirected():
    """Aucune cible du backend ne doit être une source de redirection Next :
    ce serait précisément le double saut qu'on cherche à éliminer."""
    next_redirects = _next_redirects()
    # `/` est la racine, pas un alias : elle vise `/`, dont la redirection est
    # décidée par `frontend/src/app/page.tsx` et non par `next.config.mjs`.
    offenders = {
        route: target
        for route, target in HTML_ROUTES_TO_REDIRECT.items()
        if route != "/" and target.split("?")[0] in next_redirects
    }
    assert not offenders, (
        f"Ces routes visent une cible elle-même redirigée par Next : {offenders}"
    )
