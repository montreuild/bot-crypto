"""Persistance du dernier backtest par slot (stratégie::timeframe).

Chaque lancement de backtest via l'API enregistre un résumé léger des métriques
par slot ``{stratégie}::{timeframe}``. La page Audit OOS lit ces résumés pour
afficher, à côté du score OOS de l'optimiseur, le résultat du dernier backtest
réel de chaque stratégie pour chaque slot.

Stockage : ``data/backtest_history.json`` — un dict ``slot_key → résumé``, le
dernier backtest écrasant le précédent pour le même slot.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_HISTORY_PATH = os.path.join("data", "backtest_history.json")
_lock = threading.Lock()

# Champs conservés du résumé d'un backtest (métriques clés, pas les trades bruts).
_SUMMARY_FIELDS = (
    "total_trades", "win_rate", "total_pnl", "sharpe",
    "max_drawdown", "profit_factor", "final_equity",
    "buy_and_hold_pnl", "alpha",
)


def _path() -> str:
    os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)
    return _HISTORY_PATH


def load_backtest_history() -> dict:
    """Retourne le dict ``slot_key → résumé`` (vide si fichier absent/illisible)."""
    try:
        with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning(f"[backtest_history] lecture KO : {e}")
        return {}


def record_backtest(strategy: str, timeframe: str, symbol: str,
                    result: dict, n_bars: int = 0) -> None:
    """Enregistre/écrase le résumé du dernier backtest d'un slot.

    Thread-safe : le backtest API exécute les stratégies en parallèle (threads).
    Tolérant aux erreurs : un échec d'écriture ne doit jamais casser le backtest.
    """
    if not strategy or not timeframe:
        return
    slot_key = f"{strategy}::{timeframe}"
    summary = {k: result.get(k) for k in _SUMMARY_FIELDS if result.get(k) is not None}
    summary.update({
        "slot_key":  slot_key,
        "strategy":  strategy,
        "timeframe": timeframe,
        "symbol":    symbol,
        "n_bars":    n_bars,
        "run_date":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    })
    try:
        with _lock:
            data = load_backtest_history()
            data[slot_key] = summary
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[backtest_history] écriture KO ({slot_key}) : {e}")
