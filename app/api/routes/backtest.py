"""Route backtest — POST /api/backtest."""
import importlib
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import (
    verify_api_key, _clean, _discover_strategies, _get_bt_exchange, detect_ohlcv_gaps
)
from app.core.candle_store import get_store
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.engine.engine import Engine
from app.engine.backtest import Backtester, WalkForwardAnalyzer, MonteCarlo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/backtest/cancel", dependencies=[Depends(verify_api_key)])
def cancel_backtest():
    """Annule le backtest en cours en levant le signal d'arrêt."""
    state._bt_cancel_event.set()
    return {"status": "cancelling"}


@router.get("/api/backtest/status", dependencies=[Depends(verify_api_key)])
def backtest_status():
    """Indique si un backtest est en cours côté serveur.

    Utilisé par l'UI au chargement de la page pour désactiver le bouton 'Lancer'
    si un backtest tourne déjà (ex. après reload pendant un long backtest).
    """
    # Le sémaphore est libre = pas de backtest en cours ; on l'acquiert puis on
    # le relâche aussitôt — le pattern évite une race avec ``run_backtest``.
    running = not state._bt_semaphore.acquire(blocking=False)
    if not running:
        state._bt_semaphore.release()
    return {"running": running}



@router.post("/api/backtest", dependencies=[Depends(verify_api_key)])
def run_backtest(
    symbol:       str  = DEFAULT_CONFIG_SYMBOL,
    limit:        int  = 500,
    timeframe:    str  = "",
    walk_forward: bool = False,
    monte_carlo:  bool = False,
    strategies:   str  = "",
):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    if not state._bt_semaphore.acquire(blocking=False):
        raise HTTPException(429, "Un backtest est déjà en cours.")
    state._bt_cancel_event.clear()
    try:
        from app.core.config import load_config as _reload_cfg
        try:
            state.cfg.update(_reload_cfg("config.yaml"))
        except Exception as e:
            logger.warning(f"[backtest] rechargement config KO : {e}")

        tf    = timeframe.strip() or state.cfg["trading"].get("timeframe", "1h")
        limit = max(100, min(limit, 50000))
        if tf == "1d":
            limit = min(limit, 5000)

        exchange = _get_bt_exchange(state.cfg)
        df       = get_store().fetch(exchange, symbol, tf, total=limit, prefer_cache=True)
        if df is None or len(df) == 0:
            raise HTTPException(400, f"Aucune donnée disponible pour {symbol}/{tf}")

        ohlcv_payload = {
            "time":   df["time"].dt.epoch(time_unit="s").to_list(),
            "open":   [round(float(v), 6) for v in df["open"].to_list()],
            "close":  [round(float(v), 6) for v in df["close"].to_list()],
            "high":   [round(float(v), 6) for v in df["high"].to_list()],
            "low":    [round(float(v), 6) for v in df["low"].to_list()],
            "volume": [round(float(v), 2) for v in df["volume"].to_list()],
        }

        _ALLOWED_STRATS = _discover_strategies()
        strats_to_run   = (
            [s.strip() for s in strategies.split(",") if s.strip()]
            if strategies.strip()
            else state.cfg["strategies"]["enabled"]
        )
        invalid = [s for s in strats_to_run if s not in _ALLOWED_STRATS]
        if invalid:
            raise HTTPException(400, f"Stratégie(s) inconnue(s) : {', '.join(invalid)}")
        strats_to_run = [s for s in strats_to_run if s in _ALLOWED_STRATS]

        tf_mins      = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
                        "1h": 60, "4h": 240, "1d": 1440}
        days_covered = round(len(df) * tf_mins.get(tf, 60) / 1440, 1)
        _bars_warning = None
        if days_covered < 30:
            _bars_warning = (
                f"⚠ Seulement {days_covered} jours de données ({len(df)} bougies × {tf}). "
                f"Visez ≥ 90 jours pour des résultats fiables."
            )

        by_strategy = {}

        def _run_one(name: str) -> tuple:
            try:
                if state._bt_cancel_event.is_set():
                    return name, {"error": "Backtest annulé", "trades": []}
                mod  = importlib.import_module(f"app.strategies.{name}")
                inst = mod.Strategy()
                # Pass cancel event to ML strategies so they can abort training
                if hasattr(inst, '_cancel_event'):
                    inst._cancel_event = state._bt_cancel_event
                eng = Engine()
                eng.register(inst, silent=True)
                bt  = Backtester(eng, state.cfg, cancel_event=state._bt_cancel_event)
                res = bt.run(df, symbol, timeframe=tf)
                d   = res.to_dict()
                strat_key  = next(iter(res.by_strategy.keys()), name) if res.by_strategy else name
                strat_data = res.by_strategy.get(strat_key, {})
                all_trades = strat_data.get("trades", [])
                entry = {
                    "total_trades":     strat_data.get("total_trades",  d["total_trades"]),
                    "win_rate":         strat_data.get("win_rate",      d["win_rate"]),
                    "total_pnl":        strat_data.get("total_pnl",     d["total_pnl"]),
                    "total_fees":       strat_data.get("total_fees",    d["total_fees"]),
                    "max_drawdown":     strat_data.get("max_drawdown",  d["max_drawdown"]),
                    "sharpe":           strat_data.get("sharpe",        d["sharpe"]),
                    "expectancy":       strat_data.get("expectancy",    d["expectancy"]),
                    "profit_factor":    strat_data.get("profit_factor", d["profit_factor"]),
                    "avg_win":          strat_data.get("avg_win",       d.get("avg_win", 0)),
                    "avg_loss":         strat_data.get("avg_loss",      d.get("avg_loss", 0)),
                    "initial_capital":  d["initial_capital"],
                    "final_equity":     strat_data.get("final_equity",  d["final_equity"]),
                    "equity_curve":     strat_data.get("equity_curve",  d.get("equity_curve", [])),
                    "buy_and_hold_pnl": d.get("buy_and_hold_pnl"),
                    "buy_and_hold_pct": d.get("buy_and_hold_pct"),
                    "alpha":            d.get("alpha"),
                    "trades":           all_trades,
                    "days_covered":     days_covered,
                    "bars_warning":     _bars_warning,
                    "diagnostics":      d.get("diagnostics"),
                }
                if walk_forward and len(df) >= 200:
                    wf = WalkForwardAnalyzer(
                        eng, state.cfg,
                        n_folds=state.cfg.get("backtest", {}).get("walk_forward_folds", 5)
                    )
                    entry["walk_forward"] = wf.run(df, symbol)
                if monte_carlo and all_trades:
                    mc = MonteCarlo(
                        n_runs=state.cfg.get("backtest", {}).get("monte_carlo_runs", 200)
                    )
                    entry["monte_carlo"] = mc.run(all_trades, state.cfg["trading"]["capital"])
                # Persiste le résumé du dernier backtest pour ce slot (strategy::tf)
                # → consommé par la page Audit OOS. Non bloquant.
                try:
                    from app.core.backtest_history import record_backtest
                    record_backtest(name, tf, symbol, entry, n_bars=len(df))
                except Exception as _rec_e:
                    logger.debug(f"[backtest] record_backtest({name}) KO : {_rec_e}")
                return name, entry
            except InterruptedError:
                return name, {"error": "Backtest annulé", "trades": []}
            except Exception as e:
                logger.error(f"[API] Backtest {name} : {e}", exc_info=True)
                return name, {"error": str(e), "trades": []}

        with ThreadPoolExecutor(max_workers=min(len(strats_to_run), 4)) as pool:
            futures = {pool.submit(_run_one, n): n for n in strats_to_run}
            for fut in as_completed(futures):
                if state._bt_cancel_event.is_set():
                    # Cancel remaining futures
                    for f in futures:
                        f.cancel()
                    raise HTTPException(499, "Backtest annulé par l'utilisateur")
                name, result = fut.result()
                by_strategy[name] = result

        ohlcv_gaps   = detect_ohlcv_gaps(df, tf)
        gaps_warning = None
        if ohlcv_gaps:
            total_missing = sum(g["gap_bars"] for g in ohlcv_gaps)
            gaps_warning  = (
                f"⚠ {len(ohlcv_gaps)} gap(s) détecté(s) — "
                f"{total_missing} bougies manquantes."
            )

        payload = {
            "symbol":          symbol,
            "timeframe":       tf,
            "limit":           limit,
            "n_bars":          len(df),
            "date_from":       str(df["time"][0])[:16] if len(df) else "",
            "date_to":         str(df["time"][-1])[:16] if len(df) else "",
            "ohlcv":           ohlcv_payload,
            "by_strategy":     by_strategy,
            "score_threshold": state.cfg["trading"].get("score_threshold", 0.55),
            "ohlcv_gaps":      ohlcv_gaps[:20],
            "gaps_warning":    gaps_warning,
        }
        return JSONResponse(content=_clean(payload))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} backtest : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
    finally:
        state._bt_semaphore.release()
