"""
Module 5 — Features ML : extraction depuis OHLCV pour modèles prédictifs.
Bibliothèque principale : Polars (Rust, Arrow, multi-threadé).
"""
import numpy as np
import polars as pl
from typing import Tuple


def extract_features(df: pl.DataFrame, window: int = 50) -> pl.DataFrame:
    """
    Extrait les features techniques pour le ML via Polars lazy evaluation.
    Retourne un pl.DataFrame avec une ligne par bougie (à partir de `window`).
    Appelants ML : utiliser .to_numpy() pour scikit-learn.
    """
    if len(df) < window + 1:
        return pl.DataFrame()

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # Prix normalisés (z-score glissant)
    close_roll_mean = close.rolling_mean(window)
    close_roll_std  = close.rolling_std(window)
    close_roll_std_safe = pl.when(close_roll_std == 0).then(pl.lit(1.0)).otherwise(close_roll_std)
    close_z = (close - close_roll_mean) / close_roll_std_safe

    range_  = high - low
    range_mean = range_.rolling_mean(window)
    range_std  = range_.rolling_std(window)
    range_std_safe = pl.when(range_std == 0).then(pl.lit(1.0)).otherwise(range_std)
    range_z = (range_ - range_mean) / range_std_safe

    # Returns
    ret1  = close.pct_change(1)
    ret5  = close.pct_change(5)
    ret20 = close.pct_change(20)

    # EMAs relatifs
    ema10 = close.ewm_mean(span=10, adjust=False)
    ema20 = close.ewm_mean(span=20, adjust=False)
    ema50 = close.ewm_mean(span=50, adjust=False)
    ema10_rel = (close - ema10) / close
    ema20_rel = (close - ema20) / close
    ema50_rel = (close - ema50) / close
    ema_cross = (ema10 - ema20) / close

    # RSI (SMA version pour les features ML — différent du EWM des stratégies)
    delta = close.diff(1)
    gain  = delta.clip(lower_bound=0).rolling_mean(14)
    loss  = (-delta.clip(upper_bound=0)).rolling_mean(14)
    loss_safe = pl.when(loss == 0).then(pl.lit(None)).otherwise(loss)
    rsi_raw = 100 - 100 / (1 + gain / loss_safe)
    rsi_feat = rsi_raw / 100  # normalisé [0,1]

    # MACD histogram normalisé
    ema12  = close.ewm_mean(span=12, adjust=False)
    ema26  = close.ewm_mean(span=26, adjust=False)
    ml     = ema12 - ema26
    macd_h = ml - ml.ewm_mean(span=9, adjust=False)
    macd_hist = macd_h / close

    # ATR relatif (SMA du TR)
    c_prev = close.shift(1)
    tr = pl.Series(np.maximum((high - low).to_numpy(), np.maximum((high - c_prev).abs().to_numpy(), (low - c_prev).abs().to_numpy())))
    atr_sma  = tr.rolling_mean(14)
    atr_rel  = atr_sma / close

    # Bollinger %B
    sma20 = close.rolling_mean(20)
    std20 = close.rolling_std(20)
    std20_safe = pl.when(std20 == 0).then(pl.lit(1.0)).otherwise(std20)
    bb_pct = (close - (sma20 - 2 * std20)) / (4 * std20_safe)

    # Volume ratio
    vol_ma   = volume.rolling_mean(20)
    vol_ma_safe = pl.when(vol_ma == 0).then(pl.lit(1.0)).otherwise(vol_ma)
    vol_ratio = volume / vol_ma_safe
    vol_trend = vol_ma.pct_change(5)

    # Momentum
    mom10 = close / close.shift(10) - 1
    mom20 = close / close.shift(20) - 1

    # Volatilité réalisée
    vol_real = close.pct_change(1).rolling_std(20)

    result = pl.DataFrame({
        "close_z":   close_z,
        "range_z":   range_z,
        "ret1":      ret1,
        "ret5":      ret5,
        "ret20":     ret20,
        "ema10_rel": ema10_rel,
        "ema20_rel": ema20_rel,
        "ema50_rel": ema50_rel,
        "ema_cross": ema_cross,
        "rsi":       rsi_feat,
        "macd_hist": macd_hist,
        "atr_rel":   atr_rel,
        "bb_pct":    bb_pct,
        "vol_ratio": vol_ratio,
        "vol_trend": vol_trend,
        "mom10":     mom10,
        "mom20":     mom20,
        "vol_real":  vol_real,
    })

    # Remplacer inf/-inf et NaN/null par 0 sans supprimer de lignes intermédiaires
    # (drop_nulls() pourrait supprimer des lignes hors de la fenêtre de warmup,
    # rompant l'alignement temporel avec les labels)
    result = result.with_columns([
        pl.when(pl.col(c).is_infinite()).then(pl.lit(0.0)).otherwise(pl.col(c)).alias(c)
        for c in result.columns
    ]).fill_nan(0.0).fill_null(0.0)

    # Supprimer uniquement la fenêtre de warmup (début — indicators non fiables)
    return result[window:]


def build_labels(df_close: pl.Series, lookahead: int = 5,
                 threshold: float = 0.003) -> pl.Series:
    """
    Label binaire : 1 si le prix monte de >threshold en `lookahead` barres, sinon 0.
    """
    future = df_close.shift(-lookahead)
    return ((future - df_close) / df_close > threshold).cast(pl.Int32)
