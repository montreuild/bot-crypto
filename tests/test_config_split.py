"""S11 — découpage de la configuration par responsabilité.

`config.yaml` ne porte plus que le sommaire (`include:`) ; chaque fichier de
`config/` porte une brique. Ce qui doit rester vrai :

1. la config effective est **identique** à la version monolithique ;
2. une section ne vit que dans un fichier — sinon l'ordre de chargement
   déciderait en silence laquelle s'applique ;
3. les écritures de l'UI (budgets, forçages, params) atterrissent dans le
   fichier **propriétaire** de la section, pas dans le sommaire ;
4. une config monolithique reste chargeable (rétro-compatibilité).
"""
import textwrap

import pytest
import yaml

from app.core.config import _load_and_merge
from app.core.yaml_io import load_config_documents, update_config_yaml


def _write(root, name, payload):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


@pytest.fixture
def split_cfg(tmp_path):
    _write(tmp_path, "config.yaml", {"include": ["config/a.yaml", "config/b.yaml"]})
    _write(tmp_path, "config/a.yaml", {"trading": {"capital": 1000, "risk_per_trade": 0.01}})
    _write(tmp_path, "config/b.yaml", {"lifecycle": {"force_active": []},
                                       "database": {"url": "sqlite:///t.db"}})
    return tmp_path


# ── 1. Fusion ───────────────────────────────────────────────────────────────

class TestMerge:
    def test_sections_are_merged_from_every_file(self, split_cfg):
        cfg = _load_and_merge(str(split_cfg / "config.yaml"))
        assert set(cfg) == {"trading", "lifecycle", "database"}
        assert cfg["trading"]["capital"] == 1000
        assert cfg["database"]["url"] == "sqlite:///t.db"

    def test_include_key_is_not_a_config_section(self, split_cfg):
        assert "include" not in _load_and_merge(str(split_cfg / "config.yaml"))

    def test_monolithic_config_still_loads(self, tmp_path):
        _write(tmp_path, "config.yaml", {"trading": {"capital": 500}})
        assert _load_and_merge(str(tmp_path / "config.yaml"))["trading"]["capital"] == 500

    def test_duplicate_section_is_refused(self, tmp_path):
        """L'ambiguïté la plus coûteuse : deux fichiers, même section, et c'est
        l'ordre de lecture qui tranche sans le dire."""
        _write(tmp_path, "config.yaml", {"include": ["config/a.yaml"],
                                         "trading": {"capital": 1}})
        _write(tmp_path, "config/a.yaml", {"trading": {"capital": 2}})
        with pytest.raises(ValueError, match="'trading'"):
            _load_and_merge(str(tmp_path / "config.yaml"))

    def test_missing_include_is_refused(self, tmp_path):
        """Une brique absente ferait tourner le bot sur des valeurs par défaut :
        mieux vaut ne pas démarrer."""
        _write(tmp_path, "config.yaml", {"include": ["config/fantome.yaml"]})
        with pytest.raises(FileNotFoundError, match="fantome"):
            _load_and_merge(str(tmp_path / "config.yaml"))


# ── 2. Écritures routées vers le bon fichier ────────────────────────────────

class TestWriteRouting:
    def test_update_lands_in_the_owning_file(self, split_cfg):
        root = str(split_cfg / "config.yaml")

        def _set(cfg):
            cfg["lifecycle"]["force_active"] = ["tvr_trend::1h::BTC/USDC"]

        update_config_yaml(_set, path=root)

        b = yaml.safe_load((split_cfg / "config/b.yaml").read_text(encoding="utf-8"))
        assert b["lifecycle"]["force_active"] == ["tvr_trend::1h::BTC/USDC"]
        # Le sommaire ne reçoit RIEN : sinon la section vivrait dans deux
        # fichiers et le chargement suivant échouerait.
        root_doc = yaml.safe_load((split_cfg / "config.yaml").read_text(encoding="utf-8"))
        assert "lifecycle" not in root_doc

    def test_untouched_files_are_not_rewritten(self, split_cfg):
        root = str(split_cfg / "config.yaml")
        a_before = (split_cfg / "config/a.yaml").read_text(encoding="utf-8")

        update_config_yaml(lambda cfg: cfg["lifecycle"].update({"force_active": ["x"]}),
                           path=root)

        assert (split_cfg / "config/a.yaml").read_text(encoding="utf-8") == a_before

    def test_removing_a_key_is_persisted(self, split_cfg):
        """Cas réel : la route force-active supprime `manual_active` (D6)."""
        _write(split_cfg, "config/b.yaml",
               {"lifecycle": {"force_active": [], "manual_active": ["vieux::1h"]},
                "database": {"url": "sqlite:///t.db"}})
        root = str(split_cfg / "config.yaml")

        update_config_yaml(lambda cfg: cfg["lifecycle"].pop("manual_active", None),
                           path=root)

        b = yaml.safe_load((split_cfg / "config/b.yaml").read_text(encoding="utf-8"))
        assert "manual_active" not in b["lifecycle"]

    def test_a_new_section_goes_to_the_root_file(self, split_cfg):
        root = str(split_cfg / "config.yaml")
        update_config_yaml(lambda cfg: cfg.update({"inedite": {"k": 1}}), path=root)
        root_doc = yaml.safe_load((split_cfg / "config.yaml").read_text(encoding="utf-8"))
        assert root_doc["inedite"] == {"k": 1}

    def test_documents_expose_their_owner(self, split_cfg):
        _, merged, owner = load_config_documents(str(split_cfg / "config.yaml"))
        assert owner["trading"].endswith("a.yaml")
        assert owner["lifecycle"].endswith("b.yaml")
        assert merged["trading"]["capital"] == 1000


# ── 3. Le vrai fichier du dépôt ─────────────────────────────────────────────

def test_repository_config_is_split_and_complete():
    """Garde-fou sur la config livrée : chaque brique attendue est présente et
    provient bien de son fichier dédié."""
    _, _, owner = load_config_documents("config.yaml")
    attendu = {
        "venues": "venues.yaml", "exchange": "venues.yaml",
        "trading": "risk.yaml", "risk": "risk.yaml", "live": "risk.yaml",
        "scanner": "data.yaml", "providers": "data.yaml",
        "lifecycle": "lifecycle.yaml",
        "web": "ops.yaml", "logging": "ops.yaml", "database": "ops.yaml",
    }
    for section, fichier in attendu.items():
        assert section in owner, f"section '{section}' absente de la config"
        assert owner[section].endswith(fichier), (
            f"'{section}' devrait vivre dans config/{fichier}, "
            f"trouvé dans {owner[section]}")


def test_comments_survive_a_write(tmp_path):
    """Le découpage n'a d'intérêt que si les commentaires — qui portent le
    « pourquoi » de chaque réglage — survivent aux écritures de l'UI."""
    (tmp_path / "config.yaml").write_text("include:\n  - config/a.yaml\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/a.yaml").write_text(textwrap.dedent("""\
        # Pourquoi ce plancher existe : ne pas descendre sous 2 bots actifs.
        lifecycle:
          min_active_bots: 2
          force_active: []
        """), encoding="utf-8")

    update_config_yaml(lambda cfg: cfg["lifecycle"].update({"force_active": ["x::1h"]}),
                       path=str(tmp_path / "config.yaml"))

    texte = (tmp_path / "config/a.yaml").read_text(encoding="utf-8")
    assert "Pourquoi ce plancher existe" in texte
    assert "x::1h" in texte
