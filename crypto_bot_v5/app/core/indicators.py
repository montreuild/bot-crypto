"""Bibliothèque d'indicateurs techniques + détection de régime de marché."""
import numpy as np
import pandas as pd
from typing import Tuple


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d  = close.diff()
    g  = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l  = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(close: pd.Series, fast=12, slow=26, signal=9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig  = ema(line, signal)
    return line, sig, line - sig

def bollinger(close: pd.Series, n=20, std=2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid   = sma(close, n)
    sigma = close.rolling(n).std()
    return mid + std*sigma, mid, mid - std*sigma

def atr(df: pd.DataFrame, n=14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def atr_val(df: pd.DataFrame, n=14) -> float:
    v = float(atr(df, n).iloc[-1])
    return v if not np.isnan(v) and v > 0 else 0.0

def adx(df: pd.DataFrame, n=14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    if len(df) < n*2:
        z = pd.Series(0.0, index=df.index)
        return z, z, z
    h, l, c = df["high"], df["low"], df["close"]
    up, dn  = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr14 = atr(df, n).replace(0, np.nan)
    pdi  = 100 * pd.Series(pdm, index=df.index).ewm(span=n, adjust=False).mean() / atr14
    ndi  = 100 * pd.Series(ndm, index=df.index).ewm(span=n, adjust=False).mean() / atr14
    dx   = 100 * (pdi-ndi).abs() / (pdi+ndi).replace(0, np.nan)
    return dx.ewm(span=n, adjust=False).mean().fillna(0), pdi.fillna(0), ndi.fillna(0)

def adx_val(df: pd.DataFrame, n=14) -> float:
    v = float(adx(df, n)[0].iloc[-1])
    return v if not np.isnan(v) else 0.0

def supertrend(df: pd.DataFrame, n=10, mult=3.0) -> Tuple[pd.Series, pd.Series]:
    """Retourne (direction 1=bull/-1=bear, niveau)."""
    close, high, low = df["close"], df["high"], df["low"]
    prev = close.shift(1)
    tr   = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    atr_s = tr.ewm(span=n, adjust=False).mean()
    hl2   = (high + low) / 2
    ub    = hl2 + mult * atr_s
    lb    = hl2 - mult * atr_s
    final_ub = ub.copy(); final_lb = lb.copy()
    st = pd.Series(np.nan, index=close.index)
    di = pd.Series(1,     index=close.index)
    for i in range(1, len(close)):
        fu = ub.iloc[i] if ub.iloc[i] < final_ub.iloc[i-1] or close.iloc[i-1] > final_ub.iloc[i-1] else final_ub.iloc[i-1]
        fl = lb.iloc[i] if lb.iloc[i] > final_lb.iloc[i-1] or close.iloc[i-1] < final_lb.iloc[i-1] else final_lb.iloc[i-1]
        final_ub.iloc[i] = fu; final_lb.iloc[i] = fl
        if st.iloc[i-1] == final_ub.iloc[i-1]:
            di.iloc[i] = -1 if close.iloc[i] > fu else 1
        else:
            di.iloc[i] =  1 if close.iloc[i] < fl else -1
        st.iloc[i] = fl if di.iloc[i] == 1 else fu
    return di.map({1:-1,-1:1}), st

def volume_ratio(df: pd.DataFrame, n=20) -> pd.Series:
    avg = df["volume"].rolling(n).mean()
    return df["volume"] / avg.replace(0, np.nan)

def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()) * df["volume"]).cumsum()

def donchian(df: pd.DataFrame, n=20) -> Tuple[pd.Series, pd.Series]:
    return df["high"].rolling(n).max(), df["low"].rolling(n).min()

def detect_regime(df: pd.DataFrame) -> dict:
    """Classifie le marché : trending | ranging | volatile."""
    if len(df) < 30:
        return {"regime":"unknown","adx":0,"atr_pct":0,"confidence":0,"trend_dir":"flat"}
    adx_s, _, _ = adx(df, 14)
    adx_l  = float(adx_s.iloc[-1])
    atr_l  = float(atr(df, 14).iloc[-1])
    price  = float(df["close"].iloc[-1])
    atr_p  = atr_l / price * 100 if price > 0 else 0
    ema20  = float(ema(df["close"], 20).iloc[-1])
    ema50  = float(ema(df["close"], 50).iloc[-1])
    tdir   = "up" if ema20 > ema50 else "down"
    if atr_p > 3.0:
        regime, conf = "volatile", min(atr_p/5, 1.0)
    elif adx_l >= 25:
        regime, conf = "trending", min((adx_l-25)/50, 1.0)
    else:
        regime, conf = "ranging",  max(0, 1-(adx_l-15)/10)
    return {"regime":regime,"adx":round(adx_l,2),"atr_pct":round(atr_p,3),"confidence":round(conf,3),"trend_dir":tdir}

def build_features(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Features ML depuis OHLCV."""
    if len(df) < window + 10:
        return pd.DataFrame()
    close = df["close"]
    feat  = pd.DataFrame(index=df.index)
    feat["ret1"]      = close.pct_change(1)
    feat["ret5"]      = close.pct_change(5)
    feat["ret20"]     = close.pct_change(20)
    feat["rsi"]       = rsi(close, 14)
    ml, ms, mh        = macd(close)
    feat["macd_h"]    = mh; feat["macd_hd"] = mh.diff()
    feat["ema_ratio"] = ema(close, 10) / ema(close, 30)
    adx_s, pdi, ndi   = adx(df, 14)
    feat["adx"]       = adx_s; feat["pdi"] = pdi; feat["ndi"] = ndi
    feat["atr_pct"]   = atr(df, 14) / close * 100
    bb_u, _, bb_l     = bollinger(close)
    feat["bb_width"]  = (bb_u - bb_l) / close
    feat["vol_ratio"] = volume_ratio(df, 20)
    st_d, _           = supertrend(df)
    feat["st_dir"]    = st_d
    return feat.dropna()
