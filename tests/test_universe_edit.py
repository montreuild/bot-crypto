"""Édition d'un univers — l'insertion doit être CHIRURGICALE.

`data/universe/sbf120.yaml` est tenu à la main : alignement en colonnes,
commentaires de section au milieu de la liste, en-tête documentant la révision
trimestrielle de l'indice. Un aller-retour `ruamel` y réécrivait les 122 lignes
de `members:` et perdait le commentaire « ── Reste du SBF 120 ── » — un diff de
122 lignes dont une seule était voulue, sur un fichier qu'on relit à chaque
revue d'indice.

D'où l'insertion textuelle, et d'où ces tests : ils vérifient que le fichier ne
bouge QUE d'une ligne, fins de ligne comprises.
"""
import pytest

from app.core.universe import (
    add_member,
    clear_cache,
    remove_member,
    universe_symbols,
)

_FILE = """\
# En-tête documenté
#   deuxième ligne

name: Test
venue: test-venue

members:
  # ── Groupe A ────────────────────────────
  - {symbol: AAA.PA,    name: Alpha}
  - {symbol: BBB.PA,    name: Beta}

  # ── Groupe B ────────────────────────────
  - {symbol: CCC.PA,    name: Gamma}
"""


@pytest.fixture
def uni(tmp_path, monkeypatch):
    """Univers jetable, écrit en CRLF comme celui du dépôt."""
    monkeypatch.setattr("app.core.universe.UNIVERSE_DIR", str(tmp_path))
    path = tmp_path / "essai.yaml"
    path.write_bytes(_FILE.replace("\n", "\r\n").encode("utf-8"))
    clear_cache()
    yield path
    clear_cache()


def _raw(path):
    return path.read_bytes()


class TestSurgicalInsert:
    def test_only_one_line_is_added(self, uni):
        before = _raw(uni).decode("utf-8").splitlines()
        add_member("essai", "DDD.PA", "Delta")
        after = _raw(uni).decode("utf-8").splitlines()
        assert len(after) == len(before) + 1
        # Toutes les lignes d'origine sont intactes, dans le même ordre.
        assert after[:len(before)] == before
        assert after[-1].strip() == "- {symbol: DDD.PA, name: Delta}"

    def test_comments_and_alignment_survive(self, uni):
        add_member("essai", "DDD.PA", "Delta")
        text = _raw(uni).decode("utf-8")
        assert "# ── Groupe A ─" in text, "commentaire de section perdu"
        assert "# ── Groupe B ─" in text
        assert "# En-tête documenté" in text
        assert "- {symbol: AAA.PA,    name: Alpha}" in text, "alignement perdu"

    def test_crlf_is_preserved(self, uni):
        """Le fichier du dépôt est en CRLF. Écrire en LF ferait un diff de
        168 lignes au lieu d'une — le piège qui a motivé ce test."""
        add_member("essai", "DDD.PA", "Delta")
        raw = _raw(uni)
        assert raw.count(b"\r\n") == raw.count(b"\n"), "ligne(s) en LF introduites"

    def test_the_symbol_is_visible_immediately(self, uni):
        """Le module mémoïse les univers pour la boucle live : sans purge du
        cache, l'ajout resterait invisible jusqu'au redémarrage."""
        add_member("essai", "DDD.PA", "Delta")
        assert "DDD.PA" in universe_symbols("essai")

    def test_a_duplicate_is_refused(self, uni):
        with pytest.raises(ValueError, match="déjà"):
            add_member("essai", "AAA.PA")

    def test_extra_fields_are_written(self, uni):
        add_member("essai", "DDD.PA", "Delta", provider_symbol="DDD.XPAR")
        assert "provider_symbol: DDD.XPAR" in _raw(uni).decode("utf-8")


class TestRemoval:
    def test_only_the_target_line_goes(self, uni):
        before = _raw(uni).decode("utf-8").splitlines()
        remove_member("essai", "BBB.PA")
        after = _raw(uni).decode("utf-8").splitlines()
        assert len(after) == len(before) - 1
        assert "BBB.PA" not in "\n".join(after)
        assert "# ── Groupe A ─" in "\n".join(after)
        assert "- {symbol: AAA.PA,    name: Alpha}" in "\n".join(after)

    def test_an_absent_symbol_raises(self, uni):
        with pytest.raises(ValueError, match="absent"):
            remove_member("essai", "ZZZ.PA")

    def test_round_trip_leaves_the_membership_unchanged(self, uni):
        before = universe_symbols("essai")
        add_member("essai", "DDD.PA", "Delta")
        remove_member("essai", "DDD.PA")
        assert universe_symbols("essai") == before


def test_a_malformed_block_is_refused_not_guessed(tmp_path, monkeypatch):
    """Sans bloc `members:` reconnaissable, échouer vaut mieux que réécrire à
    l'aveugle un fichier qu'on n'a pas compris."""
    monkeypatch.setattr("app.core.universe.UNIVERSE_DIR", str(tmp_path))
    (tmp_path / "vide.yaml").write_text("name: Vide\nmembers: []\n", encoding="utf-8")
    clear_cache()
    with pytest.raises(ValueError):
        add_member("vide", "AAA.PA")
