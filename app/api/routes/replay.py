"""Route replay — rejeu multi-timeframe sur N mois pour validation."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import _clean, _discover_strategies, _get_bt_exchange, detect_ohlcv_gaps, verify_api_key
from app.core.candle_store import get_store
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.core.timeframes import TF_MINUTES as _TF_MINUTES  # V4-A : source unique

logger = logging.getLogger(__name__)
router = APIRouter()


def _months_to_bars(months: float, tf: str) -> int:
    """Convertit N mois en nombre de bougies selon le timeframe."""
    mins_per_bar = _TF_MINUTES.get(tf, 60)
    return max(100, int(months * 30 * 24 * 60 / mins_per_bar))


@router.post("/api/replay/cancel", dependencies=[Depends(verify_api_key)])
@state.limiter.limit("30/minute")
def cancel_replay(request: Request):
    """Annule le replay en cours."""
    state._rp_cancel_event.set()
    from app.engine.compute_pool import request_cancel
    request_cancel()
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

            by_strategy = {}
            from app.engine.compute_pool import clear_cancel, map_jobs
            clear_cancel()
            bt_cfg = state.cfg.get("backtest") or {}
            payloads = [{
                "name": name,
                "cfg": state.cfg,
                "df": df,
                "symbol": symbol,
                "timeframe": tf,
                "realistic_risk": False,
                "dual_pass": False,
                "envelope": None,
                "walk_forward": walk_forward,
                "monte_carlo": monte_carlo,
                "days_covered": days_covered,
                "bars_warning": None,
                "wf_folds": bt_cfg.get("walk_forward_folds", 5),
                "mc_runs": bt_cfg.get("monte_carlo_runs", 200),
            } for name in strats_to_run]
            computed = map_jobs(payloads, max_workers=min(len(strats_to_run), 4))
            if state._rp_cancel_event.is_set():
                raise HTTPException(499, "Replay annulé par l'utilisateur")
            for name, result in computed:
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

            ohlcv_gaps   = detect_ohlcv_gaps(df, tf, symbol=symbol)
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
