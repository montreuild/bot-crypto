"""Route replay — rejeu multi-timeframe sur N mois pour validation."""
import importlib
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException, Request
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

from app.core.timeframes import TF_MINUTES as _TF_MINUTES  # V4-A : source unique


def _months_to_bars(months: float, tf: str) -> int:
    """Convertit N mois en nombre de bougies selon le timeframe."""
    mins_per_bar = _TF_MINUTES.get(tf, 60)
    return max(100, int(months * 30 * 24 * 60 / mins_per_bar))


@router.post("/api/replay/cancel", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def cancel_replay(request: Request):
    """Annule le replay en cours."""
    state._rp_cancel_event.set()
    return {"status": "cancelling"}


@router.post("/api/replay", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("10/minute")
def run_replay(
    request:      Request,
    symbol:       str   = DEFAULT_CONFIG_SYMBOL,
    months:       float = 6.0,
    timeframes:   str   = "1h,4h,1d",
    strategies:   str   = "",
    walk_forward: bool  = False,
    monte_carlo:  bool  = False,
):
    """Lance un rejeu des bougies sur N mois pour plusieurs timeframes."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    if not state._rp_semaphore.acquire(blocking=False):
        raise HTTPException(429, "Un replay est déjà en cours.")
    state._rp_cancel_event.clear()

    try:
        # Rechargement config à chaud
        try:
            from app.core.config import load_config as _reload
            state.cfg.update(_reload("config.yaml"))
        except Exception as e:
            logger.warning(f"[replay] rechargement config KO : {e}")

        months = max(0.5, min(float(months), 24.0))

        tfs_raw   = [t.strip() for t in timeframes.split(",") if t.strip()]
        valid_tfs = [tf for tf in tfs_raw if tf in _TF_MINUTES]
        if not valid_tfs:
            raise HTTPException(400, "Aucun timeframe valide (ex: 1h,4h,1d)")

        _ALLOWED = _discover_strategies()
        strats_raw = (
            [s.strip() for s in strategies.split(",") if s.strip()]
            if strategies.strip()
            else state.cfg["strategies"]["enabled"]
        )
        strats_to_run = [s for s in strats_raw if s in _ALLOWED]
        if not strats_to_run:
            raise HTTPException(400, "Aucune stratégie valide")

        exchange        = _get_bt_exchange(state.cfg)
        by_timeframe    = {}
        cross_tf_summary = []

        for tf in valid_tfs:
            if state._rp_cancel_event.is_set():
                raise HTTPException(499, "Replay annulé par l'utilisateur")

            n_bars = _months_to_bars(months, tf)
            if tf == "1d":
                n_bars = min(n_bars, 5000)

            df = get_store().fetch(exchange, symbol, tf, total=n_bars)
            if df is None or len(df) == 0:
                by_timeframe[tf] = {"error": f"Aucune donnée disponible pour {symbol}/{tf}"}
                continue

            actual_bars  = len(df)
            tf_mins      = _TF_MINUTES.get(tf, 60)
            days_covered = round(actual_bars * tf_mins / 1440, 1)
            date_from    = str(df["time"][0])[:16]
            date_to      = str(df["time"][-1])[:16]

            ohlcv_payload = {
                "time":   df["time"].dt.epoch(time_unit="s").to_list(),
                "close":  [round(float(v), 6) for v in df["close"].to_list()],
                "open":   [round(float(v), 6) for v in df["open"].to_list()],
                "high":   [round(float(v), 6) for v in df["high"].to_list()],
                "low":    [round(float(v), 6) for v in df["low"].to_list()],
            }

            mc_runner   = (
                MonteCarlo(n_runs=state.cfg.get("backtest", {}).get("monte_carlo_runs", 200))
                if monte_carlo else None
            )
            by_strategy = {}

            def _run_one(name: str, _df=df, _tf=tf) -> tuple:
                try:
                    if state._rp_cancel_event.is_set():
                        return name, {"error": "Annulé"}
                    mod  = importlib.import_module(f"app.strategies.{name}")
                    inst = mod.Strategy()
                    if hasattr(inst, "_cancel_event"):
                        inst._cancel_event = state._rp_cancel_event
                    eng = Engine()
                    eng.register(inst, silent=True)
                    bt  = Backtester(eng, state.cfg, cancel_event=state._rp_cancel_event)
                    res = bt.run(_df, symbol, timeframe=_tf)
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
                        "buy_and_hold_pct": d.get("buy_and_hold_pct", 0),
                        "trades":           all_trades,
                        "days_covered":     days_covered,
                    }

                    if walk_forward and actual_bars >= 200:
                        wf = WalkForwardAnalyzer(
                            eng, state.cfg,
                            n_folds=state.cfg.get("backtest", {}).get("walk_forward_folds", 5)
                        )
                        entry["walk_forward"] = wf.run(_df, symbol)

                    if mc_runner and all_trades:
                        entry["monte_carlo"] = mc_runner.run(
                            all_trades, state.cfg["trading"]["capital"]
                        )

                    return name, entry

                except Exception as e:
                    logger.error(f"[Replay] {name}/{_tf} : {e}", exc_info=True)
                    return name, {"error": str(e), "trades": []}

            with ThreadPoolExecutor(max_workers=min(len(strats_to_run), 4)) as pool:
                futures = {pool.submit(_run_one, n): n for n in strats_to_run}
                for fut in as_completed(futures):
                    if state._rp_cancel_event.is_set():
                        for f in futures:
                            f.cancel()
                        raise HTTPException(499, "Replay annulé par l'utilisateur")
                    name, result = fut.result()
                    by_strategy[name] = result

                    if "error" not in result and result.get("total_trades", 0) > 0:
                        cap = result.get("initial_capital", 1) or 1
                        cross_tf_summary.append({
                            "tf":            tf,
                            "strategy":      name,
                            "n_bars":        actual_bars,
                            "days_covered":  days_covered,
                            "trades":        result["total_trades"],
                            "win_rate":      result["win_rate"],
                            "pnl":           result["total_pnl"],
                            "pnl_pct":       round(result["total_pnl"] / cap * 100, 2),
                            "sharpe":        result["sharpe"],
                            "max_drawdown":  result["max_drawdown"],
                            "profit_factor": result["profit_factor"],
                            "final_equity":  result["final_equity"],
                        })

            ohlcv_gaps   = detect_ohlcv_gaps(df, tf)
            gaps_warning = None
            if ohlcv_gaps:
                total_missing = sum(g["gap_bars"] for g in ohlcv_gaps)
                gaps_warning  = (
                    f"⚠ {len(ohlcv_gaps)} gap(s) — {total_missing} bougies manquantes."
                )

            by_timeframe[tf] = {
                "n_bars":       actual_bars,
                "date_from":    date_from,
                "date_to":      date_to,
                "days_covered": days_covered,
                "ohlcv":        ohlcv_payload,
                "by_strategy":  by_strategy,
                "gaps_warning": gaps_warning,
            }

        cross_tf_summary.sort(key=lambda x: x["pnl"], reverse=True)

        payload = {
            "symbol":            symbol,
            "months":            months,
            "timeframes_tested": valid_tfs,
            "strategies_tested": strats_to_run,
            "by_timeframe":      by_timeframe,
            "cross_tf_summary":  cross_tf_summary,
        }
        return JSONResponse(content=_clean(payload))

    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} replay : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
    finally:
        state._rp_semaphore.release()
