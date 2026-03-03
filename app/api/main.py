"""
Module 6 — API FastAPI v5 — Routes complètes.
"""
import csv, importlib, io, logging, os, time
from typing import Optional
import pandas as pd
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config     import load_config
from app.core.exchange   import create_exchange
from app.core.database   import init_db, get_trades, DailyStats
from app.engine.engine   import Engine
from app.engine.backtest import Backtester, WalkForwardAnalyzer, MonteCarlo
from app.scanner.scanner import MarketScanner

logger = logging.getLogger(__name__)

# ── Globals ──────────────────────────────────────────────────────────────
cfg         = None
trader      = None
SessionLocal= None
_engine_cache = {}

app = FastAPI(title="Crypto Bot V5", version="5.0.0", docs_url="/docs")

try:
    tpl_path = os.path.join(os.path.dirname(__file__), "..", "web", "templates")
    templates = Jinja2Templates(directory=tpl_path)
except Exception:
    templates = None


def init_app(config: dict, live_trader=None):
    global cfg, trader, SessionLocal
    cfg    = config
    trader = live_trader
    _, SessionLocal = init_db(cfg["database"]["url"])


# ── Auth ─────────────────────────────────────────────────────────────────
async def verify_api_key(request: Request):
    key = cfg["web"].get("api_key", "") if cfg else ""
    if not key: return
    token = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if token != key:
        raise HTTPException(status_code=403, detail="Clé API invalide")


# ═══════════════════════════════════════════════════════════════
#  PAGES WEB
# ═══════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if templates:
        return templates.TemplateResponse("dashboard.html", {"request": request})
    return HTMLResponse("<h1>Crypto Bot V5</h1><p>Dashboard non disponible.</p>")

@app.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request):
    if templates:
        return templates.TemplateResponse("backtest.html", {"request": request})
    return HTMLResponse("<h1>Backtest</h1>")

@app.get("/scanner", response_class=HTMLResponse)
def scanner_page(request: Request):
    if templates:
        return templates.TemplateResponse("scanner.html", {"request": request})
    return HTMLResponse("<h1>Scanner</h1>")

# ═══════════════════════════════════════════════════════════════
#  API — STATUS & CONFIG
# ═══════════════════════════════════════════════════════════════
@app.get("/api/status")
def get_status():
    if not cfg:
        return {"status": "not_started"}
    base = {
        "status":     "running" if (trader and trader.running) else "idle",
        "paper_mode": cfg["trading"].get("paper_mode", True),
        "timeframe":  cfg["trading"]["timeframe"],
        "strategies": cfg["strategies"]["enabled"],
        "capital":    trader.capital_display if trader else cfg["trading"]["capital"],
        "version":    "5.0.0",
    }
    if trader:
        base.update(trader.status)
    return base

@app.get("/api/config")
def get_config():
    if not cfg: raise HTTPException(503, "Config non chargée")
    safe = {k: v for k, v in cfg.items() if k not in ("exchange", "notifications")}
    safe["exchange"] = {"name": cfg["exchange"]["name"]}
    return safe

@app.get("/api/backtest/settings")
def backtest_settings():
    if not cfg: raise HTTPException(503, "Config non chargée")
    return {
        "timeframe":             cfg["trading"]["timeframe"],
        "available_timeframes":  ["1m","3m","5m","15m","30m","1h","2h","4h","1d"],
        "strategies":            cfg["strategies"]["enabled"],
        "strategy_params":       cfg.get("strategy_params", {}),
        "score_threshold":       cfg["trading"].get("score_threshold", 0.55),
        "taker_fee":             cfg["trading"].get("taker_fee", 0.001),
        "maker_fee":             cfg["trading"].get("maker_fee", 0.0004),
        "capital":               cfg["trading"]["capital"],
        "risk_per_trade":        cfg["trading"]["risk_per_trade"],
        "spread_pct":            cfg.get("backtest",{}).get("spread_pct", 0.0005),
        "partial_fill_pct":      cfg.get("backtest",{}).get("partial_fill_pct", 0.95),
    }

# ═══════════════════════════════════════════════════════════════
#  API — TRADES & STATS
# ═══════════════════════════════════════════════════════════════
@app.get("/api/trades", dependencies=[Depends(verify_api_key)])
def list_trades(limit: int = 100, symbol: str = None, strategy: str = None):
    if not SessionLocal: raise HTTPException(503, "DB non initialisée")
    session = SessionLocal()
    try:
        trades = get_trades(session, limit=limit, symbol=symbol, strategy=strategy)
        return [{"id": t.id, "time": str(t.time), "symbol": t.symbol, "side": t.side,
                 "strategy": t.strategy, "entry": t.entry, "exit": t.exit_price,
                 "pnl": t.pnl, "pnl_pct": t.pnl_pct, "fees": t.fees,
                 "status": t.status, "score": t.score, "reason": t.reason} for t in trades]
    finally:
        session.close()

@app.get("/api/trades/export", dependencies=[Depends(verify_api_key)])
def export_trades():
    session = SessionLocal()
    try:
        trades = get_trades(session, limit=50000)
        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow(["id","time","symbol","side","strategy","entry","exit","pnl","fees","status"])
        for t in trades:
            w.writerow([t.id, t.time, t.symbol, t.side, t.strategy,
                        t.entry, t.exit_price, t.pnl, t.fees, t.status])
        out.seek(0)
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=trades.csv"})
    finally:
        session.close()

@app.get("/api/stats/daily", dependencies=[Depends(verify_api_key)])
def daily_stats(days: int = 30):
    session = SessionLocal()
    try:
        rows = session.query(DailyStats).order_by(DailyStats.date.desc()).limit(days).all()
        return [{"date": r.date, "trades": r.trades, "wins": r.wins,
                 "pnl": r.pnl, "fees": r.fees, "equity_close": r.equity_close} for r in rows]
    finally:
        session.close()

@app.get("/api/risk", dependencies=[Depends(verify_api_key)])
def risk_status():
    if not trader: raise HTTPException(503, "Trader non démarré")
    return trader.risk.status_dict()

@app.post("/api/risk/reset-halt", dependencies=[Depends(verify_api_key)])
def reset_halt():
    if not trader: raise HTTPException(503, "Trader non démarré")
    trader.risk.reset_halt()
    return {"status": "ok", "message": "Circuit breaker réinitialisé"}

# ═══════════════════════════════════════════════════════════════
#  API — BACKTEST PRO
# ═══════════════════════════════════════════════════════════════
@app.post("/api/backtest", dependencies=[Depends(verify_api_key)])
def run_backtest(symbol: str = "BTC/USDC", limit: int = 500, timeframe: str = "",
                 walk_forward: bool = False, monte_carlo: bool = False):
    if not cfg: raise HTTPException(503, "Config non chargée")
    try:
        exchange  = create_exchange(cfg)
        tf        = timeframe or cfg["trading"]["timeframe"]
        ohlcv_raw = exchange.fetch_ohlcv(symbol, tf, limit=limit)
        df        = pd.DataFrame(ohlcv_raw, columns=["time","open","high","low","close","volume"])
        df["time"]= pd.to_datetime(df["time"], unit="ms")
        df.reset_index(drop=True, inplace=True)

        ohlcv_payload = {
            "time":   [str(t) for t in df["time"].tolist()],
            "close":  [round(float(v),6) for v in df["close"].tolist()],
            "high":   [round(float(v),6) for v in df["high"].tolist()],
            "low":    [round(float(v),6) for v in df["low"].tolist()],
            "volume": [round(float(v),2) for v in df["volume"].tolist()],
        }

        # ── Structure retournée : by_strategy[name] = stats + trades avec bar correct ──
        # Chaque stratégie est backtestée individuellement.
        # Le champ `trades` contient les objets complets avec bar/exit_bar (indices dans df).
        by_strategy = {}
        for name in cfg["strategies"]["enabled"]:
            try:
                mod = importlib.import_module(f"app.strategies.{name}")
                eng = Engine(); eng.register(mod.Strategy())
                bt  = Backtester(eng, cfg)
                res = bt.run(df, symbol)
                d   = res.to_dict()

                # Extraire les stats de la stratégie depuis by_strategy interne
                # (le backtest individuel aura une seule entrée dans by_strategy)
                strat_key = next(iter(res.by_strategy.keys()), name) if res.by_strategy else name
                strat_data = res.by_strategy.get(strat_key, {})

                # Les trades de strat_data ont bar/exit_bar corrects (issus du backtest)
                all_trades = strat_data.get("trades", [])

                entry_for_strat = {
                    "total_trades":  strat_data.get("total_trades", d["total_trades"]),
                    "win_rate":      strat_data.get("win_rate",     d["win_rate"]),
                    "total_pnl":     strat_data.get("total_pnl",    d["total_pnl"]),
                    "total_fees":    strat_data.get("total_fees",   d["total_fees"]),
                    "max_drawdown":  strat_data.get("max_drawdown", d["max_drawdown"]),
                    "sharpe":        strat_data.get("sharpe",       d["sharpe"]),
                    "expectancy":    strat_data.get("expectancy",   d["expectancy"]),
                    "profit_factor": strat_data.get("profit_factor",d["profit_factor"]),
                    "avg_win":       strat_data.get("avg_win",      d["avg_win"]),
                    "avg_loss":      strat_data.get("avg_loss",     d["avg_loss"]),
                    "initial_capital":d["initial_capital"],
                    "final_equity":  strat_data.get("final_equity", d["final_equity"]),
                    "equity_curve":  strat_data.get("equity_curve", d["equity_curve"]),
                    "avg_mae":       d.get("avg_mae", 0),
                    "avg_mfe":       d.get("avg_mfe", 0),
                    "trades":        all_trades,  # <-- trades COMPLETS avec bar/exit_bar
                }

                if walk_forward and len(df) >= 200:
                    wf = WalkForwardAnalyzer(eng, cfg,
                          n_folds=cfg.get("backtest",{}).get("walk_forward_folds",5))
                    entry_for_strat["walk_forward"] = wf.run(df, symbol)

                if monte_carlo and all_trades:
                    mc = MonteCarlo(n_runs=cfg.get("backtest",{}).get("monte_carlo_runs",200))
                    entry_for_strat["monte_carlo"] = mc.run(all_trades, cfg["trading"]["capital"])

                by_strategy[name] = entry_for_strat

            except Exception as e:
                logger.error(f"[API] Backtest {name} : {e}", exc_info=True)
                by_strategy[name] = {"error": str(e), "trades": []}

        return {
            "symbol":           symbol,
            "timeframe":        tf,
            "limit":            limit,
            "n_bars":           len(df),
            "ohlcv":            ohlcv_payload,
            "by_strategy":      by_strategy,
            "score_threshold":  cfg["trading"].get("score_threshold", 0.55),
        }
    except Exception as e:
        logger.error(f"[API] Backtest error : {e}", exc_info=True)
        raise HTTPException(500, str(e))

# ═══════════════════════════════════════════════════════════════
#  API — SCANNER
# ═══════════════════════════════════════════════════════════════
@app.get("/api/scanner", dependencies=[Depends(verify_api_key)])
def run_scanner(timeframe: str = None, limit: int = 200):
    if not cfg: raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(cfg)
        scanner  = MarketScanner(exchange, cfg)
        tf       = timeframe or cfg["trading"]["timeframe"]
        results  = scanner.screen(tf, limit)
        return {"timeframe": tf, "symbols_scanned": len(results), "results": results}
    except Exception as e:
        raise HTTPException(500, str(e))

# ═══════════════════════════════════════════════════════════════
#  API — OPTIMIZER
# ═══════════════════════════════════════════════════════════════
@app.post("/api/optimize", dependencies=[Depends(verify_api_key)])
def run_optimizer(symbol: str = "BTC/USDC", strategy: str = "trend",
                  method: str = "random", n_trials: int = 30, limit: int = 1000):
    if not cfg: raise HTTPException(503, "Config non chargée")
    try:
        from app.optimizer.optimizer import StrategyOptimizer, DEFAULT_SPACES
        exchange = create_exchange(cfg)
        ohlcv    = exchange.fetch_ohlcv(symbol, cfg["trading"]["timeframe"], limit=limit)
        df       = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"]= pd.to_datetime(df["time"], unit="ms")
        # IS = 70%, OOS = 30%
        split    = int(len(df) * 0.7)
        df_is, df_oos = df.iloc[:split], df.iloc[split:]
        space    = DEFAULT_SPACES.get(strategy, {})
        opt      = StrategyOptimizer(strategy, cfg, df_is, df_oos, space)
        if method == "grid":
            result = opt.grid_search()
        elif method == "bayesian":
            result = opt.bayesian_search(n_trials)
        else:
            result = opt.random_search(n_trials)
        return {"symbol": symbol, "strategy": strategy, "method": method,
                "n_trials": n_trials, **result}
    except Exception as e:
        logger.error(f"[API] Optimizer error : {e}")
        raise HTTPException(500, str(e))

# ═══════════════════════════════════════════════════════════════
#  API — ML
# ═══════════════════════════════════════════════════════════════
@app.post("/api/ml/train", dependencies=[Depends(verify_api_key)])
def train_ml(symbol: str = "BTC/USDC", limit: int = 2000):
    if not cfg: raise HTTPException(503, "Config non chargée")
    try:
        from app.ml.model import MLPredictor
        exchange = create_exchange(cfg)
        ohlcv    = exchange.fetch_ohlcv(symbol, cfg["trading"]["timeframe"], limit=limit)
        df       = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"]= pd.to_datetime(df["time"], unit="ms")
        ml = MLPredictor(cfg)
        metrics = ml.train(df)
        ml.save()
        return {"status": "trained", "symbol": symbol, **metrics}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/ml/optimize", dependencies=[Depends(verify_api_key)])
def optimize_ml(symbol: str = "BTC/USDC", limit: int = 1000, n_trials: int = 15):
    """Optimise les hyperparamètres de la stratégie ML par Random Search."""
    if not cfg: raise HTTPException(503, "Config non chargée")
    try:
        from app.strategies.ml_strategy import optimize_ml_strategy
        exchange = create_exchange(cfg)
        ohlcv    = exchange.fetch_ohlcv(symbol, cfg["trading"]["timeframe"], limit=limit)
        df       = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"]= pd.to_datetime(df["time"], unit="ms")
        result   = optimize_ml_strategy(df, cfg, n_outer=n_trials)
        return {"status": "done", "symbol": symbol, **result}
    except Exception as e:
        logger.error(f"[API] ML optimize : {e}", exc_info=True)
        raise HTTPException(500, str(e))


@app.get("/api/ml/strategy-info", dependencies=[Depends(verify_api_key)])
def ml_strategy_info():
    """Retourne les infos sur la stratégie ML (features, paramètres)."""
    from app.strategies.ml_strategy import (compute_features, ML_STRATEGY_PARAM_SPACE,
                                             PARAM_SPACE_RF, PARAM_SPACE_LR)
    import pandas as pd, numpy as np
    dummy = pd.DataFrame({
        "time":   pd.date_range("2024-01-01", periods=100, freq="15min"),
        "open":   np.random.uniform(60000,70000,100),
        "high":   np.random.uniform(61000,71000,100),
        "low":    np.random.uniform(59000,69000,100),
        "close":  np.random.uniform(60000,70000,100),
        "volume": np.random.uniform(1e6,1e8,100),
    })
    feats = compute_features(dummy)
    return {
        "n_features":         len(feats.columns),
        "feature_names":      list(feats.columns),
        "strategy_params":    {k: v for k, v in ML_STRATEGY_PARAM_SPACE.items()},
        "rf_hyperparams":     {k: v for k, v in PARAM_SPACE_RF.items()},
        "lr_hyperparams":     {k: v for k, v in PARAM_SPACE_LR.items()},
    }
