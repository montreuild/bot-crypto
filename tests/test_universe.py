"""G2 — univers d'instruments statiques (app/core/universe.py)."""
import app.core.universe as universe_mod
from app.core.universe import (
    clear_cache,
    load_universe,
    resolve_universes,
    universe_members,
    universe_path,
    universe_symbols,
    universe_venue,
)


def _write(tmp_path, name: str, body: str, monkeypatch) -> None:
    """Redirige UNIVERSE_DIR vers tmp_path et y dépose un fichier."""
    monkeypatch.setattr(universe_mod, "UNIVERSE_DIR", str(tmp_path))
    (tmp_path / f"{name}.yaml").write_text(body, encoding="utf-8")
    clear_cache()


class TestLoading:
    def test_long_and_short_member_forms_coexist(self, tmp_path, monkeypatch):
        _write(tmp_path, "mix", """
name: Mix
venue: euronext-paper
members:
  - {symbol: AIR.PA, name: Airbus, sector: Aéronautique}
  - MC.PA
""", monkeypatch)
        members = universe_members("mix")
        assert [m["symbol"] for m in members] == ["AIR.PA", "MC.PA"]
        assert members[0]["name"] == "Airbus"
        assert members[0]["sector"] == "Aéronautique"
        assert members[1]["name"] == ""

    def test_duplicates_and_blanks_are_dropped(self, tmp_path, monkeypatch):
        _write(tmp_path, "dup", """
members:
  - AIR.PA
  - {symbol: AIR.PA}
  - {symbol: ""}
  - 42
  - MC.PA
""", monkeypatch)
        assert universe_symbols("dup") == ["AIR.PA", "MC.PA"]

    def test_venue_is_exposed(self, tmp_path, monkeypatch):
        _write(tmp_path, "v", "venue: euronext-paper\nmembers: [AIR.PA]\n", monkeypatch)
        assert universe_venue("v") == "euronext-paper"

    def test_missing_file_is_empty_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(universe_mod, "UNIVERSE_DIR", str(tmp_path))
        clear_cache()
        assert load_universe("inexistant") == {}
        assert universe_symbols("inexistant") == []

    def test_malformed_yaml_is_empty_not_fatal(self, tmp_path, monkeypatch):
        _write(tmp_path, "bad", "members: [AIR.PA\n  - oops:\n", monkeypatch)
        assert universe_symbols("bad") == []

    def test_non_dict_root_is_ignored(self, tmp_path, monkeypatch):
        _write(tmp_path, "list", "- AIR.PA\n- MC.PA\n", monkeypatch)
        assert universe_symbols("list") == []


class TestResolve:
    def test_concatenates_preserving_order_without_duplicates(self, tmp_path, monkeypatch):
        _write(tmp_path, "a", "members: [AIR.PA, MC.PA]\n", monkeypatch)
        (tmp_path / "b.yaml").write_text("members: [MC.PA, OR.PA]\n", encoding="utf-8")
        clear_cache()
        assert resolve_universes(["a", "b"]) == ["AIR.PA", "MC.PA", "OR.PA"]

    def test_accepts_a_bare_string(self, tmp_path, monkeypatch):
        _write(tmp_path, "a", "members: [AIR.PA]\n", monkeypatch)
        assert resolve_universes("a") == ["AIR.PA"]

    def test_empty_input_is_empty_output(self):
        assert resolve_universes(None) == []
        assert resolve_universes([]) == []

    def test_path_accepts_name_with_or_without_extension(self):
        assert universe_path("sbf120") == universe_path("sbf120.yaml")

    def test_path_traversal_is_stripped(self):
        """Un nom d'univers vient de la config : ne jamais sortir du dossier."""
        assert universe_path("../../etc/passwd").endswith("passwd.yaml")
        assert ".." not in universe_path("../../etc/passwd")


class TestShippedSBF120:
    """Le fichier livré doit être lisible et honnête sur son statut."""

    def test_loads_and_has_paris_tickers(self):
        clear_cache()
        data = load_universe("sbf120")
        symbols = universe_symbols("sbf120")
        assert data.get("asset_class") == "equity"
        assert data.get("calendar") == "XPAR"
        assert len(symbols) > 80
        assert all(s.endswith(".PA") for s in symbols), \
            "tous les membres doivent être des tickers Euronext Paris"
        assert len(set(symbols)) == len(symbols), "aucun doublon"

    def test_declares_itself_unverified(self):
        """Instantané écrit hors ligne : le flag doit rester explicite tant
        que scripts/check_universe.py n'a pas été passé."""
        assert load_universe("sbf120").get("verified") is False
