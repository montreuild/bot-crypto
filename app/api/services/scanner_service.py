"""Couche service du scanner (V4-K / ARCH-07) — calculs purs, testables
sans monter FastAPI.

Les routes (app/api/routes/scanner.py) ne font plus que parser les query
params, charger les données et sérialiser ; toute la logique métier
(payload graphique, séries de setups, analyse SMC, rejeu, signaux) vit ici.
"""
import importlib
import logging
import math

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategyML

logger = logging.getLogger(__name__)


# ── Graphique OHLCV + indicateurs ────────────────────────────────────────────
def build_chart_payload(df, symbol: str, timeframe: str) -> dict:
    """Bougies OHLCV + séries d'indicateurs pour le graphique scanner."""
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
            g, ls = _safe(gain[i]), _safe(loss[i])
            if g is None or ls is None:
                continue
            rsi = 100.0 if ls == 0.0 else 100 - (100 / (1 + g / ls))
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

    return {
        "symbol":     symbol,
        "timeframe":  timeframe,
        "n_bars":     n,
        "candles":    candles,
        "indicators": indicators,
    }


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
    from app.core.indicators import bearish_excess_series
    from app.ml.backend import MLBackend
    from app.strategies.opus_omnibus_v8 import (
        _DEFAULT_SETUPS,
        REGIME_CHOPPY,
        REGIME_LABELS,
        _classify_regime,
        _evaluate_setup,
    )
    from app.strategies.opus_stat_pretrained_v4 import (
        _detect_timeframe,
        _FeatureBuilder,
        _load_pretrained,
    )

    tf_detected = _detect_timeframe(df)
    if tf_detected not in ("15m", "30m", "1h"):
        return {"supported": False, "reason": f"Timeframe {tf_detected} non supporté par V8",
                "markers": []}

    # Phase6 : _FeatureBuilder.build prend/retourne du Polars maintenant.
    window = MLBackend.window_polars(df, n=len(df))
    feats  = _FeatureBuilder().build(window)
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
        # Phase6 : batch prediction Polars → ndarray 2D (n_rows, n_features)
        # avec imputation des NaN/inf par les médianes du train.
        feat_names = list(entry["features"])
        med = medians_all.get((tf_detected, target), {})
        cols_present = [c for c in feat_names if c in feats.columns]
        arr = feats.select(cols_present).to_numpy()
        # Replace inf by NaN puis impute colonne par colonne
        arr = np.where(np.isfinite(arr), arr, np.nan)
        for j, col in enumerate(cols_present):
            col_data = arr[:, j]
            mask = ~np.isfinite(col_data)
            if mask.any():
                col_data[mask] = float(med.get(col, 0.0))
        # Booster natif LightGBM : predict() retourne P(class=1) en binaire.
        return entry["model"].predict(arr)

    p_amp = _batch(amp_entry, "amp")
    p_up  = _batch(dir_entry, "dir")

    adx_arr  = feats["ADX"].fill_null(0.0).to_numpy()
    bull_arr = feats["MM_bullish_align"].fill_null(0).cast(pl.Int64).to_numpy()
    bear_arr = feats["MM_bearish_align"].fill_null(0).cast(pl.Int64).to_numpy()
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
        _SUPPORTED_TFS,
        REGIME_LABELS,
        _apply_setup_overrides,
        _build_features,
        _detect_timeframe,
        _exit_td_window_active,
        _regime_history_v11,
        _select_setup,
        _signal_up_dynamic_risk,
        _window_polars,
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


# ── Analyse Smart Money Concepts (overlay graphique scanner/replay) ─────────
def build_smc_payload(cfg: dict, df, symbol: str, tf: str) -> dict:
    """Analyse SMC complète (structure, liquidité, OB/FVG, sessions, HTF,
    signal smart_money) — payload brut prêt pour _clean/JSONResponse."""
    from app.core import smc
    from app.core.param_resolution import resolve_strategy_params
    from app.strategies.smart_money import Strategy as _SMCStrategy

    # Overlay optimizer_results du timeframe (même résolution que le live et
    # le backtest) → l'UI reflète la config RÉELLEMENT tradée par le bot.
    resolved_params = {"smart_money":
                       resolve_strategy_params(cfg, tf, symbol).get("smart_money", {})}
    # Une seule analyse partagée par la sérialisation ET par score/trade_plans
    # (via le cache d'instance de la stratégie) : len(df) ≤ 3000 = max_window,
    # donc la fenêtre de la stratégie == df.
    strat = _SMCStrategy()
    p_full = strat._p(resolved_params)
    res, res_aux = strat._analyze_cached(df, p_full)

    times = df["time"].dt.epoch(time_unit="s").to_list()
    n = len(df)
    last_t = int(times[-1])

    def _t(idx, default=None):
        try:
            return int(times[int(idx)])
        except (TypeError, ValueError, IndexError):
            return default

    # ── Zones (rectangles top/bottom bornés dans le temps) ───────────────
    order_blocks = [{
        "kind": ob["kind"], "top": ob["top"], "bottom": ob["bottom"],
        "time_start": _t(ob["index"]),
        "time_end":   _t(ob["invalidated_at"], last_t),
        "status": ("invalidated" if ob["invalidated_at"] is not None
                   else "touched" if ob["touched_at"] is not None
                   else "fresh"),
        "strength": ob["strength"],
    } for ob in res["order_blocks"][-14:]]

    pools = [{
        "kind": pool["kind"], "level": pool["level"],
        "top": pool["top"], "bottom": pool["bottom"],
        "time_start": _t(min(pool["indices"])),
        "time_end":   _t(pool["swept_at"], last_t),
        "status": "swept" if pool["swept_at"] is not None else "active",
        "n_touches": len(pool["indices"]),
    } for pool in res["liquidity_pools"][-16:]]

    fvgs = [{
        "kind": fv["kind"], "top": fv["top"], "bottom": fv["bottom"],
        "time_start": _t(fv["index"]),
        "time_end":   _t(fv["filled_at"], last_t),
        "status": ("filled" if fv["filled_at"] is not None
                   else "mitigated" if fv["mitigated_at"] is not None
                   else "open"),
    } for fv in res["fvgs"][-14:]]

    voids = [{
        "kind": vd["kind"], "top": vd["top"], "bottom": vd["bottom"],
        "time_start": _t(vd["start_index"]),
        "time_end":   _t(vd["filled_at"], last_t),
        "status": ("filled" if vd["filled_at"] is not None
                   else "mitigated" if vd["mitigated_at"] is not None
                   else "open"),
    } for vd in res["liquidity_voids"][-12:]]

    breakers = [{
        "kind": brk["kind"], "top": brk["top"], "bottom": brk["bottom"],
        "time_start": _t(brk["created_at"]),
        "time_end":   _t(brk["invalidated_at"], last_t),
        "status": ("invalidated" if brk["invalidated_at"] is not None
                   else "touched" if brk["touched_at"] is not None
                   else "fresh"),
    } for brk in res["breakers"][-10:]]

    rejections = [{
        "kind": rb["kind"], "top": rb["top"], "bottom": rb["bottom"],
        "time_start": _t(rb["index"]),
        "time_end":   _t(rb["invalidated_at"], last_t),
        "status": ("invalidated" if rb["invalidated_at"] is not None
                   else "touched" if rb["touched_at"] is not None
                   else "fresh"),
    } for rb in res["rejection_blocks"][-10:]]

    # Volume profile (POC / HVN / LVN), session courante et biais HTF
    import numpy as _np
    _h = df["high"].to_numpy().astype(float)
    _l = df["low"].to_numpy().astype(float)
    _c = df["close"].to_numpy().astype(float)
    _v = df["volume"].to_numpy().astype(float)
    vp = smc.volume_profile(_h, _l, _c, _v, n - 1,
                            lookback=int(p_full.get("vp_lookback", 240)),
                            n_bins=int(p_full.get("vp_bins", 40)))
    vprofile = None
    if vp:
        vprofile = {"poc": round(vp["poc"], 8),
                    "hvns": [round(x, 8) for x in vp["hvns"][:8]],
                    "lvns": [round(x, 8) for x in vp["lvns"][:8]]}
    last_epoch = int(times[-1])
    kz_arr = smc.killzone_flags(_np.array([last_epoch], dtype=_np.int64))
    session = {"name": smc.session_label(last_epoch),
               "in_killzone": bool(kz_arr[0])}
    # Biais HTF : réutilise l'analyse de l'aux si disponible (mêmes params
    # swing que le signal), sinon calcule avec _smc_params (cohérence).
    htf_meta = (res_aux or {}).get("htf_meta")
    if htf_meta is None:
        from app.strategies.smart_money import _HTF_SEC_MAP
        _, htf_meta = smc.htf_trend_series(
            df, _SMCStrategy._smc_params(p_full),
            mult=int(p_full.get("htf_mult", 4)), htf_sec_map=_HTF_SEC_MAP)
    htf_bias = {
        "trend": htf_meta["trend"],
        "label": {1: "haussier", -1: "baissier", 0: "neutre"}[htf_meta["trend"]],
        "n_htf": htf_meta["n_htf"],
    }

    # Zigzag de structure (peaks/troughs) + projection de cycle
    structure_line = [{
        "time": _t(pt["index"]), "price": pt["price"],
        "kind": pt["kind"], "label": pt["label"],
    } for pt in res["structure_line"][-40:] if _t(pt["index"]) is not None]
    cycle = None
    if res["cycle"]:
        cy = res["cycle"]
        cycle = {
            "phase": cy["phase"], "boundary": cy["boundary"],
            "target": cy["target"], "progress": cy["progress"],
            "from_time": _t(cy["from_index"]),
            "from_price": cy["from_price"],
        }

    # ── Markers (structure + sweeps + swings labellisés) ─────────────────
    markers = []
    for ev in res["structure_events"][-30:]:
        markers.append({
            "time": _t(ev["index"]), "type": ev["kind"],
            "direction": ev["direction"], "level": ev["level"],
        })
    for ev in res["sweeps"][-20:]:
        markers.append({
            "time": _t(ev["index"]), "type": "SWEEP",
            "direction": "down" if ev["kind"] == "sell_side" else "up",
            "level": ev["level"], "rejected": ev["rejected"],
            "source": ev["source"],
        })
    swing_marks = [{
        "time": _t(sw["index"]), "type": "SWING",
        "label": sw["label"], "price": sw["price"], "kind": sw["kind"],
    } for sw in res["swings"][-24:] if sw["label"]]

    # ── Trendlines + canal ────────────────────────────────────────────────
    trendlines = [{
        "kind": t["kind"],
        "time1": _t(t["x1"]), "y1": t["y1"],
        "time2": _t(t["x2"]), "y2": t["y2"],
    } for t in res["trendlines"]]
    channel = None
    if res["channel"]:
        ch = res["channel"]
        channel = {
            "time_start": _t(ch["start_index"]),
            "time_end":   _t(ch["end_index"]),
            "mid_start":  ch["mid_start"], "mid_end": ch["mid_end"],
            "half_width": ch["half_width"],
        }

    # ── Signal courant + plans de trade de la stratégie smart_money ──────
    # Réutilisent le cache d'analyse de `strat` (fenêtre == df) et les mêmes
    # params résolus par TF → aucune analyse redondante, cohérence UI/live.
    signal = None
    trade_plans = []
    try:
        trade_plans = strat.trade_plans(df, resolved_params)
        sig = strat.score(df, resolved_params)
        if sig.get("side") not in (None, "none"):
            signal = {
                "side":   sig["side"],
                "score":  sig.get("score"),
                "setup":  sig.get("setup"),
                "entry":  round(float(df["close"][-1]), 8),
                "stop":   sig.get("stop_hint"),
                "tp":     sig.get("tp_hint"),
                "reason": sig.get("reason", ""),
                **{k: (sig.get("indicators") or {}).get(k)
                   for k in ("gain_pct", "rr", "pd_zone", "tp_source")},
            }
        else:
            signal = {"side": "none", "reason": sig.get("reason", "")}
    except Exception as e:
        logger.warning(f"[smc] signal stratégie KO : {e}")

    return {
        "symbol": symbol, "timeframe": tf, "n_bars": n,
        "bias": res["bias"],
        "premium_discount": res["premium_discount"],
        "order_blocks": order_blocks,
        "liquidity_pools": pools,
        "fvgs": fvgs,
        "liquidity_voids": voids,
        "breakers": breakers,
        "rejection_blocks": rejections,
        "volume_profile": vprofile,
        "session": session,
        "htf_bias": htf_bias,
        "structure_line": structure_line,
        "cycle": cycle,
        "markers": markers,
        "swing_labels": swing_marks,
        "trendlines": trendlines,
        "channel": channel,
        "signal": signal,
        "trade_plans": trade_plans,
    }


# ── Smart replay : payload complet pour rejeu bougie par bougie ─────────────
def build_smc_replay_payload(cfg: dict, df, symbol: str, tf: str) -> dict:
    """Payload de rejeu Smart Money : moteur causal + trades du VRAI
    Backtester avec les paramètres résolus du TF/symbole."""
    from app.core import smc
    from app.core.param_resolution import resolve_strategy_params
    from app.engine.backtest import Backtester as _Backtester
    from app.engine.engine import Engine as _Engine
    from app.strategies.smart_money import Strategy as _SMCStrategy

    n = len(df)
    times = df["time"].dt.epoch(time_unit="s").to_list()

    # Paramètres résolus (base YAML + overlay optimizer_results du TF/symbole)
    resolved = resolve_strategy_params(cfg, tf, symbol)
    p_strat  = {**_SMCStrategy.fixed_params,
                **{k: v for k, v in (resolved.get("smart_money") or {}).items()
                   if v is not None}}
    from app.strategies.smart_money import _HTF_SEC_MAP
    res = smc.analyze(df, _SMCStrategy._smc_params(p_strat))
    htf_arr, htf_meta = smc.htf_trend_series(
        df, _SMCStrategy._smc_params(p_strat),
        mult=int(p_strat.get("htf_mult", 4)), htf_sec_map=_HTF_SEC_MAP)

    # Trades réels : Backtester (mêmes coûts/priorités que le backtest)
    eng = _Engine()
    eng.register(_SMCStrategy(), silent=True)
    bt_res = _Backtester(eng, cfg).run(df, symbol, timeframe=tf)
    trades = [{
        "entry_bar": t["bar"], "exit_bar": t.get("exit_bar"),
        "side": t["side"], "setup": t.get("setup"),
        "entry": t["entry"], "stop": t["stop"],
        "tp": t.get("take_profit"),
        "exit": t.get("exit"), "exit_reason": t.get("exit_reason"),
        "pnl": round(t.get("pnl") or 0.0, 4),
        "pnl_pct": t.get("pnl_pct"),
        "score": t.get("score"), "reason": t.get("reason", ""),
    } for t in bt_res.trades]

    candles = [{
        "time": int(times[i]),
        "open":  round(float(df["open"][i]), 8),
        "high":  round(float(df["high"][i]), 8),
        "low":   round(float(df["low"][i]), 8),
        "close": round(float(df["close"][i]), 8),
    } for i in range(n)]

    def _swings():
        return [{"index": s["index"], "price": s["price"], "kind": s["kind"],
                 "label": s["label"], "confirmed_at": s["confirmed_at"],
                 "swept_at": s["swept_at"]} for s in res["_all_swings"]]

    def _pools():
        return [{"kind": x["kind"], "level": x["level"], "top": x["top"],
                 "bottom": x["bottom"], "formed_at": x["formed_at"],
                 "start_index": min(x["indices"]),
                 "n_touches": len(x["indices"]),
                 "swept_at": x["swept_at"]} for x in res["_all_pools"]]

    def _zones(lst, start_key="index"):
        return [{"kind": x["kind"], "top": x["top"], "bottom": x["bottom"],
                 "index": x[start_key], "created_at": x["created_at"],
                 "touched_at": x["touched_at"],
                 "invalidated_at": x["invalidated_at"],
                 "strength": x.get("strength", 1)} for x in lst]

    payload = {
        "symbol": symbol, "timeframe": tf, "n_bars": n,
        "start_index": max(260, int(_SMCStrategy.warmup_bars)),
        "candles": candles,
        "swings": _swings(),
        "struct_events": res["_all_struct_events"],
        "sweeps": res["_all_sweeps"],
        "pools": _pools(),
        "order_blocks": _zones(res["_all_obs"]),
        "breakers": _zones(res["_all_breakers"]),
        "rejections": _zones(res["_all_rejections"]),
        "fvgs": [{"kind": x["kind"], "top": x["top"], "bottom": x["bottom"],
                  "index": x["index"], "mitigated_at": x["mitigated_at"],
                  "filled_at": x["filled_at"]} for x in res["_all_fvgs"]],
        "voids": [{"kind": x["kind"], "top": x["top"], "bottom": x["bottom"],
                   "start_index": x["start_index"], "end_index": x["end_index"],
                   "filled_at": x["filled_at"]} for x in res["_all_voids"]],
        "trend": [int(v) for v in res["_trend_arr"]],
        "htf_trend": [int(v) for v in htf_arr],
        "trades": trades,
        "params_used": {k: p_strat.get(k) for k in
                        ("min_score", "min_rr", "min_gain_pct", "htf_filter",
                         "sl_buffer_atr", "use_rejection_blocks", "kz_filter",
                         "kz_bonus", "amd_bonus", "vp_confluence")},
    }
    return payload


# ── Signaux de toutes les stratégies sur un symbole ─────────────────────────
def build_signals_payload(cfg: dict, df, symbol: str, tf: str) -> dict:
    """Exécute toutes les stratégies découvertes sur le df et retourne leurs
    signaux (side/score/détails V7, ML pré-entraîné chargé si présent)."""
    from app.api.helpers import _discover_strategies
    enabled_set = set(cfg["strategies"].get("enabled", []))
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
            result = inst.score(df, cfg.get("strategy_params", {}))
            side   = result.get("side", "none")
            score  = result.get("score", 0.0)
            reason = result.get("reason", "")
            # Détails exposés pour les stratégies V7 (omnibus / stat-V4) :
            # setup, régime, probas, paramètres de sortie. Tout est optionnel —
            # une stratégie classique renverra simplement None côté UI.
            def _num(v, nd=3):
                try:
                    return round(float(v), nd) if v is not None else None
                except (TypeError, ValueError):
                    return None
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
    return {"symbol": symbol, "timeframe": tf, "signals": signals}
