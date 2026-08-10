"""Invariant : aucune route de `app/api/routes/` ne doit être ouverte.

Régression P0-1 : `GET /api/risk`, `GET /api/risk/diagnostics` et
`GET /api/ws/status` étaient servies sans `verify_api_key`. La première
exposait le capital, les enveloppes par venue/symbole/slot et le risque engagé
— à un visiteur non authentifié. L'asymétrie était frappante : `POST
/api/risk/envelopes` (l'écriture) était protégée, pas la lecture.

Ce test n'énumère pas les trois routes fautives, il vérifie la règle : une
route ajoutée demain sans `dependencies=[Depends(verify_api_key)]` fait échouer
la suite. C'est la seule forme qui protège durablement.

Les routes montées à la RACINE (`/health`, `/metrics`) sont volontairement
ouvertes — elles vivent dans `app/api/main.py`, hors du périmètre de ce test,
et sont restreintes côté nginx.
"""
import pathlib
import re

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"

# Décorateur de route : @router.get("/api/...", ...) sur une ou plusieurs lignes.
_ROUTE_RE = re.compile(r'@router\.(get|post|delete|put|patch)\(\s*"([^"]+)"([^)]*)\)', re.S)


def _routes_sans_auth() -> list[str]:
    manquantes = []
    for fichier in sorted(ROUTES_DIR.glob("*.py")):
        src = fichier.read_text(encoding="utf-8")
        for m in _ROUTE_RE.finditer(src):
            verbe, chemin, reste = m.group(1), m.group(2), m.group(3)
            if "verify_api_key" not in reste:
                manquantes.append(f"{fichier.name} :: {verbe.upper()} {chemin}")
    return manquantes


def test_aucune_route_api_sans_verify_api_key():
    manquantes = _routes_sans_auth()
    assert not manquantes, (
        "Routes sans `dependencies=[Depends(verify_api_key)]` :\n  "
        + "\n  ".join(manquantes)
        + "\n\nUne route de `app/api/routes/` sert des données du bot : elle doit "
          "être authentifiée. Si l'ouverture est intentionnelle (sonde de santé), "
          "monter la route dans `app/api/main.py` plutôt qu'ici."
    )


def test_le_detecteur_verrait_une_route_non_protegee():
    """Garde-fou du garde-fou : un test d'invariant qui ne détecte rien ne
    protège rien. On vérifie que la regex reconnaît bien une route ouverte."""
    echantillon = '@router.get("/api/exemple")\ndef exemple(): ...'
    m = _ROUTE_RE.search(echantillon)
    assert m is not None and "verify_api_key" not in m.group(3)
