"""
Logger centralisé avec rotation, niveau debug global et formatage coloré console.

OBS-02 — le handler FICHIER écrit désormais du JSON Lines par défaut, tandis
que la console garde son format coloré. C'est la répartition qui correspond à
l'usage réel : la console est lue par un humain pendant qu'il travaille, le
fichier est lu par un outil (ou par un humain qui cherche, des jours plus
tard, tout ce qui concerne une opération). Repli par ``logging.format: text``
dans config.yaml si un outillage externe dépendait de l'ancien format.
"""
import logging
import logging.handlers
import os
import sys

from app.core.log_context import CorrelationFilter, JsonFormatter


def setup_logging(cfg: dict) -> logging.Logger:
    """Configure le système de logging à partir de la config."""
    log_cfg   = cfg.get("logging", {})
    level_str = log_cfg.get("level", "INFO").upper()
    debug     = log_cfg.get("debug", False)
    level     = logging.DEBUG if debug else getattr(logging, level_str, logging.INFO)
    log_file  = log_cfg.get("log_file", "logs/bot.log")
    max_bytes = log_cfg.get("max_bytes", 10_485_760)
    backups   = log_cfg.get("backup_count", 5)
    file_fmt  = str(log_cfg.get("format", "json")).lower()

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt     = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # L'identifiant de corrélation est posé sur le RECORD, donc par un filtre
    # attaché à chaque handler — les deux formatters en ont besoin.
    correlation = CorrelationFilter()

    # Console (coloré)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(_ColorFormatter(fmt, datefmt))
    console.addFilter(correlation)
    root.addHandler(console)

    # Fichier avec rotation
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(JsonFormatter() if file_fmt == "json"
                    else logging.Formatter(fmt, datefmt))
    fh.addFilter(correlation)
    root.addHandler(fh)

    logger = logging.getLogger("bot")
    logger.info(f"Logging démarré — niveau={level_str}, debug={debug}, "
                f"fichier={log_file} (format={file_fmt})")
    return logger


class _ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # vert
        "WARNING":  "\033[33m",   # jaune
        "ERROR":    "\033[31m",   # rouge
        "CRITICAL": "\033[35m",   # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        # NE PAS muter ``record`` : le même enregistrement est ensuite remis au
        # handler fichier. L'ancienne version écrivait
        # ``record.levelname = f"{color}{levelname}{RESET}"``, si bien que les
        # séquences ANSI finissaient DANS bot.log — visible dans les rotations
        # archivées (`[[32mINFO[0m]`). Le fichier n'était donc grep-able
        # qu'en connaissant les codes d'échappement.
        color = self.COLORS.get(record.levelname, "")
        cid = getattr(record, "correlation_id", "")
        prefix = f"{color}{record.levelname}{self.RESET}" if color else record.levelname
        base = super().format(record)
        # Substitution locale sur la chaîne produite, une seule occurrence :
        # le levelname peut aussi apparaître dans le message de l'utilisateur.
        base = base.replace(f"[{record.levelname}]", f"[{prefix}]", 1)
        return f"{base} [{cid}]" if cid else base
