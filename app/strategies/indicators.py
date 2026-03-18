"""
Module d'indicateurs partagé — utilisé par toutes les stratégies.
Centralise RSI, ADX, ATR, MACD, SuperTrend, structure de marché.
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
    # fill_null : la première barre n'a pas de clôture précédente → on utilise
    # la clôture courante (tr[0] = high[0] - low[0], sans distorsion).
    c_prev = df["close"].shift(1).fill_null(df["close"][0])
    return pl.Series(np.maximum(
        (h - l).to_numpy(),
        np.maximum((h - c_prev).abs().to_numpy(), (l - c_prev).abs().to_numpy()),
    ))


def rsi(close: pl.Series, period: int = 14) -> pl.Series:
    d     = close.diff(1)
    g_np  = d.clip(lower_bound=0).ewm_mean(alpha=1 / period, adjust=False).to_numpy()
    dn_np = (-d.clip(upper_bound=0)).ewm_mean(alpha=1 / period, adjust=False).to_numpy()
    # Division sécurisée via numpy pour éviter qu'un pl.when retourne un Expr
    dn_safe = np.where(dn_np == 0, 1e-10, dn_np)
    return pl.Series(100 - 100 / (1 + g_np / dn_safe))


def atr(df: pl.DataFrame, period: int = 14) -> float:
    tr = _true_range(df)
    v = tr.ewm_mean(span=period, adjust=False)[-1]
    return float(v) if v is not None and float(v) > 0 else 0.0


def atr_series(df: pl.DataFrame, period: int = 14) -> pl.Series:
    return _true_range(df).ewm_mean(span=period, adjust=False)


def adx(df: pl.DataFrame, period: int = 14) -> float:
    if len(df) < period * 2:
        return 0.0
    h    = df["high"].to_numpy()
    l    = df["low"].to_numpy()
    tr   = _true_range(df).to_numpy()

    up   = np.maximum(np.diff(h, prepend=h[0]), 0)
    down = np.maximum(-np.diff(l, prepend=l[0]), 0)
    pdm  = np.where(up > down, up, 0.0)
    mdm  = np.where(down > up, down, 0.0)

    atr_s = pl.Series(tr).ewm_mean(span=period, adjust=False).to_numpy()
    atr_s = np.where(atr_s == 0, 1e-10, atr_s)

    dip   = 100 * pl.Series(pdm).ewm_mean(span=period, adjust=False).to_numpy() / atr_s
    dim   = 100 * pl.Series(mdm).ewm_mean(span=period, adjust=False).to_numpy() / atr_s
    sum_d = dip + dim
    sum_d = np.where(sum_d == 0, 1e-10, sum_d)
    dx    = 100 * np.abs(dip - dim) / sum_d

    v = pl.Series(dx).ewm_mean(span=period, adjust=False)[-1]
    return float(v) if v is not None and not np.isnan(float(v)) else 0.0


def macd(close: pl.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[pl.Series, pl.Series, pl.Series]:
    """Retourne (macd_line, signal_line, histogram)."""
    ema_f = close.ewm_mean(span=fast, adjust=False)
    ema_s = close.ewm_mean(span=slow, adjust=False)
    line = ema_f - ema_s
    sig = line.ewm_mean(span=signal, adjust=False)
    return line, sig, line - sig


def supertrend(df: pl.DataFrame, period: int = 10,
               mult: float = 3.0) -> Tuple[pl.Series, pl.Series]:
    """
    Retourne (direction: +1/-1, st_line).
    Implémentation vectorisée numpy : boucle séquentielle inévitable
    (upper[i] dépend de upper[i-1]).
    """
    high  = df["high"].to_numpy()
    low   = df["low"].to_numpy()
    close = df["close"].to_numpy()
    n     = len(close)

    prev_close     = np.empty(n)
    prev_close[0]  = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    atr_arr    = np.empty(n)
    atr_arr[0] = tr[0]
    alpha      = 2.0 / (period + 1)
    for i in range(1, n):
        atr_arr[i] = atr_arr[i - 1] * (1 - alpha) + tr[i] * alpha

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


def bb_squeeze(close: pl.Series, lookback: int = 15,
               bb_period: int = 20, quantile: float = 0.30) -> bool:
    if len(close) < bb_period + lookback:
        return False
    sma      = close.rolling_mean(bb_period)
    std      = close.rolling_std(bb_period)
    # clip évite pl.when qui retourne un Expr (non subscriptable)
    sma_safe = sma.clip(lower_bound=1e-9)
    width    = 4 * std / sma_safe
    cur_w    = width[-1]
    if cur_w is None:
        return False
    past = width[-(lookback + 1):-1].drop_nulls()
    return len(past) >= 5 and float(cur_w) <= float(past.quantile(quantile))


def market_structure(high: pl.Series, low: pl.Series,
                     n_pivots: int = 4, window: int = 5) -> int:
    """
    +1 = HH/HL (uptrend), -1 = LL/LH (downtrend), 0 = neutral.
    """
    if len(high) < n_pivots * window * 2:
        return 0
    highs = [float(high[-i * window - 1:-i * window + window - 1].max()) for i in range(1, n_pivots + 1)]
    lows  = [float(low[-i * window - 1:-i * window + window - 1].min())  for i in range(1, n_pivots + 1)]
    hh = sum(1 for i in range(len(highs) - 1) if highs[i] > highs[i + 1])
    hl = sum(1 for i in range(len(lows) - 1)  if lows[i]  > lows[i + 1])
    ll = sum(1 for i in range(len(highs) - 1) if highs[i] < highs[i + 1])
    lh = sum(1 for i in range(len(lows) - 1)  if lows[i]  < lows[i + 1])
    if (hh + hl) >= n_pivots - 1: return 1
    if (ll + lh) >= n_pivots - 1: return -1
    return 0


def vol_ratio(df: pl.DataFrame, period: int = 20) -> float:
    avg = df["volume"].rolling_mean(period)[-1]
    now = float(df["volume"][-1])
    return now / max(float(avg) if avg is not None else 1e-9, 1e-9)


def htf_trend(df_htf, ema_period: int = 50) -> int:
    """
    Tendance du timeframe supérieur : +1 haussier, -1 baissier, 0 neutre.
    """
    if df_htf is None or len(df_htf) < ema_period + 3:
        return 0
    c   = df_htf["close"]
    ema = c.ewm_mean(span=ema_period, adjust=False)
    price_above = float(c[-1]) > float(ema[-1])
    slope_up    = float(ema[-1]) > float(ema[-4])
    if price_above and slope_up:        return 1
    if not price_above and not slope_up: return -1
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Pré-calcul vectorisé — O(n) unique pour accélérer le backtest (~180×)
# ══════════════════════════════════════════════════════════════════════════════

def precompute_df(df: pl.DataFrame) -> pl.DataFrame:
    """
    Enrichit le df avec les colonnes _pre_* calculées une seule fois via Polars.
    Appelé par Backtester.run() AVANT la boucle barre-par-barre.
    Les stratégies lisent ces colonnes via pre_val() → O(1).

    Indicateurs couverts :
      _pre_rsi14       RSI(14)
      _pre_atr14       ATR(14)  — EWM
      _pre_adx14       ADX(14)
      _pre_pdi14       +DI(14)
      _pre_ndi14       −DI(14)
      _pre_macd_line   MACD line (12,26,9)
      _pre_macd_sig    MACD signal
      _pre_macd_hist   MACD histogram
      _pre_volratio20  volume_ratio(20)
    """
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # True Range (réutilisé pour ATR et ADX)
    c_prev = c.shift(1).fill_null(c[0])
    tr = pl.Series(np.maximum(
        (h - l).to_numpy(),
        np.maximum((h - c_prev).abs().to_numpy(), (l - c_prev).abs().to_numpy()),
    ))

    # RSI(14)
    d  = c.diff(1)
    g  = d.clip(lower_bound=0).ewm_mean(alpha=1 / 14, adjust=False)
    dn = (-d.clip(upper_bound=0)).ewm_mean(alpha=1 / 14, adjust=False)
    dn_safe = pl.when(dn == 0).then(1e-10).otherwise(dn)
    pre_rsi14 = 100 - 100 / (1 + g / dn_safe)

    # ATR(14)
    pre_atr14 = tr.ewm_mean(span=14, adjust=False)

    # ADX(14)
    up   = h.diff(1).clip(lower_bound=0)
    down = (-l.diff(1)).clip(lower_bound=0)
    pdm  = pl.when(up > down).then(up).otherwise(0.0)
    mdm  = pl.when(down > up).then(down).otherwise(0.0)
    atr_safe = pl.when(pre_atr14 == 0).then(None).otherwise(pre_atr14)
    dip  = 100 * pdm.ewm_mean(span=14, adjust=False) / atr_safe
    dim  = 100 * mdm.ewm_mean(span=14, adjust=False) / atr_safe
    dip_plus_dim = dip + dim
    dip_plus_dim_safe = pl.when(dip_plus_dim == 0).then(None).otherwise(dip_plus_dim)
    dx   = 100 * (dip - dim).abs() / dip_plus_dim_safe
    pre_adx14 = dx.ewm_mean(span=14, adjust=False).fill_null(0)
    pre_pdi14 = dip.fill_null(0)
    pre_ndi14 = dim.fill_null(0)

    # MACD(12,26,9)
    ema_f = c.ewm_mean(span=12, adjust=False)
    ema_s = c.ewm_mean(span=26, adjust=False)
    ml    = ema_f - ema_s
    ms    = ml.ewm_mean(span=9, adjust=False)
    pre_macd_line = ml
    pre_macd_sig  = ms
    pre_macd_hist = ml - ms

    # volume_ratio(20)
    vm = v.rolling_mean(20)
    vm_safe = pl.when(vm < 1e-9).then(1e-9).otherwise(vm)
    pre_volratio20 = v / vm_safe

    return df.with_columns([
        pre_rsi14.alias("_pre_rsi14"),
        pre_atr14.alias("_pre_atr14"),
        pre_adx14.alias("_pre_adx14"),
        pre_pdi14.alias("_pre_pdi14"),
        pre_ndi14.alias("_pre_ndi14"),
        pre_macd_line.alias("_pre_macd_line"),
        pre_macd_sig.alias("_pre_macd_sig"),
        pre_macd_hist.alias("_pre_macd_hist"),
        pre_volratio20.alias("_pre_volratio20"),
    ])


def pre_val(df: pl.DataFrame, col: str) -> float:
    """
    Lit la valeur pré-calculée à la dernière ligne si disponible.
    Retourne None si la colonne n'existe pas ou si la valeur est nulle.
    Usage :
        rsi_now = pre_val(df, "_pre_rsi14") or float(rsi(df["close"])[-1])
    """
    if col in df.columns:
        v = df[col][-1]
        if v is not None:
            return float(v)
    return None


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
    """
    Détecte supports et résistances via pivots swing (hauts/bas locaux).

    Un pivot haut (résistance candidate) : high[i] = max(high[i-w:i+w+1])
    Un pivot bas  (support candidate)    : low[i]  = min(low[i-w:i+w+1])

    Les pivots proches (< cluster_pct × prix) sont fusionnés en une seule zone.
    La "force" d'un niveau = nombre de pivots fusionnés (nombre de touches).

    Args:
        window       : Fenêtre de chaque côté pour valider un pivot (barres)
        cluster_pct  : Distance relative max pour fusionner deux niveaux (ex: 0.005 = 0.5%)
        min_touches  : Force minimum pour retenir un niveau
        max_levels   : Nombre max de niveaux retournés par côté
        lookback     : Nombre de bougies analysées (les plus récentes)

    Returns:
        {
            "supports":    [{"price": float, "strength": int}, ...],  # triés décroissant (plus proche en premier)
            "resistances": [{"price": float, "strength": int}, ...],  # triés croissant
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

    # Pivots hauts → résistances potentielles
    res_pivots: list[float] = []
    for i in range(window, m - window):
        if high[i] == max(high[i - window:i + window + 1]):
            res_pivots.append(float(high[i]))

    # Pivots bas → supports potentiels
    sup_pivots: list[float] = []
    for i in range(window, m - window):
        if low[i] == min(low[i - window:i + window + 1]):
            sup_pivots.append(float(low[i]))

    def _cluster(prices: list[float], tol: float) -> list[tuple[float, int]]:
        """Groupe les prix proches et retourne (prix_moyen, nombre_de_touches)."""
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

    price_now = float(close[-1])

    # Supports : en-dessous du prix courant, triés décroissant (plus proche en premier)
    supports = sorted(
        [{"price": round(p, 8), "strength": t} for p, t in sup_clusters
         if p < price_now and t >= min_touches],
        key=lambda x: -x["price"]
    )[:max_levels]

    # Résistances : au-dessus du prix courant, triées croissant (plus proche en premier)
    resistances = sorted(
        [{"price": round(p, 8), "strength": t} for p, t in res_clusters
         if p > price_now and t >= min_touches],
        key=lambda x: x["price"]
    )[:max_levels]

    return {"supports": supports, "resistances": resistances}


def nearest_support(price: float, levels: list) -> float | None:
    """Retourne le support le plus proche en-dessous du prix courant."""
    candidates = [l["price"] for l in levels if l["price"] < price]
    return max(candidates) if candidates else None


def nearest_resistance(price: float, levels: list) -> float | None:
    """Retourne la résistance la plus proche au-dessus du prix courant."""
    candidates = [l["price"] for l in levels if l["price"] > price]
    return min(candidates) if candidates else None


# ══════════════════════════════════════════════════════════════════════════════
#  Stochastique (K%, D%)
# ══════════════════════════════════════════════════════════════════════════════

def stochastic(df: pl.DataFrame, k_period: int = 14, d_period: int = 3) -> tuple:
    """
    Oscillateur Stochastique — K% et D%.

    K% = (Close - LowestLow_k) / (HighestHigh_k - LowestLow_k) × 100
    D% = SMA(K%, d_period)

    Retourne (K_val, D_val) — scalaires flottants.
    Retourne (50.0, 50.0) si données insuffisantes.
    """
    if len(df) < k_period + d_period:
        return 50.0, 50.0

    close         = df["close"]
    high          = df["high"]
    low           = df["low"]

    lowest_low    = low.rolling_min(k_period)
    highest_high  = high.rolling_max(k_period)
    hl_range      = highest_high - lowest_low
    # clip évite la division par zéro sans créer d'Expr lazy (pl.when retournerait un Expr)
    hl_safe       = hl_range.clip(lower_bound=1e-10)

    k_series = (close - lowest_low) / hl_safe * 100.0
    d_series = k_series.rolling_mean(d_period)

    k_val = float(k_series[-1]) if k_series[-1] is not None else 50.0
    d_val = float(d_series[-1]) if d_series[-1] is not None else 50.0

    # Clip to [0, 100] — guard against float precision drift
    k_val = max(0.0, min(100.0, k_val))
    d_val = max(0.0, min(100.0, d_val))
    return k_val, d_val
