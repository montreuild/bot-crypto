"""Structure de marché, régimes, niveaux S/R et statistiques roulantes.

Extrait de ``indicators.py`` (découpage V13).
"""
import logging
import warnings

import numpy as np
import polars as pl

from app.core.indicators_core import (
    adx,
    adx_val,
    atr,
    atr_val,
    bollinger,
    ema,
    macd,
    rsi,
    sma,
    supertrend,
    volume_ratio,
)

_log = logging.getLogger(__name__)

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


def htf_trend(df_htf, ema_period: int = 50, *, df_ltf=None,
              mult: int = 4, full_df=None, cache: dict | None = None) -> int:
    """Tendance du timeframe supérieur : +1 haussier, −1 baissier, 0 neutre.

    ⚠ **Parité backtest ↔ live (L5).** ``df_htf`` n'est fourni QUE par le live
    (`app/live/signal_pipeline.py`) ; le backtest ne le passe jamais. Sans
    repli, cette fonction renvoyait donc 0 en backtest et une vraie tendance en
    live : le filtre HTF de neuf stratégies était **inerte en simulation et
    actif en production**, ce qu'aucun test ne signalait.

    ``df_ltf`` active le repli : le HTF est reconstruit par rééchantillonnage
    causal du timeframe de base (mêmes buckets horloge que
    ``smc_sessions._htf_buckets``, donc seuls les buckets ENTIÈREMENT clôturés
    sont vus). Les appelants doivent le passer systématiquement.

    ``full_df``/``cache`` (PERF) : ce repli reconstruit TOUTE l'agrégation HTF
    à chaque appel — O(n) par barre, donc O(n²) sur un backtest, et c'est ce
    qui domine le profil des stratégies qui l'appellent (mesuré : 82 % du temps
    de ``breakout``). Quand ``df_ltf`` est un préfixe causal de ``full_df``, la
    série complète est calculée UNE fois (mémoïsée dans ``cache``, détenu par
    l'instance stratégie) puis indexée en O(1) — même idiome que
    :func:`app.core.indicators_causal.ema_window` / ``supertrend_last``.
    Résultat strictement identique (cf. tests/test_htf_trend_causal.py) ; le
    repli barre à barre reste le chemin par défaut, y compris en live.
    """
    if df_htf is None and df_ltf is not None:
        if cache is not None and full_df is not None:
            from app.core.indicators_causal import (
                _causal_prefix_index,
                htf_trend_ema_series,
            )
            pos = _causal_prefix_index(df_ltf, full_df)
            if pos is not None:
                key = (id(full_df), full_df.height, int(ema_period), int(mult))
                if key in cache:
                    arr = cache[key]
                else:
                    if len(cache) > 8:      # garde-fou mémoire
                        cache.clear()
                    # ``None`` mémoïsé aussi : sur une grille irrégulière la
                    # série vectorisée n'est pas démontrable, inutile de
                    # repayer la vérification à chaque barre.
                    arr = htf_trend_ema_series(full_df, ema_period, mult)
                    cache[key] = arr
                if arr is not None:
                    return int(arr[pos])
                fb_key = (id(full_df), full_df.height, int(ema_period), int(mult), "fb")
                if fb_key not in cache:
                    from app.core.smc_sessions import _htf_buckets
                    htf_df_f, idx_f, _, _ = _htf_buckets(full_df, None, mult)
                    if htf_df_f is None or len(htf_df_f) < ema_period + 3:
                        cache[fb_key] = (None, None)
                    else:
                        c_h = htf_df_f["close"]
                        ema_h = c_h.ewm_mean(span=ema_period, adjust=False)
                        n_h = len(htf_df_f)
                        arr_h = [0] * n_h
                        for j in range(ema_period + 2, n_h):
                            pa = float(c_h[j]) > float(ema_h[j])
                            su = float(ema_h[j]) > float(ema_h[j - 3])
                            arr_h[j] = 1 if (pa and su) else (-1 if (not pa and not su) else 0)
                        cache[fb_key] = (arr_h, idx_f)
                arr_h, idx_f = cache[fb_key]
                if arr_h is not None and idx_f is not None and pos < len(idx_f):
                    hi = int(idx_f[pos])
                    if 0 <= hi < len(arr_h):
                        return int(arr_h[hi])
        from app.core.smc_sessions import _htf_buckets
        htf_df, idx, _, _ = _htf_buckets(df_ltf, None, mult)
        if htf_df is None or idx[-1] < 0:
            return 0
        # Seuls les buckets clos à la dernière barre LTF sont connaissables.
        df_htf = htf_df.head(int(idx[-1]) + 1)
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

    Vectorisé : ``pente = cov(x,y)/var(x)`` avec ``x = arange(window)`` FIXE
    (indépendant de la position de la fenêtre) et ``x`` centré
    (``sum(x_dev) == 0``) — le terme ``y.mean()`` de la formule de covariance
    s'annule, ce qui réduit le calcul à une corrélation de ``y`` par le noyau
    fixe ``x_dev`` (``np.correlate``, O(n·window) en C plutôt qu'en boucle
    Python). Un NaN dans une fenêtre se propage naturellement à travers la
    somme et produit NaN en sortie pour cette position — même comportement
    que la version scalaire.
    """
    arr = s.to_numpy().astype(np.float64)
    n   = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if window <= 1 or n < window:
        return pl.Series(out)
    x = np.arange(window, dtype=np.float64)
    x_dev  = x - x.mean()
    denom  = float((x_dev * x_dev).sum())
    if denom == 0:
        return pl.Series(out)
    out[window - 1:] = np.correlate(arr, x_dev, mode="valid") / denom
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
    """Exposant de Hurst glissant — ``hurst_exponent`` appliqué à chaque fenêtre.

    Vectorisé sur toutes les fenêtres à la fois (``sliding_window_view`` +
    régression log-log en forme fermée ``cov/var``, comme ``rolling_slope``,
    appliquée par ligne via des réductions ``nan*`` pour tolérer un nombre de
    lags valides différent d'une fenêtre à l'autre — pas de boucle Python sur
    les ``n - window + 1`` positions, seulement sur les ``max_lag - 2`` lags
    (petite constante). Les fenêtres contenant un NaN en entrée retombent sur
    ``hurst_exponent`` scalaire (compaction ``arr[~isnan]`` avant calcul,
    impossible à vectoriser sans casser l'alignement entre fenêtres) — cas
    rare en pratique (essentiellement le warmup en tête de série), donc sans
    impact mesurable sur le coût total.
    """
    arr = s.to_numpy().astype(np.float64)
    n   = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 2 or n < window:
        return pl.Series(out)

    windows = np.lib.stride_tricks.sliding_window_view(arr, window)  # (n-window+1, window)
    has_nan = np.isnan(windows).any(axis=1)

    lags = np.arange(2, max_lag)
    n_win = windows.shape[0]
    if len(lags) == 0:
        return pl.Series(out)

    log_tau = np.full((n_win, len(lags)), np.nan, dtype=np.float64)
    for li, lag in enumerate(lags):
        if window - lag < 2:
            continue
        d = windows[:, lag:] - windows[:, :-lag]
        with np.errstate(invalid="ignore"):
            std_d = d.std(axis=1)
        valid = std_d > 0
        with np.errstate(divide="ignore"):
            log_tau[valid, li] = 0.5 * np.log(std_d[valid])  # log(sqrt(x)) = 0.5*log(x)

    log_lags = np.log(lags.astype(np.float64))
    xb = np.broadcast_to(log_lags, log_tau.shape).astype(np.float64).copy()
    xb[np.isnan(log_tau)] = np.nan
    valid_count = np.sum(~np.isnan(log_tau), axis=1)

    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        # "Mean of empty slice" attendu pour les fenêtres à 0 lag valide
        # (nanmean sur une ligne intégralement NaN) — résultat NaN correct,
        # filtré ensuite par valid_count >= 5.
        warnings.filterwarnings("ignore", message="Mean of empty slice")
        x_mean = np.nanmean(xb, axis=1)
        y_mean = np.nanmean(log_tau, axis=1)
        x_dev  = xb - x_mean[:, None]
        y_dev  = log_tau - y_mean[:, None]
        cov = np.nansum(x_dev * y_dev, axis=1)
        var = np.nansum(x_dev * x_dev, axis=1)
        slope = np.where(var > 0, cov / var, np.nan)

    vectorized = np.where(valid_count >= 5, 2.0 * slope, np.nan)
    vectorized[has_nan] = np.nan  # recalculées ci-dessous par le repli scalaire

    result = vectorized
    for idx in np.nonzero(has_nan)[0]:
        result[idx] = hurst_exponent(windows[idx], max_lag=max_lag)

    out[window - 1:] = result
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


def bearish_excess_series(df: pl.DataFrame, rsi_period: int = 14,
                          rsi_threshold: float = 38.0,
                          sma_period: int = 20,
                          price_dev_pct: float = 1.5) -> pl.Series:
    """Excès baissier vectorisé — True si au moins une condition est remplie :
      1. 2+ bougies rouges consécutives (close < open sur les 2 dernières barres)
      2. RSI(rsi_period) < rsi_threshold
      3. Prix > price_dev_pct% en-dessous de SMA(sma_period)
    Conçu pour le scanner batch (vectorisé, zéro boucle Python).
    """
    close = df["close"]
    open_ = df["open"]
    red = (close < open_).cast(pl.Int8)
    red_prev = red.shift(1).fill_null(0)
    consec_red = (red & red_prev).cast(pl.Boolean)
    rsi_s = rsi(close, rsi_period)
    rsi_excess = rsi_s < rsi_threshold
    sma_s = sma(close, sma_period)
    sma_safe = sma_s.clip(lower_bound=1e-9)
    price_below = close < sma_safe * (1.0 - price_dev_pct / 100.0)
    return (consec_red | rsi_excess | price_below).fill_null(False)



