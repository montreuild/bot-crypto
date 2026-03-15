"""
Module d'indicateurs partagé — utilisé par toutes les stratégies.
Centralise RSI, ADX, ATR, MACD, SuperTrend, structure de marché.
"""
import numpy as np
import pandas as pd
from typing import Tuple


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    # l=0 means no down days → RSI=100; use tiny value to avoid nan
    return 100 - 100 / (1 + g / l.replace(0, 1e-10))


def atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    v = float(tr.ewm(span=period, adjust=False).mean().iloc[-1])
    return v if v > 0 else 0.0


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period * 2:
        return 0.0
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    up   = h.diff().clip(lower=0)
    down = (-l.diff()).clip(lower=0)
    pdm  = up.where(up > down, 0.0)
    mdm  = down.where(down > up, 0.0)
    tr   = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(span=period, adjust=False).mean().replace(0, np.nan)
    dip  = 100 * pdm.ewm(span=period, adjust=False).mean() / atr_
    dim  = 100 * mdm.ewm(span=period, adjust=False).mean() / atr_
    dx   = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    v    = float(dx.ewm(span=period, adjust=False).mean().iloc[-1])
    return v if not np.isnan(v) else 0.0


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Retourne (macd_line, signal_line, histogram)."""
    ema_f   = close.ewm(span=fast,   adjust=False).mean()
    ema_s   = close.ewm(span=slow,   adjust=False).mean()
    line    = ema_f - ema_s
    sig     = line.ewm(span=signal,  adjust=False).mean()
    hist    = line - sig
    return line, sig, hist


def supertrend(df: pd.DataFrame, period: int = 10,
               mult: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """
    Retourne (direction: +1/-1, st_line) — signatures identiques à l'original.
    Implémentation vectorisée numpy : ~50× plus rapide que .iloc en boucle.
    """
    high  = df["high"].astype(float).values
    low   = df["low"].astype(float).values
    close = df["close"].astype(float).values
    n     = len(close)

    # True Range vectorisé
    prev_close        = np.empty(n)
    prev_close[0]     = close[0]
    prev_close[1:]    = close[:-1]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    # ATR EWM (Wilder — cohérent avec atr())
    atr_arr       = np.empty(n)
    atr_arr[0]    = tr[0]
    alpha         = 2.0 / (period + 1)
    for i in range(1, n):
        atr_arr[i] = atr_arr[i-1] * (1 - alpha) + tr[i] * alpha

    hl2         = (high + low) / 2.0
    upper_basic = hl2 + mult * atr_arr
    lower_basic = hl2 - mult * atr_arr

    upper  = upper_basic.copy()
    lower  = lower_basic.copy()
    st     = np.empty(n)
    dir_   = np.ones(n, dtype=np.int8)

    st[0] = lower[0]

    for i in range(1, n):
        # Correction bande supérieure
        upper[i] = upper_basic[i] if (upper_basic[i] < upper[i-1]
                                       or close[i-1] > upper[i-1]) else upper[i-1]
        # Correction bande inférieure
        lower[i] = lower_basic[i] if (lower_basic[i] > lower[i-1]
                                       or close[i-1] < lower[i-1]) else lower[i-1]
        # Direction
        if dir_[i-1] == 1:
            dir_[i] = 1 if close[i] >= lower[i] else -1
        else:
            dir_[i] = -1 if close[i] <= upper[i] else 1
        st[i] = lower[i] if dir_[i] == 1 else upper[i]

    return (pd.Series(dir_.astype(np.float64), index=df.index),
            pd.Series(st, index=df.index))


def bb_squeeze(close: pd.Series, lookback: int = 15,
               bb_period: int = 20, quantile: float = 0.30) -> bool:
    if len(close) < bb_period + lookback:
        return False
    sma   = close.rolling(bb_period).mean()
    std   = close.rolling(bb_period).std()
    width = (4 * std / sma.clip(lower=1e-9))
    cur_w = float(width.iloc[-1])
    past  = width.iloc[-(lookback + 1):-1].dropna()
    return len(past) >= 5 and cur_w <= float(past.quantile(quantile))


def market_structure(high: pd.Series, low: pd.Series,
                     n_pivots: int = 4, window: int = 5) -> int:
    """
    +1 = HH/HL (uptrend), -1 = LL/LH (downtrend), 0 = neutral.
    """
    if len(high) < n_pivots * window * 2:
        return 0
    highs = [float(high.iloc[-i*window-1:-i*window+window-1].max()) for i in range(1, n_pivots+1)]
    lows  = [float(low.iloc[-i*window-1:-i*window+window-1].min())  for i in range(1, n_pivots+1)]
    hh = sum(1 for i in range(len(highs)-1) if highs[i] > highs[i+1])
    hl = sum(1 for i in range(len(lows)-1)  if lows[i]  > lows[i+1])
    ll = sum(1 for i in range(len(highs)-1) if highs[i] < highs[i+1])
    lh = sum(1 for i in range(len(lows)-1)  if lows[i]  < lows[i+1])
    if (hh + hl) >= n_pivots - 1: return 1
    if (ll + lh) >= n_pivots - 1: return -1
    return 0


def vol_ratio(df: pd.DataFrame, period: int = 20) -> float:
    avg = float(df["volume"].rolling(period).mean().iloc[-1])
    now = float(df["volume"].iloc[-1])
    return now / max(avg, 1e-9)


def htf_trend(df_htf, ema_period: int = 50) -> int:
    """
    Tendance du timeframe supérieur : +1 haussier, -1 baissier, 0 neutre.
    Utilise EMA50 + pente comme proxy rapide.
    """
    if df_htf is None or len(df_htf) < ema_period + 3:
        return 0
    c   = df_htf["close"].astype(float)
    ema = c.ewm(span=ema_period, adjust=False).mean()
    price_above = float(c.iloc[-1]) > float(ema.iloc[-1])
    slope_up    = float(ema.iloc[-1]) > float(ema.iloc[-4])
    if price_above and slope_up:   return 1
    if not price_above and not slope_up: return -1
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Pré-calcul vectorisé — O(n) unique pour accélérer le backtest (~180×)
# ══════════════════════════════════════════════════════════════════════════════

def precompute_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enrichit le df avec les colonnes _pre_* calculées une seule fois.
    Appelé par Backtester.run() AVANT la boucle barre-par-barre.
    Les stratégies lisent ces colonnes via pre_val() → O(1) au lieu de O(n).

    Indicateurs couverts (spans FIXES — les spans param-driven (EMA21/50/200) ne
    peuvent pas être pré-calculés car ils varient selon les params de l'optimizer) :
      _pre_rsi14       RSI(14)
      _pre_atr14       ATR(14)  — EWM, cohérent avec atr()
      _pre_adx14       ADX(14)
      _pre_macd_line   MACD line (12,26,9)
      _pre_macd_sig    MACD signal
      _pre_macd_hist   MACD histogram
      _pre_volratio20  volume_ratio(20)
    """
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    v = df["volume"].astype(float)

    # RSI(14)
    d  = c.diff()
    g  = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df["_pre_rsi14"] = 100 - 100 / (1 + g / dn.replace(0, 1e-10))

    # ATR(14) — EWM pour cohérence avec atr()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["_pre_atr14"] = tr.ewm(span=14, adjust=False).mean()

    # ADX(14)
    up   = h.diff().clip(lower=0)
    down = (-l.diff()).clip(lower=0)
    pdm  = up.where(up > down, 0.0)
    mdm  = down.where(down > up, 0.0)
    atr_ = tr.ewm(span=14, adjust=False).mean().replace(0, np.nan)
    dip  = 100 * pdm.ewm(span=14, adjust=False).mean() / atr_
    dim  = 100 * mdm.ewm(span=14, adjust=False).mean() / atr_
    dx   = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    df["_pre_adx14"]  = dx.ewm(span=14, adjust=False).mean().fillna(0)
    df["_pre_pdi14"]  = dip.fillna(0)
    df["_pre_ndi14"]  = dim.fillna(0)

    # MACD(12,26,9)
    ema_f = c.ewm(span=12, adjust=False).mean()
    ema_s = c.ewm(span=26, adjust=False).mean()
    ml    = ema_f - ema_s
    ms    = ml.ewm(span=9, adjust=False).mean()
    df["_pre_macd_line"] = ml
    df["_pre_macd_sig"]  = ms
    df["_pre_macd_hist"] = ml - ms

    # volume_ratio(20)
    vm = v.rolling(20).mean()
    df["_pre_volratio20"] = v / vm.clip(lower=1e-9)

    return df


def pre_val(df: pd.DataFrame, col: str) -> float:
    """
    Lit la valeur pré-calculée à la dernière ligne si disponible.
    Retourne None si la colonne n'existe pas ou si la valeur est NaN.
    Usage :
        rsi_now = pre_val(df, "_pre_rsi14") or float(rsi(df["close"]).iloc[-1])
    """
    if col in df.columns:
        v = df[col].iloc[-1]
        if pd.notna(v):
            return float(v)
    return None
