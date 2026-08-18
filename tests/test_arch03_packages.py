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


def test_legacy_import_paths_still_work():
    from app.core.risk_ledger import RiskLedger
    from app.core.smc import analyze as analyze_facade
    from app.core.smc_structure import analyze
    assert RiskLedger is not None
    assert analyze is analyze_facade or callable(analyze)
