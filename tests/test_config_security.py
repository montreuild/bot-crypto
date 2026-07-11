"""OPS-02 / OPS-04 — garde-fous de sécurité au chargement de la config.

- OPS-02 : ``web.host=0.0.0.0`` (ou ``::``) SANS ``web.api_key`` doit BLOQUER
  le chargement (ValueError), sauf override explicite ``ALLOW_INSECURE_WEB=1``.
- OPS-04 : ``paper_mode=false`` sans aucun canal de notification externe émet
  une alerte CRITICAL (non bloquante).
"""
import logging

import pytest
import yaml

from app.core.config import load_config


def _write_cfg(tmp_path, host="0.0.0.0", api_key="", paper=True, notif=None):
    cfg = {
        "exchange": {"name": "okx"},
        "trading": {"timeframe": "1h", "paper_mode": paper,
                    "capital": 1000, "risk_per_trade": 0.01},
        "database": {"url": f"sqlite:///{tmp_path}/trades.db"},
        "web": {"host": host, "api_key": api_key},
    }
    if notif is not None:
        cfg["notifications"] = notif
    p = tmp_path / "config.yaml"
    yaml.safe_dump(cfg, p.open("w"))
    (tmp_path / "strategies").mkdir(exist_ok=True)
    return str(p)


def test_open_host_without_api_key_is_blocking(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_WEB", raising=False)
    path = _write_cfg(tmp_path, host="0.0.0.0", api_key="")
    with pytest.raises(ValueError, match="web.api_key"):
        load_config(path)


def test_open_host_with_override_env_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_WEB", "1")
    path = _write_cfg(tmp_path, host="0.0.0.0", api_key="")
    assert isinstance(load_config(path), dict)


def test_open_host_with_api_key_loads(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_WEB", raising=False)
    path = _write_cfg(tmp_path, host="0.0.0.0", api_key="s3cret")
    assert isinstance(load_config(path), dict)


def test_localhost_without_api_key_loads(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_WEB", raising=False)
    path = _write_cfg(tmp_path, host="127.0.0.1", api_key="")
    assert isinstance(load_config(path), dict)


def test_live_without_notifications_logs_critical(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_WEB", raising=False)
    path = _write_cfg(tmp_path, host="127.0.0.1", paper=False,
                      notif={"telegram_enabled": False, "email_enabled": False})
    with caplog.at_level(logging.CRITICAL):
        load_config(path)
    assert any("canal de notification" in r.message for r in caplog.records)


def test_live_with_channel_no_critical(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_WEB", raising=False)
    path = _write_cfg(tmp_path, host="127.0.0.1", paper=False,
                      notif={"telegram_enabled": True})
    with caplog.at_level(logging.CRITICAL):
        load_config(path)
    assert not any("canal de notification" in r.message for r in caplog.records)
