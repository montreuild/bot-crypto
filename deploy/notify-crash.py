#!/usr/bin/env python3
"""
notify-crash.sh — Notification de crash envoyée par systemd via ExecStopPost.

Ce script est appelé automatiquement par systemd après chaque arrêt anormal
du bot. Il lit config.yaml, extrait les tokens Telegram/WhatsApp et envoie
une alerte avec la cause du crash (dernières lignes de log).

Appelé par systemd : ExecStopPost=/opt/crypto_bot/deploy/notify-crash.sh
Les variables d'environnement MAINPID, EXIT_CODE, EXIT_STATUS sont injectées
automatiquement par systemd.
"""
import logging
import os
import re
import subprocess
import sys
import time

import requests
import yaml

CONFIG_PATH  = "/opt/crypto_bot/config.yaml"
LOG_PATH     = "/opt/crypto_bot/logs/bot.log"
BOT_NAME     = "Crypto Bot"
MAX_LOG_TAIL = 20          # nombre de lignes de log à inclure dans l'alerte

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger("notify-crash")

# ── Variables systemd ─────────────────────────────────────────────────────
# Disponibles uniquement quand appelé depuis ExecStopPost
exit_code   = os.environ.get("EXIT_CODE",   "?")
exit_status = os.environ.get("EXIT_STATUS", "?")
main_pid    = os.environ.get("MAINPID",     "?")
service     = os.environ.get("UNIT",        "crypto-bot.service")

# Ne pas notifier les arrêts propres (EXIT_CODE=exited, EXIT_STATUS=0)
if exit_code == "exited" and exit_status == "0":
    sys.exit(0)


def read_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Impossible de lire config.yaml : {e}")
        return {}


def tail_log(n: int = MAX_LOG_TAIL) -> str:
    """Retourne les N dernières lignes du log, après sanitisation des données sensibles."""
    try:
        result = subprocess.run(
            ["tail", "-n", str(n), LOG_PATH],
            capture_output=True, text=True, timeout=5
        )
        return _sanitize_log(result.stdout.strip())
    except Exception as e:
        logger.warning(f"tail_log KO : {e}")
        return "(log inaccessible)"


# Patterns pouvant contenir des secrets (clés API, tokens, mots de passe)
_SECRET_PATTERNS = [
    # Valeurs de paramètres/headers contenant des tokens/clés (au moins 8 caractères)
    (re.compile(r'((?:api[_-]?key|token|secret|password|passwd|Authorization)[=:\s]+)[^\s,\]"]{8,}',
                re.IGNORECASE), r'\1[REDACTED]'),
    # Hex strings longues précédées d'un contexte de clé (API key / secret patterns)
    (re.compile(r'(?<=[=:\s])([0-9a-fA-F]{40,})\b'), '[REDACTED_HEX]'),
]


def _sanitize_log(text: str) -> str:
    """Supprime les fragments de secrets potentiels des lignes de log."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def get_restart_count() -> str:
    """Récupère le nombre de redémarrages depuis systemd."""
    try:
        result = subprocess.run(
            ["systemctl", "show", "crypto-bot.service", "--property=NRestarts"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip().replace("NRestarts=", "") or "?"
    except Exception as e:
        logger.warning(f"get_restart_count KO : {e}")
        return "?"


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=15
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram KO : {e}")
        return False


def send_whatsapp(token: str, number: str, message: str) -> bool:
    try:
        url = "https://api.callmebot.com/whatsapp.php"
        resp = requests.get(
            url,
            params={"phone": number, "text": message, "apikey": token},
            timeout=15
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"WhatsApp KO : {e}")
        return False


def main():
    cfg = read_config()
    notif = cfg.get("notifications", {})

    tg_enabled = notif.get("telegram_enabled", False)
    tg_token   = notif.get("telegram_bot_token", "")
    tg_chat    = notif.get("telegram_chat_id", "")
    wa_enabled = notif.get("whatsapp_enabled", False)
    wa_token   = notif.get("whatsapp_token", "")
    wa_number  = notif.get("whatsapp_number", "")

    if not tg_enabled and not wa_enabled:
        logger.info("Aucun canal activé, pas de notification.")
        sys.exit(0)

    log_tail    = tail_log()
    restarts    = get_restart_count()
    ts          = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    message = (
        f"🚨 *{BOT_NAME} — CRASH DÉTECTÉ*\n"
        f"Heure      : `{ts}`\n"
        f"PID        : `{main_pid}`\n"
        f"Exit code  : `{exit_code}` / `{exit_status}`\n"
        f"Redémarrages : `{restarts}`\n"
        f"_systemd redémarre automatiquement dans 30s_\n\n"
        f"📋 *Dernières lignes de log :*\n"
        f"```\n{log_tail[-800:]}\n```"
    )

    if tg_enabled and tg_token and tg_chat:
        ok = send_telegram(tg_token, tg_chat, message)
        logger.info(f"Telegram : {'✅ envoyé' if ok else '❌ échec'}")

    if wa_enabled and wa_token and wa_number:
        # WhatsApp (callmebot) ne supporte pas le Markdown → version simplifiée
        msg_plain = (
            f"🚨 CRYPTO BOT CRASH\n"
            f"Heure: {ts}\n"
            f"Exit: {exit_code}/{exit_status}\n"
            f"Redémarrages: {restarts}\n"
            f"Redémarrage auto dans 30s\n\n"
            f"Log:\n{log_tail[-400:]}"
        )
        ok = send_whatsapp(wa_token, wa_number, msg_plain)
        logger.info(f"WhatsApp : {'✅ envoyé' if ok else '❌ échec'}")


if __name__ == "__main__":
    main()
