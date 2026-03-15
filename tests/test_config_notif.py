"""
Tests unitaires — Config (variables d'env) & Notifier
"""
import pytest
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import _expand_env, load_config
from app.core.notifications import Notifier


# ── Tests _expand_env ────────────────────────────────────────────────────────

class TestExpandEnv:
    def test_simple_substitution(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        assert _expand_env("${MY_KEY}") == "secret123"

    def test_dollar_syntax(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        assert _expand_env("$MY_KEY") == "secret123"

    def test_missing_var_returns_empty(self):
        # Variable non définie → chaîne vide (pas d'erreur)
        result = _expand_env("${NONEXISTENT_VAR_12345}")
        assert result == ""

    def test_no_substitution_plain_string(self):
        assert _expand_env("binance") == "binance"

    def test_dict_recursive(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "abc")
        monkeypatch.setenv("SECRET", "xyz")
        d = _expand_env({"api_key": "${API_KEY}", "secret": "${SECRET}", "name": "binance"})
        assert d["api_key"] == "abc"
        assert d["secret"] == "xyz"
        assert d["name"] == "binance"

    def test_list_recursive(self, monkeypatch):
        monkeypatch.setenv("SYM1", "BTC/USDC")
        result = _expand_env(["${SYM1}", "ETH/USDC"])
        assert result[0] == "BTC/USDC"
        assert result[1] == "ETH/USDC"

    def test_non_string_untouched(self):
        assert _expand_env(42) == 42
        assert _expand_env(3.14) == 3.14
        assert _expand_env(None) is None
        assert _expand_env(True) is True


# ── Tests Notifier ───────────────────────────────────────────────────────────

def _notif_cfg(telegram=False, min_pnl=5.0):
    return {
        "trading": {"daily_drawdown_limit": 0.05},
        "notifications": {
            "telegram_enabled": telegram,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "whatsapp_enabled": False,
            "whatsapp_token": "",
            "whatsapp_number": "",
            "min_pnl_to_notify": min_pnl,
            "dd_warning_ratio": 0.80,
            "exchange_error_threshold": 3,
            "position_loss_warn_pct": 5.0,
        },
    }


class TestNotifier:
    def test_instantiate(self):
        n = Notifier(_notif_cfg())
        assert not n.telegram_enabled
        assert not n.whatsapp_enabled

    def test_notify_trade_below_min_pnl_no_send(self):
        """Aucun envoi si PnL < min_pnl."""
        n = Notifier(_notif_cfg(min_pnl=10.0))
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_trade({"pnl": 3.0, "symbol": "BTC/USDC", "side": "long",
                        "strategy": "test", "entry": 100, "exit": 103,
                        "fees": 0.1, "score": 0.7})
        assert len(sent) == 0

    def test_notify_trade_above_min_pnl_sends(self):
        n = Notifier(_notif_cfg(min_pnl=5.0))
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_trade({"pnl": 20.0, "symbol": "BTC/USDC", "side": "long",
                        "strategy": "test", "entry": 100, "exit": 120,
                        "fees": 0.2, "score": 0.8})
        assert len(sent) == 1
        assert "BTC/USDC" in sent[0]

    def test_dd_warning_anti_spam(self):
        n = Notifier(_notif_cfg())
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_dd_warning(4.5, 5.0)
        n.notify_dd_warning(4.8, 5.0)  # 2ème appel → ignoré
        assert len(sent) == 1

    def test_dd_warning_reset(self):
        n = Notifier(_notif_cfg())
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_dd_warning(4.5, 5.0)
        n.reset_dd_warning()
        n.notify_dd_warning(4.5, 5.0)  # doit passer après reset
        assert len(sent) == 2

    def test_halt_anti_spam(self):
        n = Notifier(_notif_cfg())
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_halt("Circuit breaker", equity=900, dd_pct=10.0)
        n.notify_halt("Circuit breaker", equity=900, dd_pct=10.0)
        assert len(sent) == 1

    def test_exchange_error_threshold(self):
        n = Notifier(_notif_cfg())
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_exchange_error("BTC/USDC", "timeout", 1)   # pas encore
        n.notify_exchange_error("BTC/USDC", "timeout", 2)   # pas encore
        n.notify_exchange_error("BTC/USDC", "timeout", 3)   # seuil atteint → envoi
        assert len(sent) == 1

    def test_position_loss_no_alert_if_above_threshold(self):
        n = Notifier(_notif_cfg())
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_position_loss("BTC/USDC", "trend", "long", -2.0)  # -2% < seuil de 5%
        assert len(sent) == 0

    def test_position_loss_alert_if_below_threshold(self):
        n = Notifier(_notif_cfg())
        sent = []
        n._send_all = lambda msg: sent.append(msg)
        n.notify_position_loss("BTC/USDC", "trend", "long", -8.0)  # -8% > seuil de 5%
        assert len(sent) == 1
