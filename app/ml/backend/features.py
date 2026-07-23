"""MLBackend.features — construction des features V4 (polars) partagée.

Ce module extrait le `_build_features` polars (~462 colonnes) et les helpers
associés (`_select_feature_columns`, `_impute_inplace`, `_window_polars`,
`_detect_timeframe`, `_last_bar_hour_dow`, `_multi_horizon_labels`) qui
étaient dupliqués dans plusieurs stratégies Opus (V11, V11_followsetup,
opus_stat_retrained_v4, opus_omnibus_v7, opus_omnibus_v10_retrained).

L'objectif est de fournir une source unique, générique (sans référence à
"Opus"), réutilisable par toute stratégie ML qui souhaite ce pipeline V4.

La signature publique est volontairement stable : ``build_features(raw_df)``
retourne un DataFrame polars avec les colonnes OHLCV + ~462 features
(lags 1/3/6/12 inclus). Le catalogue FeatureStore reste ``v4_polars`` /
version ``1`` — toute modification Cassante du builder doit bump la version
pour invalider proprement le cache disque.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import (
    bars_since_cross,
    rolling_hurst,
    rolling_rank_pct,
    rolling_slope,
)

logger = logging.getLogger(__name__)

# ── Catalogue FeatureStore (à garder synchronisé entre toutes les stratégies
#    qui partagent ce builder — sinon bump version). ────────────────────────
FEATURES_CATALOG_NAME = "v4_polars"
FEATURES_CATALOG_VERSION = "1"

# Timeframes supportés par le builder V4.
SUPPORTED_TFS: Tuple[str, ...] = ("15m", "30m", "1h", "4h", "1d")

# Régimes 4-classes (compatibles setups V10/V11).
REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3
REGIME_LABELS: Dict[int, str] = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
    -1:              "?",
}

# Colonnes systématiquement exclues des features ML (identifiants, prix bruts,
# MMs utilisés comme filtres et non comme entrées).
EXCLUDED_COLS: frozenset = frozenset({
    "time", "open", "high", "low", "close", "volume",
    "log_ret", "OBV",
    "SMA_20", "SMA_50", "SMA_100", "SMA_200",
    "EMA_20", "EMA_50", "EMA_100", "EMA_200",
    "EMA_9", "EMA_21",
    "high_20", "low_20", "high_50", "low_50", "high_100", "low_100",
    "ATR_14",
})

NUMERIC_DTYPES = (
    pl.Float32, pl.Float64,
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
)

# Features continues et binaires (les lags 1/3/6/12 sont ajoutés
# automatiquement à la fin du builder).
_CONTINUOUS_FEATS: Tuple[str, ...] = (
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

_BINARY_FEATS: Tuple[str, ...] = (
    "MM_bullish_align", "MM_bearish_align", "RSI_oversold", "RSI_overbought",
    "bear_div", "bull_div", "MACD_above_signal",
    "break_high_20", "break_low_20", "break_high_50", "break_low_50",
    "break_high_100", "break_low_100",
    "false_break_high_20", "false_break_low_20", "BB_squeeze", "BB_expansion",
    "pullback_after_rally", "bounce_after_drop", "trend_strong", "trend_very_strong",
    "doji", "three_green", "three_red", "MACD_zero_cross", "vol_x_break",
)

LAG_PERIODS: Tuple[int, ...] = (1, 3, 6, 12)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers de détection
# ─────────────────────────────────────────────────────────────────────────────
def detect_timeframe(df: pl.DataFrame) -> Optional[str]:
    """Détecte le timeframe à partir des deltas temporels (O(1) sur tail 64)."""
    if "time" not in df.columns or len(df) < 3:
        return None
    times = df["time"].tail(64)
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
    if abs(med_s - 900)   < 60:
        return "15m"
    if abs(med_s - 1800)  < 120:
        return "30m"
    if abs(med_s - 3600)  < 240:
        return "1h"
    if abs(med_s - 14400) < 960:
        return "4h"
    if abs(med_s - 86400) < 5760:
        return "1d"
    return None


def last_bar_hour_dow(df: pl.DataFrame) -> Tuple[Optional[int], Optional[int]]:
    """Renvoie (hour_utc, weekday) de la dernière barre."""
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
#  EWM / window helpers
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


def window_polars(df: pl.DataFrame, n: int = 260) -> pl.DataFrame:
    """Retourne les `n` dernières lignes avec les colonnes OHLCV+time."""
    cols = [c for c in ("time", "open", "high", "low", "close", "volume") if c in df.columns]
    return df.select(cols).tail(n)


# ─────────────────────────────────────────────────────────────────────────────
#  Builder principal — ~462 colonnes V4 (avec lags 1/3/6/12)
# ─────────────────────────────────────────────────────────────────────────────
def build_features(raw_df: pl.DataFrame) -> Optional[pl.DataFrame]:
    """Construit le DataFrame polars avec les ~462 features V4.

    Retourne ``None`` si les données sont insuffisantes (< 210 barres).
    Le DataFrame retourné contient les colonnes OHLCV+time d'origine + toutes
    les features. Les features sont causales (n'utilisent que les barres
    passées), ce qui permet leur utilisation en backtest walk-forward.
    """
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

    # 1. Rendements + MMs
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

    # 2. Distances + alignements
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

    # 3. Slopes + croisements
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

    # 4. RSI / ROC
    rsi_roc_exprs: List[pl.Expr] = []
    for n in (7, 14, 21):
        delta = pl.col("close").diff()
        gain  = pl.when(delta > 0).then(delta).otherwise(0.0).ewm_mean(alpha=1.0 / n, adjust=False)
        loss  = pl.when(delta < 0).then(-delta).otherwise(0.0).ewm_mean(alpha=1.0 / n, adjust=False)
        rs    = gain / pl.when(loss == 0).then(None).otherwise(loss)
        rsi_roc_exprs.append((100 - (100 / (1 + rs))).alias(f"RSI_{n}"))
        rsi_roc_exprs.append((pl.col("close").pct_change(n) * 100).alias(f"ROC_{n}"))
    df = df.with_columns(rsi_roc_exprs)

    # 5. MACD
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

    # 6. Breakout
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

    # 7. Bollinger
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

    # 8. ADX / DI
    h_np = df["high"].to_numpy()
    l_np = df["low"].to_numpy()
    c_np = df["close"].to_numpy()
    a    = 1.0 / 14.0
    up = np.empty_like(h_np)
    up[:] = np.nan
    up[1:] = h_np[1:] - h_np[:-1]
    dn = np.empty_like(l_np)
    dn[:] = np.nan
    dn[1:] = -(l_np[1:] - l_np[:-1])
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

    # 9. Trend duration
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

    # 10. Hurst
    df = df.with_columns(rolling_hurst(df["log_ret"], 100).alias("hurst_100"))

    # 11. Volatilité / volume / OBV
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

    # 12. Bougie + interactions
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

    # 13. Lags 1/3/6/12 sur les features continues + binaires
    lag_exprs = [
        pl.col(feat).shift(lag).alias(f"{feat}_lag{lag}")
        for feat in _CONTINUOUS_FEATS + _BINARY_FEATS if feat in df.columns
        for lag in LAG_PERIODS
    ]
    if lag_exprs:
        df = df.with_columns(lag_exprs)

    return df


def select_feature_columns(features_df: pl.DataFrame) -> List[str]:
    """Retourne la liste des colonnes numériques utilisables comme features ML."""
    return [
        c for c, dt in features_df.schema.items()
        if c not in EXCLUDED_COLS and dt in NUMERIC_DTYPES
    ]


def impute_inplace(arr: np.ndarray, feature_cols: List[str],
                   medians: Dict[str, float]) -> None:
    """Impute les NaN/inf d'un array 2D par les médianes d'entraînement."""
    if np.isfinite(arr).all():
        return
    for j, col in enumerate(feature_cols):
        mask = ~np.isfinite(arr[:, j])
        if mask.any():
            arr[mask, j] = float(medians.get(col, 0.0))


# ─────────────────────────────────────────────────────────────────────────────
#  Labellisation multi-horizon
# ─────────────────────────────────────────────────────────────────────────────
def multi_horizon_labels(close: np.ndarray, horizons: List[int],
                         amp_top_pct: float) -> Tuple[np.ndarray, np.ndarray, int, float, dict]:
    """Construit (y_amp, y_dir) agrégés sur plusieurs horizons.

    amplitude : 1 si le rendement absolu MAX sur les horizons dépasse le
                quantile (1 - amp_top_pct) — capte les mouvements qui se
                développent sur plusieurs bougies.
    direction : 1 si le rendement MOYEN (pondéré 1/h) sur les horizons est positif.

    Retourne (y_amp, y_dir, n, amp_thr, stats).
    """
    hs = sorted(set(int(h) for h in horizons if int(h) >= 1)) or [1]
    maxh = max(hs)
    N = len(close)
    n = N - maxh
    if n <= 0:
        return np.zeros(0, np.int8), np.zeros(0, np.int8), 0, 0.0, {}

    base = close[:n]
    base_safe = np.maximum(base, 1e-9)
    rets = np.empty((n, len(hs)), dtype=np.float64)
    weights = np.empty(len(hs), dtype=np.float64)
    for j, h in enumerate(hs):
        rets[:, j] = (close[h:h + n] - base) / base_safe
        weights[j] = 1.0 / h
    weights /= weights.sum()

    abs_max = np.max(np.abs(rets), axis=1)
    amp_thr = float(np.quantile(abs_max, 1.0 - amp_top_pct))
    y_amp = (abs_max >= amp_thr).astype(np.int8)

    mean_ret = rets @ weights
    y_dir = (mean_ret > 0).astype(np.int8)

    stats = {
        "horizons":      hs,
        "n_labels":      int(n),
        "amp_thr_pct":   round(amp_thr * 100, 4),
        "amp_pos_rate":  round(float(y_amp.mean()), 4),
        "dir_pos_rate":  round(float(y_dir.mean()), 4),
    }
    return y_amp, y_dir, n, amp_thr, stats


def single_horizon_labels(close: np.ndarray, amp_top_pct: float) -> Tuple[np.ndarray, np.ndarray, int, float]:
    """Labellisation single-horizon (t+1) — pour les stratégies V4 retrained simples.

    amplitude : 1 si |ret_t+1| > quantile(1 - amp_top_pct).
    direction : 1 si ret_t+1 > 0.
    """
    n = len(close) - 1
    if n <= 0:
        return np.zeros(0, np.int8), np.zeros(0, np.int8), 0, 0.0
    base = close[:n]
    base_safe = np.maximum(base, 1e-9)
    ret = (close[1:n + 1] - base) / base_safe
    abs_ret = np.abs(ret)
    amp_thr = float(np.quantile(abs_ret, 1.0 - amp_top_pct))
    y_amp = (abs_ret >= amp_thr).astype(np.int8)
    y_dir = (ret > 0).astype(np.int8)
    return y_amp, y_dir, n, amp_thr


# ─────────────────────────────────────────────────────────────────────────────
#  Régime (classification 4-classes, version enrichie V11)
# ─────────────────────────────────────────────────────────────────────────────
def classify_regime(adx: float, bull: int, bear: int,
                    di_diff: float, slope20: float, bb_rank: Any,
                    adx_threshold: float = 20.0,
                    di_rescue: float = 10.0) -> Tuple[int, str]:
    """Classifie en l'un des 4 régimes (compatible setups V10/V11) avec affinage.

    Retourne (regime_code, sub_label). Le sub_label est purement descriptif
    (logs/analyse) ; seul regime_code pilote le routing des setups.
    """
    if adx < adx_threshold:
        return REGIME_RANGE, ("Range-Squeeze" if (bb_rank is not None and bb_rank < 0.2) else "Range-Open")
    if bull == 1:
        return REGIME_TREND_UP, "TrendUp-aligned"
    if bear == 1:
        return REGIME_TREND_DN, "TrendDown-aligned"
    # Alignement strict des 4 SMA absent mais ADX élevé : on récupère les
    # tendances naissantes via la concordance DI_diff + pente SMA20.
    if di_diff > di_rescue and slope20 > 0:
        return REGIME_TREND_UP, "TrendUp-DI"
    if di_diff < -di_rescue and slope20 < 0:
        return REGIME_TREND_DN, "TrendDown-DI"
    return REGIME_CHOPPY, "Choppy"


def regime_history(features_df: pl.DataFrame, n_last: int,
                   adx_threshold: float, di_rescue: float
                   ) -> Tuple[List[int], List[str]]:
    """Calcule l'historique des régimes sur les `n_last` dernières barres."""
    sub = features_df.tail(n_last)
    cols = ["ADX", "MM_bullish_align", "MM_bearish_align",
            "DI_diff", "slope_SMA20", "BB_width_rank100"]
    present = [c for c in cols if c in sub.columns]
    rows = sub.select(present).rows(named=True)
    regimes: List[int] = []
    subs: List[str] = []
    for r in rows:
        reg, lbl = classify_regime(
            float(r.get("ADX") or 0.0),
            int(r.get("MM_bullish_align") or 0),
            int(r.get("MM_bearish_align") or 0),
            float(r.get("DI_diff") or 0.0),
            float(r.get("slope_SMA20") or 0.0),
            r.get("BB_width_rank100"),
            adx_threshold, di_rescue,
        )
        regimes.append(reg)
        subs.append(lbl)
    return regimes, subs


def exit_td_window_active(regimes: List[int], window_bars: int) -> bool:
    """Détecte une sortie récente d'un régime Trend Down."""
    n = len(regimes)
    if n < 2:
        return False
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False
