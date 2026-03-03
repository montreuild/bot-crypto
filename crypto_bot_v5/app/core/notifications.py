"""
Module 8 — Notifications multi-canaux : Telegram + WhatsApp.
Imports conditionnels pour éviter les erreurs si libs non installées.
"""
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ── Telegram ──────────────────────────────────────────────────────────────
try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


class Notifier:
    def __init__(self, cfg: dict):
        n = cfg.get("notifications", {})
        self.telegram_enabled    = n.get("telegram_enabled", False)
        self.telegram_token      = n.get("telegram_bot_token", "")
        self.telegram_chat_id    = n.get("telegram_chat_id", "")
        self.whatsapp_enabled    = n.get("whatsapp_enabled", False)
        self.whatsapp_token      = n.get("whatsapp_token", "")
        self.whatsapp_number     = n.get("whatsapp_number", "")
        self.min_pnl             = n.get("min_pnl_to_notify", 5.0)
        if not _HAS_REQUESTS and (self.telegram_enabled or self.whatsapp_enabled):
            logger.warning("[Notifier] 'requests' non installé — notifications désactivées.")
            self.telegram_enabled = self.whatsapp_enabled = False

    def send(self, message: str, async_: bool = True):
        """Envoi asynchrone pour ne pas bloquer la boucle principale."""
        if async_:
            threading.Thread(target=self._send_all, args=(message,), daemon=True).start()
        else:
            self._send_all(message)

    def notify_trade(self, trade: dict):
        pnl = trade.get("pnl") or 0
        if abs(pnl) < self.min_pnl:
            return
        emoji  = "✅" if pnl >= 0 else "🔴"
        symbol = trade.get("symbol", "?")
        side   = trade.get("side", "?").upper()
        strat  = trade.get("strategy", "?")
        entry  = trade.get("entry", 0)
        exit_  = trade.get("exit", 0)
        fees   = trade.get("fees", 0)
        msg    = (f"{emoji} *Trade Clôturé*\n"
                  f"Pair   : `{symbol}`\n"
                  f"Side   : `{side}` — Stratégie : `{strat}`\n"
                  f"Entrée : `{entry:.4f}` → Sortie : `{exit_:.4f}`\n"
                  f"PnL    : `{'+' if pnl>=0 else ''}{pnl:.4f}` USDC (frais: {fees:.4f})\n")
        self.send(msg)

    def notify_halt(self, reason: str):
        self.send(f"🚨 *CIRCUIT BREAKER ACTIVÉ*\n{reason}", async_=False)

    def notify_start(self, cfg: dict):
        t = cfg.get("trading", {})
        self.send(f"🤖 *Bot démarré*\n"
                  f"Capital : `${t.get('capital', '?')}`\n"
                  f"Mode    : `{'PAPER' if t.get('paper_mode') else '🔴 LIVE'}`\n"
                  f"TF      : `{t.get('timeframe', '?')}`")

    def notify_status(self, status: dict):
        eq    = status.get("equity", 0)
        dd    = status.get("global_dd_pct", 0)
        pos   = status.get("open_positions", 0)
        dpnl  = status.get("daily_pnl_pct", 0)
        emoji = "📈" if dpnl >= 0 else "📉"
        self.send(f"{emoji} *Rapport Bot*\n"
                  f"Equity     : `${eq:.2f}`\n"
                  f"PnL/jour   : `{'+' if dpnl>=0 else ''}{dpnl:.2f}%`\n"
                  f"Drawdown   : `{dd:.2f}%`\n"
                  f"Positions  : `{pos}` ouvertes")

    def _send_all(self, message: str):
        if self.telegram_enabled:
            self._telegram(message)
        if self.whatsapp_enabled:
            self._whatsapp(message)

    def _telegram(self, text: str):
        if not _HAS_REQUESTS or not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            _requests.post(url, json={"chat_id": self.telegram_chat_id,
                                      "text": text, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            logger.error(f"[Notifier] Telegram KO : {e}")

    def _whatsapp(self, text: str):
        """WhatsApp via CallMeBot ou solution similaire."""
        if not _HAS_REQUESTS or not self.whatsapp_token:
            return
        try:
            url = "https://api.callmebot.com/whatsapp.php"
            _requests.get(url, params={"phone": self.whatsapp_number,
                                       "text": text, "apikey": self.whatsapp_token}, timeout=10)
        except Exception as e:
            logger.error(f"[Notifier] WhatsApp KO : {e}")
