"""Travailleurs pickleables pour les calculs lourds (A-02).

Ces fonctions tournent dans un process enfant (spawn). Elles n'importent
pas ``app.api.state`` : tout le contexte utile voyage dans ``payload``.

Le fetch OHLCV reste dans le process API. Ici : Backtester / dual-pass /
walk-forward / Monte-Carlo uniquement.
"""
from __future__ import annotations

import importlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _envelope_payload(env) -> Optional[dict]:
    """Sérialise l'enveloppe pour l'UI « Étude vs Réel ».

    La carte lit ``risk_amount`` et ``min_notional`` : les omettre faisait
    planter le Laboratoire (``undefined.toFixed``) dès qu'un backtest
    dual-pass renvoyait un slot. Aligné sur ``app.api.routes.backtest``.
    """
    if env is None:
        return None
    if isinstance(env, dict):
        slot = float(env.get("slot_envelope") or 0.0)
        pct = float(env.get("trade_risk_pct") or 0.0)
        risk = env.get("risk_amount", env.get("slot_risk_amount"))
        if risk is None:
            risk = slot * pct
        return {
            "venue": env.get("venue", ""),
            "symbol": env.get("symbol", ""),
            "currency": env.get("currency", ""),
            "slot_envelope": round(slot, 4),
            "weight": round(float(env.get("weight") or 0.0), 4),
            "trade_risk_pct": pct,
            "risk_amount": round(float(risk or 0.0), 4),
            "min_notional": float(env.get("min_notional") or 0.0),
        }
    return {
        "venue": env.venue, "symbol": env.symbol, "currency": env.currency,
        "slot_envelope": round(env.slot_envelope, 4),
        "weight": round(env.weight, 4),
        "trade_risk_pct": env.trade_risk_pct,
        "risk_amount": round(env.slot_risk_amount, 4),
        "min_notional": env.min_notional,
    }


def _pass_summary(res) -> dict:
    base = res.initial_capital or 0.0
    return {
        "initial_capital": round(base, 4),
        "total_trades":    res.total_trades,
        "total_pnl":       round(res.total_pnl, 4),
        "pnl_pct":         round(res.total_pnl / base * 100, 4) if base else 0.0,
        "max_drawdown":    round(res.max_drawdown, 4),
        "rejections":      res.rejections,
    }


def _entry_from_result(res, name: str, days_covered, bars_warning,
                       env=None, runs_payload=None) -> dict:
    d = res.to_dict()
    strat_key = next(iter(res.by_strategy.keys()), name) if res.by_strategy else name
    strat_data = res.by_strategy.get(strat_key, {})
    all_trades = strat_data.get("trades", [])
    return {
        "total_trades":        strat_data.get("total_trades",  d["total_trades"]),
        "win_rate":            strat_data.get("win_rate",      d["win_rate"]),
        "total_pnl":           strat_data.get("total_pnl",     d["total_pnl"]),
        "total_fees":          strat_data.get("total_fees",    d["total_fees"]),
        "total_borrow_cost":   d.get("total_borrow_cost", 0.0),
        "total_slippage_cost": d.get("total_slippage_cost", 0.0),
        "max_drawdown":        strat_data.get("max_drawdown",  d["max_drawdown"]),
        "sharpe":              strat_data.get("sharpe",        d["sharpe"]),
        "realistic_risk_diagnostics": d.get("realistic_risk_diagnostics"),
        "sortino":             d.get("sortino", 0.0),
        "calmar":              d.get("calmar", 0.0),
        "cagr":                d.get("cagr", 0.0),
        "alpha_vs_bh":         d.get("alpha_vs_bh", 0.0),
        "expectancy":          strat_data.get("expectancy",    d["expectancy"]),
        "profit_factor":       strat_data.get("profit_factor", d["profit_factor"]),
        "avg_win":             strat_data.get("avg_win",       d.get("avg_win", 0)),
        "avg_loss":            strat_data.get("avg_loss",      d.get("avg_loss", 0)),
        "initial_capital":     d["initial_capital"],
        "final_equity":        strat_data.get("final_equity",  d["final_equity"]),
        "equity_curve":        strat_data.get("equity_curve",  d.get("equity_curve", [])),
        "buy_and_hold_pnl":    d.get("buy_and_hold_pnl"),
        "buy_and_hold_pct":    d.get("buy_and_hold_pct"),
        "alpha":               d.get("alpha"),
        "trades":              all_trades,
        "days_covered":        days_covered,
        "bars_warning":        bars_warning,
        "diagnostics":         d.get("diagnostics"),
        "rejections":          d.get("rejections"),
        "envelope":            _envelope_payload(env),
        "runs":                runs_payload,
    }


def run_strategy_job(payload: dict) -> tuple:
    """Exécute un backtest (éventuellement WF/MC/dual) pour UNE stratégie.

    ``payload`` est un dict pickleable. Retourne ``(name, entry)``.
    """
    name = payload["name"]
    cancel = payload.get("cancel_event")
    try:
        if cancel is not None and cancel.is_set():
            return name, {"error": "Backtest annulé", "trades": []}

        from app.core.risk_gate import _default_venue_capital
        from app.engine.backtest import (
            Backtester,
            MonteCarlo,
            WalkForwardAnalyzer,
            run_dual_pass,
        )
        from app.engine.engine import Engine

        cfg = payload["cfg"]
        df = payload["df"]
        symbol = payload["symbol"]
        tf = payload["timeframe"]
        realistic_risk = bool(payload.get("realistic_risk"))
        dual_pass = bool(payload.get("dual_pass"))
        env = payload.get("envelope")
        walk_forward = bool(payload.get("walk_forward"))
        monte_carlo = bool(payload.get("monte_carlo"))
        days_covered = payload.get("days_covered")
        bars_warning = payload.get("bars_warning")
        wf_folds = int(payload.get("wf_folds") or 5)
        mc_runs = int(payload.get("mc_runs") or 200)

        mod = importlib.import_module(f"app.strategies.{name}")
        inst = mod.Strategy()
        if hasattr(inst, "_cancel_event") and cancel is not None:
            inst._cancel_event = cancel
        eng = Engine()
        eng.register(inst, silent=True)

        runs_payload = None
        if dual_pass and env is not None:
            runs = run_dual_pass(
                eng, cfg, df, env, symbol=symbol, timeframe=tf,
                cancel_event=cancel, realistic_risk=realistic_risk,
            )
            res = runs["live"]
            runs_payload = {k: _pass_summary(r) for k, r in runs.items()}
            runs_payload["ecart_pnl_pct"] = round(
                runs_payload["live"]["pnl_pct"] - runs_payload["reference"]["pnl_pct"], 4)
        else:
            bt = Backtester(eng, cfg, cancel_event=cancel,
                            realistic_risk=realistic_risk)
            res = bt.run(df, symbol, timeframe=tf)

        entry = _entry_from_result(
            res, name, days_covered, bars_warning, env=env, runs_payload=runs_payload)

        if walk_forward and len(df) >= 200:
            wf = WalkForwardAnalyzer(eng, cfg, n_folds=wf_folds)
            entry["walk_forward"] = wf.run(df, symbol, timeframe=tf)

        all_trades = entry.get("trades") or []
        if monte_carlo and all_trades:
            mc = MonteCarlo(n_runs=mc_runs)
            entry["monte_carlo"] = mc.run(all_trades, _default_venue_capital(cfg))

        return name, entry
    except InterruptedError:
        return name, {"error": "Backtest annulé", "trades": []}
    except Exception as e:
        logger.error("[compute] %s KO : %s", name, e, exc_info=True)
        return name, {"error": str(e), "trades": []}
