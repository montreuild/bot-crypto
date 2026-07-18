"""Stratégie Opus Omnibus V10 RETRAINED — V10 (routing setups) + entraînement inline.

V10 (``opus_omnibus_v10``) utilise les modèles V4 PRÉ-ENTRAÎNÉS figés (pkl).
Cette variante conserve la logique de routing V10 (8 setups, SIGNAL_UP à risque
dynamique, LONG_TU strict, LONG_RANGE_LIGHT, excès baissier) mais **entraîne ses
propres modèles LightGBM amp/dir inline** (réentraînement walk-forward toutes les
``retrain_every`` barres ; en live, ``managed_externally=True`` délègue au
``MLStrategyTrainer``).

Fichier **autonome** : tout le pipeline (FeatureBuilder V4 polars, moteur
d'entraînement LightGBM, helpers de régime, définitions des setups) est dupliqué
ici plutôt qu'importé d'autres stratégies. Supprimer une autre stratégie ne peut
donc pas casser celle-ci.
"""

import datetime as _dt
import gc
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategyML
from app.core.indicators import (
    safe_num as _safe_num,
    bars_since_cross,
    rolling_slope,
    rolling_hurst,
    rolling_rank_pct,
    pre_val,
)

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")

# Codes de régime
REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3
REGIME_LABELS   = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
    -1:              "?",
}

_EXIT_TD_WINDOW_BARS = 3

_EXCLUDED_COLS = frozenset({
    "time", "open", "high", "low", "close", "volume",
    "log_ret", "OBV",
    "SMA_20", "SMA_50", "SMA_100", "SMA_200",
    "EMA_20", "EMA_50", "EMA_100", "EMA_200",
    "EMA_9", "EMA_21",
    "high_20", "low_20", "high_50", "low_50", "high_100", "low_100",
    "ATR_14",
})

_NUMERIC_DTYPES = (
    pl.Float32, pl.Float64,
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers temporels
# ─────────────────────────────────────────────────────────────────────────────
def _detect_timeframe(df: pl.DataFrame) -> Optional[str]:
    if "time" not in df.columns or len(df) < 3:
        return None
    times = df["time"].tail(64)  # TF constant: derniers deltas suffisent (O(1))
    try:
        deltas = times.diff().drop_nulls()
        if len(deltas) == 0:
            return None
        try:
            med_us = deltas.dt.total_microseconds().median()
            med_s  = float(med_us) / 1_000_000.0
        except Exception:
            med_s = float(deltas.median().total_seconds())
    except Exception:
        arr = times.to_numpy()
        try:
            diffs = np.diff(arr.astype("float64"))
            med_s = float(np.median(diffs))
            if med_s > 1e6:
                med_s /= 1000.0
        except Exception:
            return None
    if med_s <= 0:
        return None
    if abs(med_s - 900)  < 60:  return "15m"
    if abs(med_s - 1800) < 120: return "30m"
    if abs(med_s - 3600) < 240: return "1h"
    if abs(med_s - 14400) < 960:  return "4h"
    if abs(med_s - 86400) < 5760: return "1d"
    return None


def _last_bar_hour_dow(df: pl.DataFrame) -> tuple:
    if "time" not in df.columns or len(df) == 0:
        return None, None
    ts = df["time"][-1]
    try:
        if hasattr(ts, "hour") and hasattr(ts, "weekday"):
            return int(ts.hour), int(ts.weekday())
    except Exception:
        pass
    try:
        raw = float(ts)
        if raw > 1e12:
            raw /= 1000.0
        d = _dt.datetime.utcfromtimestamp(raw)
        return d.hour, d.weekday()
    except Exception:
        return None, None


# ─────────────────────────────────────────────────────────────────────────────
#  FeatureBuilder V4 (polars)
# ─────────────────────────────────────────────────────────────────────────────
def _ewm_alpha_np(arr: np.ndarray, alpha: float) -> np.ndarray:
    out = np.full_like(arr, np.nan, dtype=np.float64)
    init = False
    prev = 0.0
    for i in range(len(arr)):
        v = arr[i]
        if np.isnan(v):
            out[i] = np.nan
            continue
        if not init:
            prev, init = v, True
        else:
            prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _build_features(raw_df: pl.DataFrame) -> Optional[pl.DataFrame]:
    """Construit ~462 colonnes V4 (avec lags 1/3/6/12)."""
    if raw_df is None or len(raw_df) < 210:
        return None

    df = raw_df
    if "time" in df.columns:
        df = df.sort("time")
    cast_exprs = [
        pl.col(c).cast(pl.Float64) for c in ("open", "high", "low", "close", "volume")
        if c in df.columns
    ]
    if cast_exprs:
        df = df.with_columns(cast_exprs)

    mm_exprs: List[pl.Expr] = [
        pl.col("close").pct_change().alias("ret"),
        ((pl.col("close") - pl.col("open")) / pl.col("open")).alias("ret_intra"),
        (pl.col("close") / pl.col("close").shift(1)).log().alias("log_ret"),
        pl.col("close").ewm_mean(span=9,  adjust=False).alias("EMA_9"),
        pl.col("close").ewm_mean(span=21, adjust=False).alias("EMA_21"),
    ]
    for n in (20, 50, 100, 200):
        mm_exprs.append(pl.col("close").rolling_mean(n).alias(f"SMA_{n}"))
        mm_exprs.append(pl.col("close").ewm_mean(span=n, adjust=False).alias(f"EMA_{n}"))
    df = df.with_columns(mm_exprs)

    dist_exprs: List[pl.Expr] = [
        ((pl.col("SMA_20") > pl.col("SMA_50")) &
         (pl.col("SMA_50") > pl.col("SMA_100")) &
         (pl.col("SMA_100") > pl.col("SMA_200"))).cast(pl.Int8).alias("MM_bullish_align"),
        ((pl.col("SMA_20") < pl.col("SMA_50")) &
         (pl.col("SMA_50") < pl.col("SMA_100")) &
         (pl.col("SMA_100") < pl.col("SMA_200"))).cast(pl.Int8).alias("MM_bearish_align"),
    ]
    for n in (20, 50, 100, 200):
        dist_exprs += [
            ((pl.col("close") - pl.col(f"SMA_{n}")) / pl.col(f"SMA_{n}")).alias(f"dist_SMA{n}"),
            ((pl.col("close") - pl.col(f"EMA_{n}")) / pl.col(f"EMA_{n}")).alias(f"dist_EMA{n}"),
        ]
    df = df.with_columns(dist_exprs)

    close_np = df["close"].to_numpy()
    slope_cols = {}
    for n in (20, 50, 100, 200):
        sl = rolling_slope(df[f"SMA_{n}"], min(n, 20)).to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            slope_cols[f"slope_SMA{n}"] = np.where(close_np != 0, sl / close_np, np.nan)
    cross_cols = {
        "cross_9_21":   bars_since_cross(df["EMA_9"],  df["EMA_21"]).to_numpy(),
        "cross_20_50":  bars_since_cross(df["SMA_20"], df["SMA_50"]).to_numpy(),
        "cross_50_100": bars_since_cross(df["SMA_50"], df["SMA_100"]).to_numpy(),
        "cross_50_200": bars_since_cross(df["SMA_50"], df["SMA_200"]).to_numpy(),
    }
    df = df.with_columns(
        [pl.Series(k, v) for k, v in slope_cols.items()] +
        [pl.Series(k, v) for k, v in cross_cols.items()]
    )

    rsi_roc_exprs: List[pl.Expr] = []
    for n in (7, 14, 21):
        delta = pl.col("close").diff()
        gain  = pl.when(delta > 0).then(delta).otherwise(0.0).ewm_mean(alpha=1.0 / n, adjust=False)
        loss  = pl.when(delta < 0).then(-delta).otherwise(0.0).ewm_mean(alpha=1.0 / n, adjust=False)
        rs    = gain / pl.when(loss == 0).then(None).otherwise(loss)
        rsi_roc_exprs.append((100 - (100 / (1 + rs))).alias(f"RSI_{n}"))
        rsi_roc_exprs.append((pl.col("close").pct_change(n) * 100).alias(f"ROC_{n}"))
    df = df.with_columns(rsi_roc_exprs)

    macd_fast = pl.col("close").ewm_mean(span=12, adjust=False)
    macd_slow = pl.col("close").ewm_mean(span=26, adjust=False)
    green     = (pl.col("close") > pl.col("open")).cast(pl.Float64)
    df = df.with_columns([
        pl.col("RSI_14").diff().alias("RSI_14_d1"),
        pl.col("RSI_14").diff(3).alias("RSI_14_d3"),
        (pl.col("RSI_14") < 30).cast(pl.Int8).alias("RSI_oversold"),
        (pl.col("RSI_14") > 70).cast(pl.Int8).alias("RSI_overbought"),
        ((pl.col("close") == pl.col("close").rolling_max(14)) &
         (pl.col("RSI_14") < pl.col("RSI_14").rolling_max(14) * 0.97)
        ).cast(pl.Int8).alias("bear_div"),
        ((pl.col("close") == pl.col("close").rolling_min(14)) &
         (pl.col("RSI_14") > pl.col("RSI_14").rolling_min(14) * 1.03)
        ).cast(pl.Int8).alias("bull_div"),
        green.rolling_mean(10).alias("green_ratio_10"),
        green.rolling_mean(20).alias("green_ratio_20"),
        pl.col("ret").rolling_mean(5).diff(5).alias("accel_5"),
        (macd_fast - macd_slow).alias("MACD"),
    ])
    df = df.with_columns([
        pl.col("RSI_14_d1").diff().alias("RSI_14_accel"),
        pl.col("MACD").ewm_mean(span=9, adjust=False).alias("MACD_signal"),
    ])
    df = df.with_columns([
        (pl.col("MACD") - pl.col("MACD_signal")).alias("MACD_hist"),
        (pl.col("MACD") > pl.col("MACD_signal")).cast(pl.Int8).alias("MACD_above_signal"),
        (pl.col("MACD").sign() - pl.col("MACD").shift(1).sign()).fill_null(0).alias("MACD_zero_cross"),
    ])
    df = df.with_columns([
        pl.col("MACD_hist").diff().alias("MACD_hist_d1"),
        pl.col("MACD_hist").diff(3).alias("MACD_hist_d3"),
    ])

    breakout_exprs: List[pl.Expr] = []
    for n in (20, 50, 100):
        h_n = pl.col("high").rolling_max(n).shift(1)
        l_n = pl.col("low").rolling_min(n).shift(1)
        rng = h_n - l_n
        breakout_exprs += [
            h_n.alias(f"high_{n}"),
            l_n.alias(f"low_{n}"),
            (pl.col("close") > h_n).cast(pl.Int8).alias(f"break_high_{n}"),
            (pl.col("close") < l_n).cast(pl.Int8).alias(f"break_low_{n}"),
            ((pl.col("close") - h_n) / h_n).alias(f"dist_high_{n}"),
            ((pl.col("close") - l_n) / l_n).alias(f"dist_low_{n}"),
            ((pl.col("close") - l_n) /
             pl.when(rng == 0).then(None).otherwise(rng)
            ).alias(f"range_pos_{n}"),
        ]
    df = df.with_columns(breakout_exprs)
    df = df.with_columns([
        ((pl.col("high").rolling_max(3).shift(1) > pl.col("high_20").shift(2)) &
         (pl.col("close") < pl.col("high_20"))
        ).cast(pl.Int8).alias("false_break_high_20"),
        ((pl.col("low").rolling_min(3).shift(1) < pl.col("low_20").shift(2)) &
         (pl.col("close") > pl.col("low_20"))
        ).cast(pl.Int8).alias("false_break_low_20"),
    ])

    bb_ma = pl.col("close").rolling_mean(20)
    bb_sd = pl.col("close").rolling_std(20)
    bb_up = bb_ma + 2.0 * bb_sd
    bb_lo = bb_ma - 2.0 * bb_sd
    bb_w  = bb_up - bb_lo
    big   = pl.col("ret").rolling_sum(5)
    sh50  = pl.col("high").rolling_max(50)
    sl50  = pl.col("low").rolling_min(50)
    df = df.with_columns([
        (bb_w / bb_ma).alias("BB_width"),
        ((pl.col("close") - bb_lo) /
         pl.when(bb_w == 0).then(None).otherwise(bb_w)
        ).alias("BB_pos"),
        ((pl.col("SMA_20") - pl.col("close")) / pl.col("close")).alias("pullback_to_sma20"),
        ((big.shift(3) > 0.01) &
         (pl.col("ret").rolling_sum(3) < 0)
        ).cast(pl.Int8).alias("pullback_after_rally"),
        ((big.shift(3) < -0.01) &
         (pl.col("ret").rolling_sum(3) > 0)
        ).cast(pl.Int8).alias("bounce_after_drop"),
        ((pl.col("close") - sl50) /
         pl.when((sh50 - sl50) == 0).then(None).otherwise(sh50 - sl50)
        ).alias("fib_pos"),
    ])
    df = df.with_columns([
        rolling_rank_pct(df["BB_width"], 100).alias("BB_width_rank100"),
    ])
    df = df.with_columns([
        (pl.col("BB_width_rank100") < 0.2).cast(pl.Int8).alias("BB_squeeze"),
        (pl.col("BB_width") > pl.col("BB_width").shift(5) * 1.2).cast(pl.Int8).alias("BB_expansion"),
    ])

    h_np = df["high"].to_numpy()
    l_np = df["low"].to_numpy()
    c_np = df["close"].to_numpy()
    a    = 1.0 / 14.0
    up = np.empty_like(h_np); up[:] = np.nan; up[1:] = h_np[1:] - h_np[:-1]
    dn = np.empty_like(l_np); dn[:] = np.nan; dn[1:] = -(l_np[1:] - l_np[:-1])
    plus_dm  = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    c_prev = np.concatenate(([np.nan], c_np[:-1]))
    tr  = np.maximum.reduce([h_np - l_np, np.abs(h_np - c_prev), np.abs(l_np - c_prev)])
    atr = _ewm_alpha_np(tr, a)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * _ewm_alpha_np(plus_dm,  a) / atr
        mdi = 100.0 * _ewm_alpha_np(minus_dm, a) / atr
        dx  = 100.0 * np.abs(pdi - mdi) / np.where(pdi + mdi == 0, np.nan, pdi + mdi)
    adx = _ewm_alpha_np(dx, a)
    df = df.with_columns([
        pl.Series("ATR_14",   atr),
        pl.Series("ADX",      adx),
        pl.Series("DI_plus",  pdi),
        pl.Series("DI_minus", mdi),
    ])
    df = df.with_columns([
        (pl.col("DI_plus") - pl.col("DI_minus")).alias("DI_diff"),
        (pl.col("ADX") > 25).cast(pl.Int8).alias("trend_strong"),
        (pl.col("ADX") > 40).cast(pl.Int8).alias("trend_very_strong"),
    ])

    strong = df["trend_strong"].to_numpy().astype(np.int64)
    if len(strong) > 0:
        shifted = np.concatenate(([0], strong[:-1]))
        grp = np.cumsum((strong != shifted).astype(np.int64))
        td = np.zeros(len(strong), dtype=np.int64)
        cur_grp, running = grp[0] if len(grp) > 0 else 0, 0
        for i in range(len(strong)):
            if grp[i] != cur_grp:
                cur_grp, running = grp[i], 0
            running += int(strong[i])
            td[i] = running
    else:
        td = np.zeros(0, dtype=np.int64)
    df = df.with_columns(pl.Series("trend_duration", td))

    df = df.with_columns(rolling_hurst(df["log_ret"], 100).alias("hurst_100"))

    df = df.with_columns([
        (pl.col("ATR_14") / pl.col("close")).alias("ATR_pct"),
        pl.col("ret").rolling_std(20).alias("vol_std_20"),
        (pl.col("volume") / pl.col("volume").rolling_mean(20)).alias("vol_ratio"),
        (pl.col("volume") / pl.col("volume").rolling_mean(50)).alias("vol_ratio_50"),
    ])
    obv_np = (
        df["close"].diff().sign().fill_null(0).cast(pl.Float64) * df["volume"]
    ).cum_sum().to_numpy()
    obv_s  = pl.Series("OBV", obv_np)
    vol_mean_10 = df.select(pl.col("volume").rolling_mean(10))["volume"].to_numpy()
    obv_slope = rolling_slope(obv_s, 10).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        obv_slope_norm = np.where(vol_mean_10 != 0, obv_slope / vol_mean_10, np.nan)
    df = df.with_columns([
        obv_s,
        pl.Series("OBV_slope", obv_slope_norm),
    ])

    max_oc = pl.max_horizontal([pl.col("open"), pl.col("close")])
    min_oc = pl.min_horizontal([pl.col("open"), pl.col("close")])
    body   = ((pl.col("close") - pl.col("open")) / pl.col("open"))
    df = df.with_columns([
        body.alias("body"),
        body.abs().alias("body_abs"),
        ((pl.col("high") - max_oc) / pl.col("open")).alias("upper_wick"),
        ((min_oc - pl.col("low")) / pl.col("open")).alias("lower_wick"),
        ((pl.col("high") - pl.col("low")) / pl.col("open")).alias("range_size"),
        ((pl.col("close") > pl.col("open")) &
         (pl.col("close").shift(1) > pl.col("open").shift(1)) &
         (pl.col("close").shift(2) > pl.col("open").shift(2))
        ).cast(pl.Int8).alias("three_green"),
        ((pl.col("close") < pl.col("open")) &
         (pl.col("close").shift(1) < pl.col("open").shift(1)) &
         (pl.col("close").shift(2) < pl.col("open").shift(2))
        ).cast(pl.Int8).alias("three_red"),
        (pl.col("RSI_14") * pl.col("ADX")).alias("RSI_x_ADX"),
        (pl.col("BB_pos") * pl.col("ADX")).alias("BBpos_x_ADX"),
        (pl.col("vol_ratio") *
         (pl.col("break_high_20") + pl.col("break_low_20"))
        ).alias("vol_x_break"),
    ])
    df = df.with_columns([
        (pl.col("body_abs") < 0.001).cast(pl.Int8).alias("doji"),
    ])

    continuous_feats = (
        "dist_SMA20", "dist_SMA50", "dist_SMA100", "dist_SMA200",
        "dist_EMA20", "dist_EMA50", "slope_SMA20", "slope_SMA50", "slope_SMA100",
        "cross_9_21", "cross_20_50", "cross_50_100", "cross_50_200",
        "RSI_7", "RSI_14", "RSI_21", "RSI_14_d1", "RSI_14_d3", "RSI_14_accel",
        "ROC_7", "ROC_14", "ROC_21", "green_ratio_10", "green_ratio_20", "accel_5",
        "MACD", "MACD_signal", "MACD_hist", "MACD_hist_d1", "MACD_hist_d3",
        "dist_high_20", "dist_low_20", "dist_high_50", "dist_low_50",
        "range_pos_20", "range_pos_50", "range_pos_100",
        "BB_width", "BB_pos", "BB_width_rank100", "pullback_to_sma20", "fib_pos",
        "ADX", "DI_plus", "DI_minus", "DI_diff", "trend_duration", "hurst_100",
        "ATR_pct", "vol_std_20", "vol_ratio", "vol_ratio_50", "OBV_slope",
        "body", "body_abs", "upper_wick", "lower_wick", "range_size",
        "RSI_x_ADX", "BBpos_x_ADX",
    )
    binary_feats = (
        "MM_bullish_align", "MM_bearish_align", "RSI_oversold", "RSI_overbought",
        "bear_div", "bull_div", "MACD_above_signal",
        "break_high_20", "break_low_20", "break_high_50", "break_low_50",
        "break_high_100", "break_low_100",
        "false_break_high_20", "false_break_low_20", "BB_squeeze", "BB_expansion",
        "pullback_after_rally", "bounce_after_drop", "trend_strong", "trend_very_strong",
        "doji", "three_green", "three_red", "MACD_zero_cross", "vol_x_break",
    )
    lag_exprs = [
        pl.col(feat).shift(lag).alias(f"{feat}_lag{lag}")
        for feat in continuous_feats + binary_feats if feat in df.columns
        for lag in (1, 3, 6, 12)
    ]
    if lag_exprs:
        df = df.with_columns(lag_exprs)

    return df


def _select_feature_columns(features_df: pl.DataFrame) -> List[str]:
    return [
        c for c, dt in features_df.schema.items()
        if c not in _EXCLUDED_COLS and dt in _NUMERIC_DTYPES
    ]


def _window_polars(df: pl.DataFrame, n: int = 260) -> pl.DataFrame:
    cols = [c for c in ("time", "open", "high", "low", "close", "volume") if c in df.columns]
    return df.select(cols).tail(n)


def _impute_inplace(arr: np.ndarray, feature_cols: List[str],
                    medians: Dict[str, float]) -> None:
    if np.isfinite(arr).all():
        return
    for j, col in enumerate(feature_cols):
        mask = ~np.isfinite(arr[:, j])
        if mask.any():
            arr[mask, j] = float(medians.get(col, 0.0))


# ─────────────────────────────────────────────────────────────────────────────
#  Régime
# ─────────────────────────────────────────────────────────────────────────────
def _classify_regime(adx_val: float, bull: int, bear: int,
                     adx_threshold: float = 20.0) -> int:
    if adx_val < adx_threshold:
        return REGIME_RANGE
    if bull == 1:
        return REGIME_TREND_UP
    if bear == 1:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _regime_history(features_df: pl.DataFrame, n_last: int = 5,
                    adx_threshold: float = 20.0) -> List[int]:
    sub = features_df.tail(n_last)
    rows = sub.select(["ADX", "MM_bullish_align", "MM_bearish_align"]).rows()
    out: List[int] = []
    for adx_v, bull, bear in rows:
        out.append(_classify_regime(
            float(adx_v) if adx_v is not None else 0.0,
            int(bull)    if bull  is not None else 0,
            int(bear)    if bear  is not None else 0,
            adx_threshold,
        ))
    return out


def _exit_td_window_active(regimes: List[int], window_bars: int) -> bool:
    n = len(regimes)
    if n < 2:
        return False
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Setups OMNIBUS V10
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "SIGNAL_UP", "priority": -1, "direction": 1, "enabled": True,
        "regime": None,
        "needs_exit_td_window": False,
        "needs_bearish_excess": True,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 1.0, "sl_mult": 1.3, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
        "tp_mult": 1.4, "sl_mult": 1.6, "max_bars": 8, "size_factor": 1.5,
    },
    {
        "name": "LONG_CHOPPY", "priority": 2, "direction": 1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
        "tp_mult": 0.9, "sl_mult": 1.2, "max_bars": 10, "size_factor": 1.0,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.4, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "LONG_TU", "priority": 3, "direction": 1, "enabled": True,
        "regime": REGIME_TREND_UP, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None,
        "needs_adx_above": 25.0,
        "amp_min": 0.55, "dir_max": None, "dir_min": 0.62,
        "tp_mult": 1.4, "sl_mult": 1.1, "max_bars": 10, "size_factor": 1.0,
    },
    {
        "name": "LONG_EXIT_TD", "priority": 4, "direction": 1, "enabled": True,
        "regime": None, "needs_exit_td_window": True,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.40, "dir_max": None, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.5, "max_bars": 8, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 5, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8, "sl_mult": 1.2, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_LIGHT", "priority": 6, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.55,
        "tp_mult": 0.7, "sl_mult": 1.0, "max_bars": 4, "size_factor": 0.6,
    },
)


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    setups: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "tp_mult", "sl_mult", "max_bars", "enabled", "size_factor",
                      "needs_bearish_excess", "needs_rsi_below", "needs_adx_above"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def _evaluate_setup(setup: Dict[str, Any],
                    regime: int, p_event: float, p_up: float,
                    exit_td_active: bool,
                    bearish_excess: bool = False,
                    rsi: float = 50.0,
                    adx: float = 0.0) -> bool:
    if not setup.get("enabled", True):
        return False
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    if setup.get("needs_exit_td_window", False):
        if not exit_td_active:
            return False
        if regime == REGIME_TREND_DN:
            return False
    if p_event < float(setup["amp_min"]):
        return False
    if setup["dir_max"] is not None and p_up >= float(setup["dir_max"]):
        return False
    if setup["dir_min"] is not None and p_up <= float(setup["dir_min"]):
        return False
    if setup.get("needs_bearish_excess", False) and not bearish_excess:
        return False
    if setup.get("needs_rsi_below") is not None and rsi >= float(setup["needs_rsi_below"]):
        return False
    if setup.get("needs_adx_above") is not None and adx < float(setup["needs_adx_above"]):
        return False
    return True


def _select_setup(setups: List[Dict[str, Any]],
                  regime: int, p_event: float, p_up: float,
                  exit_td_active: bool,
                  bearish_excess: bool = False,
                  rsi: float = 50.0,
                  adx: float = 0.0) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, exit_td_active,
                                bearish_excess, rsi, adx)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


def _check_early_exit(setup_name: str, regime: int, p_up: float,
                      dir_inv_short: float = 0.55,
                      dir_inv_long: float = 0.42,
                      dir_drop_range: float = 0.40) -> Optional[str]:
    if setup_name == "SIGNAL_UP":
        if p_up < dir_inv_long:          return "p_dir_drop"
        if regime == REGIME_TREND_DN:    return "to_TD"
    elif setup_name == "SHORT_TD_HIGH":
        if regime != REGIME_TREND_DN:    return "regime_exit_TD"
        if p_up > dir_inv_short:         return "p_dir_inversion"
    elif setup_name == "LONG_CHOPPY":
        if p_up < dir_inv_long:          return "p_dir_drop"
        if regime == REGIME_TREND_DN:    return "to_TD"
    elif setup_name == "SHORT_CHOPPY":
        if regime != REGIME_CHOPPY:      return "regime_exit_choppy"
        if p_up > 0.58:                  return "p_dir_inversion"
    elif setup_name == "LONG_TU":
        if regime == REGIME_TREND_DN:    return "to_TD"
        if p_up < dir_inv_long:          return "p_dir_drop"
    elif setup_name == "LONG_EXIT_TD":
        if regime == REGIME_TREND_DN:    return "back_to_TD"
    elif setup_name in ("LONG_RANGE_STRICT", "LONG_RANGE_LIGHT"):
        if regime == REGIME_TREND_DN:    return "regime_to_TD"
        if p_up < dir_drop_range:        return "p_dir_drop"
    return None


def _signal_up_dynamic_risk(regime: int) -> Tuple[float, float]:
    """size_factor et sl_mult adaptés au régime pour SIGNAL_UP (mean-reversion)."""
    if regime == REGIME_CHOPPY or regime == REGIME_RANGE:
        return 1.5, 1.3
    if regime == REGIME_TREND_DN:
        return 1.2, 1.1
    if regime == REGIME_TREND_UP:
        return 1.0, 1.0
    return 1.0, 1.3


# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """OMNIBUS V10 — routing V10 sur modèles LightGBM entraînés inline."""

    name      = "opus_omnibus_v10_retrained"
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        # ── Setups V10 ──
        "setup_signal_up_amp_min":          [0.45, 0.50, 0.55],
        "setup_signal_up_dir_min":          [0.55, 0.60, 0.65],
        "setup_short_td_high_amp_min":      [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":      [0.25, 0.30, 0.35],
        "setup_long_choppy_amp_min":        [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":        [0.55, 0.58, 0.62],
        "setup_short_choppy_amp_min":       [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":       [0.38, 0.42, 0.46],
        "setup_long_tu_amp_min":            [0.50, 0.55, 0.60],
        "setup_long_tu_dir_min":            [0.58, 0.62, 0.66],
        "setup_long_tu_needs_adx_above":    [22.0, 25.0, 28.0],
        "setup_long_range_strict_amp_min":  [0.55, 0.60, 0.65],
        "setup_long_range_light_amp_min":   [0.45, 0.50, 0.55],
        "exit_td_window_bars":              [2, 3, 4],
    }
    # Hyperparamètres d'entraînement figés (hors espace de recherche) : les
    # échantillonner invalide le cache d'entraînement process-wide entre les
    # trials de l'optimiseur — chaque trial repaye alors l'intégralité des
    # retrains LightGBM walk-forward (rédhibitoire sur 50k bougies). Valeurs
    # effectives : _DEFAULTS ; surchargables via le YAML stratégie.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":     0.30,
        "warmup_bars":     750,
        "retrain_every":   800,
        "n_estimators":    500,
        "num_leaves":      31,
        "learning_rate":   0.03,
    }

    _DEFAULTS = {
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),
        "active_days":         [0, 1, 2, 3, 4],
        "adx_threshold":       20.0,
        "exit_td_window_bars": _EXIT_TD_WINDOW_BARS,
        "disable_trailing":    True,
        "use_fixed_tp":        True,
        "bearish_excess_rsi_threshold": 38.0,
        "bearish_excess_sma_pct":        1.5,
        "signal_up_dynamic_risk":        True,
        # Entraînement
        "amp_top_pct":         0.30,
        "warmup_bars":         750,
        "retrain_every":       800,
        "n_estimators":        500,
        "num_leaves":          31,
        "learning_rate":       0.03,
    }

    retrain_interval_h: int = 6

    def __init__(self):
        self._lock = threading.Lock()
        self._amp_models:    Dict[str, Any]              = {}
        self._dir_models:    Dict[str, Any]              = {}
        self._feature_cols:  Dict[str, List[str]]        = {}
        self._medians:       Dict[str, Dict[str, float]] = {}
        self._trained_tfs:   set                         = set()
        self._managed_externally: bool                   = False
        self._call_cnt:      Dict[str, int]              = {}
        self._last_retrain:  Dict[str, int]              = {}
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        self._cancel_event = None
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            # Catalogue partagé "v4_polars" (build identique entre v7/v10_rt/v11/stat_rt).
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="v4_polars", version="1",
                builder=lambda w: _build_features(_window_polars(w, n=len(w))),
                in_kind="polars", out_kind="polars")
            self._bt_features = feats
            self._bt_features_len = len(df) if feats is not None else 0
            logger.info(
                f"[OmnibusV10-RT] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(len(feats.columns) if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    # ── Cycle de vie ML ──────────────────────────────────────────────────────
    @property
    def is_trained(self) -> bool:
        return bool(self._trained_tfs)

    @property
    def managed_externally(self) -> bool:
        return self._managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self._managed_externally = bool(v)

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        warmup = int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))
        return max(230, warmup + 30)

    def reset_model(self) -> None:
        with self._lock:
            self._amp_models.clear()
            self._dir_models.clear()
            self._feature_cols.clear()
            self._medians.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._train_meta.clear()
            self._last_retrain.clear()
            self._managed_externally = False
            self._best_auc = 0.0
        self._bt_features = None
        self._bt_features_len = 0
        gc.collect()

    # ── Persistance par TF ────────────────────────────────────────────────────
    @staticmethod
    def _tf_from_path(path: str) -> str:
        base = os.path.splitext(os.path.basename(path))[0]
        return base.rsplit("_", 1)[-1]

    def save_model(self, path: str) -> None:
        import joblib
        tf_key = self._tf_from_path(path)
        with self._lock:
            amp_m = self._amp_models.get(tf_key)
            dir_m = self._dir_models.get(tf_key)
            feats = self._feature_cols.get(tf_key)
            meds  = self._medians.get(tf_key)
            auc   = self._best_auc_per_tf.get(tf_key, 0.0)
            meta  = self._train_meta.get(tf_key, {})
        if amp_m is None or dir_m is None:
            logger.debug(f"[OmnibusV10-RT] save_model({path}) : pas de modèle pour {tf_key}")
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        joblib.dump({
            "amp_model": amp_m, "dir_model": dir_m,
            "features": feats, "medians": meds,
            "best_auc": auc, "train_meta": meta, "tf": tf_key,
        }, path)
        logger.info(f"[OmnibusV10-RT] Modèles sauvegardés → {path} (AUC={auc:.3f})")

    def load_model(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            import joblib
            data = joblib.load(path)
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] Chargement échoué {path}: {e}")
            return False
        tf_key = self._tf_from_path(path)
        with self._lock:
            self._amp_models[tf_key]      = data["amp_model"]
            self._dir_models[tf_key]      = data["dir_model"]
            self._feature_cols[tf_key]    = list(data.get("features") or [])
            self._medians[tf_key]         = dict(data.get("medians") or {})
            self._best_auc_per_tf[tf_key] = float(data.get("best_auc", 0.0))
            self._train_meta[tf_key]      = dict(data.get("train_meta") or {})
            self._trained_tfs.add(tf_key)
            self._best_auc = max(self._best_auc, self._best_auc_per_tf[tf_key])
        logger.info(
            f"[OmnibusV10-RT] Modèle {tf_key} chargé depuis {path} "
            f"(AUC={self._best_auc_per_tf[tf_key]:.3f})"
        )
        return True

    # ── Entraînement ──────────────────────────────────────────────────────────
    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        tf = _detect_timeframe(df) or "default"
        p  = (params or {}).get(self.name, {})
        self._train(df, tf_key=tf, params=p)

    # Entraînement avec cache process-wide (cf. app/core/train_cache.py) :
    # les retrains identiques (même fenêtre, mêmes hyperparams d'entraînement)
    # sont réutilisés entre les trials de l'optimiseur au lieu d'être relancés.
    _TRAIN_STATE_ATTRS = ('_amp_models', '_dir_models', '_feature_cols', '_medians', '_best_auc_per_tf', '_train_meta')
    _TRAIN_PARAM_KEYS  = ('amp_top_pct', 'n_estimators', 'num_leaves', 'learning_rate')

    def _train(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        from app.core.train_cache import cached_train
        return cached_train(self, df, tf_key, params, self._train_impl,
                            self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS)

    def _train_impl(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        try:
            import lightgbm as lgb
        except ImportError:
            logger.error("[OmnibusV10-RT] lightgbm requis : pip install lightgbm")
            return False

        amp_top_pct   = float(params.get("amp_top_pct",   self._DEFAULTS["amp_top_pct"]))
        n_estimators  = int(params.get("n_estimators",    self._DEFAULTS["n_estimators"]))
        num_leaves    = int(params.get("num_leaves",      self._DEFAULTS["num_leaves"]))
        learning_rate = float(params.get("learning_rate", self._DEFAULTS["learning_rate"]))

        n_keep = max(2200, len(df))
        # Réutilise le cache backtest si dispo (features causales déterministes).
        # ``_bt_train_offset`` (posé par score() avant _train) repère la position
        # de la fenêtre d'entraînement dans la fenêtre complète : sans lui,
        # head(len(df)) lisait les PREMIÈRES lignes des features alors que
        # train_df est une tranche de FIN.
        _off = int(getattr(self, "_bt_train_offset", None) or 0)
        if (self._bt_features is not None and
                self._bt_features_len > 0 and
                _off + len(df) <= self._bt_features_len):
            feats = self._bt_features.slice(_off, len(df))
        else:
            feats = _build_features(_window_polars(df, n=n_keep))
        if feats is None or len(feats) < 250:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : données insuffisantes")
            return False

        feature_cols = _select_feature_columns(feats)
        if not feature_cols:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : aucune feature exploitable")
            return False

        close = feats["close"].to_numpy().astype(np.float64)
        n     = len(close) - 1
        if n < 200:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : pas assez de barres labélisables")
            return False
        ret_t1  = (close[1:] - close[:n]) / np.maximum(close[:n], 1e-9)
        abs_ret = np.abs(ret_t1)
        amp_thr = float(np.quantile(abs_ret, 1.0 - amp_top_pct))
        y_amp   = (abs_ret >= amp_thr).astype(np.int8)
        y_dir   = (ret_t1 > 0).astype(np.int8)

        X_full = feats.head(n).select(feature_cols).to_numpy().astype(np.float32)

        split = max(int(n * 0.8), 100)
        split = min(split, n - 50)
        if split < 100 or n - split < 50:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : split impossible (n={n})")
            return False

        medians: Dict[str, float] = {}
        for j, col in enumerate(feature_cols):
            col_train = X_full[:split, j]
            mask = np.isfinite(col_train)
            medians[col] = float(np.median(col_train[mask])) if mask.any() else 0.0

        X_train = X_full[:split].copy()
        X_valid = X_full[split:n].copy()
        del X_full
        _impute_inplace(X_train, feature_cols, medians)
        _impute_inplace(X_valid, feature_cols, medians)

        common = dict(
            objective="binary", metric="auc",
            num_leaves=num_leaves, learning_rate=learning_rate,
            min_child_samples=20, subsample=0.8, subsample_freq=5,
            colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
            max_bin=63, force_col_wise=True,
            verbosity=-1, n_jobs=1,
        )

        with self._lock:
            self._amp_models.pop(tf_key, None)
            self._dir_models.pop(tf_key, None)
        gc.collect()

        ds_train_amp = lgb.Dataset(X_train, label=y_amp[:split], feature_name=feature_cols,
                                   free_raw_data=False)
        ds_valid_amp = lgb.Dataset(X_valid, label=y_amp[split:n], reference=ds_train_amp,
                                   feature_name=feature_cols, free_raw_data=False)
        try:
            booster_amp = lgb.train(
                {**common, "scale_pos_weight":
                    (y_amp[:split] == 0).sum() / max((y_amp[:split] == 1).sum(), 1)},
                ds_train_amp, num_boost_round=n_estimators,
                valid_sets=[ds_valid_amp],
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(-1)],
            )
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : entraînement amp KO ({e})")
            del ds_train_amp, ds_valid_amp; gc.collect()
            return False
        auc_amp = booster_amp.best_score.get("valid_0", {}).get("auc", 0.0)
        del ds_train_amp, ds_valid_amp

        ds_train_dir = lgb.Dataset(X_train, label=y_dir[:split], feature_name=feature_cols,
                                   free_raw_data=False)
        ds_valid_dir = lgb.Dataset(X_valid, label=y_dir[split:n], reference=ds_train_dir,
                                   feature_name=feature_cols, free_raw_data=False)
        try:
            booster_dir = lgb.train(
                {**common, "scale_pos_weight":
                    (y_dir[:split] == 0).sum() / max((y_dir[:split] == 1).sum(), 1)},
                ds_train_dir, num_boost_round=n_estimators,
                valid_sets=[ds_valid_dir],
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(-1)],
            )
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] {tf_key} : entraînement dir KO ({e})")
            del ds_train_dir, ds_valid_dir; gc.collect()
            return False
        auc_dir = booster_dir.best_score.get("valid_0", {}).get("auc", 0.0)
        del ds_train_dir, ds_valid_dir, X_train, X_valid

        auc_combined = (auc_amp + auc_dir) / 2.0
        with self._lock:
            self._amp_models[tf_key]      = booster_amp
            self._dir_models[tf_key]      = booster_dir
            self._feature_cols[tf_key]    = feature_cols
            self._medians[tf_key]         = medians
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key] = auc_combined
            self._best_auc                = max(self._best_auc, auc_combined)
            self._train_meta[tf_key] = {
                "n_train":     int(split),
                "n_valid":     int(n - split),
                "n_features":  len(feature_cols),
                "auc_amp":     round(float(auc_amp), 4),
                "auc_dir":     round(float(auc_dir), 4),
                "amp_thr_pct": round(float(amp_thr) * 100, 3),
                "amp_top_pct": amp_top_pct,
            }
        gc.collect()
        logger.info(
            f"[OmnibusV10-RT] {tf_key} entraîné : {split} train / {n - split} val | "
            f"{len(feature_cols)} features | AUC amp={auc_amp:.3f} dir={auc_dir:.3f} | "
            f"amp_thr={amp_thr * 100:.2f}%"
        )
        return True

    # ── Prédictions ────────────────────────────────────────────────────────────
    def _predict(self, features_df: pl.DataFrame, tf: str, target: str) -> Optional[float]:
        with self._lock:
            booster   = (self._amp_models if target == "amp" else self._dir_models).get(tf)
            feat_cols = self._feature_cols.get(tf)
            medians   = self._medians.get(tf, {})
        if booster is None or not feat_cols:
            return None
        try:
            # Extraction de la derniere ligne en UN appel (dict col->valeur) au
            # lieu d'un acces colonne polars par feature (~440 get_column par
            # prediction, x2/barre). row.get(c)=None pour les colonnes absentes
            # -> repli sur la mediane d'entrainement.
            row  = features_df.tail(1).row(0, named=True)
            vals = np.empty(len(feat_cols), dtype=np.float32)
            for j, c in enumerate(feat_cols):
                v = row.get(c)
                vals[j] = v if (v is not None and np.isfinite(v)) else medians.get(c, 0.0)
            return float(booster.predict(vals.reshape(1, -1))[0])
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] Prédiction {tf}/{target} KO : {e}")
            return None

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "amp")

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self._predict(features_df, tf, "dir")

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Score V10 ───────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        enable_hour_filter  = bool(p.get("enable_hour_filter",  self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",    self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",         self._DEFAULTS["active_days"]))
        adx_threshold       = float(p.get("adx_threshold",      self._DEFAULTS["adx_threshold"]))
        exit_td_window_bars = int(p.get("exit_td_window_bars",  self._DEFAULTS["exit_td_window_bars"]))
        disable_trailing    = bool(p.get("disable_trailing",    self._DEFAULTS["disable_trailing"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",        self._DEFAULTS["use_fixed_tp"]))
        signal_up_dyn       = bool(p.get("signal_up_dynamic_risk", self._DEFAULTS["signal_up_dynamic_risk"]))

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        if enable_hour_filter:
            hour, dow = _last_bar_hour_dow(df)
            if hour is not None and dow is not None:
                if dow not in active_days:
                    return self._none(
                        f"Hors jours actifs (weekday={dow}, autorisés={active_days})"
                    )
                if hour not in active_hours_utc:
                    return self._none(
                        f"Hors session ({hour}h UTC, autorisées={active_hours_utc})"
                    )

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(
                f"Timeframe non supporté (détecté={tf}, attendus={_SUPPORTED_TFS})"
            )

        # ── Réentraînement inline (walk-forward) ─────────────────────────────
        cnt = self._call_cnt.get(tf, 0) + 1
        self._call_cnt[tf] = cnt
        last       = self._last_retrain.get(tf, 0)
        need_train = (tf not in self._trained_tfs) or (cnt - last >= retrain_every)
        if need_train and not self._managed_externally:
            from app.core.train_cache import aligned_train_window
            n_train = min(len(df) - 1, warmup_bars * 2)
            train_df, self._bt_train_offset = aligned_train_window(
                df, retrain_every, n_train)
            ok = self._train(train_df, tf, p)
            self._bt_train_offset = None
            if ok:
                self._last_retrain[tf] = cnt

        if tf not in self._trained_tfs:
            return self._none("Modèle pas encore entraîné (warmup en cours)")

        if self._bt_features is not None and len(df) <= self._bt_features_len:
            features = self._bt_features.head(len(df))
        else:
            features = _build_features(_window_polars(df, n=max(260, self.min_bars_required(params))))
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v = _safe_num(last_row.get("ATR_14"), 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime_history = _regime_history(
            features, n_last=max(exit_td_window_bars + 2, 5),
            adx_threshold=adx_threshold,
        )
        regime         = regime_history[-1]
        regime_lbl     = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regime_history, exit_td_window_bars)

        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")

        # ── Détection d'excès baissier ───────────────────────────────────────
        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma_pct = float(p.get("bearish_excess_sma_pct", 1.5))
        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False
        rsi_v = _safe_num(last_row.get("RSI_14"), 50.0)
        adx_v = _safe_num(last_row.get("ADX"), 0.0)
        rsi_excess = rsi_v < be_rsi_thr
        sma20_v = _safe_num(last_row.get("SMA_20"), 0.0)
        price_below_sma20 = (c_now < sma20_v * (1.0 - be_sma_pct / 100.0)) if sma20_v > 0 else False
        bearish_excess = consec_red or rsi_excess or price_below_sma20

        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up, exit_td_active,
                               bearish_excess, rsi_v, adx_v)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} rsi={rsi_v:.1f} adx={adx_v:.1f} "
                f"exit_td={exit_td_active} bearish_excess={bearish_excess}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        signal_up_dyn_applied = False
        if setup["name"] == "SIGNAL_UP" and signal_up_dyn:
            setup = dict(setup)
            size_mult, sl_override = _signal_up_dynamic_risk(regime)
            setup["size_factor"] = float(setup.get("size_factor", 1.0)) * size_mult
            setup["sl_mult"]     = sl_override
            signal_up_dyn_applied = True

        side        = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self._train_meta.get(tf, {})

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "disable_trailing": disable_trailing,
            "size_factor":      size_factor,
            "exit_after_bars":  max_bars,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "bearish_excess":   bool(bearish_excess),
            "signal_up_dyn":    bool(signal_up_dyn_applied),
            "rsi":              round(rsi_v, 1),
            "adx":              round(adx_v, 1),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(rsi_v, 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "max_bars":         max_bars,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
            "n_features":       meta.get("n_features", 0),
        }
        sig["conditions"] = [
            f"Setup V10-RT retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime : {regime_lbl} | ADX={adx_v:.1f} | exit_td_window={exit_td_active}",
            f"P(événement)={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse)={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse)={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse)={p_up:.2f} (pas de seuil dir)"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | max {max_bars} bougies",
            f"Modèle V4 entraîné inline / {tf} ({meta.get('n_features', 0)} features, "
            f"AUC amp={meta.get('auc_amp', 0):.2f} dir={meta.get('auc_dir', 0):.2f})",
        ]
        if setup["name"] == "SIGNAL_UP" and signal_up_dyn_applied:
            sig["conditions"].append(
                f"V10 SIGNAL_UP dynamique : size×{size_factor:.2f} SL {sl_atr_mult:.2f}×ATR "
                f"(adapté régime {regime_lbl})"
            )
        sig["reason"] = (
            f"OmnibusV10-RT {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}"
        )
        return sig

    # ── Sortie anticipée V10 ─────────────────────────────────────────────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        setup_name = position.get("setup")
        if not setup_name:
            ind = position.get("indicators") or {}
            setup_name = ind.get("setup")
        if not setup_name:
            return None
        if df is None or len(df) < self.min_bars_required(params):
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold  = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        dir_inv_short  = float(p.get("early_exit_dir_inv_short",  0.55))
        dir_inv_long   = float(p.get("early_exit_dir_inv_long",   0.42))
        dir_drop_range = float(p.get("early_exit_dir_drop_range", 0.40))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS or tf not in self._trained_tfs:
            return None

        try:
            if self._bt_features is not None and len(df) <= self._bt_features_len:
                features = self._bt_features.head(len(df))
            else:
                features = _build_features(
                    _window_polars(df, n=max(260, self.min_bars_required(params)))
                )
            if features is None or len(features) == 0:
                return None
            last_row = features.row(-1, named=True)
            regime = _classify_regime(
                _safe_num(last_row.get("ADX"), 0.0),
                int(_safe_num(last_row.get("MM_bullish_align"), 0.0)),
                int(_safe_num(last_row.get("MM_bearish_align"), 0.0)),
                adx_threshold,
            )
            p_up = self.predict_direction(features, tf)
            if p_up is None:
                return None
        except Exception as e:
            logger.warning(f"[OmnibusV10-RT] check_early_exit recompute KO : {e}")
            return None

        return _check_early_exit(
            setup_name, regime, p_up,
            dir_inv_short=dir_inv_short,
            dir_inv_long=dir_inv_long,
            dir_drop_range=dir_drop_range,
        )

    def _none(self, reason: str = "", p_event: float = 0.0, p_up: float = 0.5,
              regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "p_event":    round(p_event, 4),
            "p_up":       round(p_up, 4),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
