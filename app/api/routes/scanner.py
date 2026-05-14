"""Routes scanner — screen, opportunités, graphique OHLCV et signaux."""
import importlib
import logging
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import verify_api_key, _clean, _discover_strategies
from app.core.exchange import create_exchange
from app.core.indicators import compute_v4_scoring_series
from app.engine.engine import Engine, BaseStrategyML
from app.engine.scanner import MarketScanner

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/scanner", dependencies=[Depends(verify_api_key)])
def run_scanner(timeframe: str = None, limit: int = 200):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")
        results  = scanner.screen(tf, limit)
        return {"timeframe": tf, "symbols_scanned": len(results), "results": results}
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/config", dependencies=[Depends(verify_api_key)])
def scanner_config():
    """Retourne la configuration active du scanner."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    from app.engine.optimizer import STRATEGY_TIMEFRAMES
    if state.trader:
        active_per_tf = {tf: [s["name"] for s in v]
                         for tf, v in state.trader._active_per_tf.items()}
    else:
        tfs    = state.cfg["trading"].get("timeframes",
                                          [state.cfg["trading"].get("timeframe", "1h")])
        strats = state.cfg["strategies"].get("enabled", [])
        active_per_tf = {tf: strats for tf in tfs} if strats else {}
    return {
        "scanner":             state.cfg.get("scanner", {}),
        "timeframes":          state.cfg["trading"].get("timeframes",
                               [state.cfg["trading"].get("timeframe", "1h")]),
        "min_volume_usdc_24h": state.cfg["trading"].get("min_volume_usdc_24h", 5_000_000),
        "active_per_tf":       active_per_tf,
        "strategy_timeframes": STRATEGY_TIMEFRAMES,
        "min_viable_score":    -0.05,
    }


@router.get("/api/scanner/opportunities", dependencies=[Depends(verify_api_key)])
def scanner_opportunities(timeframe: str = None, limit: int = 200):
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")
        results  = scanner.opportunity_scan(tf)
        return {"timeframe": tf, "count": len(results), "opportunities": results}
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/opportunities : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/chart", dependencies=[Depends(verify_api_key)])
def scanner_chart(symbol: str = "BTC/USDC", timeframe: str = "1h", limit: int = 300):
    """Retourne bougies OHLCV + séries indicateurs pour le graphique scanner."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        df       = scanner.fetch_ohlcv(symbol, timeframe, limit)
        if df is None:
            raise HTTPException(404, f"Données non disponibles pour {symbol}/{timeframe}")

        n     = len(df)
        times = df["time"].dt.epoch(time_unit="s").to_list()
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

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

        # Indicateurs
        ema20_s  = close.ewm_mean(span=20,  adjust=False).to_list()
        ema50_s  = close.ewm_mean(span=50,  adjust=False).to_list()
        ema100_s = close.ewm_mean(span=100, adjust=False).to_list() if n >= 50  else [None] * n
        ema150_s = close.ewm_mean(span=150, adjust=False).to_list() if n >= 75  else [None] * n
        ema200_s = close.ewm_mean(span=200, adjust=False).to_list() if n >= 200 else [None] * n

        sma20_s = close.rolling_mean(20).to_list()
        std20_s = close.rolling_std(20).to_list()

        ema12_s = close.ewm_mean(span=12, adjust=False)
        ema26_s = close.ewm_mean(span=26, adjust=False)
        macd_s  = (ema12_s - ema26_s).to_list()
        sig_s   = (ema12_s - ema26_s).ewm_mean(span=9, adjust=False).to_list()

        delta = close.diff(1)
        gain  = delta.clip(lower_bound=0).rolling_mean(14).to_list()
        loss  = (-delta.clip(upper_bound=0)).rolling_mean(14).to_list()

        def _safe(v):
            if v is None:
                return None
            try:
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else f
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
                rsi = 100.0 if l == 0.0 else 100 - (100 / (1 + g / l))
                if not math.isnan(rsi):
                    out.append({"time": int(times[i]), "value": round(rsi, 2)})
            return out

        bb_up = [
            _safe(sma20_s[i]) + 2 * _safe(std20_s[i])
            if _safe(sma20_s[i]) is not None and _safe(std20_s[i]) is not None
            else None
            for i in range(n)
        ]
        bb_dn = [
            _safe(sma20_s[i]) - 2 * _safe(std20_s[i])
            if _safe(sma20_s[i]) is not None and _safe(std20_s[i]) is not None
            else None
            for i in range(n)
        ]
        hist_s = [
            (_safe(macd_s[i]) - _safe(sig_s[i]))
            if _safe(macd_s[i]) is not None and _safe(sig_s[i]) is not None
            else None
            for i in range(n)
        ]

        indicators = {
            "ema20":       _line(ema20_s),
            "ema50":       _line(ema50_s),
            "ema100":      _line(ema100_s),
            "ema150":      _line(ema150_s),
            "ema200":      _line(ema200_s),
            "bb_upper":    _line(bb_up),
            "bb_mid":      _line(sma20_s),
            "bb_lower":    _line(bb_dn),
            "macd":        _line(macd_s),
            "macd_signal": _line(sig_s),
            "macd_hist":   _line(hist_s),
            "rsi":         _rsi_series(),
            "volume": [
                {
                    "time":  int(times[i]),
                    "value": round(float(df["volume"][i]), 4),
                    "color": ("rgba(52,211,153,.4)"
                              if float(close[i]) >= float(df["open"][i])
                              else "rgba(251,113,133,.4)"),
                }
                for i in range(n)
            ],
        }

        # ── Scores V4 (amplitude, direction, régime) ──────────────────────
        try:
            v4_scoring = compute_v4_scoring_series(df)
            indicators["proba_amp"] = v4_scoring.get("proba_amp", [])
            indicators["proba_dir"] = v4_scoring.get("proba_dir", [])
            indicators["regime_v4"] = v4_scoring.get("regime_v4", [])
        except Exception as _v4_err:
            logger.warning(f"[API] V4 scoring series KO : {_v4_err}")
            indicators["proba_amp"] = []
            indicators["proba_dir"] = []
            indicators["regime_v4"] = []

        return JSONResponse(content=_clean({
            "symbol":     symbol,
            "timeframe":  timeframe,
            "n_bars":     n,
            "candles":    candles,
            "indicators": indicators,
        }))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/chart : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/signals", dependencies=[Depends(verify_api_key)])
def scanner_signals(symbol: str = "BTC/USDC", timeframe: str = "1h", limit: int = 300):
    """Exécute toutes les stratégies sur le symbole et retourne leurs signaux."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")
        df       = scanner.fetch_ohlcv(symbol, tf, limit)
        if df is None:
            raise HTTPException(404, f"Données non disponibles pour {symbol}/{tf}")

        enabled_set = set(state.cfg["strategies"].get("enabled", []))
        strats      = sorted(_discover_strategies())

        signals = []
        for name in strats:
            is_active = name in enabled_set
            try:
                mod = importlib.import_module(f"app.strategies.{name}")
                cls = getattr(mod, "Strategy", None)
                if cls is None:
                    continue
                inst = cls()
                # Stratégies ML : charger un modèle pré-entraîné ou marquer skipped
                if isinstance(inst, BaseStrategyML):
                    model_path = f"{inst.model_dir}/{name}_{tf}.pkl"
                    if not inst.load_model(model_path):
                        signals.append({
                            "strategy": name,
                            "side":     "none",
                            "score":    None,
                            "reason":   "Aucun modèle entraîné — lancez un backtest ou l'optimiseur",
                            "skipped":  True,
                            "active":   is_active,
                        })
                        continue
                    # Modèle chargé — inférence seulement (pas de réentraînement inline)
                    inst.managed_externally = True
                result = inst.score(df, state.cfg.get("strategy_params", {}))
                side   = result.get("side", "none")
                score  = result.get("score", 0.0)
                reason = result.get("reason", "")
                signals.append({
                    "strategy": name,
                    "side":     side,
                    "score":    round(float(score), 3) if score is not None else 0.0,
                    "reason":   reason,
                    "skipped":  False,
                    "active":   is_active,
                })
            except Exception as e:
                signals.append({
                    "strategy": name,
                    "side":     "none",
                    "score":    0.0,
                    "reason":   f"Erreur : {e}",
                    "skipped":  False,
                    "active":   is_active,
                })

        return JSONResponse(content=_clean({
            "symbol":    symbol,
            "timeframe": tf,
            "signals":   signals,
        }))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/signals : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")
