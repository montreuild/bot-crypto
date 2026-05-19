"""Indicateurs techniques — source unique pour stratégies et moteur. Basé sur Polars."""
import logging
import numpy as np
import polars as pl
from typing import Tuple

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  Primitives
# ══════════════════════════════════════════════════════════════════════════════

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
    _sma     = close.rolling_mean(bb_period)
    _std     = close.rolling_std(bb_period)
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
#  Structure de marché & HTF
# ══════════════════════════════════════════════════════════════════════════════

def market_structure(high: pl.Series, low: pl.Series,
                     n_pivots: int = 4, window: int = 5) -> int:
    """+1 = HH/HL (uptrend), −1 = LL/LH (downtrend), 0 = neutral."""
    if len(high) < n_pivots * window * 2:
        return 0
    highs = [float(high[-i * window - 1:-i * window + window - 1].max())
             for i in range(1, n_pivots + 1)]
    lows  = [float(low[-i * window - 1:-i * window + window - 1].min())
             for i in range(1, n_pivots + 1)]
    hh = sum(1 for i in range(len(highs) - 1) if highs[i] > highs[i + 1])
    hl = sum(1 for i in range(len(lows) - 1)  if lows[i]  > lows[i + 1])
    ll = sum(1 for i in range(len(highs) - 1) if highs[i] < highs[i + 1])
    lh = sum(1 for i in range(len(lows) - 1)  if lows[i]  < lows[i + 1])
    if (hh + hl) >= n_pivots - 1:
        return 1
    if (ll + lh) >= n_pivots - 1:
        return -1
    return 0


def htf_trend(df_htf, ema_period: int = 50) -> int:
    """Tendance du timeframe supérieur : +1 haussier, −1 baissier, 0 neutre."""
    if df_htf is None or len(df_htf) < ema_period + 3:
        return 0
    c        = df_htf["close"]
    ema_line = c.ewm_mean(span=ema_period, adjust=False)
    price_above = float(c[-1]) > float(ema_line[-1])
    slope_up    = float(ema_line[-1]) > float(ema_line[-4])
    if price_above and slope_up:
        return 1
    if not price_above and not slope_up:
        return -1
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Détection de régime
# ══════════════════════════════════════════════════════════════════════════════

def detect_regime_full(df: pl.DataFrame,
                      adx_trend_threshold: float = 25.0,
                      atr_volatile_threshold: float = 3.0) -> dict:
    """Classifie le marché : trending | ranging | volatile.

    Args:
        adx_trend_threshold: ADX above this = trending (default 25)
        atr_volatile_threshold: ATR% above this = volatile (default 3.0%)
    """
    if len(df) < 30:
        return {"regime": "unknown", "adx": 0, "atr_pct": 0,
                "confidence": 0, "trend_dir": "flat"}
    adx_s, _, _ = adx(df, 14)
    adx_l  = float(adx_s[-1]) if adx_s[-1] is not None else 0.0
    atr_l  = atr_val(df, 14)
    price  = float(df["close"][-1])
    atr_p  = atr_l / price * 100 if price > 0 else 0
    ema20  = float(ema(df["close"], 20)[-1])
    ema50  = float(ema(df["close"], 50)[-1])
    tdir   = "up" if ema20 > ema50 else "down"
    if atr_p > atr_volatile_threshold:
        regime, conf = "volatile", min(atr_p / 5, 1.0)
    elif adx_l >= adx_trend_threshold:
        regime, conf = "trending", min((adx_l - adx_trend_threshold) / 50, 1.0)
    else:
        regime, conf = "ranging",  max(0, 1 - (adx_l - 15) / 10)
    return {"regime": regime, "adx": round(adx_l, 2), "atr_pct": round(atr_p, 3),
            "confidence": round(conf, 3), "trend_dir": tdir}


# ══════════════════════════════════════════════════════════════════════════════
#  Features ML
# ══════════════════════════════════════════════════════════════════════════════

def build_features(df: pl.DataFrame, window: int = 20) -> pl.DataFrame:
    """Features ML depuis OHLCV."""
    if len(df) < window + 10:
        return pl.DataFrame()
    close      = df["close"]
    adx_s, pdi, ndi = adx(df, 14)
    atr14      = atr(df, 14)
    ml, ms, mh = macd(close)
    bb_u, _, bb_l = bollinger(close)
    vr         = volume_ratio(df, 20)
    st_d, _    = supertrend(df)

    result = pl.DataFrame({
        "ret1":      close.pct_change(1),
        "ret5":      close.pct_change(5),
        "ret20":     close.pct_change(20),
        "rsi":       rsi(close, 14),
        "macd_h":    mh,
        "macd_hd":   mh.diff(1),
        "ema_ratio": ema(close, 10) / ema(close, 30).clip(lower_bound=1e-10),
        "adx":       adx_s,
        "pdi":       pdi,
        "ndi":       ndi,
        "atr_pct":   atr14 / close.clip(lower_bound=1e-10) * 100,
        "bb_width":  (bb_u - bb_l) / close.clip(lower_bound=1e-10),
        "vol_ratio": vr,
        "st_dir":    st_d,
    })
    return result.drop_nulls()


# ══════════════════════════════════════════════════════════════════════════════
#  Support / Résistance — détection via pivots swing
# ══════════════════════════════════════════════════════════════════════════════

def support_resistance_levels(
    df: pl.DataFrame,
    window: int = 5,
    cluster_pct: float = 0.005,
    min_touches: int = 1,
    max_levels: int = 6,
    lookback: int = 150,
) -> dict:
    """Détecte supports et résistances via pivots swing (hauts/bas locaux).

    Optimized: uses numpy argmax/argmin on rolling windows instead of Python loops.

    Args:
        window       : Fenêtre de chaque côté pour valider un pivot (barres)
        cluster_pct  : Distance relative max pour fusionner deux niveaux
        min_touches  : Force minimum pour retenir un niveau
        max_levels   : Nombre max de niveaux retournés par côté
        lookback     : Nombre de bougies analysées (les plus récentes)

    Returns:
        {"supports": [...], "resistances": [...]}
    """
    n = len(df)
    if n < window * 2 + 5:
        return {"supports": [], "resistances": []}

    start = max(0, n - lookback)
    high  = df["high"][start:].to_numpy()
    low   = df["low"][start:].to_numpy()
    close = df["close"][start:].to_numpy()
    m     = len(high)

    # Vectorized pivot detection using rolling max/min
    from numpy.lib.stride_tricks import sliding_window_view
    win_size = 2 * window + 1

    if m < win_size:
        return {"supports": [], "resistances": []}

    high_wins = sliding_window_view(high, win_size)
    low_wins  = sliding_window_view(low, win_size)

    # Pivot high: center element is the max of its window
    high_center = high[window:m - window]
    high_max    = high_wins.max(axis=1)
    res_mask    = high_center == high_max
    res_pivots  = high_center[res_mask].tolist()

    # Pivot low: center element is the min of its window
    low_center = low[window:m - window]
    low_min    = low_wins.min(axis=1)
    sup_mask   = low_center == low_min
    sup_pivots = low_center[sup_mask].tolist()

    def _cluster(prices: list[float], tol: float) -> list[tuple[float, int]]:
        if not prices:
            return []
        prices = sorted(prices)
        clusters: list[list[float]] = [[prices[0]]]
        for p in prices[1:]:
            ref = clusters[-1][0]
            if ref > 0 and (p - ref) / ref <= tol:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [(sum(c) / len(c), len(c)) for c in clusters]

    res_clusters = _cluster(res_pivots, cluster_pct)
    sup_clusters = _cluster(sup_pivots, cluster_pct)
    price_now    = float(close[-1])

    supports = sorted(
        [{"price": round(p, 8), "strength": t}
         for p, t in sup_clusters if p < price_now and t >= min_touches],
        key=lambda x: -x["price"],
    )[:max_levels]

    resistances = sorted(
        [{"price": round(p, 8), "strength": t}
         for p, t in res_clusters if p > price_now and t >= min_touches],
        key=lambda x: x["price"],
    )[:max_levels]

    return {"supports": supports, "resistances": resistances}


def nearest_support(price: float, levels: list) -> float | None:
    """Retourne le support le plus proche en-dessous du prix courant."""
    candidates = [lv["price"] for lv in levels if lv["price"] < price]
    return max(candidates) if candidates else None


def nearest_resistance(price: float, levels: list) -> float | None:
    """Retourne la résistance la plus proche au-dessus du prix courant."""
    candidates = [lv["price"] for lv in levels if lv["price"] > price]
    return min(candidates) if candidates else None


# ══════════════════════════════════════════════════════════════════════════════
#  Pré-calcul vectorisé — O(n) unique pour accélérer le backtest (~180×)
# ══════════════════════════════════════════════════════════════════════════════

def precompute_df(df: pl.DataFrame) -> pl.DataFrame:
    """Enrichit le df avec les colonnes _pre_* calculées une seule fois via Polars.
    Appelé par Backtester.run() AVANT la boucle barre-par-barre.
    Les stratégies lisent ces colonnes via pre_val() → O(1).

    Colonnes ajoutées :
      _pre_rsi14       RSI(14)
      _pre_atr14       ATR(14)  — EWM
      _pre_adx14       ADX(14)
      _pre_pdi14       +DI(14)
      _pre_ndi14       −DI(14)
      _pre_macd_line   MACD line (12,26,9)
      _pre_macd_sig    MACD signal
      _pre_macd_hist   MACD histogram
      _pre_volratio20  volume_ratio(20)
      _pre_ema20       EMA(20)
      _pre_ema50       EMA(50)
      _pre_ema200      EMA(200)

    Entièrement Polars — zéro round-trip numpy.
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # True Range — via _true_range (pur Polars, pl.max_horizontal)
    tr = _true_range(df)

    # RSI(14) — clip(lower_bound) pour division sécurisée (pas de pl.when)
    d         = c.diff(1)
    g         = d.clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False)
    dn        = (-d.clip(upper_bound=0)).ewm_mean(alpha=1 / 14, adjust=False)
    dn_safe   = dn.clip(lower_bound=1e-10)
    pre_rsi14 = 100 - 100 / (1 + g / dn_safe)

    # ATR(14)
    pre_atr14 = tr.ewm_mean(span=14, adjust=False)

    # ADX(14) — multiplication booléenne, clip pour division sécurisée
    up       = h.diff(1).clip(lower_bound=0)
    down     = (-l.diff(1)).clip(lower_bound=0)
    pdm      = up   * (up > down).cast(pl.Float64)
    ndm      = down * (down > up).cast(pl.Float64)
    atr_safe = pre_atr14.clip(lower_bound=1e-10)
    dip      = 100 * pdm.ewm_mean(span=14, adjust=False) / atr_safe
    dim      = 100 * ndm.ewm_mean(span=14, adjust=False) / atr_safe
    sum_di   = (dip + dim).clip(lower_bound=1e-10)
    dx       = 100 * (dip - dim).abs() / sum_di

    # MACD(12,26,9)
    ema_f = c.ewm_mean(span=12, adjust=False)
    ema_s = c.ewm_mean(span=26, adjust=False)
    ml    = ema_f - ema_s
    ms    = ml.ewm_mean(span=9, adjust=False)

    # volume_ratio(20) — clip pour division sécurisée
    vm     = v.rolling_mean(20)
    vm_safe = vm.clip(lower_bound=1e-9)

    # EMAs standard (20, 50, 200)
    pre_ema20  = c.ewm_mean(span=20,  adjust=False)
    pre_ema50  = c.ewm_mean(span=50,  adjust=False)
    pre_ema200 = c.ewm_mean(span=200, adjust=False)

    # SMAs (régime rapport V4 : SMA20/50/100/200)
    pre_sma20  = c.rolling_mean(20)
    pre_sma50  = c.rolling_mean(50)
    pre_sma100 = c.rolling_mean(100)
    pre_sma200 = c.rolling_mean(200)

    # ── Features scoring Opus (calculées une fois, lues en O(1) dans score()) ──
    c_safe = c.clip(lower_bound=1e-9)
    o      = df["open"]

    # ATR% + range + vol_std20 (volatilité)
    pre_atr_pct   = pre_atr14 / c_safe
    pre_range     = (h - l) / c_safe
    log_ret       = (c / c.shift(1).fill_null(c).clip(lower_bound=1e-9)).log(2.718281828)
    pre_volstd20  = log_ret.rolling_std(20).fill_null(0)

    # Ratios normalisés /mean100 → TF-indépendants (rapport §6.2, calibration)
    _rm100 = lambda s: s.rolling_mean(100).clip(lower_bound=1e-9)
    pre_atr_pct_r  = pre_atr_pct  / _rm100(pre_atr_pct)
    pre_range_r    = pre_range     / _rm100(pre_range)
    pre_volstd20_r = pre_volstd20  / _rm100(pre_volstd20)

    # Structure de bougie (rapport §6.3 — body #1 feature, wicks importants)
    t_range    = (h - l).clip(lower_bound=1e-9)
    body_top   = pl.max_horizontal(c, o)
    body_bot   = pl.min_horizontal(c, o)
    pre_body        = (c - o) / c_safe                          # direction corps signé
    pre_upper_wick  = (h - body_top) / t_range                  # mèche haute [0-1]
    pre_lower_wick  = (body_bot - l) / t_range                  # mèche basse [0-1]

    # Corps de bougie absolu (amplitude formula rapport §6.2 : poids 60/250)
    pre_body_abs   = (c - o).abs() / c_safe
    pre_body_abs_r = pre_body_abs / _rm100(pre_body_abs)

    # RSI velocity lag6 + lag12 + accélération (rapport §6.3 features direction)
    pre_rsi_vel6  = (pre_rsi14 - pre_rsi14.shift(6)).fill_null(0)
    pre_rsi_vel12 = (pre_rsi14 - pre_rsi14.shift(12)).fill_null(0)
    pre_rsi_accel = (pre_rsi_vel6 - pre_rsi_vel6.shift(6)).fill_null(0)

    # MACD hist velocity (rapport §6.3 : MACD_hist_d1, pas le niveau)
    macd_hist_full = ml - ms
    pre_macd_hist_d1 = (macd_hist_full - macd_hist_full.shift(1)).fill_null(0)

    # Position dans la range 20 barres [0-1] (rapport §6.3 : range_pos_20)
    roll_min20 = c.rolling_min(20)
    roll_max20 = c.rolling_max(20)
    pre_range_pos20 = ((c - roll_min20) / (roll_max20 - roll_min20).clip(lower_bound=1e-9)).fill_null(0.5)

    return df.with_columns([
        pre_rsi14.alias("_pre_rsi14"),
        pre_atr14.alias("_pre_atr14"),
        dx.ewm_mean(span=14, adjust=False).fill_null(0).alias("_pre_adx14"),
        dip.fill_null(0).alias("_pre_pdi14"),
        dim.fill_null(0).alias("_pre_ndi14"),
        ml.alias("_pre_macd_line"),
        ms.alias("_pre_macd_sig"),
        (ml - ms).alias("_pre_macd_hist"),
        (v / vm_safe).alias("_pre_volratio20"),
        pre_ema20.alias("_pre_ema20"),
        pre_ema50.alias("_pre_ema50"),
        pre_ema200.alias("_pre_ema200"),
        # SMAs régime
        pre_sma20.alias("_pre_sma20"),
        pre_sma50.alias("_pre_sma50"),
        pre_sma100.alias("_pre_sma100"),
        pre_sma200.alias("_pre_sma200"),
        # Features scoring Opus
        pre_atr_pct_r.alias("_pre_atr_pct_r"),
        pre_range_r.alias("_pre_range_r"),
        pre_volstd20_r.alias("_pre_volstd20_r"),
        pre_body.alias("_pre_body"),
        pre_body_abs_r.alias("_pre_body_abs_r"),
        pre_upper_wick.alias("_pre_upper_wick"),
        pre_lower_wick.alias("_pre_lower_wick"),
        pre_rsi_vel6.alias("_pre_rsi_vel6"),
        pre_rsi_vel12.alias("_pre_rsi_vel12"),
        pre_rsi_accel.alias("_pre_rsi_accel"),
        pre_macd_hist_d1.alias("_pre_macd_hist_d1"),
        pre_range_pos20.alias("_pre_range_pos20"),
    ])


# Alias pour la compatibilité avec l'ancien app/core/indicators.precompute
precompute = precompute_df


def pre_val(df: pl.DataFrame, col: str) -> float | None:
    """Lit la valeur pré-calculée à la dernière ligne si disponible.
    Retourne None si la colonne n'existe pas ou si la valeur est nulle.
    Logue un avertissement si la colonne n'existe pas (fallback vers recalcul on-demand).

    Usage :
        rsi_now = pre_val(df, "_pre_rsi14") or float(rsi(df["close"])[-1])
    """
    if col and col in df.columns:
        v = df[col][-1]
        if v is not None:
            return float(v)
    elif col:
        _log.debug(
            f"[indicators] Colonne pré-calculée '{col}' absente — "
            f"fallback on-demand (precompute_df() non appliqué ?)"
        )
    return None


def compute_v4_scoring_series(df: pl.DataFrame,
                              adx_threshold: float = 20.0) -> dict:
    """Calcule les séries de scores V4 (amplitude, direction, régime) pour le graphique.

    Basé sur les résultats du rapport V4 :
    - Amplitude (AUC 0.75) : heuristique GARCH (ATR, vol_std, range_size, BB_width)
    - Direction (AUC 0.87 en Trend Down) : RSI + structure bougie + MACD + régime
    - Régime 4 classes : 0=Range, 1=Trend Up, 2=Trend Down, 3=Choppy

    Returns dict with lists of {time, value} for: proba_amp, proba_dir, regime_v4
    """
    n = len(df)
    if n < 50:
        return {"proba_amp": [], "proba_dir": [], "regime_v4": []}

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    open_  = df["open"]
    vol    = df["volume"]
    times  = df["time"].dt.epoch(time_unit="s")

    # ── ATR(14) ─────────────────────────────────────────────────────────────
    tr    = _true_range(df)
    atr14 = tr.ewm_mean(span=14, adjust=False)
    atr_pct_s = atr14 / close.clip(lower_bound=1e-9)

    # ── Volatilité réalisée 20 bougies (std des log-retours) ─────────────
    log_ret    = (close / close.shift(1).clip(lower_bound=1e-9)).log(2.718281828)
    vol_std20_s = log_ret.rolling_std(20)

    # ── Range size = (H-L)/Close ─────────────────────────────────────────
    range_size_s = (high - low) / close.clip(lower_bound=1e-9)

    # ── Bollinger width = 4×std(20) / SMA(20) ───────────────────────────
    sma20_c = close.rolling_mean(20)
    std20_c = close.rolling_std(20)
    bb_width_s = (4.0 * std20_c) / sma20_c.clip(lower_bound=1e-9)

    # ── Ratios par moyenne mobile 100 (TF-indépendant) ────────────────────
    atr_pct_ratio_s    = atr_pct_s    / atr_pct_s.rolling_mean(100).clip(lower_bound=1e-9)
    vol_std20_ratio_s  = vol_std20_s  / vol_std20_s.rolling_mean(100).clip(lower_bound=1e-9)
    range_size_ratio_s = range_size_s / range_size_s.rolling_mean(100).clip(lower_bound=1e-9)
    bb_width_ratio_s   = bb_width_s   / bb_width_s.rolling_mean(100).clip(lower_bound=1e-9)

    # ── SMAs pour détection de régime ────────────────────────────────────
    sma20_s  = sma20_c
    sma50_s  = close.rolling_mean(50)
    sma100_s = close.rolling_mean(100)
    sma200_s = close.rolling_mean(200)

    # ── ADX(14) ──────────────────────────────────────────────────────────
    up    = high.diff(1).clip(lower_bound=0)
    down  = (-low.diff(1)).clip(lower_bound=0)
    pdm   = up   * (up > down).cast(pl.Float64)
    ndm   = down * (down > up).cast(pl.Float64)
    as_   = atr14.clip(lower_bound=1e-10)
    dip   = 100 * pdm.ewm_mean(span=14, adjust=False) / as_
    dim   = 100 * ndm.ewm_mean(span=14, adjust=False) / as_
    dx    = 100 * (dip - dim).abs() / (dip + dim).clip(lower_bound=1e-10)
    adx_s = dx.ewm_mean(span=14, adjust=False).fill_null(0)

    # ── RSI(14) ──────────────────────────────────────────────────────────
    d_c   = close.diff(1)
    g_c   = d_c.clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False)
    l_c   = (-d_c.clip(upper_bound=0)).ewm_mean(alpha=1 / 14, adjust=False)
    rsi_s = 100 - 100 / (1 + g_c / l_c.clip(lower_bound=1e-10))

    # ── MACD histogram normalisé ──────────────────────────────────────────
    ema12 = close.ewm_mean(span=12, adjust=False)
    ema26 = close.ewm_mean(span=26, adjust=False)
    ml    = ema12 - ema26
    ms    = ml.ewm_mean(span=9, adjust=False)
    macd_hist_norm_s = (ml - ms) / close.clip(lower_bound=1e-9)

    # ── Structure de bougie (numpy pour max/min element-wise) ─────────────
    import numpy as np
    close_arr = close.to_numpy().astype(np.float64)
    open_arr  = open_.to_numpy().astype(np.float64)
    high_arr  = high.to_numpy().astype(np.float64)
    low_arr   = low.to_numpy().astype(np.float64)
    vol_arr   = vol.to_numpy().astype(np.float64)
    times_arr = times.to_numpy()

    body_top  = np.maximum(close_arr, open_arr)
    body_bot  = np.minimum(close_arr, open_arr)
    t_range   = np.maximum(high_arr - low_arr, 1e-9)
    upper_wick_arr = (high_arr - body_top) / t_range
    lower_wick_arr = (body_bot - low_arr)  / t_range
    close_pos_arr  = (close_arr - low_arr) / t_range
    vol_ma_arr     = np.array(vol.rolling_mean(20).to_list(), dtype=np.float64)
    vol_ratio_arr  = vol_arr / np.maximum(vol_ma_arr, 1e-9)

    # ── Conversion en numpy ───────────────────────────────────────────────
    def _to_np(s): return s.to_numpy().astype(np.float64)
    atr_pct_arr      = _to_np(atr_pct_s)
    atr_ratio_arr    = np.nan_to_num(_to_np(atr_pct_ratio_s),    nan=1.0)
    vs_ratio_arr     = np.nan_to_num(_to_np(vol_std20_ratio_s),  nan=1.0)
    rs_ratio_arr     = np.nan_to_num(_to_np(range_size_ratio_s), nan=1.0)
    bb_ratio_arr     = np.nan_to_num(_to_np(bb_width_ratio_s),   nan=1.0)
    sma20_arr      = _to_np(sma20_s)
    sma50_arr      = _to_np(sma50_s)
    sma100_arr     = _to_np(sma100_s)
    sma200_arr     = _to_np(sma200_s)
    adx_arr        = _to_np(adx_s)
    rsi_arr        = np.nan_to_num(_to_np(rsi_s),        nan=50.0)
    macd_arr       = np.nan_to_num(_to_np(macd_hist_norm_s), nan=0.0)

    # ── Régime vectorisé ──────────────────────────────────────────────────
    regime_arr = np.full(n, 3, dtype=np.int8)   # Choppy par défaut
    range_mask    = adx_arr < adx_threshold
    trend_up_mask = (adx_arr >= adx_threshold) & (sma20_arr > sma50_arr) & (sma50_arr > sma100_arr) & (sma100_arr > sma200_arr)
    trend_dn_mask = (adx_arr >= adx_threshold) & (sma20_arr < sma50_arr) & (sma50_arr < sma100_arr) & (sma100_arr < sma200_arr)
    regime_arr[range_mask]    = 0
    regime_arr[trend_up_mask] = 1
    regime_arr[trend_dn_mask] = 2

    # ── Scores vectorisés ─────────────────────────────────────────────────
    def _sigmoid(z): return 1.0 / (1.0 + np.exp(-np.clip(z, -20, 20)))

    # Amplitude : heuristique GARCH normalisée par rolling 100 (TF-indépendant)
    # Formule identique à la stratégie scoring_statistique_opus._proba_amplitude
    z_amp = (-0.5
             + 1.5 * (atr_ratio_arr - 1.0)
             + 1.0 * (rs_ratio_arr  - 1.0)
             + 0.8 * (vs_ratio_arr  - 1.0)
             + 0.5 * (bb_ratio_arr  - 1.0))
    proba_amp_arr = _sigmoid(z_amp)

    # Direction : régime-conditionnel (rapport V4 §6.2-6.3)
    rsi_n  = (50.0 - rsi_arr) / 25.0          # [-2,+2] : oversold = positif
    wick_n = lower_wick_arr * 3.0 - upper_wick_arr * 2.0
    pos_n  = (0.5 - close_pos_arr) * 2.0      # [-1,+1] : clôture basse = haussier
    macd_n = np.clip(macd_arr * 500.0, -1.5, 1.5)
    vol_n  = np.clip(vol_ratio_arr - 1.0, 0, 1.0) * 0.3

    z_dir = np.zeros(n, dtype=np.float64)
    # Trend Down (régime 2) : AUC 0.87 dans le rapport — signaux RSI+wick forts
    m2 = regime_arr == 2
    z_dir[m2] = (-2.0                             # biais baissier de base
                 + rsi_n[m2]  * 2.5               # RSI oversold → rebond
                 + wick_n[m2] * 2.0               # mèche basse → absorption
                 + pos_n[m2]  * 1.0
                 + macd_n[m2] * 0.6
                 + vol_n[m2])
    # Range (régime 0) : AUC 0.50-0.70 — position dans range domine
    m0 = regime_arr == 0
    z_dir[m0] = (rsi_n[m0] * 0.8 + wick_n[m0] * 1.0
                 + pos_n[m0] * 1.5 + macd_n[m0] * 1.0 + vol_n[m0])
    # Choppy (régime 3) : AUC 0.58-0.60 — signal modeste
    m3 = regime_arr == 3
    z_dir[m3] = (rsi_n[m3] * 1.0 + wick_n[m3] * 1.5
                 + pos_n[m3] * 0.8 + macd_n[m3] * 1.2 + vol_n[m3])
    # Trend Up (régime 1) : AUC ~0.50 — signal faible
    m1 = regime_arr == 1
    z_dir[m1] = (rsi_n[m1] * 0.4 + wick_n[m1] * 0.4
                 + pos_n[m1] * 0.4 + macd_n[m1] * 0.4 + vol_n[m1])

    proba_dir_arr = _sigmoid(z_dir)

    # ── Masque de validité (besoin de SMA200 + rolling 100 sur ratios) ──
    valid = np.ones(n, dtype=bool)
    valid[:200] = False
    nan_mask = (np.isnan(atr_pct_arr) | np.isnan(sma200_arr)
                | np.isnan(adx_arr) | (atr_pct_arr <= 0)
                | np.isnan(atr_ratio_arr))
    valid[nan_mask] = False

    # ── Assemblage des séries de sortie ──────────────────────────────────
    proba_amp_out, proba_dir_out, regime_out = [], [], []
    for i in range(n):
        t = int(times_arr[i])
        if not valid[i]:
            continue
        proba_amp_out.append({"time": t, "value": round(float(proba_amp_arr[i]), 3)})
        proba_dir_out.append({"time": t, "value": round(float(proba_dir_arr[i]), 3)})
        regime_out.append(   {"time": t, "value": int(regime_arr[i])})

    return {
        "proba_amp": proba_amp_out,
        "proba_dir": proba_dir_out,
        "regime_v4": regime_out,
    }


def detect_regime(df: pl.DataFrame, adx_threshold: float = 25.0) -> str:
    """
    Détecte le régime de marché à partir de l'ADX(14).

    Retourne ``"trend"`` si ADX >= adx_threshold, ``"range"`` sinon,
    ou ``"unknown"`` si le DataFrame est trop court.

    For detailed regime detection (trending/ranging/volatile with confidence),
    use detect_regime_full() instead.

    Utilisé par SignalPipeline (ML blending) et MarketScanner (screen/UI).
    Extrait ici pour éviter que SignalPipeline dépende de MarketScanner.
    """
    if len(df) < 30:
        return "unknown"
    adx = adx_val(df, 14)
    return "trend" if adx >= adx_threshold else "range"


# ══════════════════════════════════════════════════════════════════════════════
#  Primitives glissantes génériques (numpy fallback)
#  Réutilisables par n'importe quelle stratégie.
# ══════════════════════════════════════════════════════════════════════════════

def bars_since_cross(s_fast: pl.Series, s_slow: pl.Series) -> pl.Series:
    """Bougies écoulées depuis le dernier croisement signé de ``s_fast`` et ``s_slow``.

    Convention : valeur positive ``+n`` = ``s_fast`` au-dessus depuis n barres,
    négative ``-n`` = en-dessous. ``NaN`` tant qu'aucun croisement n'a eu lieu.
    """
    sf = s_fast.to_numpy()
    ss = s_slow.to_numpy()
    n  = len(sf)
    above = (sf > ss).astype(np.int8)
    diff  = np.zeros(n, dtype=np.int8)
    if n > 1:
        diff[1:] = above[1:] - above[:-1]
    out = np.full(n, np.nan, dtype=np.float64)
    last_idx, last_dir = -1, 0
    for i in range(n):
        if diff[i] != 0:
            last_idx, last_dir = i, int(diff[i])
        if last_idx >= 0:
            out[i] = last_dir * (i - last_idx)
    return pl.Series(out)


def rolling_slope(s: pl.Series, window: int) -> pl.Series:
    """Pente d'ordre 1 (``np.polyfit``) sur fenêtre glissante.

    Retourne ``NaN`` pour les ``window-1`` premières barres.
    """
    arr = s.to_numpy()
    n   = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if window <= 1 or n < window:
        return pl.Series(out)
    x = np.arange(window, dtype=np.float64)
    x_mean = x.mean()
    x_dev  = x - x_mean
    denom  = float((x_dev * x_dev).sum())
    if denom == 0:
        return pl.Series(out)
    for i in range(window - 1, n):
        y = arr[i - window + 1 : i + 1]
        if np.any(np.isnan(y)):
            continue
        # pente = cov(x,y)/var(x) — équivalent à np.polyfit(x, y, 1)[0]
        out[i] = float(((y - y.mean()) * x_dev).sum()) / denom
    return pl.Series(out)


def hurst_exponent(arr: np.ndarray, max_lag: int = 20) -> float:
    """Exposant de Hurst par méthode R/S (scalaire).

    Utilise les écarts-types des différences décalées (``arr[lag:] - arr[:-lag]``)
    et ajuste une droite log-log pour estimer 2H. NaN si données insuffisantes.
    """
    a = arr[~np.isnan(arr)]
    if len(a) < max_lag + 5:
        return np.nan
    tau = []
    for lag in range(2, max_lag):
        d = a[lag:] - a[:-lag]
        if len(d) < 2 or np.std(d) == 0:
            continue
        tau.append(np.sqrt(np.std(d)))
    if len(tau) < 5:
        return np.nan
    try:
        poly = np.polyfit(np.log(np.arange(2, 2 + len(tau))), np.log(tau), 1)
        return float(poly[0] * 2.0)
    except Exception:
        return np.nan


def rolling_hurst(s: pl.Series, window: int = 100, max_lag: int = 20) -> pl.Series:
    """Exposant de Hurst glissant — appelle ``hurst_exponent`` sur chaque fenêtre."""
    arr = s.to_numpy()
    n   = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        out[i] = hurst_exponent(arr[i - window + 1 : i + 1], max_lag=max_lag)
    return pl.Series(out)


def rolling_rank_pct(s: pl.Series, window: int) -> pl.Series:
    """Rang percentile glissant (méthode ``average``).

    Équivalent ``pd.Series.rolling(window).rank(pct=True)``.
    Pour chaque barre, calcule la position de la valeur courante dans les
    ``window`` dernières valeurs et la divise par ``window``.
    """
    arr = s.to_numpy()
    n   = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        w = arr[i - window + 1 : i + 1]
        if np.any(np.isnan(w)):
            continue
        last = w[-1]
        rank_avg = (np.sum(w < last) + np.sum(w <= last) + 1) / 2.0
        out[i] = rank_avg / window
    return pl.Series(out)

