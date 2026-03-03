"""Notifications multi-canaux (Telegram, WhatsApp/Twilio, Email) — async queue."""
import logging, queue, smtplib, threading
from datetime import datetime, timezone
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

try:
    import requests as _req; HAS_REQ = True
except ImportError:
    HAS_REQ = False

try:
    from twilio.rest import Client as TwilioClient; HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False


class Notifier:
    def __init__(self, cfg: dict):
        self.cfg      = cfg.get("notifications", {})
        self.events   = self.cfg.get("events", {})
        self._q       = queue.Queue()
        self._stop    = threading.Event()
        self._t       = threading.Thread(target=self._worker, daemon=True, name="notifier")
        self._t.start()
        active = []
        if self.cfg.get("telegram",{}).get("enabled"): active.append("Telegram")
        if self.cfg.get("whatsapp",{}).get("enabled"): active.append("WhatsApp")
        if self.cfg.get("email",{}).get("enabled"):    active.append("Email")
        logger.info(f"[Notifier] Canaux : {active or ['aucun']}")

    def notify_trade_open(self, t: dict):
        if not self.events.get("on_trade_open", True): return
        e = "🟢" if t.get("side") == "long" else "🔴"
        self._push(f"{e} *TRADE OUVERT*\nSymbole: `{t.get('symbol')}`\n"
                   f"Side: {t.get('side','').upper()} | Stratégie: {t.get('strategy')}\n"
                   f"Entrée: `{t.get('entry',0):.4f}` | Stop: `{t.get('stop_initial',0):.4f}`\n"
                   f"Notionnel: ${t.get('notional',0):.2f} | Score: {t.get('score',0):.2f}")

    def notify_trade_close(self, t: dict):
        if not self.events.get("on_trade_close", True): return
        pnl = t.get("pnl", 0) or 0
        e   = "✅" if pnl >= 0 else "❌"
        self._push(f"{e} *TRADE FERMÉ*\nSymbole: `{t.get('symbol')}`\n"
                   f"PnL: `{pnl:+.4f}` ({t.get('pnl_pct',0):.2f}%)\n"
                   f"Raison: {t.get('exit_reason','—')}")

    def notify_circuit_breaker(self, loss_pct: float, capital: float):
        if not self.events.get("on_circuit_breaker", True): return
        self._push(f"⛔ *CIRCUIT BREAKER*\nPerte: `{loss_pct*100:.1f}%`\nCapital: `${capital:.2f}`\nTrading suspendu.", level="critical")

    def notify_daily_report(self, stats: dict):
        if not self.events.get("on_daily_report", True): return
        self._push(f"📊 *RAPPORT JOURNALIER*\nCapital: `${stats.get('capital',0):.2f}`\n"
                   f"PnL jour: `{stats.get('daily_pnl',0):+.4f}`\nTrades: {stats.get('trades_today',0)}\n"
                   f"Win Rate: {stats.get('win_rate',0):.1f}% | DD: {stats.get('drawdown',0):.1f}%")

    def send(self, msg: str): self._push(msg)

    def _push(self, msg: str, level: str = "info"):
        self._q.put({"text": msg, "level": level})

    def _worker(self):
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=1.0)
                plain = item["text"].replace("*","").replace("`","")
                self._telegram(item["text"])
                self._whatsapp(plain)
                self._email(plain, item.get("level","info"))
                self._q.task_done()
            except queue.Empty: continue
            except Exception as e: logger.error(f"[Notifier] Worker: {e}")

    def _telegram(self, msg: str):
        c = self.cfg.get("telegram", {})
        if not c.get("enabled") or not c.get("bot_token") or not HAS_REQ: return
        try:
            _req.post(f"https://api.telegram.org/bot{c['bot_token']}/sendMessage",
                      json={"chat_id": c["chat_id"], "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e: logger.warning(f"[Notifier] Telegram: {e}")

    def _whatsapp(self, msg: str):
        c = self.cfg.get("whatsapp", {})
        if not c.get("enabled") or not HAS_TWILIO or not c.get("account_sid"): return
        try:
            TwilioClient(c["account_sid"], c["auth_token"]).messages.create(
                body=msg, from_=c["from_number"], to=c["to_number"])
        except Exception as e: logger.warning(f"[Notifier] WhatsApp: {e}")

    def _email(self, msg: str, level: str):
        c = self.cfg.get("email", {})
        if not c.get("enabled") or not c.get("smtp") or not c.get("user"): return
        try:
            m = MIMEText(msg, "plain", "utf-8")
            m["Subject"] = f"[CryptoBot V5] {level.upper()}"
            m["From"] = c["user"]; m["To"] = c.get("to", c["user"])
            with smtplib.SMTP(c["smtp"], c.get("port", 587)) as s:
                s.starttls(); s.login(c["user"], c["password"]); s.send_message(m)
        except Exception as e: logger.warning(f"[Notifier] Email: {e}")

    def stop(self):
        self._stop.set(); self._t.join(timeout=5)
