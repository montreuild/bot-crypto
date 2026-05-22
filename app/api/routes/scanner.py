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


@router.get("/api/scanner/v7_series", dependencies=[Depends(verify_api_key)])
def scanner_v7_series(symbol: str = "BTC/USDC", timeframe: str = "1h",
                      limit: int = 300):
    """Séries V7 par bougie pour le graphique scanner.

    Calcule, sur la fenêtre OHLCV demandée, les probabilités V4 (``p_event`` /
    ``p_up``) et le setup V7 sélectionné par bougie, en réutilisant le pkl
    pré-entraîné partagé avec ``opus_omnibus_v7_pretrained``. Renvoie des
    séries alignées sur les ``time`` du graphique scanner et la liste des
    bougies déclenchant un setup (pour markers).
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        # Imports paresseux : évite de charger pandas/lightgbm tant qu'on ne
        # demande pas explicitement le sous-graphique V7.
        from app.strategies.opus_stat_pretrained_v4 import (
            _FeatureBuilder,
            _load_pretrained,
            _to_pandas_window,
            _detect_timeframe,
        )
        from app.strategies.opus_omnibus_v7_pretrained import (
            _DEFAULT_SETUPS,
            _classify_regime,
            _evaluate_setup,
            _exit_td_window_active,
            REGIME_LABELS,
            _EXIT_TD_WINDOW_BARS,
        )

        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")

        # Le FeatureBuilder V4 a besoin de ≥210 bougies d'historique. On
        # fetch limit + 260 puis on coupe pour renvoyer ``limit`` valeurs max.
        fetch_n = max(limit + 260, 460)
        df = scanner.fetch_ohlcv(symbol, tf, fetch_n)
        if df is None or len(df) < 230:
            raise HTTPException(404, f"Données insuffisantes pour {symbol}/{tf}")

        tf_detected = _detect_timeframe(df)
        if tf_detected not in ("15m", "30m", "1h"):
            return JSONResponse(content={
                "symbol":     symbol,
                "timeframe":  tf,
                "supported":  False,
                "reason":     f"Timeframe {tf_detected} non supporté par V7",
                "p_event":    [],
                "p_up":       [],
                "setups":     [],
            })

        # 1. Features V4 sur toute la fenêtre (vectorisé)
        pdf   = _to_pandas_window(df, n=len(df))
        feats = _FeatureBuilder().build(pdf)
        if feats is None or len(feats) == 0:
            raise HTTPException(500, "Construction des features V4 impossible")

        # 2. Modèles pré-entraînés + médianes
        models, medians_all = _load_pretrained()
        amp_entry = models.get((tf_detected, "amp", "single"))
        dir_entry = models.get((tf_detected, "dir", "single"))
        if amp_entry is None or dir_entry is None:
            raise HTTPException(503, f"Modèles V4 indisponibles pour {tf_detected}")

        def _batch_predict(entry: dict, target: str) -> np.ndarray:
            feat_names = list(entry["features"])
            med = medians_all.get((tf_detected, target), {})
            X = feats.reindex(columns=feat_names).copy()
            X = X.replace([np.inf, -np.inf], np.nan)
            for col in feat_names:
                X[col] = X[col].fillna(med.get(col, 0.0))
            return entry["model"].predict_proba(X.values)[:, 1]

        p_amp = _batch_predict(amp_entry, "amp")
        p_up  = _batch_predict(dir_entry, "dir")

        # 3. Régime par bougie + fenêtre exit-TD glissante
        adx_arr  = feats["ADX"].fillna(0.0).to_numpy()
        bull_arr = feats["MM_bullish_align"].fillna(0).astype(int).to_numpy()
        bear_arr = feats["MM_bearish_align"].fillna(0).astype(int).to_numpy()
        n_feats  = len(feats)
        regimes  = [
            _classify_regime(float(adx_arr[i]), int(bull_arr[i]),
                             int(bear_arr[i]), 20.0)
            for i in range(n_feats)
        ]

        setups_def = sorted(
            [dict(s) for s in _DEFAULT_SETUPS], key=lambda s: s["priority"]
        )
        win = _EXIT_TD_WINDOW_BARS

        setup_at: list = [None] * n_feats
        side_at:  list = [None] * n_feats
        for i in range(n_feats):
            regime_hist = regimes[max(0, i - win - 1): i + 1]
            exit_td = _exit_td_window_active(regime_hist, win)
            for s in setups_def:
                if _evaluate_setup(s, regimes[i],
                                   float(p_amp[i]), float(p_up[i]), exit_td):
                    setup_at[i] = s["name"]
                    side_at[i]  = "long" if s["direction"] == 1 else "short"
                    break

        # 4. Garde uniquement les `limit` dernières bougies pour rester aligné
        # avec ``/api/scanner/chart``.
        times = df["time"].dt.epoch(time_unit="s").to_list()
        start = max(0, len(df) - limit)

        def _ok(v):
            try:
                f = float(v)
                return not (math.isnan(f) or math.isinf(f))
            except Exception:
                return False

        p_event_series = [
            {"time": int(times[i]), "value": round(float(p_amp[i]), 4)}
            for i in range(start, n_feats) if _ok(p_amp[i])
        ]
        p_up_series = [
            {"time": int(times[i]), "value": round(float(p_up[i]), 4)}
            for i in range(start, n_feats) if _ok(p_up[i])
        ]

        # 5. Markers = bougies où un setup s'arme (avec dédup sur séquences
        # consécutives identiques pour ne pas saturer le graphique).
        setup_markers: list = []
        last_seen = None
        for i in range(start, n_feats):
            name = setup_at[i]
            if name is None:
                last_seen = None
                continue
            if name == last_seen:
                continue
            last_seen = name
            setup_markers.append({
                "time":       int(times[i]),
                "setup":      name,
                "side":       side_at[i],
                "p_event":    round(float(p_amp[i]), 4),
                "p_up":       round(float(p_up[i]), 4),
                "regime_lbl": REGIME_LABELS.get(regimes[i], "?"),
            })

        return JSONResponse(content=_clean({
            "symbol":      symbol,
            "timeframe":   tf_detected,
            "supported":   True,
            "n_bars":      len(p_up_series),
            "p_event":     p_event_series,
            "p_up":        p_up_series,
            "setups":      setup_markers,
            "n_setups":    len(setup_markers),
        }))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/v7_series : {e}", exc_info=True)
        raise HTTPException(500, f"Erreur interne ({err_id})")


@router.get("/api/scanner/v8_series", dependencies=[Depends(verify_api_key)])
def scanner_v8_series(symbol: str = "BTC/USDC", timeframe: str = "1h",
                      limit: int = 300):
    """Séries V8 par bougie — p_event/p_up + SIGNAL_UP + setups V8.

    Calcule l'excès baissier vectorisé par bougie (RSI<38, 2+ rouges, prix<SMA20-1.5%)
    et évalue les setups V8 (SIGNAL_UP principal, SHORT_TD_HIGH/TD/CHOPPY, LONG_RANGE_STRICT).
    LONG_CHOPPY apparaît comme "confluence" quand SIGNAL_UP + LONG_CHOPPY simultanés.
    """
    if not state.cfg:
        raise HTTPException(503, "Config non chargée")
    try:
        from app.strategies.opus_stat_pretrained_v4 import (
            _FeatureBuilder,
            _load_pretrained,
            _to_pandas_window,
            _detect_timeframe,
        )
        from app.strategies.opus_omnibus_v8 import (
            _DEFAULT_SETUPS,
            _classify_regime,
            _evaluate_setup,
            REGIME_LABELS,
            REGIME_CHOPPY,
        )
        from app.core.indicators import bearish_excess_series

        exchange = create_exchange(state.cfg)
        scanner  = MarketScanner(exchange, state.cfg)
        tf       = timeframe or state.cfg["trading"].get("timeframe", "1h")

        fetch_n = max(limit + 260, 460)
        df = scanner.fetch_ohlcv(symbol, tf, fetch_n)
        if df is None or len(df) < 230:
            raise HTTPException(404, f"Données insuffisantes pour {symbol}/{tf}")

        tf_detected = _detect_timeframe(df)
        if tf_detected not in ("15m", "30m", "1h"):
            return JSONResponse(content={
                "symbol": symbol, "timeframe": tf, "supported": False,
                "reason": f"Timeframe {tf_detected} non supporté par V8",
                "p_event": [], "p_up": [], "setups": [], "bearish_excess": [],
            })

        # 1. Features V4
        pdf   = _to_pandas_window(df, n=len(df))
        feats = _FeatureBuilder().build(pdf)
        if feats is None or len(feats) == 0:
            raise HTTPException(500, "Construction des features V4 impossible")

        # 2. Prédictions batch
        models, medians_all = _load_pretrained()
        amp_entry = models.get((tf_detected, "amp", "single"))
        dir_entry = models.get((tf_detected, "dir", "single"))
        if amp_entry is None or dir_entry is None:
            raise HTTPException(503, f"Modèles V4 indisponibles pour {tf_detected}")

        def _batch_predict(entry: dict, target: str) -> np.ndarray:
            feat_names = list(entry["features"])
            med = medians_all.get((tf_detected, target), {})
            X = feats.reindex(columns=feat_names).copy()
            X = X.replace([np.inf, -np.inf], np.nan)
            for col in feat_names:
                X[col] = X[col].fillna(med.get(col, 0.0))
            return entry["model"].predict_proba(X.values)[:, 1]

        p_amp = _batch_predict(amp_entry, "amp")
        p_up  = _batch_predict(dir_entry, "dir")

        # 3. Régime par bougie
        adx_arr  = feats["ADX"].fillna(0.0).to_numpy()
        bull_arr = feats["MM_bullish_align"].fillna(0).astype(int).to_numpy()
        bear_arr = feats["MM_bearish_align"].fillna(0).astype(int).to_numpy()
        n_feats  = len(feats)
        regimes  = [
            _classify_regime(float(adx_arr[i]), int(bull_arr[i]),
                             int(bear_arr[i]), 20.0)
            for i in range(n_feats)
        ]

        # 4. Excès baissier vectorisé — seuils alignés avec les défauts V8
        be_series = bearish_excess_series(df, rsi_threshold=42.0, price_dev_pct=1.0).to_numpy().astype(bool)
        # Align with feats length (feats may be shorter than df due to feature window)
        be_offset = len(df) - n_feats
        be_aligned = be_series[be_offset:] if be_offset > 0 else be_series[:n_feats]

        # 5. Setups V8 par bougie
        setups_def = sorted([dict(s) for s in _DEFAULT_SETUPS], key=lambda s: s["priority"])

        setup_at: list = [None] * n_feats
        side_at:  list = [None] * n_feats
        confluence_at: list = [False] * n_feats
        for i in range(n_feats):
            be_val = bool(be_aligned[i]) if i < len(be_aligned) else False
            for s in setups_def:
                if _evaluate_setup(s, regimes[i], float(p_amp[i]), float(p_up[i]),
                                   False, be_val):
                    setup_at[i] = s["name"]
                    side_at[i]  = "long" if s["direction"] == 1 else "short"
                    # Check LONG_CHOPPY confluence for SIGNAL_UP
                    if s["name"] == "SIGNAL_UP" and regimes[i] == REGIME_CHOPPY and p_up[i] > 0.58 and p_amp[i] > 0.50:
                        confluence_at[i] = True
                    break

        # 6. Dernières `limit` bougies alignées
        times = df["time"].dt.epoch(time_unit="s").to_list()
        start = max(0, len(df) - limit)

        def _ok(v):
            try:
                f = float(v)
                return not (math.isnan(f) or math.isinf(f))
            except Exception:
                return False

        p_event_series = [
            {"time": int(times[i]), "value": round(float(p_amp[i]), 4)}
            for i in range(start, n_feats) if _ok(p_amp[i])
        ]
        p_up_series = [
            {"time": int(times[i]), "value": round(float(p_up[i]), 4)}
            for i in range(start, n_feats) if _ok(p_up[i])
        ]

        # Bearish excess series (for visualization)
        be_vis = [
            {"time": int(times[i]), "value": 1.0 if (i < len(be_aligned) and be_aligned[i]) else 0.0}
            for i in range(start, n_feats)
        ]

        # 7. Markers
        setup_markers: list = []
        last_seen = None
        for i in range(start, n_feats):
            name = setup_at[i]
            if name is None:
                last_seen = None
                continue
            if name == last_seen and not confluence_at[i]:
                continue
            last_seen = name
            be_val = bool(be_aligned[i]) if i < len(be_aligned) else False
            setup_markers.append({
                "time":         int(times[i]),
                "setup":        name,
                "side":         side_at[i],
                "p_event":      round(float(p_amp[i]), 4),
                "p_up":         round(float(p_up[i]), 4),
                "regime_lbl":   REGIME_LABELS.get(regimes[i], "?"),
                "bearish_excess": be_val,
                "confluence":   bool(confluence_at[i]),
            })

        return JSONResponse(content=_clean({
            "symbol":        symbol,
            "timeframe":     tf_detected,
            "supported":     True,
            "n_bars":        len(p_up_series),
            "p_event":       p_event_series,
            "p_up":          p_up_series,
            "bearish_excess": be_vis,
            "setups":        setup_markers,
            "n_setups":      len(setup_markers),
        }))
    except HTTPException:
        raise
    except Exception as e:
        err_id = uuid.uuid4()
        logger.error(f"[API] Erreur {err_id} scanner/v8_series : {e}", exc_info=True)
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
