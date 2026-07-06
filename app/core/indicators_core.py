"""Indicateurs techniques de base (EMA/RSI/MACD/BB/ATR/ADX/volume/SuperTrend…).

Extrait de ``indicators.py`` (découpage V13). Importer de préférence via la
façade ``app.core.indicators`` qui ré-exporte tous les noms.
"""
import logging
import threading
from collections import OrderedDict
from typing import Tuple

import numpy as np
import polars as pl

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  Primitives
# ══════════════════════════════════════════════════════════════════════════════

def safe_num(val, default: float = 0.0) -> float:
    """Coercion float robuste : None / NaN / inf / NaT / non-numérique → ``default``.

    Le pipeline de features V4 est pandas ; sur certains jeux de données réels
    (trous, bougies dupliquées, jointures FeatureStore) une valeur peut ressortir
    en ``NaT``/``NaN``. ``float(pd.NaT)`` lève « float() argument must be a string
    or a real number, not 'NaTType' » — et ``bool(pd.NaT)`` vaut True, donc le
    garde-fou ``float(x or 0.0)`` ne protège pas. On neutralise toutes ces
    valeurs ici (source unique partagée par les stratégies Opus).
    """
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        # pd.NaT, pd.NA, datetime64, str non numérique…
        return default
    if not np.isfinite(f):
        return default
    return f


def _true_range(df: pl.DataFrame) -> pl.Series:
    """True Range vectorisé : TR = max(H−L, |H−C_prev|, |L−C_prev|)."""
    h      = df["high"]
    l      = df["low"]
    c_prev = df["close"].shift(1).fill_null(df["close"][0])
    hl     = h - l
    hcp    = (h - c_prev).abs()
    lcp    = (l - c_prev).abs()
    return (
        pl.DataFrame({"hl": hl, "hcp": hcp, "lcp": lcp})
        .select(pl.max_horizontal("hl", "hcp", "lcp"))
        .to_series()
    )


def ema(s: pl.Series, n: int) -> pl.Series:
    return s.ewm_mean(span=n, adjust=False)


def sma(s: pl.Series, n: int) -> pl.Series:
    return s.rolling_mean(n)


# ══════════════════════════════════════════════════════════════════════════════
#  RSI
# ══════════════════════════════════════════════════════════════════════════════

def rsi(close: pl.Series, period: int = 14) -> pl.Series:
    """RSI(period) pur Polars — division sécurisée via clip lower_bound=1e-10."""
    d      = close.diff(1)
    g      = d.clip(lower_bound=0).ewm_mean(alpha=1 / period, adjust=False)
    l      = (-d.clip(upper_bound=0)).ewm_mean(alpha=1 / period, adjust=False)
    l_safe = l.clip(lower_bound=1e-10)
    return 100 - (100 / (1 + g / l_safe))


# ══════════════════════════════════════════════════════════════════════════════
#  MACD
# ══════════════════════════════════════════════════════════════════════════════

def macd(close: pl.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[pl.Series, pl.Series, pl.Series]:
    """Retourne (macd_line, signal_line, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig  = ema(line, signal)
    return line, sig, line - sig


# ══════════════════════════════════════════════════════════════════════════════
#  Bollinger Bands
# ══════════════════════════════════════════════════════════════════════════════

def bollinger(close: pl.Series, n: int = 20,
              std: float = 2.0) -> Tuple[pl.Series, pl.Series, pl.Series]:
    """Retourne (upper, mid, lower)."""
    mid   = sma(close, n)
    sigma = close.rolling_std(n)
    return mid + std * sigma, mid, mid - std * sigma


def bb_squeeze(close: pl.Series, lookback: int = 15,
               bb_period: int = 20, quantile: float = 0.30) -> bool:
    """True si les bandes de Bollinger sont en squeeze (compression de volatilité)."""
    if len(close) < bb_period + lookback:
        return False
    # Seules les `lookback+1` dernières largeurs sont utilisées, et chacune ne
    # dépend que des `bb_period` closes précédents. On tronque à la queue → coût
    # O(bb_period+lookback) par appel au lieu de O(n) (rolling sur toute la série) :
    # transforme un backtest O(n²) en O(n) pour tous les appelants, à résultat
    # strictement identique (fenêtres rolling causales).
    tail     = close[-(bb_period + lookback + 2):]
    _sma     = tail.rolling_mean(bb_period)
    _std     = tail.rolling_std(bb_period)
    # clip évite pl.when qui retourne un Expr (non subscriptable)
    sma_safe = _sma.clip(lower_bound=1e-9)
    width    = 4 * _std / sma_safe
    cur_w    = width[-1]
    if cur_w is None:
        return False
    past = width[-(lookback + 1):-1].drop_nulls()
    return len(past) >= 5 and float(cur_w) <= float(past.quantile(quantile))


# ══════════════════════════════════════════════════════════════════════════════
#  ATR  — deux variantes : Series (pour build_features) et scalaire (stratégies)
# ══════════════════════════════════════════════════════════════════════════════

def atr(df: pl.DataFrame, n: int = 14) -> pl.Series:
    """ATR(n) — retourne une Series complète (compatible build_features)."""
    return _true_range(df).ewm_mean(span=n, adjust=False)


def atr_series(df: pl.DataFrame, period: int = 14) -> pl.Series:
    """Alias explicite pour atr() → pl.Series."""
    return _true_range(df).ewm_mean(span=period, adjust=False)


def atr_val(df: pl.DataFrame, n: int = 14) -> float:
    """ATR(n) — retourne le scalaire de la dernière barre (usage live/stratégies)."""
    v = atr(df, n)[-1]
    return float(v) if v is not None and float(v) > 0 else 0.0


def atr_wilder(df: pl.DataFrame, n: int = 14) -> pl.Series:
    """ATR(n) lissage de **Wilder** (RMA, alpha = 1/n) — la variante de
    ``ta.atr`` en Pine, distincte de :func:`atr` (EMA span=n, alpha=2/(n+1)).

    Utilisée par le moteur Smart Money (``app/core/smc.py``) et le portage
    PineScript (``liquidity_sweep_vol``) pour que les seuils « ×ATR » de ces
    modules correspondent au même ATR que TradingView. Source unique de vérité
    du lissage Wilder — ne pas ré-implémenter en local."""
    return _true_range(df).ewm_mean(alpha=1.0 / n, adjust=False)


# ══════════════════════════════════════════════════════════════════════════════
#  ADX  — deux variantes : tuple Series (pour detect_regime/build_features)
#          et scalaire (stratégies)
# ══════════════════════════════════════════════════════════════════════════════

def adx(df: pl.DataFrame, n: int = 14) -> Tuple[pl.Series, pl.Series, pl.Series]:
    """ADX(n) — pur Polars, retourne (adx_line, +DI, −DI) comme Series complètes.

    Conditionnels pdm/ndm via multiplication booléenne (zéro round-trip numpy) :
      pdm = up   si up > dn, sinon 0  →  up  * (up > dn).cast(Float64)
      ndm = dn   si dn > up, sinon 0  →  dn  * (dn > up).cast(Float64)
    Divisions sécurisées via .clip(lower_bound=1e-10).
    """
    if len(df) < n * 2:
        z = pl.Series([0.0] * len(df))
        return z, z, z
    h    = df["high"]
    l    = df["low"]
    up   = h.diff(1).clip(lower_bound=0)
    dn   = (-l.diff(1)).clip(lower_bound=0)
    pdm  = up  * (up > dn).cast(pl.Float64)
    ndm  = dn  * (dn > up).cast(pl.Float64)

    atr14    = _true_range(df).ewm_mean(span=n, adjust=False)
    atr_safe = atr14.clip(lower_bound=1e-10)

    pdi     = 100 * pdm.ewm_mean(span=n, adjust=False) / atr_safe
    ndi     = 100 * ndm.ewm_mean(span=n, adjust=False) / atr_safe
    sum_di  = (pdi + ndi).clip(lower_bound=1e-10)
    dx      = 100 * (pdi - ndi).abs() / sum_di

    adx_line = dx.ewm_mean(span=n, adjust=False).fill_null(0)
    return adx_line, pdi.fill_null(0), ndi.fill_null(0)


def adx_val(df: pl.DataFrame, n: int = 14) -> float:
    """ADX(n) — retourne le scalaire de la dernière barre (usage live/scanner/stratégies)."""
    v = adx(df, n)[0][-1]
    return float(v) if v is not None else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  Volume
# ══════════════════════════════════════════════════════════════════════════════

def volume_ratio(df: pl.DataFrame, n: int = 20) -> pl.Series:
    """Volume / SMA(volume, n) — retourne une Series (compatible scanner/build_features)."""
    avg     = df["volume"].rolling_mean(n)
    avg_safe = avg.clip(lower_bound=1e-9)
    return df["volume"] / avg_safe


def vol_ratio(df: pl.DataFrame, period: int = 20) -> float:
    """Volume ratio — retourne le scalaire de la dernière barre (usage stratégies)."""
    avg = df["volume"].rolling_mean(period)[-1]
    now = float(df["volume"][-1])
    return now / max(float(avg) if avg is not None else 1e-9, 1e-9)


def obv(df: pl.DataFrame) -> pl.Series:
    return (df["close"].diff(1).sign() * df["volume"]).cum_sum()


# ══════════════════════════════════════════════════════════════════════════════
#  Donchian
# ══════════════════════════════════════════════════════════════════════════════

def donchian(df: pl.DataFrame, n: int = 20) -> Tuple[pl.Series, pl.Series]:
    return df["high"].rolling_max(n), df["low"].rolling_min(n)


# ══════════════════════════════════════════════════════════════════════════════
#  SuperTrend
# ══════════════════════════════════════════════════════════════════════════════

def supertrend(df: pl.DataFrame, period: int = 10,
               mult: float = 3.0) -> Tuple[pl.Series, pl.Series]:
    """Retourne (direction: +1/-1, st_line).

    TR et ATR calculés par _true_range() (Polars) ; seule la boucle
    upper/lower/direction reste en numpy — dépendance séquentielle
    incontournable (upper[i] dépend de upper[i-1]).
    Optimized: pre-shift close array and use contiguous float64 arrays.
    """
    # TR et ATR via Polars (pas de round-trip sur cette partie)
    atr_arr = _true_range(df).ewm_mean(span=period, adjust=False).to_numpy().astype(np.float64)

    high  = df["high"].to_numpy().astype(np.float64)
    low   = df["low"].to_numpy().astype(np.float64)
    close = df["close"].to_numpy().astype(np.float64)
    n     = len(close)

    hl2         = (high + low) * 0.5
    upper_basic = hl2 + mult * atr_arr
    lower_basic = hl2 - mult * atr_arr

    upper  = upper_basic.copy()
    lower  = lower_basic.copy()
    st     = np.empty(n, dtype=np.float64)
    dir_   = np.ones(n, dtype=np.int8)
    st[0]  = lower[0]

    # Pre-shift for cache-friendly access in the hot loop
    close_prev = np.empty(n, dtype=np.float64)
    close_prev[0] = close[0]
    close_prev[1:] = close[:-1]

    for i in range(1, n):
        cp = close_prev[i]
        if upper_basic[i] < upper[i - 1] or cp > upper[i - 1]:
            upper[i] = upper_basic[i]
        else:
            upper[i] = upper[i - 1]
        if lower_basic[i] > lower[i - 1] or cp < lower[i - 1]:
            lower[i] = lower_basic[i]
        else:
            lower[i] = lower[i - 1]
        if dir_[i - 1] == 1:
            dir_[i] = 1 if close[i] >= lower[i] else -1
        else:
            dir_[i] = -1 if close[i] <= upper[i] else 1
        st[i] = lower[i] if dir_[i] == 1 else upper[i]

    return pl.Series(dir_.astype(np.float64)), pl.Series(st)


# ══════════════════════════════════════════════════════════════════════════════
#  Stochastique
# ══════════════════════════════════════════════════════════════════════════════

def stochastic(df: pl.DataFrame, k_period: int = 14,
               d_period: int = 3) -> Tuple[float, float]:
    """Oscillateur Stochastique — K% et D%.

    K% = (Close − LowestLow_k) / (HighestHigh_k − LowestLow_k) × 100
    D% = SMA(K%, d_period)

    Retourne (K_val, D_val) — scalaires flottants.
    Retourne (50.0, 50.0) si données insuffisantes.
    """
    if len(df) < k_period + d_period:
        return 50.0, 50.0

    close        = df["close"]
    high         = df["high"]
    low          = df["low"]
    lowest_low   = low.rolling_min(k_period)
    highest_high = high.rolling_max(k_period)
    hl_range     = highest_high - lowest_low
    # clip évite pl.when qui retourne un Expr (non subscriptable)
    hl_safe      = hl_range.clip(lower_bound=1e-10)
    k_series     = (close - lowest_low) / hl_safe * 100.0
    d_series     = k_series.rolling_mean(d_period)

    k_val = float(k_series[-1]) if k_series[-1] is not None else 50.0
    d_val = float(d_series[-1]) if d_series[-1] is not None else 50.0
    return max(0.0, min(100.0, k_val)), max(0.0, min(100.0, d_val))


# ══════════════════════════════════════════════════════════════════════════════
#  Indicateurs repris du catalogue de features V4 (~462 colonnes)
#  Primitives génériques et réutilisables, jusque-là absentes d'indicators.py.
# ══════════════════════════════════════════════════════════════════════════════

def roc(close: pl.Series, n: int = 14) -> pl.Series:
    """Rate of Change — variation en % sur ``n`` barres : (close/close[-n] − 1)·100.

    Indicateur de momentum fondamental (utilisé tel quel dans les features V4
    ROC_7/14/21). Division sécurisée via clip(lower_bound).
    """
    return (close / close.shift(n).clip(lower_bound=1e-10) - 1.0) * 100.0


def green_ratio(df: pl.DataFrame, n: int = 10) -> pl.Series:
    """Proportion de bougies haussières (close > open) sur une fenêtre de ``n``.

    Mesure de « breadth » directionnelle locale (features V4 green_ratio_10/20).
    Retourne une Series dans [0, 1].
    """
    green = (df["close"] > df["open"]).cast(pl.Float64)
    return green.rolling_mean(n)


def rsi_divergence(df: pl.DataFrame, period: int = 14,
                   lookback: int = 14) -> pl.Series:
    """Divergence RSI/prix (features V4 bull_div / bear_div), fusionnée et signée.

    Retourne une Series dans {−1, 0, +1} :
      +1  divergence haussière : le prix inscrit un plus-bas sur ``lookback`` mais
          le RSI reste nettement au-dessus de son propre plus-bas (essoufflement
          baissier) ;
      −1  divergence baissière : le prix inscrit un plus-haut mais le RSI reste
          sous son plus-haut (essoufflement haussier) ;
       0  pas de divergence.
    """
    close = df["close"]
    r = rsi(close, period)
    bear = ((close == close.rolling_max(lookback)) &
            (r < r.rolling_max(lookback) * 0.97))
    bull = ((close == close.rolling_min(lookback)) &
            (r > r.rolling_min(lookback) * 1.03))
    return (bull.cast(pl.Int8) - bear.cast(pl.Int8)).fill_null(0)


def trend_duration(df: pl.DataFrame, n: int = 14,
                   adx_threshold: float = 25.0) -> pl.Series:
    """Persistance de tendance : nombre de barres consécutives avec ADX > seuil.

    Reprend la feature V4 ``trend_duration`` (longueur du run courant de tendance
    forte). Le compteur se remet à zéro dès que l'ADX repasse sous le seuil.
    """
    adx_line, _, _ = adx(df, n)
    strong = (adx_line > adx_threshold).fill_null(False).to_numpy().astype(np.int8)
    out = np.zeros(len(strong), dtype=np.int64)
    run = 0
    for i in range(len(strong)):
        run = run + 1 if strong[i] else 0
        out[i] = run
    return pl.Series(out)
