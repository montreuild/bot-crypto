"""Watchdog dead-man **séparé** (Phase 3 — sécurité & résilience).

Un process unique ne peut pas se surveiller lui-même (s'il se fige, son propre
watchdog se fige avec lui). Ce module fournit donc :

1. un **battement de cœur** (``write_heartbeat``) que le LiveTrader écrit à chaque
   cycle dans ``data/heartbeat.json`` ;
2. un **kill-switch fichier** (``arm_kill_switch`` / ``kill_switch_armed``) :
   présence du fichier ``data/KILL_SWITCH`` = ordre d'arrêt que le LiveTrader
   vérifie à chaque cycle (dead-man) ;
3. un **watchdog autonome** (``run`` / ``main``) à lancer dans un AUTRE process
   (``python -m app.live.watchdog``) : il lit le battement de cœur et, s'il est
   périmé au-delà de ``timeout``, arme le kill-switch et envoie une alerte
   critique. Le bot, à son prochain cycle vivant (ou au redémarrage), voit le
   fichier et se met en HALT.

Aucune dépendance au LiveTrader → pas d'import circulaire.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DATA_DIR = "data"
HEARTBEAT_PATH = os.path.join(_DATA_DIR, "heartbeat.json")
KILL_PATH = os.path.join(_DATA_DIR, "KILL_SWITCH")


# ── Battement de cœur ────────────────────────────────────────────────────────
def write_heartbeat(state: dict) -> None:
    """Écrit le battement de cœur (best-effort, ne lève jamais)."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        payload = dict(state or {})
        payload["ts"] = time.time()
        payload["iso"] = datetime.now(timezone.utc).isoformat()
        tmp = HEARTBEAT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, HEARTBEAT_PATH)   # écriture atomique
    except Exception as e:
        logger.debug(f"[Watchdog] write_heartbeat KO : {e}")


def read_heartbeat() -> dict:
    try:
        with open(HEARTBEAT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.debug(f"[Watchdog] read_heartbeat KO : {e}")
        return {}


def heartbeat_age(now: float = None) -> float:
    """Âge du battement en secondes (``inf`` si absent)."""
    hb = read_heartbeat()
    ts = hb.get("ts")
    if not ts:
        return float("inf")
    return (now if now is not None else time.time()) - float(ts)


# ── Kill-switch fichier ──────────────────────────────────────────────────────
def arm_kill_switch(reason: str) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(KILL_PATH, "w", encoding="utf-8") as f:
            json.dump({"reason": reason,
                       "iso": datetime.now(timezone.utc).isoformat()}, f)
        logger.critical(f"[Watchdog] KILL-SWITCH armé : {reason}")
    except Exception as e:
        logger.error(f"[Watchdog] arm_kill_switch KO : {e}")


def kill_switch_armed() -> bool:
    return os.path.exists(KILL_PATH)


def kill_switch_reason() -> str:
    try:
        with open(KILL_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("reason", "")
    except Exception:
        return ""


def clear_kill_switch() -> None:
    try:
        if os.path.exists(KILL_PATH):
            os.remove(KILL_PATH)
            logger.warning("[Watchdog] KILL-SWITCH levé (fichier supprimé).")
    except Exception as e:
        logger.error(f"[Watchdog] clear_kill_switch KO : {e}")


# ── Watchdog autonome ────────────────────────────────────────────────────────
def check_once(timeout_s: float, notifier=None, now: float = None) -> dict:
    """Une passe de surveillance. Retourne ``{stale, age, running, armed}``.

    Arme le kill-switch + alerte si le battement est périmé alors que le bot se
    déclare ``running``. Ignore l'absence de battement quand le bot n'a jamais
    démarré (évite les fausses alertes au repos).
    """
    hb = read_heartbeat()
    running = bool(hb.get("running"))
    age = heartbeat_age(now)
    stale = running and age > timeout_s
    if stale and not kill_switch_armed():
        reason = (f"battement de cœur périmé ({age:.0f}s > {timeout_s:.0f}s) — "
                  f"le bot s'est peut-être figé")
        arm_kill_switch(reason)
        if notifier:
            try:
                notifier.send(f"🛑 *Watchdog dead-man*\n{reason}",
                              async_=False, level="critical")
            except Exception:
                pass
    return {"stale": stale, "age": age, "running": running,
            "armed": kill_switch_armed()}


def run(cfg: dict) -> None:
    """Boucle de surveillance (process séparé). Bloquant."""
    wd = (cfg or {}).get("watchdog", {}) or {}
    interval = int(wd.get("check_interval_s", 60))
    # Tolérance : on attend plusieurs cycles manqués avant d'alerter.
    scan = int((cfg or {}).get("trading", {}).get("scan_interval", 60))
    timeout = float(wd.get("timeout_s", max(scan * 5, 300)))
    logger.info(f"[Watchdog] Démarré — timeout={timeout:.0f}s, intervalle={interval}s")
    notifier = None
    try:
        from app.core.notifications import Notifier
        notifier = Notifier(cfg)
    except Exception:
        pass
    while True:
        res = check_once(timeout, notifier=notifier)
        if res["stale"]:
            logger.critical(f"[Watchdog] Bot figé ? age={res['age']:.0f}s")
        time.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        from app.core.config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    run(cfg)


if __name__ == "__main__":
    main()
