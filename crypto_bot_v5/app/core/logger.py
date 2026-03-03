"""
Logger centralisé avec rotation, niveau debug global et formatage coloré console.
"""
import logging
import logging.handlers
import os
import sys
from typing import Optional


def setup_logging(cfg: dict) -> logging.Logger:
    """Configure le système de logging à partir de la config."""
    log_cfg   = cfg.get("logging", {})
    level_str = log_cfg.get("level", "INFO").upper()
    debug     = log_cfg.get("debug", False)
    level     = logging.DEBUG if debug else getattr(logging, level_str, logging.INFO)
    log_file  = log_cfg.get("log_file", "logs/bot.log")
    max_bytes = log_cfg.get("max_bytes", 10_485_760)
    backups   = log_cfg.get("backup_count", 5)

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt     = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Console (coloré)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(_ColorFormatter(fmt, datefmt))
    root.addHandler(console)

    # Fichier avec rotation
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(fh)

    logger = logging.getLogger("bot")
    logger.info(f"Logging démarré — niveau={level_str}, debug={debug}, fichier={log_file}")
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
        color = self.COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)
