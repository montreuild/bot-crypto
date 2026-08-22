"""DETTE-04 — le seuil de 700 lignes, mesuré au lieu d'être espéré.

Le constat listait onze fichiers. Il en exemptait explicitement ceux
d'`app/strategies` : « une stratégie est une unité cohérente, et sa longueur
vient de sa logique métier ». Ce test verrouille le reste, et rend l'exemption
visible plutôt que tacite — sans quoi la liste se reconstitue en silence,
comme elle l'a déjà fait (`candle_store` 1 087 → 1 213 entre deux revues).
"""
import pathlib

import pytest

SEUIL = 700

#: Unités cohérentes par nature, exemptées avec leur raison.
EXEMPTES = {
    "app/strategies": "une stratégie est une unité cohérente (constat DETTE-04)",
    "app/api/schemas.py": "contrat de sérialisation — un modèle par réponse",
}


def _exempte(chemin: str) -> str | None:
    for prefixe, raison in EXEMPTES.items():
        if chemin == prefixe or chemin.startswith(prefixe + "/"):
            return raison
    return None


def _fichiers_longs():
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        chemin = f.as_posix()
        n = len(f.read_text(encoding="utf-8").splitlines())
        if n > SEUIL and _exempte(chemin) is None:
            yield chemin, n


def test_aucun_module_non_exempte_ne_depasse_le_seuil():
    longs = list(_fichiers_longs())
    assert not longs, (
        "modules au-delà de "
        f"{SEUIL} lignes : {longs}. Découper, ou exempter explicitement dans "
        "EXEMPTES avec la raison."
    )


@pytest.mark.parametrize("chemin", [
    "app/core/candle_store.py",
    "app/engine/auto_optimizer.py",
    "app/engine/position_lifecycle.py",
    "app/engine/backtest.py",
    "app/engine/optimizer_search.py",
    "app/api/services/scanner_service.py",
])
def test_les_fichiers_du_constat_restent_decoupes(chemin):
    """Les six modules que la revue désignait nommément."""
    n = len(pathlib.Path(chemin).read_text(encoding="utf-8").splitlines())
    assert n <= SEUIL, f"{chemin} : {n} lignes"


def test_l_exemption_des_strategies_reste_justifiee():
    """Un test vacant n'aurait aucune valeur : l'exemption doit servir."""
    longues = [f.as_posix() for f in pathlib.Path("app/strategies").rglob("*.py")
               if len(f.read_text(encoding="utf-8").splitlines()) > SEUIL]
    assert longues, (
        "aucune stratégie ne dépasse le seuil — l'exemption ne protège plus "
        "rien et peut être retirée."
    )
