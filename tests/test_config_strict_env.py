"""S0-04 — ``${VAR}`` référencée mais absente de l'environnement.

En mode live (paper_mode=false), démarrer avec des identifiants vides mène
à des échecs d'authentification silencieux côté exchange. load_config doit
lever une ValueError dans ce cas, sauf override explicite
``config.strict_env: false``. En mode paper, seul un WARNING est émis
(comportement non bloquant préservé).
"""
import logging

import pytest
import yaml

from app.core.config import load_config


def _write_cfg(tmp_path, paper=True, api_key="${MISSING_API_KEY}", strict_env=None):
    cfg = {
        "exchange": {"name": "okx", "api_key": api_key},
        "trading": {"timeframe": "1h", "paper_mode": paper,
                    "capital": 1000, "risk_per_trade": 0.01},
        "database": {"url": f"sqlite:///{tmp_path}/trades.db"},
        "web": {"host": "127.0.0.1", "api_key": ""},
    }
    if strict_env is not None:
        cfg["config"] = {"strict_env": strict_env}
    p = tmp_path / "config.yaml"
    yaml.safe_dump(cfg, p.open("w"))
    (tmp_path / "strategies").mkdir(exist_ok=True)
    return str(p)


def test_missing_env_var_blocks_in_live_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    path = _write_cfg(tmp_path, paper=False)
    with pytest.raises(ValueError, match=r"MISSING_API_KEY"):
        load_config(path)


def test_missing_env_var_warns_only_in_paper_mode(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    path = _write_cfg(tmp_path, paper=True)
    with caplog.at_level(logging.WARNING):
        cfg = load_config(path)
    assert isinstance(cfg, dict)
    assert any("MISSING_API_KEY" in r.message for r in caplog.records)


def test_missing_env_var_in_live_mode_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    path = _write_cfg(tmp_path, paper=False, strict_env=False)
    assert isinstance(load_config(path), dict)


def test_defined_env_var_never_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("MISSING_API_KEY", "real-key")
    path = _write_cfg(tmp_path, paper=False)
    cfg = load_config(path)
    assert cfg["exchange"]["api_key"] == "real-key"
