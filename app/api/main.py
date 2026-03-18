"""
API FastAPI — V9 (Multi-Timeframe)

Nouveautés V9 :
  - Pages web : scanner.html ajoutée, liens nav mis à jour
  - /api/status   : active_per_tf dans la réponse
  - /api/config   : timeframes + optimizer_results exposés
  - /api/config/timeframes POST : changement des TFs à chaud
  - /api/optimize/start  : accepte maintenant plusieurs TFs
  - /api/optimize/results GET : résultats classés par (strategy, tf)
  - /api/scanner/opportunities GET
  - /api/scanner/config GET
  - /health : vérification de l'état du service
  - Sécurité : whitelist stratégies, aucune injection module possible
  - Cache TTL sur la découverte de stratégies
  - Lock threading sur les écritures config.yaml
"""
import csv, glob, importlib, io, json, logging, os, time
from typing import Any, Optional
import polars as pl
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core.config    import load_config
from app.core.exchange  import create_exchange
import threading as _threading
import math as _math


def _clean(obj: Any) -> Any:
    """Sanitise récursivement pour la sérialisation JSON :
    - float NaN  → None
    - float ±Inf → ±1e308
    - clés privées (_xxx) → ignorées (ex: _trailing, _stop_trail)
    - objets Python non-sérialisables (TrailingStopManager…) → None
    """
    if isinstance(obj, float):
        if _math.isnan(obj):
            return None
        if _math.isinf(obj):
            return 1e308 if obj > 0 else -1e308
        return obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, (int, str, bool, type(None))):
        return obj
    # Objets Python non-sérialisables → None
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return None

logger = logging.getLogger(__name__)

# ── Sémaphores & verrous ──────────────────────────────────────────────────
_bt_exchange      = None
_bt_exchange_lock = _threading.Lock()
_bt_semaphore     = _threading.Semaphore(1)
_opt_semaphore    = _threading.Semaphore(1)
_config_write_lock = _threading.Lock()  # Protège les écritures concurrentes sur config.yaml

# ── Cache stratégies ──────────────────────────────────────────────────────
_strategies_cache: frozenset | None = None
_strategies_cache_ts: float = 0.0
_STRATEGIES_CACHE_TTL: float = 60.0  # secondes

def _get_bt_exchange(cfg: dict):
    global _bt_exchange
    with _bt_exchange_lock:
        if _bt_exchange is None:
            from app.core.exchange import RobustExchange
            import ccxt as _ccxt
            name  = cfg["exchange"]["name"].lower()
            klass = getattr(_ccxt, name, None)
            if klass is None:
                return create_exchange(cfg)
            ex = klass({"enableRateLimit": True})
            _bt_exchange = RobustExchange(ex, paper=True)
        return _bt_exchange

from app.core.database   import init_db, get_trades, DailyStats
from app.engine.engine   import Engine
from app.engine.backtest import Backtester, WalkForwardAnalyzer, MonteCarlo
from app.scanner.scanner import MarketScanner


# ── Globals ───────────────────────────────────────────────────────────────
cfg          = None
trader       = None
SessionLocal = None

class CleanJSONResponse(JSONResponse):
    """JSONResponse qui neutralise automatiquement les float NaN/Inf
    sur TOUTES les réponses de l'API, sans toucher chaque endpoint."""
    def render(self, content) -> bytes:
        return json.dumps(
            _clean(content),
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")

app = FastAPI(
    title="Crypto Bot V9",
    version="9.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    description=(
        "API de trading algorithmique multi-stratégies. "
        "Tous les endpoints protégés exigent l'en-tête `X-API-Key`."
    ),
    default_response_class=CleanJSONResponse,
)

# Compression gzip automatique pour toutes les réponses JSON ≥ 500 bytes
app.add_middleware(GZipMiddleware, minimum_size=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1",
                   "http://localhost:8000", "http://127.0.0.1:8000",
                   "http://localhost:8001", "http://127.0.0.1:8001"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)

try:
    tpl_path  = os.path.join(os.path.dirname(__file__), "..", "web", "templates")
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
        client_host = getattr(request.client, "host", "unknown") if request.client else "unknown"
        logger.warning(f"[Auth] Clé API invalide depuis {client_host} — {request.method} {request.url.path}")
        raise HTTPException(status_code=403, detail="Clé API invalide")


# ── Helpers ───────────────────────────────────────────────────────────────
def detect_ohlcv_gaps(df, timeframe: str) -> list:
    tf_mins = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,
               "1h":60,"4h":240,"1d":1440}
    expected_mins  = tf_mins.get(timeframe, 60)
    from datetime import timedelta as _timedelta
    expected_delta = _timedelta(minutes=expected_mins)
    gaps  = []
    times = df["time"]
    for i in range(1, len(times)):
        delta = times[i] - times[i-1]
        if delta > expected_delta * 1.5:
            gap_bars = round(delta.total_seconds() / 60 / expected_mins) - 1
            gaps.append({
                "index":       int(i),
                "time_before": str(times[i-1])[:16],
                "time_after":  str(times[i])[:16],
                "gap_bars":    gap_bars,
                "gap_duration":str(delta),
            })
    return gaps


def fetch_ohlcv_paged(exchange, symbol: str, timeframe: str, total: int = 1000) -> list:
    BINANCE_LIMIT = 1000
    if total <= BINANCE_LIMIT:
        return exchange.fetch_ohlcv(symbol, timeframe, limit=total)
    tf_ms     = exchange.parse_timeframe(timeframe) * 1000
    since     = exchange.milliseconds() - total * tf_ms
    all_ohlcv = []
    seen_ts   = set()
    while len(all_ohlcv) < total:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=BINANCE_LIMIT)
        if not batch:
            break
        added = 0
        for candle in batch:
            ts = candle[0]
            if ts not in seen_ts:
                seen_ts.add(ts)
                all_ohlcv.append(candle)
                added += 1
        if added == 0:
            break
        since = batch[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
    all_ohlcv.sort(key=lambda x: x[0])
    return all_ohlcv[-total:]


def _discover_strategies() -> frozenset:
    """Retourne les noms de stratégies valides sur disque (whitelist).
    Résultat mis en cache 60 secondes pour éviter des lectures disque répétées.
    """
    global _strategies_cache, _strategies_cache_ts
    now = time.monotonic()
    if _strategies_cache is not None and (now - _strategies_cache_ts) < _STRATEGIES_CACHE_TTL:
        return _strategies_cache
    strat_dir = os.path.join(os.path.dirname(__file__), "..", "strategies")
    _EXCLUDED = {"indicators", "base"}
    result = frozenset(
        os.path.splitext(os.path.basename(f))[0]
        for f in glob.glob(os.path.join(strat_dir, "*.py"))
        if not os.path.basename(f).startswith("__")
        and os.path.splitext(os.path.basename(f))[0] not in _EXCLUDED
    )
    _strategies_cache = result
    _strategies_cache_ts = now
    return result


# ═══════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ═══════════════════════════════════════════════════════════════
@app.get("/health")
def health_check():
    """Vérification de l'état du service (pas d'auth requise)."""
    db_ok = SessionLocal is not None
    exchange_ok = cfg is not None
    return {
        "status":      "ok" if (db_ok and exchange_ok) else "degraded",
        "version":     "9.0.0",
        "db":          db_ok,
        "exchange":    exchange_ok,
        "trader":      trader is not None and getattr(trader, "running", False),
    }


# ═══════════════════════════════════════════════════════════════
#  PAGES WEB
# ═══════════════════════════════════════════════════════════════
def _tpl(name: str, request: Request, extra: dict = None):
    api_key = cfg["web"].get("api_key", "") if cfg else ""
    ctx = {"request": request, "api_key": api_key, **(extra or {})}
    if templates:
        return templates.TemplateResponse(name, ctx)
    return HTMLResponse(f"<h1>{name}</h1>")

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return _tpl("dashboard.html", request, {"active_page": "dashboard"})

@app.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request):
    return _tpl("backtest.html", request, {"active_page": "backtest"})

@app.get("/optimizer", response_class=HTMLResponse)
def optimizer_page(request: Request):
    return _tpl("optimizer.html", request, {"active_page": "optimizer"})

@app.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    return _tpl("config.html", request, {"active_page": "config"})

@app.get("/scanner", response_class=HTMLResponse)
def scanner_page(request: Request):
    return _tpl("scanner.html", request, {"active_page": "scanner"})


# ═══════════════════════════════════════════════════════════════
#  API — STATUS & CONFIG
# ═══════════════════════════════════════════════════════════════
@app.get("/api/status")
def get_status(request: Request):
    if not cfg:
        return {"status": "not_started"}
    api_key_cfg = cfg["web"].get("api_key", "")
    token = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    authenticated = not api_key_cfg or token == api_key_cfg
    base = {
        "status":     "running" if (trader and trader.running) else "idle",
        "paper_mode": cfg["trading"].get("paper_mode", True),
        "timeframe":  cfg["trading"].get("timeframe", "1h"),
        "timeframes": cfg["trading"].get("timeframes", [cfg["trading"].get("timeframe", "1h")]),
        "strategies": cfg["strategies"]["enabled"],
        "version":    "9.0.0",
    }
    if authenticated:
        base["capital"] = trader.capital_display if trader else cfg["trading"]["capital"]
        if trader:
            base.update(trader.status)
        else:
            # Bot non démarré — valeurs par défaut pour le dashboard
            base.update({
                "total_pnl":              0.0,
                "total_pnl_pct":          0.0,
                "total_trades":           0,
                "win_rate":               0.0,
                "profit_factor":          0.0,
                "total_fees":             0.0,
                "best_trade":             0.0,
                "positions":              [],
                "by_strategy":            {},
                "signal_log":             [],
                "active_per_tf":          {},
                "circuit_breaker_active": False,
                "circuit_breaker_reason": "",
                "daily_pnl_pct":          0.0,
                "global_dd_pct":          0.0,
                "current_risk":           round(cfg["trading"].get("risk_per_trade", 0.01) * 100, 2),
                "daily_dd_limit":         cfg["trading"].get("daily_drawdown_limit", 0.05),
                "global_dd_limit":        cfg["trading"].get("max_drawdown_global", 0.20),
            })
    return base


@app.get("/api/config")
def get_config():
    if not cfg: raise HTTPException(503, "Config non chargée")
    all_strats = sorted(_discover_strategies())
    safe = {k: v for k, v in cfg.items() if k not in ("exchange", "notifications")}
    safe["exchange"]      = {"name": cfg["exchange"]["name"]}
    safe["all_strategies"]= all_strats
    from app.optimizer.optimizer import STRATEGY_TIMEFRAMES, RECOMMENDED_LIMIT
    safe["strategy_timeframes"] = STRATEGY_TIMEFRAMES
    safe["recommended_limits"]  = RECOMMENDED_LIMIT
    if trader:
        safe["_auto_opt_enabled"]    = trader._auto_opt_enabled
        safe["_auto_opt_interval_h"] = trader._auto_opt_interval // 3600
        safe["_auto_opt_next_run"]   = trader._auto_opt_next_run
        safe["_active_per_tf"] = {tf: [s["name"] for s in v]
                                  for tf, v in trader._active_per_tf.items()}
    return safe


@app.post("/api/config/strategies", dependencies=[Depends(verify_api_key)])
def update_strategies(enabled: str = ""):
    if not cfg: raise HTTPException(503, "Config non chargée")
    strat_list = [s.strip() for s in enabled.split(",") if s.strip()]
    if not strat_list:
        raise HTTPException(400, "Aucune stratégie spécifiée")
    allowed = _discover_strategies()
    invalid = [s for s in strat_list if s not in allowed]
    if invalid:
        raise HTTPException(400, f"Stratégie(s) inconnue(s) : {', '.join(invalid)}")
    cfg["strategies"]["enabled"] = strat_list
    result = {"config_updated": True, "strategies": strat_list, "trader_updated": False}
    if trader:
        reload_result = trader.reload_strategies(strat_list)
        result["trader_updated"] = True
        result.update(reload_result)
    try:
        import yaml as _yaml
        with _config_write_lock:
            with open("config.yaml", "r", encoding="utf-8") as f:
                disk_cfg = _yaml.safe_load(f) or {}
            disk_cfg.setdefault("strategies", {})["enabled"] = strat_list
            with open("config.yaml", "w", encoding="utf-8") as f:
                _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        result["saved_to_disk"] = True
    except Exception as e:
        result["save_error"] = str(e)
    return result


@app.post("/api/config/timeframes", dependencies=[Depends(verify_api_key)])
def update_timeframes(timeframes: str = "1h"):
    """Met à jour les timeframes actifs. timeframes = CSV ex: '5m,1h,4h'"""
    if not cfg: raise HTTPException(503, "Config non chargée")
    allowed_tfs = {"1m","3m","5m","15m","30m","1h","2h","4h","1d"}
    tf_list = [t.strip() for t in timeframes.split(",") if t.strip()]
    invalid = [t for t in tf_list if t not in allowed_tfs]
    if invalid:
        raise HTTPException(400, f"Timeframe(s) invalide(s) : {', '.join(invalid)}")
    if not tf_list:
        raise HTTPException(400, "Aucun timeframe spécifié")

    cfg["trading"]["timeframes"] = tf_list
    cfg["trading"]["timeframe"]  = tf_list[0]  # compat

    result = {"timeframes": tf_list, "trader_updated": False}
    if trader:
        trader.timeframes = tf_list
        trader.tf         = tf_list[0]
        trader._build_active_per_tf()
        result["trader_updated"]  = True
        result["active_per_tf"]   = {tf: [s["name"] for s in v]
                                     for tf, v in trader._active_per_tf.items()}
    try:
        import yaml as _yaml
        with _config_write_lock:
            with open("config.yaml", "r", encoding="utf-8") as f:
                disk_cfg = _yaml.safe_load(f) or {}
            disk_cfg.setdefault("trading", {})["timeframes"] = tf_list
            disk_cfg["trading"]["timeframe"] = tf_list[0]
            with open("config.yaml", "w", encoding="utf-8") as f:
                _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        result["saved_to_disk"] = True
    except Exception as e:
        result["save_error"] = str(e)
    return result


@app.post("/api/config/auto-optimizer", dependencies=[Depends(verify_api_key)])
def update_auto_optimizer(enabled: bool = False, interval_h: int = 24):
    if not cfg: raise HTTPException(503, "Config non chargée")
    cfg.setdefault("optimizer", {})["enabled"] = enabled
    cfg["optimizer"]["auto_interval_h"] = interval_h
    if trader:
        trader.set_auto_optimizer(enabled, interval_h)
    try:
        import yaml as _yaml
        with _config_write_lock:
            with open("config.yaml", "r", encoding="utf-8") as f:
                disk_cfg = _yaml.safe_load(f) or {}
            disk_cfg.setdefault("optimizer", {})["enabled"]        = enabled
            disk_cfg["optimizer"]["auto_interval_h"]                = interval_h
            with open("config.yaml", "w", encoding="utf-8") as f:
                _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        saved = True
    except Exception as e:
        saved = False
    return {"enabled": enabled, "interval_h": interval_h,
            "trader_updated": trader is not None, "saved_to_disk": saved}


@app.post("/api/config/trading", dependencies=[Depends(verify_api_key)])
def update_trading_params(
    score_threshold:      float = None,
    risk_per_trade:       float = None,
    max_positions:        int   = None,
    paper_mode:           bool  = None,
    daily_drawdown_limit: float = None,
):
    if not cfg: raise HTTPException(503, "Config non chargée")
    changed = {}
    mapping = {
        "score_threshold":      score_threshold,
        "risk_per_trade":       risk_per_trade,
        "max_positions":        max_positions,
        "paper_mode":           paper_mode,
        "daily_drawdown_limit": daily_drawdown_limit,
    }
    for key, val in mapping.items():
        if val is not None:
            cfg["trading"][key] = val
            changed[key] = val
            if trader:
                if hasattr(trader, key):
                    setattr(trader, key, val)
                if hasattr(trader.risk, key):
                    setattr(trader.risk, key, val)
                if key == "score_threshold":
                    trader.threshold = val
    try:
        import yaml as _yaml
        with _config_write_lock:
            with open("config.yaml", "r", encoding="utf-8") as f:
                disk_cfg = _yaml.safe_load(f) or {}
            disk_cfg.setdefault("trading", {}).update(changed)
            with open("config.yaml", "w", encoding="utf-8") as f:
                _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        saved = True
    except Exception as e:
        saved = False
    return {"changed": changed, "saved_to_disk": saved, "trader_updated": trader is not None}


@app.post("/api/config/strategy-params", dependencies=[Depends(verify_api_key)])
def update_strategy_params(strategy: str, params: dict):
    if not cfg: raise HTTPException(503, "Config non chargée")
    allowed = _discover_strategies()
    if strategy not in allowed:
        raise HTTPException(400, f"Stratégie inconnue : {strategy}")
    cfg.setdefault("strategy_params", {})[strategy] = params
    if trader:
        trader.strat_params = cfg["strategy_params"]
    try:
        import yaml as _yaml
        with _config_write_lock:
            with open("config.yaml", "r", encoding="utf-8") as f:
                disk_cfg = _yaml.safe_load(f) or {}
            disk_cfg.setdefault("strategy_params", {})[strategy] = params
            with open("config.yaml", "w", encoding="utf-8") as f:
                _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return {"saved": True, "strategy": strategy, "params": params}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/backtest/settings")
def backtest_settings():
    if not cfg: raise HTTPException(503, "Config non chargée")
    all_strats = sorted(_discover_strategies())
    return {
        "timeframe":            cfg["trading"].get("timeframe", "1h"),
        "timeframes":           cfg["trading"].get("timeframes", ["1h"]),
        "available_timeframes": ["1m","3m","5m","15m","30m","1h","2h","4h","1d"],
        "strategies":           cfg["strategies"]["enabled"],
        "all_strategies":       all_strats,
        "strategy_params":      cfg.get("strategy_params", {}),
        "score_threshold":      cfg["trading"].get("score_threshold", 0.55),
        "taker_fee":            cfg["trading"].get("taker_fee", 0.001),
        "maker_fee":            cfg["trading"].get("maker_fee", 0.0004),
        "capital":              cfg["trading"]["capital"],
        "risk_per_trade":       cfg["trading"]["risk_per_trade"],
        "spread_pct":           cfg.get("backtest", {}).get("spread_pct", 0.0005),
        "partial_fill_pct":     cfg.get("backtest", {}).get("partial_fill_pct", 0.95),
    }


# ═══════════════════════════════════════════════════════════════
#  API — TRADES & STATS
# ═══════════════════════════════════════════════════════════════
@app.get("/api/trades", dependencies=[Depends(verify_api_key)])
def list_trades(limit: int = 100, offset: int = 0, symbol: str = None, strategy: str = None):
    """Retourne les trades paginés. Paramètres : limit, offset, symbol, strategy."""
    if not SessionLocal: raise HTTPException(503, "DB non initialisée")
    limit  = max(1, min(limit, 1000))   # reasonable upper bound per page
    offset = max(0, offset)
    session = SessionLocal()
    try:
        from app.core.database import Trade as _Trade
        q = session.query(_Trade)
        if symbol:   q = q.filter(_Trade.symbol   == symbol)
        if strategy: q = q.filter(_Trade.strategy == strategy)
        total = q.count()                            # compte total réel (sans limite)
        page  = q.order_by(_Trade.time.desc()).offset(offset).limit(limit).all()
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "trades": [{"id": t.id, "time": str(t.time), "symbol": t.symbol, "side": t.side,
                         "strategy": t.strategy, "entry": t.entry, "exit": t.exit_price,
                         "pnl": t.pnl, "pnl_pct": t.pnl_pct, "fees": t.fees,
                         "status": t.status, "score": t.score, "reason": t.reason} for t in page],
        }
    finally:
        session.close()


@app.get("/api/trades/export", dependencies=[Depends(verify_api_key)])
def export_trades(limit: int = 10000):
    """Export CSV des trades. limit = nombre max de trades (défaut 10 000, max 50 000)."""
    if not SessionLocal: raise HTTPException(503, "DB non initialisée")
    export_limit = max(1, min(limit, 50000))
    session = SessionLocal()
    try:
        trades = get_trades(session, limit=export_limit)
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
    if not SessionLocal: return []
    session = SessionLocal()
    try:
        rows = session.query(DailyStats).order_by(DailyStats.date.desc()).limit(days).all()
        return [{"date": r.date, "trades": r.trades, "wins": r.wins,
                 "pnl": r.pnl, "fees": r.fees,
                 "max_dd": r.max_dd, "equity_open": r.equity_open,
                 "equity_close": r.equity_close} for r in rows]
    finally:
        session.close()


@app.get("/api/risk", dependencies=[Depends(verify_api_key)])
def risk_status():
    if not trader: raise HTTPException(503, "Trader non initialisé")
    return trader.risk.status_dict()


@app.post("/api/risk/reset-halt", dependencies=[Depends(verify_api_key)])
def reset_halt():
    if not trader: raise HTTPException(503, "Trader non initialisé")
    trader.risk.reset_halt()
    trader.notif.reset_halt_notification()
    trader.notif.reset_dd_warning()
    return {"status": "reset", "message": "Circuit breaker réinitialisé"}


# ═══════════════════════════════════════════════════════════════
#  API — NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════
@app.get("/api/config/changelog")
def get_changelog(limit: int = 50):
    """Retourne les N dernières entrées du changelog d'optimisation."""
    import json, os as _os
    changelog_path = _os.path.join(_os.path.dirname(_os.path.abspath("config.yaml")),
                                   "optimizer_changelog.json")
    try:
        with open(changelog_path, "r") as f:
            log = json.load(f)
        # Plus récent en premier
        log = list(reversed(log))[:max(1, min(limit, 500))]
        return log
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning(f"[changelog] lecture KO : {e}")
        return []


@app.get("/api/config/notifications", dependencies=[Depends(verify_api_key)])
def get_notifications_config():
    if not cfg: raise HTTPException(503, "Config non chargée")
    notif = dict(cfg.get("notifications", {}))
    def _mask(token: str) -> str:
        if not token: return ""
        return token[:4] + "****" + token[-3:] if len(token) > 8 else "****"
    sensitive = ["telegram_bot_token","twilio_auth_token","twilio_account_sid",
                 "whatsapp_token","email_password"]
    for k in sensitive:
        if k in notif and notif[k]:
            notif[k] = _mask(str(notif[k]))
    return notif


@app.post("/api/config/notifications", dependencies=[Depends(verify_api_key)])
def update_notifications_config(
    telegram_enabled:    bool = None,
    telegram_bot_token:  str  = None,
    telegram_chat_id:    str  = None,
    whatsapp_enabled:    bool = None,
    whatsapp_number:     str  = None,
    whatsapp_token:      str  = None,
    email_enabled:       bool = None,
    email_smtp:          str  = None,
    email_port:          int  = None,
    email_user:          str  = None,
    email_password:      str  = None,
    email_to:            str  = None,
    min_pnl_to_notify:   float= None,
    position_loss_warn_pct: float = None,
):
    if not cfg: raise HTTPException(503, "Config non chargée")
    notif = cfg.setdefault("notifications", {})
    mapping = {
        "telegram_enabled": telegram_enabled,
        "telegram_bot_token": telegram_bot_token,
        "telegram_chat_id": telegram_chat_id,
        "whatsapp_enabled": whatsapp_enabled,
        "whatsapp_number": whatsapp_number,
        "whatsapp_token": whatsapp_token,
        "email_enabled": email_enabled,
        "email_smtp": email_smtp,
        "email_port": email_port,
        "email_user": email_user,
        "email_password": email_password,
        "email_to": email_to,
        "min_pnl_to_notify": min_pnl_to_notify,
        "position_loss_warn_pct": position_loss_warn_pct,
    }
    changed = {}
    for k, v in mapping.items():
        if v is not None:
            notif[k] = v
            changed[k] = v

    if trader:
        from app.core.notifications import Notifier
        trader.notif = Notifier(cfg)
        trader.risk.attach_notifier(trader.notif)
    try:
        import yaml as _yaml
        with _config_write_lock:
            with open("config.yaml", "r", encoding="utf-8") as f:
                disk_cfg = _yaml.safe_load(f) or {}
            disk_cfg.setdefault("notifications", {}).update({
                k: v for k, v in changed.items() if "password" not in k and "token" not in k
            })
            # secrets non loggés dans config.yaml — écriture directe uniquement si fournis
            for k in ["telegram_bot_token","whatsapp_token","email_password"]:
                if k in changed:
                    disk_cfg["notifications"][k] = changed[k]
            with open("config.yaml", "w", encoding="utf-8") as f:
                _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        saved = True
    except Exception as e:
        saved = False
    return {"changed": list(changed.keys()), "saved_to_disk": saved}


@app.post("/api/config/notifications/test", dependencies=[Depends(verify_api_key)])
def test_notification():
    if not trader: raise HTTPException(503, "Trader non initialisé")
    trader.notif.send("🔔 Test de notification depuis le bot V9", async_=False)
    return {"status": "sent"}


@app.post("/api/config/margin", dependencies=[Depends(verify_api_key)])
def update_margin_config(
    margin:       bool  = None,
    margin_mode:  str   = None,
    max_leverage: int   = None,
):
    if not cfg: raise HTTPException(503, "Config non chargée")
    if margin is not None:      cfg["exchange"]["margin"]       = margin
    if margin_mode is not None: cfg["trading"]["margin_mode"]   = margin_mode
    if max_leverage is not None:cfg["trading"]["max_leverage"]  = max_leverage
    try:
        import yaml as _yaml
        with _config_write_lock:
            with open("config.yaml", "r", encoding="utf-8") as f:
                disk_cfg = _yaml.safe_load(f) or {}
            if margin is not None:      disk_cfg.setdefault("exchange", {})["margin"]      = margin
            if margin_mode is not None: disk_cfg.setdefault("trading", {})["margin_mode"]  = margin_mode
            if max_leverage is not None:disk_cfg.setdefault("trading", {})["max_leverage"] = max_leverage
            with open("config.yaml", "w", encoding="utf-8") as f:
                _yaml.dump(disk_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        saved = True
    except Exception as e:
        saved = False
    return {"saved_to_disk": saved}


# ═══════════════════════════════════════════════════════════════
#  API — BOT START / STOP
# ═══════════════════════════════════════════════════════════════
@app.post("/api/bot/start", dependencies=[Depends(verify_api_key)])
def bot_start():
    if not trader: raise HTTPException(503, "Trader non initialisé")
    if trader.running: return {"status": "already_running"}
    t = _threading.Thread(target=trader.start, daemon=True)
    t.start()
    return {"status": "started"}


@app.post("/api/bot/stop", dependencies=[Depends(verify_api_key)])
def bot_stop(close_positions: bool = False):
    if not trader: raise HTTPException(503, "Trader non initialisé")
    n_open = len(trader.open_positions)
    trader.stop(close_positions=close_positions)
    return {
        "status":           "stopped",
        "close_positions":  close_positions,
        "positions_closed": n_open if close_positions else 0,
        "positions_kept":   n_open if not close_positions else 0,
    }


# ═══════════════════════════════════════════════════════════════
#  API — BACKTEST
# ═══════════════════════════════════════════════════════════════
@app.post("/api/backtest", dependencies=[Depends(verify_api_key)])
def run_backtest(symbol: str = "BTC/USDC", limit: int = 500, timeframe: str = "",
                 walk_forward: bool = False, monte_carlo: bool = False,
                 strategies: str = ""):
    if not cfg: raise HTTPException(503, "Config non chargée")
    if not _bt_semaphore.acquire(blocking=False):
        raise HTTPException(429, "Un backtest est déjà en cours.")
    try:
        from app.core.config import load_config as _reload_cfg
        try:
            cfg.update(_reload_cfg("config.yaml"))
        except Exception:
            pass
        tf    = timeframe.strip() or cfg["trading"].get("timeframe", "1h")
        limit = max(100, min(limit, 50000))
        if tf == "1d":
            limit = min(limit, 5000)

        exchange  = _get_bt_exchange(cfg)
        ohlcv_raw = fetch_ohlcv_paged(exchange, symbol, tf, total=limit)
        df = pl.DataFrame(ohlcv_raw, schema=["time","open","high","low","close","volume"], orient="row").with_columns(pl.from_epoch("time", time_unit="ms"))

        ohlcv_payload = {
            "time":   [str(t) for t in df["time"].to_list()],
            "close":  [round(float(v), 6) for v in df["close"].to_list()],
            "high":   [round(float(v), 6) for v in df["high"].to_list()],
            "low":    [round(float(v), 6) for v in df["low"].to_list()],
            "volume": [round(float(v), 2) for v in df["volume"].to_list()],
        }

        _ALLOWED_STRATS = _discover_strategies()
        strats_to_run   = ([s.strip() for s in strategies.split(",") if s.strip()]
                           if strategies.strip() else cfg["strategies"]["enabled"])
        invalid = [s for s in strats_to_run if s not in _ALLOWED_STRATS]
        if invalid:
            raise HTTPException(400, f"Stratégie(s) inconnue(s) : {', '.join(invalid)}")
        strats_to_run = [s for s in strats_to_run if s in _ALLOWED_STRATS]

        tf_mins     = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}
        days_covered= round(len(df) * tf_mins.get(tf, 60) / 1440, 1)
        _bars_warning= None
        if days_covered < 30:
            _bars_warning = (f"⚠ Seulement {days_covered} jours de données ({len(df)} bougies × {tf}). "
                             f"Visez ≥ 90 jours pour des résultats fiables.")

        by_strategy = {}

        def _run_one_strategy(name: str) -> tuple:
            try:
                mod = importlib.import_module(f"app.strategies.{name}")
                eng = Engine(); eng.register(mod.Strategy(), silent=True)
                bt  = Backtester(eng, cfg)
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
                    "avg_win":          strat_data.get("avg_win",       d.get("avg_win",0)),
                    "avg_loss":         strat_data.get("avg_loss",      d.get("avg_loss",0)),
                    "initial_capital":  d["initial_capital"],
                    "final_equity":     strat_data.get("final_equity",  d["final_equity"]),
                    "equity_curve":     strat_data.get("equity_curve",  d.get("equity_curve",[])),
                    "buy_and_hold_pnl": d.get("buy_and_hold_pnl"),
                    "buy_and_hold_pct": d.get("buy_and_hold_pct"),
                    "alpha":            d.get("alpha"),
                    "trades":           all_trades,
                    "days_covered":     days_covered,
                    "bars_warning":     _bars_warning,
                }
                if walk_forward and len(df) >= 200:
                    wf = WalkForwardAnalyzer(eng, cfg,
                           n_folds=cfg.get("backtest", {}).get("walk_forward_folds", 5))
                    entry["walk_forward"] = wf.run(df, symbol)
                if monte_carlo and all_trades:
                    mc = MonteCarlo(n_runs=cfg.get("backtest", {}).get("monte_carlo_runs", 200))
                    entry["monte_carlo"] = mc.run(all_trades, cfg["trading"]["capital"])
                return name, entry
            except Exception as e:
                logger.error(f"[API] Backtest {name} : {e}", exc_info=True)
                return name, {"error": str(e), "trades": []}

        from concurrent.futures import ThreadPoolExecutor, as_completed as _ac
        with ThreadPoolExecutor(max_workers=min(len(strats_to_run), 4)) as pool:
            futures = {pool.submit(_run_one_strategy, n): n for n in strats_to_run}
            for fut in _ac(futures):
                name, result = fut.result()
                by_strategy[name] = result

        ohlcv_gaps   = detect_ohlcv_gaps(df, tf)
        gaps_warning = None
        if ohlcv_gaps:
            total_missing = sum(g["gap_bars"] for g in ohlcv_gaps)
            gaps_warning  = (f"⚠ {len(ohlcv_gaps)} gap(s) détecté(s) — "
                             f"{total_missing} bougies manquantes.")

        payload = {
            "symbol":          symbol,
            "timeframe":       tf,
            "limit":           limit,
            "n_bars":          len(df),
            "date_from":       str(df["time"][0])[:16] if len(df) else "",
            "date_to":         str(df["time"][-1])[:16] if len(df) else "",
            "ohlcv":           ohlcv_payload,
            "by_strategy":     by_strategy,
            "score_threshold": cfg["trading"].get("score_threshold", 0.55),
            "ohlcv_gaps":      ohlcv_gaps[:20],
            "gaps_warning":    gaps_warning,
        }
        return JSONResponse(content=_clean(payload))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Backtest error : {e}", exc_info=True)
        raise HTTPException(500, str(e))
    finally:
        _bt_semaphore.release()


# ═══════════════════════════════════════════════════════════════
#  API — SCANNER
# ═══════════════════════════════════════════════════════════════
@app.get("/api/scanner", dependencies=[Depends(verify_api_key)])
def run_scanner(timeframe: str = None, limit: int = 200):
    if not cfg: raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(cfg)
        scanner  = MarketScanner(exchange, cfg)
        tf       = timeframe or cfg["trading"].get("timeframe", "1h")
        results  = scanner.screen(tf, limit)
        return {"timeframe": tf, "symbols_scanned": len(results), "results": results}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/scanner/config", dependencies=[Depends(verify_api_key)])
def scanner_config():
    """Retourne la configuration active du scanner."""
    if not cfg: raise HTTPException(503, "Config non chargée")
    from app.optimizer.optimizer import STRATEGY_TIMEFRAMES
    # active_per_tf depuis le trader si dispo, sinon depuis la config statique
    if trader:
        active_per_tf = {tf: [s["name"] for s in v]
                         for tf, v in trader._active_per_tf.items()}
    else:
        # Fallback : toutes les stratégies activées sur tous les TFs configurés
        tfs = cfg["trading"].get("timeframes", [cfg["trading"].get("timeframe", "1h")])
        strats = cfg["strategies"].get("enabled", [])
        active_per_tf = {tf: strats for tf in tfs} if strats else {}
    return {
        "scanner":            cfg.get("scanner", {}),
        "timeframes":         cfg["trading"].get("timeframes", [cfg["trading"].get("timeframe","1h")]),
        "min_volume_usdc_24h":cfg["trading"].get("min_volume_usdc_24h", 5_000_000),
        "active_per_tf":      active_per_tf,
        "strategy_timeframes":STRATEGY_TIMEFRAMES,
        "min_viable_score":   -0.05,
    }


@app.get("/api/scanner/opportunities", dependencies=[Depends(verify_api_key)])
def scanner_opportunities(timeframe: str = None, limit: int = 200):
    if not cfg: raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(cfg)
        scanner  = MarketScanner(exchange, cfg)
        tf       = timeframe or cfg["trading"].get("timeframe", "1h")
        results  = scanner.opportunity_scan(tf)
        return {"timeframe": tf, "count": len(results), "opportunities": results}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/scanner/chart", dependencies=[Depends(verify_api_key)])
def scanner_chart(symbol: str = "BTC/USDC", timeframe: str = "1h", limit: int = 300):
    """Retourne les bougies OHLCV + séries indicateurs pour le graphique du scanner."""
    if not cfg: raise HTTPException(503, "Config non chargée")
    import math as _m
    try:
        exchange = create_exchange(cfg)
        scanner  = MarketScanner(exchange, cfg)
        df       = scanner.fetch_ohlcv(symbol, timeframe, limit)
        if df is None:
            raise HTTPException(404, f"Données non disponibles pour {symbol}/{timeframe}")

        n     = len(df)
        times = df["time"].dt.epoch(time_unit="s").to_list()
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        # ── Bougies ──────────────────────────────────────────────────────
        candles = [
            {
                "time":   int(times[i]),
                "open":   round(float(df["open"][i]), 8),
                "high":   round(float(high[i]), 8),
                "low":    round(float(low[i]), 8),
                "close":  round(float(close[i]), 8),
                "volume": round(float(df["volume"][i]), 4),
            }
            for i in range(n)
        ]

        # ── Séries indicateurs ────────────────────────────────────────────
        ema20_s  = close.ewm_mean(span=20,  adjust=False).to_list()
        ema50_s  = close.ewm_mean(span=50,  adjust=False).to_list()
        ema200_s = close.ewm_mean(span=200, adjust=False).to_list() if n >= 200 else [None] * n

        sma20_s  = close.rolling_mean(20).to_list()
        std20_s  = close.rolling_std(20).to_list()

        ema12_s = close.ewm_mean(span=12, adjust=False)
        ema26_s = close.ewm_mean(span=26, adjust=False)
        macd_s  = (ema12_s - ema26_s).to_list()
        sig_s   = (ema12_s - ema26_s).ewm_mean(span=9, adjust=False).to_list()

        delta  = close.diff(1)
        gain   = delta.clip(lower_bound=0).rolling_mean(14).to_list()
        loss   = (-delta.clip(upper_bound=0)).rolling_mean(14).to_list()

        def _safe(v):
            if v is None: return None
            try:
                f = float(v)
                return None if _m.isnan(f) or _m.isinf(f) else f
            except Exception:
                return None

        def _line(series, decimals=6):
            return [
                {"time": int(times[i]), "value": round(_safe(series[i]), decimals)}
                for i in range(n)
                if _safe(series[i]) is not None
            ]

        def _rsi_series():
            out = []
            for i in range(n):
                g, l = _safe(gain[i]), _safe(loss[i])
                if g is None or l is None:
                    continue
                if l == 0.0:
                    rsi = 100.0
                else:
                    rs  = g / l
                    rsi = 100 - (100 / (1 + rs))
                if not _m.isnan(rsi):
                    out.append({"time": int(times[i]), "value": round(rsi, 2)})
            return out

        bb_up  = [_safe(sma20_s[i]) + 2 * _safe(std20_s[i])
                  if _safe(sma20_s[i]) is not None and _safe(std20_s[i]) is not None
                  else None for i in range(n)]
        bb_dn  = [_safe(sma20_s[i]) - 2 * _safe(std20_s[i])
                  if _safe(sma20_s[i]) is not None and _safe(std20_s[i]) is not None
                  else None for i in range(n)]

        hist_s = [(_safe(macd_s[i]) - _safe(sig_s[i]))
                  if _safe(macd_s[i]) is not None and _safe(sig_s[i]) is not None
                  else None for i in range(n)]

        indicators = {
            "ema20":       _line(ema20_s),
            "ema50":       _line(ema50_s),
            "ema200":      _line(ema200_s),
            "bb_upper":    _line(bb_up),
            "bb_mid":      _line(sma20_s),
            "bb_lower":    _line(bb_dn),
            "macd":        _line(macd_s),
            "macd_signal": _line(sig_s),
            "macd_hist":   _line(hist_s),
            "rsi":         _rsi_series(),
            "volume":      [{"time": int(times[i]), "value": round(float(df["volume"][i]), 4),
                             "color": "rgba(52,211,153,.4)" if float(close[i]) >= float(df["open"][i]) else "rgba(251,113,133,.4)"}
                            for i in range(n)],
        }

        return JSONResponse(content=_clean({
            "symbol":    symbol,
            "timeframe": timeframe,
            "n_bars":    n,
            "candles":   candles,
            "indicators": indicators,
        }))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] scanner/chart : {e}", exc_info=True)
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════
#  API — OPTIMISEUR V9
# ═══════════════════════════════════════════════════════════════
@app.post("/api/optimize/start", dependencies=[Depends(verify_api_key)])
def optimizer_start(
    symbol:              str  = "BTC/USDC",
    strategies:          str  = "",
    timeframes:          str  = "",    # CSV ex: "5m,1h" — vide = TFs de la config
    method:              str  = "bayesian",
    n_trials:            int  = 40,
    limit:               int  = 0,
    auto_apply:          bool = False,
    n_jobs:              int  = 1,
    early_stop_patience: int  = 0,
):
    if not cfg: raise HTTPException(503, "Config non chargée")
    # Vérifier si des jobs d'optimisation sont encore en cours (threads background)
    from app.optimizer.auto_optimizer import get_all_jobs as _get_all_jobs
    _running_jobs = [jid for jid, j in _get_all_jobs().items() if j.get("status") == "running"]
    if _running_jobs:
        raise HTTPException(429, f"Une optimisation est déjà en cours ({len(_running_jobs)} job(s) actif(s) : {', '.join(_running_jobs[:3])}).")
    if not _opt_semaphore.acquire(blocking=False):
        raise HTTPException(429, "Une optimisation est déjà en cours.")
    n_trials = max(1,  min(n_trials, 200))
    if limit > 0:
        limit = max(100, min(limit, 50000))

    try:
        from app.optimizer.auto_optimizer import AutoOptimizer
        from app.optimizer.optimizer import PARAM_SPACES, RECOMMENDED_LIMIT

        # Timeframes à optimiser
        tf_list = [t.strip() for t in timeframes.split(",") if t.strip()] if timeframes.strip() else \
                  cfg["trading"].get("timeframes", [cfg["trading"].get("timeframe", "1h")])

        # Stratégies à optimiser
        strats = [s.strip() for s in strategies.split(",") if s.strip()] \
                 if strategies else list(PARAM_SPACES.keys())
        allowed = _discover_strategies()
        strats  = [s for s in strats if s in PARAM_SPACES and s in allowed]

        exchange = create_exchange(cfg)
        df_map   = {}
        fetch_details = {}   # pour retour API : limit demandée
        received_counts = {} # pour retour API : bougies effectivement reçues
        for tf in tf_list:
            # limit=0 → utiliser la valeur recommandée par TF ; l'échange retourne naturellement
            # ce qui est disponible sans plafond artificiel
            fetch_limit = limit if limit > 0 else RECOMMENDED_LIMIT.get(tf, 1000)
            fetch_details[tf] = fetch_limit
            ohlcv = fetch_ohlcv_paged(exchange, symbol, tf, total=fetch_limit)
            df = pl.DataFrame(ohlcv, schema=["time","open","high","low","close","volume"], orient="row").with_columns(pl.from_epoch("time", time_unit="ms"))
            received_counts[tf] = len(df)
            if len(df) > 0:
                df_map[tf] = df
                if len(df) < fetch_limit:
                    logger.info(f"[Optimizer] TF={tf} : {len(df)} bougies reçues (demandées: {fetch_limit}) — l'auto-optimizer validera par stratégie")
            else:
                logger.warning(f"[Optimizer] TF={tf} : aucune bougie reçue, ignoré")

        if not df_map:
            details = "; ".join(
                f"{tf}: {received_counts.get(tf, 0)} bougies reçues"
                for tf in tf_list
            )
            raise HTTPException(400, f"Aucune donnée reçue pour les TFs demandés. {details}.")

        def _on_apply(strat_name: str, params: dict):
            try:
                from app.core.config import load_config as _rl
                cfg.update(_rl("config.yaml"))
            except Exception:
                pass
            if trader:
                trader.strat_params = cfg.get("strategy_params", {})
                trader.reload_active_strategies()

        opt     = AutoOptimizer(cfg, n_trials=n_trials, method=method,
                                on_apply_callback=_on_apply,
                                notifier=trader.notif if trader else None,
                                n_jobs=n_jobs,
                                early_stop_patience=early_stop_patience)
        job_ids, skipped = opt.start_async(df_map, symbol, strats,
                                           timeframes=tf_list, auto_apply=auto_apply)

        return {"status": "started", "job_ids": job_ids, "symbol": symbol,
                "strategies": strats, "timeframes": tf_list,
                "method": method, "n_trials": n_trials,
                "skipped": skipped,
                "n_jobs_created": len(job_ids),
                "fetch_details": fetch_details,
                "received_bars": received_counts}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] optimizer/start : {e}", exc_info=True)
        raise HTTPException(500, str(e))
    finally:
        _opt_semaphore.release()


@app.get("/api/optimize/status")
def optimizer_status(job_id: str = ""):
    from app.optimizer.auto_optimizer import get_job, get_all_jobs
    if job_id:
        job = get_job(job_id)
        if not job: raise HTTPException(404, f"Job '{job_id}' introuvable")
        return job
    return get_all_jobs()


@app.get("/api/optimize/stream")
async def optimizer_stream(job_id: str):
    from app.optimizer.auto_optimizer import get_job
    import asyncio

    async def event_generator():
        last_progress = -1
        for _ in range(600):
            job = get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                break
            progress = job.get("progress", 0)
            if progress != last_progress:
                last_progress = progress
                payload = {
                    "progress":   progress,
                    "status":     job.get("status"),
                    "best_score": job.get("best_score", 0),
                    "trials":     job.get("trials", [])[-1:],
                    "strategy":   job.get("strategy"),
                    "timeframe":  job.get("timeframe"),
                    "trials_done":job.get("trials_done", 0),
                    "n_trials":   job.get("n_trials", 0),
                }
                if job.get("status") in ("done", "error"):
                    payload["result"]   = job.get("result")
                    payload["error"]    = job.get("error")
                    payload["applied"]  = job.get("applied", False)
                    payload["baseline"] = job.get("baseline", {})
                yield f"data: {json.dumps(_clean(payload))}\n\n"
                if job.get("status") in ("done", "error"):
                    break
            await asyncio.sleep(0.8)

    return StreamingResponse(event_generator(),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/optimize/apply", dependencies=[Depends(verify_api_key)])
def optimizer_apply(job_id: str, config_path: str = "config.yaml"):
    from app.optimizer.auto_optimizer import get_job
    from app.optimizer.optimizer import apply_best_params
    from app.core.config import load_config as _reload_cfg
    job = get_job(job_id)
    if not job: raise HTTPException(404, f"Job '{job_id}' introuvable")
    if job.get("status") != "done": raise HTTPException(400, "Job non terminé")
    result = job.get("result", {})
    best   = result.get("best_params", {})
    strat  = job.get("strategy", "")
    tf     = job.get("timeframe", "")
    if not best or not strat: raise HTTPException(400, "Aucun meilleur paramètre")

    ok = apply_best_params(strat, best, config_path,
                           timeframe=tf,
                           oos_score=result.get("best_oos_score", 0.0))
    if not ok: raise HTTPException(500, "Erreur écriture config")
    # Note: apply_best_params appelle déjà _append_changelog internement

    trader_updated = False
    try:
        cfg.update(_reload_cfg(config_path))
    except Exception as e:
        logger.warning(f"[apply] reload config KO: {e}")
    if trader:
        try:
            trader.strat_params = cfg.get("strategy_params", {})
            trader.reload_active_strategies()
            trader_updated = True
        except Exception as e:
            logger.warning(f"[apply] propagation trader KO: {e}")

    return {"status": "applied", "strategy": strat, "timeframe": tf,
            "params": best, "trader_updated": trader_updated}


@app.get("/api/optimize/results")
def optimizer_results():
    """Retourne les résultats d'optimisation classés par (strategy, tf)."""
    if not cfg: raise HTTPException(503, "Config non chargée")
    # Lire depuis le fichier disque pour avoir les résultats à jour des threads background
    import yaml as _yaml
    try:
        with open("config.yaml") as _f:
            _disk_cfg = _yaml.safe_load(_f) or {}
        # Mettre à jour optimizer_results en mémoire si le fichier est plus récent
        if _disk_cfg.get("optimizer_results"):
            cfg["optimizer_results"] = _disk_cfg["optimizer_results"]
    except Exception:
        pass
    raw     = cfg.get("optimizer_results") or {}
    from app.optimizer.optimizer import get_active_strategies_per_tf
    active  = get_active_strategies_per_tf(cfg)
    result  = {}
    for strat, tf_map in raw.items():
        if not isinstance(tf_map, dict):
            continue
        result[strat] = {}
        for tf, entry in tf_map.items():
            result[strat][tf] = entry
    return {
        "by_strategy_tf": result,
        "active_per_tf":  {tf: [s["name"] for s in v] for tf, v in active.items()},
    }


@app.get("/api/optimize/spaces")
def optimizer_spaces():
    from app.optimizer.optimizer import PARAM_SPACES, STRATEGY_TIMEFRAMES, RECOMMENDED_LIMIT
    return {
        strat: {
            "params":     {k: v for k, v in space.items()},
            "timeframes": STRATEGY_TIMEFRAMES.get(strat, []),
            "n_combos":   1,  # grid serait produit de len(v)
        }
        for strat, space in PARAM_SPACES.items()
    }


# ═══════════════════════════════════════════════════════════════
#  API — ML
# ═══════════════════════════════════════════════════════════════
@app.post("/api/ml/train", dependencies=[Depends(verify_api_key)])
def train_ml(symbol: str = "BTC/USDC", limit: int = 2000):
    if not cfg: raise HTTPException(503, "Config non chargée")
    if not cfg.get("ml", {}).get("enabled"):
        raise HTTPException(400, "ML désactivé dans la config")
    try:
        from app.ml.model import MLPredictor
        exchange  = create_exchange(cfg)
        ohlcv_raw = fetch_ohlcv_paged(exchange, symbol,
                                       cfg["trading"].get("timeframe","1h"), total=limit)
        df = pl.DataFrame(ohlcv_raw, schema=["time","open","high","low","close","volume"], orient="row").with_columns(pl.from_epoch("time", time_unit="ms"))
        ml  = MLPredictor(cfg)
        ml.train(df)
        ml.save()
        return {"status": "trained", "samples": len(df)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/ml/strategy-info", dependencies=[Depends(verify_api_key)])
def ml_strategy_info():
    if not cfg: raise HTTPException(503, "Config non chargée")
    enabled = cfg.get("ml", {}).get("enabled", False)
    ready   = False
    if trader and trader.ml:
        ready = trader.ml.is_ready
    return {"enabled": enabled, "ready": ready,
            "config": cfg.get("ml", {})}
