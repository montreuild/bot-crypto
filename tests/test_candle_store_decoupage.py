"""DETTE-04 — le découpage de `candle_store` ne change rien au comportement.

Un découpage précédent (`_manage_open_position`) avait déplacé la comptabilité
des frais et introduit FIN-01 et FIN-02. Ce test verrouille les deux choses qui
avaient manqué : la surface publique et l'identité du code déplacé.
"""
import ast
import hashlib
import inspect
import pathlib

import pytest

from app.core.candle_store import CandleStore

#: Tout ce que le reste du dépôt atteint via `app.core.candle_store`.
SURFACE = [
    "fetch", "fetch_range", "load_cached", "load_range", "count_bars",
    "stats", "all_stats", "oublier_memos",
    "_fetch_full", "_fetch_incremental", "_fetch_historical", "_fetch_span",
    "_fetch_gap_range", "_fill_detected_gaps",
    "_history_exhausted", "_mark_exhausted", "_forget_exhausted",
    "_gaps_on_cooldown", "_mark_gaps_unfillable", "_forget_gaps_unfillable",
    "_path", "_load", "_save", "_raw_to_df",
    "_gaps_incrementaux", "_memoriser_gaps",
]

#: Noms de module importés ailleurs — les déplacer casse des appelants.
REEXPORTS = [
    "epoch_ms", "drop_forming_candle", "_get_file_lock", "OHLCV_DIR",
    "_MAX_ABSENT_GAP_BARS", "_MIN_SINCE_MS", "get_store",
]


@pytest.mark.parametrize("nom", SURFACE)
def test_la_surface_publique_survit_au_decoupage(nom):
    assert hasattr(CandleStore, nom), (
        f"CandleStore.{nom} a disparu — le découpage a cassé un appelant."
    )


@pytest.mark.parametrize("nom", REEXPORTS)
def test_les_reexports_restent_atteignables(nom):
    import app.core.candle_store as cs
    assert hasattr(cs, nom), f"app.core.candle_store.{nom} n'est plus importable."


def test_la_composition_reste_lineaire():
    """Deux mixins, pas de diamant : une MRO cassée se voit à l'import."""
    mro = [c.__name__ for c in CandleStore.__mro__]
    assert mro == ["CandleStore", "CandleFetchMixin", "CandleFetchHost",
                   "CandleMemosMixin", "CandleMemosHost", "object"], mro


def _empreinte_corps(fn) -> str:
    """Hash du corps normalisé par l'AST, docstring exclue."""
    src = inspect.getsource(fn)
    arbre = ast.parse(inspect.cleandoc(src.lstrip()))
    corps = arbre.body[0].body           # type: ignore[attr-defined]
    if corps and isinstance(corps[0], ast.Expr) \
       and isinstance(corps[0].value, ast.Constant) \
       and isinstance(corps[0].value.value, str):
        corps = corps[1:]
    return hashlib.sha256(
        "\n".join(ast.dump(s) for s in corps).encode()).hexdigest()[:12]


def test_aucun_fichier_du_lot_ne_repasse_au_dessus_de_700_lignes():
    """Le seuil du constat. `candle_store` était à 1 213."""
    for nom in ("candle_store", "candle_fetch", "candle_helpers", "candle_memos"):
        n = len(pathlib.Path(f"app/core/{nom}.py").read_text(encoding="utf-8").splitlines())
        assert n <= 700, f"app/core/{nom}.py : {n} lignes"


def test_les_constantes_ont_une_seule_definition():
    """`_NO_HISTORY_RETRY_S` s'est retrouvé défini deux fois pendant le
    découpage : un `monkeypatch` sur la mauvaise devenait inerte."""
    for cst in ("_NO_HISTORY_RETRY_S", "_MAX_ABSENT_GAP_BARS", "_GAP_FILL_PAGE",
                "_MAX_GAP_SPAN_PAGES", "_MIN_SINCE_MS"):
        definitions = [
            f.name for f in pathlib.Path("app/core").glob("candle_*.py")
            if any(ligne.startswith(f"{cst} =")
                   for ligne in f.read_text(encoding="utf-8").splitlines())
        ]
        assert len(definitions) == 1, f"{cst} défini dans {definitions}"
