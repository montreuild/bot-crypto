"""ARCH-03 : les sous-paquets existent et le risque n'importe pas SMC."""
import ast
from pathlib import Path


def test_risk_package_does_not_import_smc():
    root = Path(__file__).resolve().parents[1] / "app" / "core" / "risk"
    offenders = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "smc" in alias.name:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if "smc" in node.module:
                    offenders.append(f"{path.name}: from {node.module}")
    assert offenders == []


def test_les_paquets_exposent_leurs_symboles():
    from app.core.risk.ledger import RiskLedger
    from app.core.smc import analyze as analyze_facade
    from app.core.smc.structure import analyze
    assert RiskLedger is not None
    assert analyze is analyze_facade or callable(analyze)


# ── ARCH-01 : les shims de compatibilité sont supprimés ─────────────────────
# `app/core/risk_gate.py` et consorts recopiaient le namespace du paquet
# (`_compat.copy_privates`), créant deux objets-module pour le même code. Un
# `monkeypatch` sur l'un laissait l'autre intact — un test aurait pu passer sans
# rien patcher. 90 imports ont été migrés vers les paquets.

_SHIMS = [f"risk_{m}" for m in ("curve", "diagnostics", "envelope", "gate",
                                "ledger", "notifier", "sizer", "state")]
_SHIMS += [f"smc_{m}" for m in ("geometry", "primitives", "quality", "sessions",
                                "state", "structure", "volume")]


def test_aucun_shim_ne_subsiste():
    core = Path(__file__).resolve().parents[1] / "app" / "core"
    presents = [n for n in _SHIMS if (core / f"{n}.py").exists()]
    assert presents == [], f"shims réapparus : {presents}"
    assert not (core / "_compat.py").exists(), "_compat n'a plus de raison d'être"


def test_plus_aucun_import_ne_passe_par_un_shim():
    racine = Path(__file__).resolve().parents[1]
    fautifs = []
    for dossier in ("app", "tests", "scripts"):
        for f in (racine / dossier).rglob("*.py"):
            texte = f.read_text(encoding="utf-8", errors="ignore")
            for shim in _SHIMS:
                if f"app.core.{shim}" in texte:
                    fautifs.append(f"{f.relative_to(racine)} → {shim}")
    assert fautifs == [], (
        "importer un shim recrée la double identité de module : "
        + ", ".join(fautifs)
    )


def test_app_core_smc_reste_un_module_distinct():
    """`app/core/smc.py` (façade) n'est pas un shim : la migration ne devait
    pas le confondre avec les `smc_*`."""
    from app.core import smc
    assert hasattr(smc, "analyze")
