"""Bibliothèque d'indicateurs techniques + détection de régime de marché.
Bibliothèque principale : Polars (Rust, Arrow, multi-threadé).
NumPy conservé uniquement pour la boucle séquentielle SuperTrend.
"""
import numpy as np
import polars as pl
from typing import Tuple


def _true_range(df: pl.DataFrame) -> pl.Series:
    """True Range vectorisé. Le null du shift(1) est remplacé par close[0] pour
    éviter la propagation de NaN dans ewm_mean (comportement polars)."""
    h      = df["high"]
    l      = df["low"]
    c_prev = df["close"].shift(1).fill_null(df["close"][0])
    return pl.Series(np.maximum(
        (h - l).to_numpy(),
        np.maximum((h - c_prev).abs().to_numpy(), (l - c_prev).abs().to_numpy()),
    ))


def ema(s: pl.Series, n: int) -> pl.Series:
    return s.ewm_mean(span=n, adjust=False)


def sma(s: pl.Series, n: int) -> pl.Series:
    return s.rolling_mean(n)


def rsi(close: pl.Series, n: int = 14) -> pl.Series:
    d  = close.diff(1)
    g  = d.clip(lower_bound=0).ewm_mean(alpha=1 / n, adjust=False)
    l  = (-d.clip(upper_bound=0)).ewm_mean(alpha=1 / n, adjust=False)
    l_safe = pl.when(l == 0).then(pl.lit(None)).otherwise(l)
    return 100 - (100 / (1 + g / l_safe))


def macd(close: pl.Series, fast=12, slow=26, signal=9) -> Tuple[pl.Series, pl.Series, pl.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig  = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: pl.Series, n=20, std=2.0) -> Tuple[pl.Series, pl.Series, pl.Series]:
    mid   = sma(close, n)
    sigma = close.rolling_std(n)
    return mid + std * sigma, mid, mid - std * sigma


def atr(df: pl.DataFrame, n=14) -> pl.Series:
    return _true_range(df).ewm_mean(span=n, adjust=False)


def atr_val(df: pl.DataFrame, n=14) -> float:
    v = atr(df, n)[-1]
    return float(v) if v is not None and float(v) > 0 else 0.0


def adx(df: pl.DataFrame, n=14) -> Tuple[pl.Series, pl.Series, pl.Series]:
    if len(df) < n * 2:
        z = pl.Series([0.0] * len(df))
        return z, z, z
    h = df["high"]
    l = df["low"]
    up  = h.diff(1).clip(lower_bound=0)
    dn  = (-l.diff(1)).clip(lower_bound=0)
    pdm = pl.when(up > dn).then(up).otherwise(0.0)
    ndm = pl.when(dn > up).then(dn).otherwise(0.0)
    atr14     = _true_range(df).ewm_mean(span=n, adjust=False)
    atr14_safe = pl.when(atr14 == 0).then(pl.lit(None)).otherwise(atr14)
    pdi  = 100 * pdm.ewm_mean(span=n, adjust=False) / atr14_safe
    ndi  = 100 * ndm.ewm_mean(span=n, adjust=False) / atr14_safe
    sum_di = pdi + ndi
    sum_di_safe = pl.when(sum_di == 0).then(pl.lit(None)).otherwise(sum_di)
    dx   = 100 * (pdi - ndi).abs() / sum_di_safe
    return (dx.ewm_mean(span=n, adjust=False).fill_null(0),
            pdi.fill_null(0),
            ndi.fill_null(0))


def adx_val(df: pl.DataFrame, n=14) -> float:
    v = adx(df, n)[0][-1]
    return float(v) if v is not None else 0.0


def supertrend(df: pl.DataFrame, n=10, mult=3.0) -> Tuple[pl.Series, pl.Series]:
    """Retourne (direction 1=bull/-1=bear, niveau).
    Boucle numpy conservée (dépendance séquentielle incontournable).
    """
    close = df["close"].to_numpy()
    high  = df["high"].to_numpy()
    low   = df["low"].to_numpy()
    size  = len(close)

    prev        = np.empty(size)
    prev[0]     = close[0]
    prev[1:]    = close[:-1]
    tr   = np.maximum(high - low,
           np.maximum(np.abs(high - prev), np.abs(low - prev)))
    atr_s = pl.Series(tr).ewm_mean(span=n, adjust=False).to_numpy()

    hl2   = (high + low) / 2.0
    ub    = hl2 + mult * atr_s
    lb    = hl2 - mult * atr_s

    final_ub = ub.copy()
    final_lb = lb.copy()
    st  = np.empty(size)
    di  = np.ones(size, dtype=np.int8)
    st[0] = lb[0]

    for i in range(1, size):
        fu = ub[i] if ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1] else final_ub[i - 1]
        fl = lb[i] if lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1] else final_lb[i - 1]
        final_ub[i] = fu
        final_lb[i] = fl
        if st[i - 1] == final_ub[i - 1]:
            di[i] = -1 if close[i] > fu else 1
        else:
            di[i] =  1 if close[i] < fl else -1
        st[i] = fl if di[i] == 1 else fu

    dir_mapped = pl.Series([-1 if x == 1 else 1 for x in di], dtype=pl.Float64)
    return dir_mapped, pl.Series(st)


def volume_ratio(df: pl.DataFrame, n=20) -> pl.Series:
    avg = df["volume"].rolling_mean(n)
    avg_safe = pl.when(avg == 0).then(pl.lit(None)).otherwise(avg)
    return df["volume"] / avg_safe


def obv(df: pl.DataFrame) -> pl.Series:
    return (df["close"].diff(1).sign() * df["volume"]).cum_sum()


def donchian(df: pl.DataFrame, n=20) -> Tuple[pl.Series, pl.Series]:
    return df["high"].rolling_max(n), df["low"].rolling_min(n)


def detect_regime(df: pl.DataFrame) -> dict:
    """Classifie le marché : trending | ranging | volatile."""
    if len(df) < 30:
        return {"regime": "unknown", "adx": 0, "atr_pct": 0, "confidence": 0, "trend_dir": "flat"}
    adx_s, _, _ = adx(df, 14)
    adx_l  = float(adx_s[-1]) if adx_s[-1] is not None else 0.0
    atr_l  = atr_val(df, 14)
    price  = float(df["close"][-1])
    atr_p  = atr_l / price * 100 if price > 0 else 0
    ema20  = float(ema(df["close"], 20)[-1])
    ema50  = float(ema(df["close"], 50)[-1])
    tdir   = "up" if ema20 > ema50 else "down"
    if atr_p > 3.0:
        regime, conf = "volatile", min(atr_p / 5, 1.0)
    elif adx_l >= 25:
        regime, conf = "trending", min((adx_l - 25) / 50, 1.0)
    else:
        regime, conf = "ranging",  max(0, 1 - (adx_l - 15) / 10)
    return {"regime": regime, "adx": round(adx_l, 2), "atr_pct": round(atr_p, 3),
            "confidence": round(conf, 3), "trend_dir": tdir}


def build_features(df: pl.DataFrame, window: int = 20) -> pl.DataFrame:
    """Features ML depuis OHLCV."""
    if len(df) < window + 10:
        return pl.DataFrame()
    close = df["close"]
    adx_s, pdi, ndi = adx(df, 14)
    atr14 = atr(df, 14)
    ml, ms, mh = macd(close)
    bb_u, _, bb_l = bollinger(close)
    vr = volume_ratio(df, 20)
    st_d, _ = supertrend(df)

    result = pl.DataFrame({
        "ret1":      close.pct_change(1),
        "ret5":      close.pct_change(5),
        "ret20":     close.pct_change(20),
        "rsi":       rsi(close, 14),
        "macd_h":    mh,
        "macd_hd":   mh.diff(1),
        "ema_ratio": ema(close, 10) / ema(close, 30),
        "adx":       adx_s,
        "pdi":       pdi,
        "ndi":       ndi,
        "atr_pct":   atr14 / close * 100,
        "bb_width":  (bb_u - bb_l) / close,
        "vol_ratio": vr,
        "st_dir":    st_d,
    })
    return result.drop_nulls()


# ══════════════════════════════════════════════════════════════════════════════
#  Pré-calcul vectorisé pour le backtest — O(n) unique
# ══════════════════════════════════════════════════════════════════════════════

def precompute(df: pl.DataFrame) -> pl.DataFrame:
    """
    Enrichit le df avec des colonnes _pre_* pré-calculées vectoriellement via Polars.
    Les stratégies peuvent lire ces colonnes (via pre_or_compute) au lieu de
    recalculer les indicateurs depuis zéro → speedup ~180×.
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    c_prev = c.shift(1).fill_null(c[0])
    tr = pl.Series(np.maximum(
        (h - l).to_numpy(),
        np.maximum((h - c_prev).abs().to_numpy(), (l - c_prev).abs().to_numpy()),
    ))

    # RSI(14)
    d  = c.diff(1)
    g  = d.clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False)
    dn = (-d.clip(upper_bound=0)).ewm_mean(alpha=1 / 14, adjust=False)
    dn_safe = pl.when(dn == 0).then(pl.lit(None)).otherwise(dn)
    pre_rsi14 = 100 - 100 / (1 + g / dn_safe)

    # ATR(14)
    pre_atr14 = tr.ewm_mean(span=14, adjust=False)

    # ADX(14)
    up   = h.diff(1).clip(lower_bound=0)
    down = (-l.diff(1)).clip(lower_bound=0)
    pdm  = pl.when(up > down).then(up).otherwise(0.0)
    ndm  = pl.when(down > up).then(down).otherwise(0.0)
    atr_safe = pl.when(pre_atr14 == 0).then(pl.lit(None)).otherwise(pre_atr14)
    pdi  = 100 * pdm.ewm_mean(span=14, adjust=False) / atr_safe
    ndi_ = 100 * ndm.ewm_mean(span=14, adjust=False) / atr_safe
    sum_di = pdi + ndi_
    sum_safe = pl.when(sum_di == 0).then(pl.lit(None)).otherwise(sum_di)
    dx   = 100 * (pdi - ndi_).abs() / sum_safe

    # MACD(12,26,9)
    ema_f = c.ewm_mean(span=12, adjust=False)
    ema_s = c.ewm_mean(span=26, adjust=False)
    ml    = ema_f - ema_s
    ms    = ml.ewm_mean(span=9, adjust=False)

    # volume_ratio(20)
    vm = v.rolling_mean(20)
    vm_safe = pl.when(vm < 1e-9).then(pl.lit(1e-9)).otherwise(vm)

    return df.with_columns([
        pre_rsi14.alias("_pre_rsi14"),
        pre_atr14.alias("_pre_atr14"),
        dx.ewm_mean(span=14, adjust=False).fill_null(0).alias("_pre_adx14"),
        pdi.fill_null(0).alias("_pre_pdi14"),
        ndi_.fill_null(0).alias("_pre_ndi14"),
        ml.alias("_pre_macd_line"),
        ms.alias("_pre_macd_sig"),
        (ml - ms).alias("_pre_macd_hist"),
        (v / vm_safe).alias("_pre_volratio20"),
    ])


def pre_or_compute(df: pl.DataFrame, col: str, fallback_fn, *args, **kwargs):
    """
    Lit la colonne pré-calculée si elle existe dans le df, sinon calcule à la volée.
    Compatible avec les backtests (avec pré-calcul) et le live trading (sans).
    """
    if col in df.columns:
        v = df[col][-1]
        if v is not None:
            return df[col]
    return fallback_fn(*args, **kwargs)
