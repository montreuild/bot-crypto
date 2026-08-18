"""Notifications multi-canaux : Telegram, WhatsApp, Email. Envoi asynchrone via queue.

Hiérarchie à 3 niveaux (Phase 4) : ``info`` / ``warning`` / ``critical``, avec
throttling (anti-spam) et un **flux en mémoire** miroir, exposé à l'UI (le même
message part vers Telegram/email ET alimente le fil d'activité du portefeuille).
Le mismatch de réconciliation est une alerte **critique** (doc §3).
"""
import logging
import queue
import smtplib
import threading
import time
from collections import deque
from datetime import datetime, timezone
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Niveaux ordonnés (pour le filtrage « ≥ niveau » côté UI).
LEVELS = ("info", "warning", "critical")
_LEVEL_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from twilio.rest import Client as _TwilioClient
    _HAS_TWILIO = True
except ImportError:
    _HAS_TWILIO = False

_DEFAULT_EVENTS = {
    "on_trade_open":      True,
    "on_trade_close":     True,
    # G2 : venue sans exécution branchée (actions en paper) — le bot calcule le
    # trade et l'envoie à exécuter à la main. Actif par défaut : sans lui,
    # l'utilisateur ne verrait tout simplement pas passer ses trades actions.
    "on_trade_signal":    True,
    "on_circuit_breaker": True,
    "on_dd_warning":      True,
    "on_position_loss":   True,
    "on_exchange_error":  True,
    "on_status_report":   True,
    "on_optimization":    True,
    "on_start_stop":      True,
}


class Notifier:
    def __init__(self, cfg: dict):
        n = cfg.get("notifications", {})

        self.telegram_enabled  = n.get("telegram_enabled", False)
        self.telegram_token    = n.get("telegram_bot_token", "")
        self.telegram_chat_id  = n.get("telegram_chat_id", "")

        self.whatsapp_enabled  = n.get("whatsapp_enabled", False)
        self.whatsapp_token    = n.get("whatsapp_token", "")
        self.whatsapp_number   = n.get("whatsapp_number", "")
        self.whatsapp_mode     = n.get("whatsapp_mode", "callmebot")
        self._twilio_sid       = n.get("twilio_account_sid", "")
        self._twilio_auth      = n.get("twilio_auth_token", "")
        self._twilio_from      = n.get("twilio_from_number", "")

        self.email_enabled     = n.get("email_enabled", False)
        self._email_smtp       = n.get("email_smtp", "smtp.gmail.com")
        self._email_port       = int(n.get("email_port", 587))
        self._email_user       = n.get("email_user", "")
        self._email_password   = n.get("email_password", "")
        self._email_to         = n.get("email_to", "")

        self.min_pnl                 = float(n.get("min_pnl_to_notify", 5.0))
        self._loss_warn_pct          = float(n.get("position_loss_warn_pct", 5.0))
        self._exchange_err_threshold = int(n.get("exchange_error_threshold", 3))

        ev = n.get("events", {})
        self._events = {k: ev.get(k, v) for k, v in _DEFAULT_EVENTS.items()}

        self._dd_warn_sent = False
        self._halt_sent    = False

        # ── Hiérarchie 3 niveaux + throttling + flux UI (Phase 4) ─────────────
        self._throttle_secs = float(n.get("throttle_secs", 60))
        self._throttle: dict = {}                 # clé → dernier envoi (ts)
        self._feed: deque = deque(maxlen=int(n.get("feed_size", 200)))
        self._feed_lock = threading.Lock()

        active = []
        if self.telegram_enabled:
            active.append("Telegram")
        if self.whatsapp_enabled:
            active.append(f"WhatsApp({self.whatsapp_mode})")
        if self.email_enabled:
            active.append("Email")
        if not _HAS_REQUESTS and (self.telegram_enabled or self.whatsapp_enabled):
            logger.warning("[Notifier] 'requests' non installé — Telegram/WhatsApp désactivés.")
            self.telegram_enabled = self.whatsapp_enabled = False
        logger.info(f"[Notifier] Canaux actifs : {active or ['aucun']}")

        # Queue pour les envois asynchrones — évite de bloquer le thread principal
        self._async_queue: queue.Queue = queue.Queue(maxsize=200)
        self._async_worker = threading.Thread(
            target=self._async_dispatch_loop, daemon=True, name="notif-worker"
        )
        self._async_worker.start()

    # ── Interface publique ────────────────────────────────────────────────────

    def send(self, message: str, async_: bool = True, level: str = "info",
             key: str | None = None, throttle: bool = True):
        """Émet une notification.

        ``level``    : "info" | "warning" | "critical".
        ``key``      : clé de throttling (par défaut le message lui-même) — deux
                       envois de même clé à moins de ``throttle_secs`` sont fusionnés.
        ``throttle`` : désactivable ponctuellement. Les ``critical`` ne sont
                       JAMAIS throttlés (on ne tait pas une catastrophe).
        """
        if level not in _LEVEL_RANK:
            level = "info"
        # Throttling (sauf critique)
        if throttle and level != "critical":
            tkey = key or message
            now = time.time()
            last = self._throttle.get(tkey, 0.0)
            if now - last < self._throttle_secs:
                return
            self._throttle[tkey] = now
        # Flux UI (miroir) — toujours alimenté, même si un canal échoue.
        self._record_feed(message, level)
        if async_:
            try:
                self._async_queue.put_nowait((message, level))
            except queue.Full:
                # Queue saturée : envoi synchrone pour ne pas perdre les messages critiques
                logger.warning("[Notifier] Queue async saturée — envoi synchrone")
                self._dispatch(message, level)
        else:
            self._dispatch(message, level)

    # ── Flux en mémoire (miroir UI) ───────────────────────────────────────────
    def _record_feed(self, message: str, level: str) -> None:
        entry = {
            "time":  datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": self._strip_markdown(message),
        }
        with self._feed_lock:
            self._feed.append(entry)

    def recent(self, limit: int = 50, min_level: str = "info") -> list:
        """Notifications récentes (plus récentes d'abord), filtrées par niveau mini."""
        floor = _LEVEL_RANK.get(min_level, 0)
        with self._feed_lock:
            items = [e for e in self._feed if _LEVEL_RANK.get(e["level"], 0) >= floor]
        return list(reversed(items))[:limit]

    def notify_reconciliation_mismatch(self, symbol: str, label: str,
                                       estimated: float, real: float,
                                       gap_pct: float) -> None:
        """Mismatch de réconciliation (coûts réels vs estimés) = alerte CRITIQUE."""
        self.send(
            f"🚨 *Mismatch réconciliation* `{symbol}`\n"
            f"{label} estimé : `{estimated:.6f}` → réel : `{real:.6f}`\n"
            f"Écart : `{gap_pct:.1f}%`\n"
            f"_Vérifiez la config de frais/funding (live ≠ backtest)._",
            async_=False, level="critical",
            key=f"reconcile::{symbol}::{label}",
        )

    def notify_trade_open(self, pos: dict):
        if not self._events.get("on_trade_open"):
            return
        side  = pos.get("side", "?")
        emoji = "🟢" if side == "long" else "🔴"
        tp    = pos.get("take_profit")
        tp_txt = f" | TP : `{tp:.4f}`" if tp else " | TP : `trailing`"
        self.send(
            f"{emoji} *Position ouverte*\n"
            f"Pair      : `{pos.get('symbol','?')}`\n"
            f"Side      : `{side.upper()}` — Stratégie : `{pos.get('strategy','?')}`\n"
            f"Entrée    : `{pos.get('entry',0):.4f}` | Stop : `{pos.get('stop',0):.4f}`"
            f"{tp_txt}\n"
            f"Notionnel : `${pos.get('notional',0):.2f}` | Score : `{pos.get('score',0):.2f}`\n"
            f"Raison    : _{pos.get('reason','—')}_"
        )

    def notify_trade_signal(self, pos: dict, venue: str = "",
                            action: str = "open") -> None:
        """Trade à exécuter **à la main** — venue sans exécution branchée (G2).

        Tant que l'exécution réelle actions n'est pas livrée (G3), le bot
        calcule et suit la position mais n'envoie aucun ordre : cette
        notification EST le passage à l'acte. Elle porte donc tout ce qu'il
        faut pour saisir l'ordre chez son courtier — symbole, sens, prix
        d'ouverture, stop, take-profit — et non un simple avis d'information.

        ``action`` : ``"open"`` (entrée) ou ``"close"`` (sortie à solder).
        Jamais throttlée (deux trades distincts ne doivent jamais fusionner) et
        envoyée **en synchrone** : c'est le seul chemin vers l'exécution, il ne
        peut pas être perdu dans une queue saturée ou un arrêt du process.
        """
        if not self._events.get("on_trade_signal"):
            return
        side = str(pos.get("side", "?"))
        symbol = pos.get("symbol", "?")
        entry = float(pos.get("entry", 0) or 0)
        stop = float(pos.get("stop", 0) or 0)
        tp = pos.get("take_profit")
        size = float(pos.get("size", 0) or 0)

        if action == "close":
            direction = "VENDRE" if side == "long" else "RACHETER"
            self.send(
                f"📤 *TRADE À SOLDER — {direction}*\n"
                f"Symbole   : `{symbol}`\n"
                f"Sens      : `{direction}` `{size:g}` (clôture {side.upper()})\n"
                f"Prix ind. : `{float(pos.get('exit', entry) or entry):.4f}`\n"
                f"Motif     : _{pos.get('exit_reason', '—')}_\n"
                f"Venue     : `{venue or '—'}` — _aucun ordre transmis, à exécuter manuellement._",
                async_=False, level="warning",
                key=f"signal_close::{pos.get('id', symbol)}", throttle=False,
            )
            return

        direction = "ACHAT" if side == "long" else "VENTE"
        emoji = "🟢" if side == "long" else "🔴"
        risk = abs(entry - stop)
        lines = [
            f"{emoji} *TRADE À EXÉCUTER — {direction}*",
            f"Symbole   : `{symbol}`",
            f"Sens      : `{direction}` (`{side.upper()}`)",
            f"Ouverture : `{entry:.4f}`",
            f"Stop-loss : `{stop:.4f}`" + (f" (`-{risk / entry * 100:.2f}%`)" if entry > 0 else ""),
        ]
        if tp:
            tp = float(tp)
            rr = abs(tp - entry) / risk if risk > 0 else 0.0
            lines.append(f"Take-profit: `{tp:.4f}`" + (f" (R:R `{rr:.2f}`)" if rr else ""))
        else:
            lines.append("Take-profit: `—` (_sortie pilotée par le trailing stop_)")
        lines += [
            f"Quantité  : `{size:g}` | Notionnel : `{float(pos.get('notional', 0) or 0):.2f}`",
            f"Stratégie : `{pos.get('strategy','?')}`@`{pos.get('timeframe','?')}` "
            f"| Score : `{float(pos.get('score', 0) or 0):.2f}`",
            f"Venue     : `{venue or '—'}`",
            "_Aucun ordre n'a été transmis : à saisir chez votre courtier._",
        ]
        self.send("\n".join(lines), async_=False, level="warning",
                  key=f"signal_open::{pos.get('id', symbol)}", throttle=False)

    def notify_trade(self, trade: dict):
        if not self._events.get("on_trade_close"):
            return
        pnl = trade.get("pnl") or 0
        if abs(pnl) < self.min_pnl:
            return
        emoji   = "✅" if pnl >= 0 else "🔴"
        entry   = trade.get("entry", 0)
        exit_   = trade.get("exit", 0)
        side    = trade.get("side", "?").upper()
        pnl_pct = trade.get("pnl_pct")
        if pnl_pct is None:
            pnl_pct = round((exit_ - entry) / max(entry, 1e-9) * 100 * (1 if side == "LONG" else -1), 2)
        self.send(
            f"{emoji} *Trade clôturé*\n"
            f"Pair      : `{trade.get('symbol','?')}`\n"
            f"Side      : `{side}` — Stratégie : `{trade.get('strategy','?')}`\n"
            f"Entrée    : `{entry:.4f}` → Sortie : `{exit_:.4f}`\n"
            f"PnL       : `{'+' if pnl>=0 else ''}{pnl:.4f}` USDC "
            f"(`{'+' if pnl_pct>=0 else ''}{pnl_pct:.2f}%`)\n"
            f"Frais     : `{trade.get('fees',0):.4f}` USDC | Score : `{trade.get('score',0):.2f}`"
        )

    def notify_start(self, cfg: dict):
        if not self._events.get("on_start_stop"):
            return
        t      = cfg.get("trading", {})
        strats = cfg.get("strategies", {}).get("enabled", [])
        self.send(
            f"🤖 *Bot démarré*\n"
            f"Capital    : `${t.get('capital','?')}`\n"
            f"Mode       : `{'📝 PAPER' if t.get('paper_mode') else '🔴 LIVE'}`\n"
            f"TF         : `{t.get('timeframe','?')}`\n"
            f"Stratégies : `{', '.join(strats)}`"
        )

    def notify_stop(self, reason: str = "Arrêt normal"):
        if not self._events.get("on_start_stop"):
            return
        self.send(f"🛑 *Bot arrêté*\nRaison : `{reason}`", async_=False)

    def notify_halt(self, reason: str, equity: float = 0, dd_pct: float = 0):
        if not self._events.get("on_circuit_breaker"):
            return
        if self._halt_sent:
            return
        self._halt_sent = True
        self.send(
            f"🚨 *CIRCUIT BREAKER ACTIVÉ*\n"
            f"Raison   : `{reason}`\n"
            f"Equity   : `${equity:.2f}`\n"
            f"Drawdown : `{dd_pct:.2f}%`\n"
            f"⚠️ _Le bot ne prend plus de nouvelles positions._",
            async_=False, level="critical"
        )

    def reset_halt_notification(self):
        self._halt_sent = False

    def notify_dd_warning(self, daily_dd_pct: float, limit_pct: float):
        if not self._events.get("on_dd_warning"):
            return
        if self._dd_warn_sent:
            return
        self._dd_warn_sent = True
        self.send(
            f"⚠️ *Alerte Drawdown*\n"
            f"DD journalier : `{daily_dd_pct:.2f}%`\n"
            f"Limite        : `{limit_pct:.2f}%`\n"
            f"_Approche du circuit breaker journalier._",
            level="warning"
        )

    def reset_dd_warning(self):
        self._dd_warn_sent = False
        self._halt_sent    = False

    def notify_exchange_error(self, symbol: str, error: str, count: int):
        if not self._events.get("on_exchange_error"):
            return
        if count != self._exchange_err_threshold:
            return
        self.send(
            f"⚠️ *Erreur Exchange Répétée*\n"
            f"Symbole    : `{symbol}`\n"
            f"Erreur     : `{error}`\n"
            f"Tentatives : `{count}`\n"
            f"_Le symbole sera ignoré temporairement._",
            level="warning"
        )

    def notify_position_loss(self, symbol: str, strategy: str,
                              side: str, unrealized_pnl_pct: float):
        if not self._events.get("on_position_loss"):
            return
        if unrealized_pnl_pct >= 0 or abs(unrealized_pnl_pct) < self._loss_warn_pct:
            return
        self.send(
            f"📉 *Position en Perte*\n"
            f"Pair      : `{symbol}` ({side.upper()})\n"
            f"Stratégie : `{strategy}`\n"
            f"PnL non réalisé : `{unrealized_pnl_pct:.2f}%`\n"
            f"_Le trailing stop gérera la sortie automatiquement._",
            level="warning", key=f"loss::{symbol}::{strategy}"
        )

    def notify_status(self, status: dict):
        if not self._events.get("on_status_report"):
            return
        eq     = status.get("equity", 0)
        dd     = status.get("global_dd_pct", 0)
        dpnl   = status.get("daily_pnl_pct", 0)
        pos    = status.get("open_positions", 0)
        halted = status.get("halted", False)
        risk   = status.get("current_risk", 0)
        emoji  = "📈" if dpnl >= 0 else "📉"
        state  = "🔴 HALTED" if halted else "🟢 Actif"
        lines  = [
            f"{emoji} *Rapport Bot* — {state}",
            f"Equity     : `${eq:.2f}`",
            f"PnL/jour   : `{'+' if dpnl>=0 else ''}{dpnl:.2f}%`",
            f"Drawdown   : `{dd:.2f}%`",
            f"Positions  : `{pos}` ouvertes",
            f"Risque act.: `{risk:.2f}%` par trade",
        ]
        for p in status.get("positions_detail", [])[:5]:
            s   = p.get("side","?").upper()
            sym = p.get("symbol","?")
            pct = p.get("unrealized_pct", 0)
            st  = p.get("strategy","?")
            e2  = "📈" if pct >= 0 else "📉"
            lines.append(f"{e2} `{sym}` {s} [{st}] `{'+' if pct>=0 else ''}{pct:.2f}%`")
        self.send("\n".join(lines))

    def notify_optimization_done(self, strategy: str, score_before: float,
                                  score_after: float, applied: bool):
        if not self._events.get("on_optimization"):
            return
        delta = score_after - score_before
        emoji = "✅" if delta > 0 else "➡️"
        self.send(
            f"{emoji} *Optimisation Terminée*\n"
            f"Stratégie   : `{strategy}`\n"
            f"Score avant : `{score_before:.4f}`\n"
            f"Score après : `{score_after:.4f}` (`{'+' if delta>=0 else ''}{delta:.4f}`)\n"
            f"Appliqué    : `{'Oui' if applied else 'Non'}`"
        )

    def stop(self):
        """Arrêt propre : vide la queue et stoppe le worker."""
        try:
            self._async_queue.put_nowait(None)   # signal d'arrêt
        except queue.Full:
            pass

    # ── Internals ─────────────────────────────────────────────────────────────

    def _async_dispatch_loop(self):
        """Worker daemon qui consomme la queue et envoie les notifications."""
        while True:
            try:
                item = self._async_queue.get(timeout=5)
                if item is None:   # signal d'arrêt
                    break
                message, level = item
                self._dispatch(message, level)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[Notifier] Worker async KO : {e}")

    def _dispatch(self, message: str, level: str = "info"):
        self._send_all(message)
        # Email : si activé, ou systématiquement pour les critiques (filet).
        if self.email_enabled or level == "critical":
            self._email(self._strip_markdown(message), level)

    def _send_all(self, message: str):
        """Central dispatch to all active channels. Override in tests to capture messages."""
        self._telegram(message)
        self._whatsapp(self._strip_markdown(message))

    @staticmethod
    def _strip_markdown(text: str) -> str:
        return text.replace("*", "").replace("`", "").replace("_", "")

    def _telegram(self, text: str):
        if not self.telegram_enabled or not _HAS_REQUESTS:
            return
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            resp = _requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10
            )
            if resp.status_code >= 400:
                logger.warning(f"[Notifier] Telegram HTTP {resp.status_code} : {resp.text[:200]}")
        except Exception as e:
            logger.error(f"[Notifier] Telegram KO : {e}")

    def _whatsapp(self, text: str):
        if not self.whatsapp_enabled or not _HAS_REQUESTS:
            return
        try:
            if self.whatsapp_mode == "twilio" and _HAS_TWILIO and self._twilio_sid:
                _TwilioClient(self._twilio_sid, self._twilio_auth).messages.create(
                    body=text, from_=self._twilio_from, to=self.whatsapp_number
                )
            else:
                resp = _requests.get("https://api.callmebot.com/whatsapp.php", params={
                    "phone": self.whatsapp_number, "text": text, "apikey": self.whatsapp_token
                }, timeout=10)
                if resp.status_code >= 400:
                    logger.warning(f"[Notifier] WhatsApp HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"[Notifier] WhatsApp KO : {e}")

    def _email(self, text: str, level: str):
        if not self.email_enabled:
            return
        if not self._email_smtp or not self._email_user or not self._email_password:
            return
        try:
            _subj = {"critical": "🚨 CRITIQUE", "warning": "⚠️ Avertissement"}.get(level, "🤖 Info")
            subject = f"[CryptoBot] {_subj}"
            m = MIMEText(text, "plain", "utf-8")
            m["Subject"] = subject
            m["From"]    = self._email_user
            m["To"]      = self._email_to or self._email_user
            with smtplib.SMTP(self._email_smtp, self._email_port, timeout=15) as s:
                s.starttls()
                s.login(self._email_user, self._email_password)
                s.send_message(m)
        except smtplib.SMTPException as e:
            logger.warning(f"[Notifier] Email SMTP erreur : {e}")
        except Exception as e:
            logger.warning(f"[Notifier] Email KO : {e}")
