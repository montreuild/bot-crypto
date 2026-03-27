"""Indicateurs techniques — source unique pour stratégies et moteur. Basé sur Polars."""
import numpy as np
import polars as pl
from typing import Tuple


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
    """
    # TR et ATR via Polars (pas de round-trip sur cette partie)
    atr_arr = _true_range(df).ewm_mean(span=period, adjust=False).to_numpy()

    high  = df["high"].to_numpy()
    low   = df["low"].to_numpy()
    close = df["close"].to_numpy()
    n     = len(close)

    hl2         = (high + low) / 2.0
    upper_basic = hl2 + mult * atr_arr
    lower_basic = hl2 - mult * atr_arr

    upper  = upper_basic.copy()
    lower  = lower_basic.copy()
    st     = np.empty(n)
    dir_   = np.ones(n, dtype=np.int8)
    st[0]  = lower[0]

    for i in range(1, n):
        upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1]
                                       or close[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1]
                                       or close[i - 1] < lower[i - 1]) else lower[i - 1]
        if dir_[i - 1] == 1:
            dir_[i] = 1 if close[i] >= lower[i] else -1
        else:
            dir_[i] = -1 if close[i] <= upper[i] else 1
        st[i] = lower[i] if dir_[i] == 1 else upper[i]

    return pl.Series(dir_.astype(float)), pl.Series(st)


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

def detect_regime(df: pl.DataFrame) -> dict:
    """Classifie le marché : trending | ranging | volatile."""
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
    if atr_p > 3.0:
        regime, conf = "volatile", min(atr_p / 5, 1.0)
    elif adx_l >= 25:
        regime, conf = "trending", min((adx_l - 25) / 50, 1.0)
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

    Un pivot haut (résistance candidate) : high[i] = max(high[i-w:i+w+1])
    Un pivot bas  (support candidate)    : low[i]  = min(low[i-w:i+w+1])

    Les pivots proches (< cluster_pct × prix) sont fusionnés en une seule zone.
    La "force" d'un niveau = nombre de pivots fusionnés (nombre de touches).

    Args:
        window       : Fenêtre de chaque côté pour valider un pivot (barres)
        cluster_pct  : Distance relative max pour fusionner deux niveaux (ex: 0.005 = 0.5 %)
        min_touches  : Force minimum pour retenir un niveau
        max_levels   : Nombre max de niveaux retournés par côté
        lookback     : Nombre de bougies analysées (les plus récentes)

    Returns:
        {
            "supports":    [{"price": float, "strength": int}, ...],
            "resistances": [{"price": float, "strength": int}, ...],
        }
    """
    n = len(df)
    if n < window * 2 + 5:
        return {"supports": [], "resistances": []}

    start = max(0, n - lookback)
    high  = df["high"][start:].to_numpy()
    low   = df["low"][start:].to_numpy()
    close = df["close"][start:].to_numpy()
    m     = len(high)

    res_pivots: list[float] = []
    for i in range(window, m - window):
        if high[i] == max(high[i - window:i + window + 1]):
            res_pivots.append(float(high[i]))

    sup_pivots: list[float] = []
    for i in range(window, m - window):
        if low[i] == min(low[i - window:i + window + 1]):
            sup_pivots.append(float(low[i]))

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
    ])


# Alias pour la compatibilité avec l'ancien app/core/indicators.precompute
precompute = precompute_df


def pre_val(df: pl.DataFrame, col: str) -> float | None:
    """Lit la valeur pré-calculée à la dernière ligne si disponible.
    Retourne None si la colonne n'existe pas ou si la valeur est nulle.

    Usage :
        rsi_now = pre_val(df, "_pre_rsi14") or float(rsi(df["close"])[-1])
    """
    if col in df.columns:
        v = df[col][-1]
        if v is not None:
            return float(v)
    return None


def detect_regime(df: pl.DataFrame, adx_threshold: float = 25.0) -> str:
    """
    Détecte le régime de marché à partir de l'ADX(14).

    Retourne ``"trend"`` si ADX >= adx_threshold, ``"range"`` sinon,
    ou ``"unknown"`` si le DataFrame est trop court.

    Utilisé par SignalPipeline (ML blending) et MarketScanner (screen/UI).
    Extrait ici pour éviter que SignalPipeline dépende de MarketScanner.
    """
    if len(df) < 30:
        return "unknown"
    adx = adx_val(df, 14)
    return "trend" if adx >= adx_threshold else "range"
