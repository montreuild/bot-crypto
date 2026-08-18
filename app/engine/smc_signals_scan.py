"""Scan périodique des signaux SMC récents (Top opportunités).

Lit le cache OHLCV (pas de fetch réseau), exécute ``smart_money.trade_plans``
sur 1h / 4h / 1d, filtre les plans dont le ``signal_time`` a < N jours, et
écrit ``data/smc_signals_recent.json``.

Démarré en thread daemon depuis l'API (``init_app`` / lifespan).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tous les TF trading actifs (alignés smart_money.timeframes / risk.yaml)
_DEFAULT_TFS = ("15m", "30m", "1h", "4h", "1d")
_DEFAULT_MAX_AGE_DAYS = 5
_DEFAULT_INTERVAL_S = 30 * 60  # 30 min
_OUTPUT = Path("data/smc_signals_recent.json")

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _repo_output() -> Path:
    # CWD conteneur = /app ; hors conteneur = racine projet
    p = Path("data")
    p.mkdir(parents=True, exist_ok=True)
    return p / "smc_signals_recent.json"


def _epoch_now() -> int:
    return int(time.time())


def _signal_ok(signal_time: Any, max_age_s: int, now: int) -> bool:
    if signal_time is None:
        return False
    try:
        st = int(signal_time)
    except (TypeError, ValueError):
        return False
    if st > 1e12:  # ms
        st //= 1000
    return 0 <= (now - st) <= max_age_s


def scan_once(
    cfg: dict,
    *,
    tfs: tuple | None = None,
    max_age_days: float = _DEFAULT_MAX_AGE_DAYS,
    max_symbols: int = 80,
    top_n: int = 80,
) -> Dict[str, Any]:
    """Exécute un scan complet et renvoie le payload (écrit aussi sur disque)."""
    from app.core.candle_store import get_store
    from app.core.indicators import precompute_df
    from app.core.param_resolution import resolve_strategy_params
    from app.engine.scanner import MarketScanner
    from app.strategies.smart_money import Strategy as SMCStrategy

    # TF : arg > trading.timeframes config > défauts multi-TF
    if not tfs:
        try:
            cfg_tfs = (cfg.get("trading") or {}).get("timeframes") or []
            tfs = tuple(str(x) for x in cfg_tfs) if cfg_tfs else _DEFAULT_TFS
        except Exception:
            tfs = _DEFAULT_TFS
    # Toujours couvrir les TF smart_money connus
    for extra in _DEFAULT_TFS:
        if extra not in tfs:
            tfs = tuple(list(tfs) + [extra])

    now = _epoch_now()
    max_age_s = int(max_age_days * 86400)
    store = get_store()

    try:
        # Univers config (static + universe YAML) — pas d'exchange obligatoire
        symbols: List[str] = []
        try:
            symbols = MarketScanner(None, cfg).get_symbols()
        except Exception as e:
            logger.debug(f"[SMCSignals] get_symbols KO : {e}")
        if not symbols:
            # fallback : dossiers sous data/ohlcv (ex. BTC_USDC/1h.parquet)
            ohlcv_root = Path("data/ohlcv")
            if ohlcv_root.is_dir():
                seen = set()
                for pq in ohlcv_root.rglob("*.parquet"):
                    parent = pq.parent.name
                    if parent and parent != "ohlcv":
                        if "." in parent:  # AIR.PA, etc.
                            seen.add(parent)
                        else:
                            seen.add(parent.replace("_", "/"))
                symbols = sorted(seen)
        symbols = list(symbols)[:max_symbols]

        strat = SMCStrategy()
        signals: List[Dict[str, Any]] = []

        for symbol in symbols:
            for tf in tfs:
                try:
                    df = store.load_cached(symbol, tf)
                    # LTF (15m/30m) : historique court (Yahoo ~88 barres) —
                    # seuil assoupli pour ne pas tout exclure.
                    min_bars = 80 if tf in ("15m", "30m") else 200
                    if df is None or len(df) < min_bars:
                        continue
                    if len(df) > 800:
                        df = df.tail(800)
                    try:
                        df = precompute_df(df)
                    except Exception:
                        pass
                    resolved = resolve_strategy_params(cfg, tf, symbol)
                    plans = strat.trade_plans(df, resolved) or []
                    for plan in plans:
                        st = plan.get("signal_time")
                        if not _signal_ok(st, max_age_s, now):
                            # Plans « immediate » sans time : utiliser last bar
                            if plan.get("status") == "immediate" and "time" in df.columns:
                                try:
                                    t = df["time"][-1]
                                    st = int(t.timestamp()) if hasattr(t, "timestamp") else int(t)
                                except Exception:
                                    continue
                                if not _signal_ok(st, max_age_s, now):
                                    continue
                            else:
                                continue
                        signals.append({
                            "symbol": symbol,
                            "timeframe": tf,
                            "side": plan.get("side"),
                            "setup": plan.get("setup"),
                            "status": plan.get("status"),
                            "entry": plan.get("entry"),
                            "stop": plan.get("stop"),
                            "tp": plan.get("tp"),
                            "score_min": plan.get("score_min"),
                            "gain_pct": plan.get("gain_pct"),
                            "rr": plan.get("rr"),
                            "distance_pct": plan.get("distance_pct"),
                            "signal_time": int(st) if st is not None else None,
                            "trigger": plan.get("trigger"),
                            "reason": plan.get("reason"),
                            "kind": "smc",
                            "kind_label": "SMC",
                            "strategy": "smart_money",
                        })
                except Exception as e:
                    logger.debug(f"[SMCSignals] {symbol}/{tf} : {e}")

        # Tri : score puis fraîcheur
        signals.sort(
            key=lambda x: (
                float(x.get("score_min") or 0),
                float(x.get("signal_time") or 0),
            ),
            reverse=True,
        )
        signals = signals[:top_n]
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_ts": now,
            "max_age_days": max_age_days,
            "timeframes": list(tfs),
            "count": len(signals),
            "signals": signals,
        }
        out = _repo_output()
        with _lock:
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[SMCSignals] {len(signals)} signaux écrits → {out}")
        return payload
    except Exception as e:
        logger.error(f"[SMCSignals] scan_once KO : {e}", exc_info=True)
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_ts": now,
            "count": 0,
            "signals": [],
            "error": str(e),
        }


def load_recent() -> Dict[str, Any]:
    """Lit le JSON sur disque (vide si absent)."""
    path = _repo_output()
    if not path.exists():
        return {"count": 0, "signals": [], "updated_at": None}
    try:
        with _lock:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[SMCSignals] lecture KO : {e}")
        return {"count": 0, "signals": [], "error": str(e)}


def _loop(cfg_provider, interval_s: float):
    # Premier scan après un court délai (laisser l'API démarrer)
    if _stop.wait(15):
        return
    while not _stop.is_set():
        try:
            cfg = cfg_provider() if callable(cfg_provider) else cfg_provider
            if cfg:
                scan_once(cfg)
        except Exception as e:
            logger.error(f"[SMCSignals] loop : {e}", exc_info=True)
        if _stop.wait(interval_s):
            break


def start_background(cfg_provider, interval_s: float = _DEFAULT_INTERVAL_S) -> None:
    """Démarre le thread daemon (idempotent)."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(cfg_provider, interval_s),
            name="smc-signals-scan",
            daemon=True,
        )
        _thread.start()
        logger.info(f"[SMCSignals] background job démarré (interval={interval_s}s)")


def stop_background() -> None:
    _stop.set()
