"""Routes scanner — screen, opportunités, graphique OHLCV et signaux."""
import importlib
import logging
import math
import uuid

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api import state
from app.api.helpers import verify_api_key, _clean, _discover_strategies
from app.core.exchange import create_exchange
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


# ── Séries de setups (markers + TP/SL) pour le scanner ─────────────────────
def _atr_at(df, idx: int) -> float:
    """ATR de la bougie idx (colonne pré-calculée _pre_atr14)."""
    try:
        if "_pre_atr14" in df.columns:
            v = df["_pre_atr14"][idx]
            return float(v) if v is not None else 0.0
    except Exception:
        pass
    return 0.0


def _tp_sl(side: str, entry: float, atr: float, tp_mult: float, sl_mult: float):
    """Niveaux TP/SL absolus à partir des multiplicateurs ATR du setup."""
    if side == "long":
        return entry + tp_mult * atr, entry - sl_mult * atr
    return entry - tp_mult * atr, entry + sl_mult * atr


def _setup_series_v8(df, tf: str, limit: int) -> dict:
    """Markers de setups V8 par bougie (modèles V4 pré-entraînés), avec TP/SL."""
    from app.strategies.opus_stat_pretrained_v4 import (
        _FeatureBuilder, _load_pretrained, _to_pandas_window, _detect_timeframe,
    )
    from app.strategies.opus_omnibus_v8 import (
        _DEFAULT_SETUPS, _classify_regime, _evaluate_setup,
        REGIME_LABELS, REGIME_CHOPPY,
    )
    from app.core.indicators import bearish_excess_series

    tf_detected = _detect_timeframe(df)
    if tf_detected not in ("15m", "30m", "1h"):
        return {"supported": False, "reason": f"Timeframe {tf_detected} non supporté par V8",
                "markers": []}

    pdf   = _to_pandas_window(df, n=len(df))
    feats = _FeatureBuilder().build(pdf)
    if feats is None or len(feats) == 0:
        return {"supported": False, "reason": "Construction des features V4 impossible",
                "markers": []}

    models, medians_all = _load_pretrained()
    amp_entry = models.get((tf_detected, "amp", "single"))
    dir_entry = models.get((tf_detected, "dir", "single"))
    if amp_entry is None or dir_entry is None:
        return {"supported": False, "reason": f"Modèles V4 indisponibles pour {tf_detected}",
                "markers": []}

    def _batch(entry, target):
        feat_names = list(entry["features"])
        med = medians_all.get((tf_detected, target), {})
        X = feats.reindex(columns=feat_names).copy()
        X = X.replace([np.inf, -np.inf], np.nan)
        for col in feat_names:
            X[col] = X[col].fillna(med.get(col, 0.0))
        return entry["model"].predict_proba(X.values)[:, 1]

    p_amp = _batch(amp_entry, "amp")
    p_up  = _batch(dir_entry, "dir")

    adx_arr  = feats["ADX"].fillna(0.0).to_numpy()
    bull_arr = feats["MM_bullish_align"].fillna(0).astype(int).to_numpy()
    bear_arr = feats["MM_bearish_align"].fillna(0).astype(int).to_numpy()
    n_feats  = len(feats)
    regimes  = [_classify_regime(float(adx_arr[i]), int(bull_arr[i]),
                                 int(bear_arr[i]), 20.0) for i in range(n_feats)]

    be_series  = bearish_excess_series(df, rsi_threshold=38.0, price_dev_pct=1.5).to_numpy().astype(bool)
    offset     = len(df) - n_feats
    be_aligned = be_series[offset:] if offset > 0 else be_series[:n_feats]
    setups_def = sorted([dict(s) for s in _DEFAULT_SETUPS], key=lambda s: s["priority"])
    times      = df["time"].dt.epoch(time_unit="s").to_list()
    closes     = df["close"].to_list()

    markers, start, last_seen = [], max(0, n_feats - limit), None
    for i in range(n_feats):
        be_val = bool(be_aligned[i]) if i < len(be_aligned) else False
        chosen = None
        for s in setups_def:
            if _evaluate_setup(s, regimes[i], float(p_amp[i]), float(p_up[i]), False, be_val):
                chosen = s
                break
        if i < start:
            last_seen = chosen["name"] if chosen else None
            continue
        if chosen is None:
            last_seen = None
            continue
        if chosen["name"] == last_seen:
            continue
        last_seen = chosen["name"]
        di    = offset + i
        entry = float(closes[di] or 0.0)
        atr   = _atr_at(df, di)
        if entry <= 0 or atr <= 0:
            continue
        side  = "long" if chosen["direction"] == 1 else "short"
        tp, sl = _tp_sl(side, entry, atr, float(chosen["tp_mult"]), float(chosen["sl_mult"]))
        confluence = (chosen["name"] == "SIGNAL_UP" and regimes[i] == REGIME_CHOPPY
                      and p_up[i] > 0.58 and p_amp[i] > 0.50)
        markers.append({
            "time": int(times[di]), "setup": chosen["name"], "side": side,
            "entry": round(entry, 8), "tp": round(tp, 8), "sl": round(sl, 8),
            "p_event": round(float(p_amp[i]), 4), "p_up": round(float(p_up[i]), 4),
            "regime_lbl": REGIME_LABELS.get(regimes[i], "?"),
            "confluence": bool(confluence),
        })
    return {"supported": True, "reason": "", "markers": markers}


def _setup_series_v11(df, tf: str, limit: int, strategy: str) -> dict:
    """Markers de setups V11/V12 par bougie, avec TP/SL.

    Instancie une stratégie dédiée (sans toucher aux modèles du live), charge le
    pkl depuis models/ ou entraîne à la volée si absent, puis batch-predict.
    Pour V12, applique le veto/confirmation ml_dynamic_threshold par marker.
    """
    import os
    from app.strategies.opus_omnibus_v11 import (
        _build_features, _window_polars, _regime_history_v11, _exit_td_window_active,
        _apply_setup_overrides, _select_setup, _signal_up_dynamic_risk,
        _detect_timeframe, _SUPPORTED_TFS, REGIME_LABELS,
    )
    if strategy == "v12":
        from app.strategies.opus_omnibus_v12 import Strategy as _S
    else:
        from app.strategies.opus_omnibus_v11 import Strategy as _S

    tf_detected = _detect_timeframe(df)
    if tf_detected not in _SUPPORTED_TFS:
        return {"supported": False,
                "reason": f"Timeframe {tf_detected} non supporté par {strategy.upper()}",
                "markers": []}

    strat = _S()
    strat.managed_externally = False
    name = strat.name
    path = os.path.join(getattr(strat, "model_dir", "models"), f"{name}_{tf_detected}.pkl")
    if os.path.exists(path):
        strat.load_model(path)
    if tf_detected not in getattr(strat, "_trained_tfs", set()):
        try:
            strat.fit(df, {name: {}})   # entraînement à la volée
        except Exception as e:
            return {"supported": False, "reason": f"Entraînement {strategy.upper()} KO : {e}",
                    "markers": []}
    if tf_detected not in getattr(strat, "_trained_tfs", set()):
        return {"supported": False, "reason": f"Modèle {strategy.upper()} non disponible",
                "markers": []}

    features = _build_features(_window_polars(df, n=len(df)))
    if features is None or len(features) == 0:
        return {"supported": False, "reason": "Construction des features impossible", "markers": []}
    n_feats = len(features)

    p_amp = strat._predict_series(features, tf_detected, "amp")
    p_up  = strat._predict_series(features, tf_detected, "dir")
    if p_amp is None or p_up is None:
        return {"supported": False, "reason": f"Modèle {tf_detected} indisponible", "markers": []}

    p   = {}
    d   = strat._DEFAULTS
    adx_threshold       = float(d["adx_threshold"])
    di_rescue           = float(d["di_rescue"])
    exit_td_window_bars = int(d["exit_td_window_bars"])
    signal_up_dyn       = bool(d["signal_up_dynamic_risk"])
    be_rsi_thr, be_sma_pct = 38.0, 1.5

    regimes, _subs = _regime_history_v11(features, n_last=n_feats,
                                         adx_threshold=adx_threshold, di_rescue=di_rescue)
    setups_def = _apply_setup_overrides(p)

    offset   = len(df) - n_feats
    closes   = df["close"].to_list()
    opens    = df["open"].to_list()
    rsi_col  = features["RSI_14"].to_list() if "RSI_14" in features.columns else [50.0] * n_feats
    adx_col  = features["ADX"].to_list()    if "ADX"    in features.columns else [0.0] * n_feats
    sma_col  = features["SMA_20"].to_list() if "SMA_20" in features.columns else [0.0] * n_feats
    atr_col  = features["ATR_14"].to_list() if "ATR_14" in features.columns else [0.0] * n_feats
    times    = df["time"].dt.epoch(time_unit="s").to_list()

    # Instance ml_dyn pour le veto V12 (entraînée paresseusement au 1er score).
    mldyn = getattr(strat, "_mldyn", None) if strategy == "v12" else None
    mldyn_params = strat._mldyn_params({}) if (mldyn is not None) else None

    markers, start, last_seen = [], max(0, n_feats - limit), None
    for i in range(n_feats):
        di = offset + i
        if di < 1:
            continue
        rsi_v   = float(rsi_col[i] if rsi_col[i] is not None else 50.0)
        adx_v   = float(adx_col[i] if adx_col[i] is not None else 0.0)
        c_now   = float(closes[di] or 0.0)
        sma20_v = float(sma_col[i] or 0.0)
        consec_red = (closes[di] < opens[di] and closes[di - 1] < opens[di - 1])
        price_below = (c_now < sma20_v * (1.0 - be_sma_pct / 100.0)) if sma20_v > 0 else False
        bearish_excess = bool(consec_red or (rsi_v < be_rsi_thr) or price_below)
        exit_td_active = _exit_td_window_active(regimes[:i + 1], exit_td_window_bars)
        chosen = _select_setup(setups_def, regimes[i], float(p_amp[i]), float(p_up[i]),
                               exit_td_active, bearish_excess, rsi_v, adx_v)
        if i < start:
            last_seen = chosen["name"] if chosen else None
            continue
        if chosen is None:
            last_seen = None
            continue
        if chosen["name"] == last_seen:
            continue
        last_seen = chosen["name"]
        atr = float(atr_col[i] or 0.0)
        if atr <= 0:
            atr = _atr_at(df, di)
        if c_now <= 0 or atr <= 0:
            continue
        side    = "long" if chosen["direction"] == 1 else "short"
        sl_mult = float(chosen["sl_mult"])
        tp_mult = float(chosen["tp_mult"])
        if chosen["name"] == "SIGNAL_UP" and signal_up_dyn:
            _, sl_mult = _signal_up_dynamic_risk(regimes[i])

        # V12 : veto/confirmation ml_dynamic_threshold sur la bougie du marker.
        fusion = None
        if mldyn is not None:
            try:
                mldyn.managed_externally = False
                md = mldyn.score(df.slice(0, di + 1), mldyn_params)
                md_proba = (md.get("indicators") or {}).get("proba_up")
                if md_proba is not None:
                    vm = float(strat._DEFAULTS["veto_margin"])
                    disagree = (md_proba < 0.5 - vm) if side == "long" else (md_proba > 0.5 + vm)
                    if disagree:
                        last_seen = None   # marker veté : pas d'affichage
                        continue
                    agree = (md_proba >= 0.5) if side == "long" else (md_proba <= 0.5)
                    fusion = "confirmed" if agree else "neutral"
            except Exception:
                fusion = None

        tp, sl = _tp_sl(side, c_now, atr, tp_mult, sl_mult)
        markers.append({
            "time": int(times[di]), "setup": chosen["name"], "side": side,
            "entry": round(c_now, 8), "tp": round(tp, 8), "sl": round(sl, 8),
            "p_event": round(float(p_amp[i]), 4), "p_up": round(float(p_up[i]), 4),
            "regime_lbl": REGIME_LABELS.get(regimes[i], "?"),
            "confluence": False, "fusion": fusion,
        })
    return {"supported": True, "reason": "", "markers": markers}


@router.get("/api/scanner/setup_series", dependencies=[Depends(verify_api_key)])
def scanner_setup_series(symbol: str = "BTC/USDC", timeframe: str = "1h",
                         limit: int = 300, strategy: str = "v8"):
    """Markers des setups (entrée/TP/SL) d'une stratégie par bougie, pour le
    graphique scanner. ``strategy`` ∈ {v8, v11, v12}."""
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    strat_key = (strategy or "v8").lower()
    if strat_key not in ("v8", "v11", "v12"):
        raise HTTPException(400, "strategy doit être v8, v11 ou v12")
    try:
        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")

        fetch_n = max(limit + 260, 460)
        df = scanner.fetch_ohlcv(symbol, tf, fetch_n)
        if df is None or len(df) < 230:
            raise HTTPException(404, f"Données insuffisantes pour {symbol}/{tf}")

        if strat_key == "v8":
            payload = _setup_series_v8(df, tf, limit)
        else:
            payload = _setup_series_v11(df, tf, limit, strat_key)

        payload.update({"symbol": symbol, "timeframe": tf, "strategy": strat_key,
                        "n_setups": len(payload.get("markers", []))})
        return JSONResponse(content=_clean(payload))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/setup_series : {e}", exc_info=True)
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
                # Détails exposés pour les stratégies V7 (omnibus / stat-V4) :
                # setup, régime, probas, paramètres de sortie. Tout est optionnel —
                # une stratégie classique renverra simplement None côté UI.
                def _num(v, nd=3):
                    try: return round(float(v), nd) if v is not None else None
                    except (TypeError, ValueError): return None
                signals.append({
                    "strategy":   name,
                    "side":       side,
                    "score":      _num(score) or 0.0,
                    "reason":     reason,
                    "skipped":    False,
                    "active":     is_active,
                    # Détails V7 (optionnels)
                    "setup":          result.get("setup"),
                    "setup_priority": result.get("setup_priority"),
                    "regime_lbl":     result.get("regime_lbl"),
                    "p_event":        _num(result.get("p_event"), 3),
                    "p_up":           _num(result.get("p_up"),    3),
                    "sl_atr_mult":    _num(result.get("sl_atr_mult"), 2),
                    "tp_atr_mult":    _num(result.get("tp_atr_mult"), 2),
                    "exit_after_bars":result.get("exit_after_bars"),
                    "size_factor":    _num(result.get("size_factor"), 3),
                    "tf_detected":    result.get("tf_detected"),
                    "exit_td_active": result.get("exit_td_active"),
                    "indicators":     {
                        # Sous-ensemble lisible — pas l'intégralité (auc, etc.)
                        k: result.get("indicators", {}).get(k)
                        for k in ("adx", "rsi", "auc_amp", "auc_dir", "n_features")
                        if result.get("indicators", {}).get(k) is not None
                    } or None,
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
