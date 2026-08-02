#!/usr/bin/env python3
"""
notify-crash.sh — Notification de crash envoyée par systemd via ExecStopPost.

Ce script est appelé automatiquement par systemd après chaque arrêt anormal
du bot. Il lit config.yaml, extrait les tokens Telegram/WhatsApp et envoie
une alerte décrivant la cause du crash.

Appelé par systemd : ExecStopPost=/opt/crypto_bot/deploy/notify-crash.sh
Les variables d'environnement MAINPID, EXIT_CODE, EXIT_STATUS sont injectées
automatiquement par systemd.

SEC-007 — l'alerte ne transporte PLUS le contenu du log par défaut.
Telegram et CallMeBot sont des tiers : tout ce qui part dans le message quitte
la machine, est stocké chez eux et reste lisible par quiconque a accès au
canal. Or `bot.log` porte des symboles, des tailles de position, des soldes,
des identifiants d'ordre — et, en cas de crash pendant un appel exchange, des
fragments de requête. Le filtre `_sanitize_log` ne rattrapait que ce qui
RESSEMBLE à un secret (`token=`, hexadécimal long) : il n'a jamais protégé les
données d'exploitation, qui sont la vraie fuite.

Le comportement historique reste accessible pour qui l'assume, via
``notifications.crash_include_log: true`` dans config.yaml. Il est désactivé
par défaut parce qu'un défaut sûr doit être le silence, pas la confiance dans
un filtre par motifs.
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
    """Lit la config, en suivant le découpage `include:` (S11).

    Fusion volontairement locale et minimale : ce script tourne depuis
    ``ExecStopPost`` de systemd, c'est-à-dire précisément quand le bot vient de
    crasher. Importer ``app.core.config`` ferait dépendre l'alerte de crash du
    code qui a crashé. La section utile (``notifications``) vit dans
    ``config/ops.yaml``.
    """
    def _read(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    try:
        cfg = _read(CONFIG_PATH)
    except Exception as e:
        logger.error(f"Impossible de lire config.yaml : {e}")
        return {}

    base = os.path.dirname(os.path.abspath(CONFIG_PATH))
    for inc in (cfg.pop("include", None) or []):
        p = inc if os.path.isabs(inc) else os.path.join(base, inc)
        try:
            for section, value in _read(p).items():
                cfg.setdefault(section, value)
        except Exception as e:
            logger.error(f"Impossible de lire {inc} : {e}")
    return cfg


def tail_log(n: int = MAX_LOG_TAIL) -> str:
    """Retourne les N dernières lignes du log, après sanitisation des données sensibles.

    N'est appelée QUE si ``notifications.crash_include_log`` vaut ``true`` —
    cf. l'en-tête du module (SEC-007). La sanitisation reste appliquée sur ce
    chemin, mais elle ne doit pas être confondue avec une garantie : elle
    masque des motifs de secrets, pas des données d'exploitation.
    """
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

    # SEC-007 : opt-in explicite. Absent de config.yaml ⇒ aucun contenu de log
    # ne quitte la machine.
    include_log = bool(notif.get("crash_include_log", False))
    log_tail    = tail_log() if include_log else ""
    restarts    = get_restart_count()
    ts          = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    # Sans le log, l'alerte doit dire OÙ regarder — sinon elle prévient d'un
    # crash sans donner le moyen de l'instruire.
    detail = (f"📋 *Dernières lignes de log :*\n```\n{log_tail[-800:]}\n```"
              if include_log else
              f"🔒 Log non transmis (`notifications.crash_include_log: false`).\n"
              f"Sur la machine : `tail -n 50 {LOG_PATH}` "
              f"ou `journalctl -u {service} -n 50`")

    message = (
        f"🚨 *{BOT_NAME} — CRASH DÉTECTÉ*\n"
        f"Heure      : `{ts}`\n"
        f"PID        : `{main_pid}`\n"
        f"Exit code  : `{exit_code}` / `{exit_status}`\n"
        f"Redémarrages : `{restarts}`\n"
        f"_systemd redémarre automatiquement dans 30s_\n\n"
        f"{detail}"
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
            + (f"Log:\n{log_tail[-400:]}" if include_log
               else f"Log non transmis — voir {LOG_PATH} sur la machine")
        )
        ok = send_whatsapp(wa_token, wa_number, msg_plain)
        logger.info(f"WhatsApp : {'✅ envoyé' if ok else '❌ échec'}")


if __name__ == "__main__":
    main()
