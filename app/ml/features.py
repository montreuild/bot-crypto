"""
Module 5 — Features ML : extraction depuis OHLCV pour modèles prédictifs.
"""
import numpy as np
import pandas as pd
from typing import Tuple, List


def extract_features(df: pd.DataFrame, window: int = 50) -> pd.DataFrame:
    """
    Extrait les features techniques pour le ML.
    Retourne un DataFrame avec une ligne par bougie (à partir de `window`).
    """
    if len(df) < window + 1:
        return pd.DataFrame()

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    feats = pd.DataFrame(index=df.index)

    # Prix normalisés (z-score sur fenêtre glissante)
    feats["close_z"]  = (close - close.rolling(window).mean()) / close.rolling(window).std().replace(0, 1)
    feats["range_z"]  = ((high - low) - (high-low).rolling(window).mean()) / (high-low).rolling(window).std().replace(0,1)

    # Returns
    feats["ret1"]  = close.pct_change(1)
    feats["ret5"]  = close.pct_change(5)
    feats["ret20"] = close.pct_change(20)

    # EMAs relatifs
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    feats["ema10_rel"] = (close - ema10) / close
    feats["ema20_rel"] = (close - ema20) / close
    feats["ema50_rel"] = (close - ema50) / close
    feats["ema_cross"] = (ema10 - ema20) / close  # cross ratio

    # RSI
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    feats["rsi"] = (100 - 100 / (1 + rs)) / 100  # normalisé [0,1]

    # MACD histogram normalisé
    ema12   = close.ewm(span=12, adjust=False).mean()
    ema26   = close.ewm(span=26, adjust=False).mean()
    macd_h  = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    feats["macd_hist"] = macd_h / close

    # ATR relatif
    tr  = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    feats["atr_rel"] = atr / close

    # Bollinger %B
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    feats["bb_pct"] = (close - (sma20 - 2*std20)) / (4 * std20.replace(0,1))

    # Volume ratio
    vol_ma = volume.rolling(20).mean()
    feats["vol_ratio"] = volume / vol_ma.replace(0, 1)
    feats["vol_trend"] = vol_ma.pct_change(5)

    # Momentum
    feats["mom10"] = close / close.shift(10) - 1
    feats["mom20"] = close / close.shift(20) - 1

    # Volatilité réalisée
    feats["vol_real"] = close.pct_change().rolling(20).std()

    feats.dropna(inplace=True)
    return feats.replace([np.inf, -np.inf], 0)


def build_labels(df_close: pd.Series, lookahead: int = 5,
                 threshold: float = 0.003) -> pd.Series:
    """
    Label binaire : 1 si le prix monte de >threshold en `lookahead` barres, sinon 0.
    """
    future = df_close.shift(-lookahead)
    return ((future - df_close) / df_close > threshold).astype(int)
