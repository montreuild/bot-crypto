"""Indicateurs techniques de base (EMA/RSI/MACD/BB/ATR/ADX/volume/SuperTrend…).

Extrait de ``indicators.py`` (découpage V13). Importer de préférence via la
façade ``app.core.indicators`` qui ré-exporte tous les noms.
"""
import logging
from typing import Any, Tuple

import numpy as np
import polars as pl

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  Primitives
# ══════════════════════════════════════════════════════════════════════════════

def num(val: Any) -> float:
    """``float()`` sur une agrégation polars — conversion stricte, inchangée.

    `Series.mean()`, `.min()`, `.max()` déclarent une union large
    (`int | float | Decimal | date | ... | None`) parce qu'elles s'appliquent
    aussi à des colonnes non numériques et rendent `None` sur une série vide.
    Ce passe-plat ne fait que dire au typage « ici c'est un nombre » : un
    `None` lève toujours, comme avant. Ne pas confondre avec `safe_num`, qui
    REMPLACE la valeur — à réserver aux entrées réellement facultatives, jamais
    à une fenêtre vide, qui est un défaut de données, pas une valeur.
    """
    return float(val)


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
    lo      = df["low"]
    c_prev = df["close"].shift(1).fill_null(df["close"][0])
    hl     = h - lo
    hcp    = (h - c_prev).abs()
    lcp    = (lo - c_prev).abs()
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
    lo      = (-d.clip(upper_bound=0)).ewm_mean(alpha=1 / period, adjust=False)
    l_safe = lo.clip(lower_bound=1e-10)
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
               bb_period: int = 20, quantile: float = 0.30,
               full_df=None, cache: dict | None = None) -> bool:
    """True si les bandes de Bollinger sont en squeeze (compression de volatilité).

    ``full_df``/``cache`` (PERF) : quand ``close`` est le préfixe causal du
    ``close`` de ``full_df``, la série de décisions est calculée UNE fois et
    indexée en O(1) — même idiome que ``ema_window``/``supertrend_last``. Sans
    ces arguments (live, fenêtre non préfixe), le calcul tronqué ci-dessous
    reste le chemin normal.
    """
    if cache is not None and full_df is not None:
        from app.core.indicators_causal import (
            bb_squeeze_series,
            close_prefix_index,
        )
        pos = close_prefix_index(close, full_df)
        if pos is not None:
            key = (id(full_df), full_df.height, int(lookback), int(bb_period),
                   float(quantile))
            if key in cache:
                arr = cache[key]
            else:
                if len(cache) > 8:      # garde-fou mémoire
                    cache.clear()
                arr = bb_squeeze_series(full_df, lookback, bb_period, quantile)
                cache[key] = arr
            if arr is not None:
                return bool(arr[pos])
            # PERF-02 : si la série vectorisée rend None (grille irrégulière),
            # le repli ci-dessous est O(lookback) via troncature — pas O(n²).
            # Contrairement à htf_trend, mémoïser ce repli ne change pas l'ordre.

    if len(close) < bb_period + lookback:
        return False
    # Seules les `lookback+1` dernières largeurs sont utilisées, et chacune ne
    # dépend que des `bb_period` closes précédents. On tronque à la queue → coût
    # O(bb_period+lookback) par appel au lieu de O(n) (rolling sur toute la série) :
    # transforme un backtest O(n²) en O(n) pour tous les appelants, à résultat
    # strictement identique (fenêtres rolling causales — vérifié par
    # tests/test_bb_squeeze_causal.py, qui compare les deux formes barre à
    # barre). La troncature reste donc la voie rapide de TOUT appelant qui ne
    # passe pas ``full_df`` : le live, et toute stratégie non câblée.
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
    q = past.quantile(quantile)
    return len(past) >= 5 and q is not None and float(cur_w) <= float(q)


# ══════════════════════════════════════════════════════════════════════════════
#  ATR  — deux variantes : Series (pour build_features) et scalaire (stratégies)
# ══════════════════════════════════════════════════════════════════════════════

def atr_wilder(df: pl.DataFrame, n: int = 14) -> pl.Series:
    """ATR(n), lissage de **Wilder** (RMA, α = 1/n) — la définition de l'ATR,
    et celle de ``ta.atr`` en Pine.

    **Source unique de vérité du lissage Wilder dans le dépôt** — ne pas
    ré-implémenter en local. Le moteur Smart Money (``app/core/smc.py``,
    ``smc_primitives._wilder_atr``) et le portage PineScript
    (``liquidity_sweep_vol``) en dépendent explicitement pour que leurs seuils
    « ×ATR » correspondent au même ATR que TradingView.

    ``atr``/``atr_series``/``atr_val`` délèguent ici depuis la correction du
    lissage (cf. ``atr``) : une seule implémentation, donc pas de dérive
    possible entre le contrat du SMC et le reste du dépôt.
    """
    return _true_range(df).ewm_mean(alpha=1.0 / n, adjust=False)


def atr_percentile(df: pl.DataFrame, n: int = 14,
                   lookback: int = 200) -> pl.Series:
    """§76 — rang de l'ATR courant dans sa propre distribution récente, [0, 1].

    Un seuil d'ATR **absolu** (« ATR% > 3 → volatile ») ne veut pas dire la même
    chose sur BTC en 2018 et sur une action du SBF 120 : c'est l'argument exact
    de §76, et le dépôt en compte deux — `risk.volatility_threshold` (frein de
    sizing) et `atr_volatile_threshold` (classification de régime).

    Causal par construction : le rang à la barre i ne regarde que les
    ``lookback`` barres qui la précèdent, elle comprise.
    """
    a = atr_wilder(df, n) / df["close"]
    return (a.rolling_map(
        lambda w: float((w <= w[-1]).sum()) / len(w),
        window_size=min(lookback, max(len(df), 1)), min_samples=n)
        .fill_null(0.5))


def atr(df: pl.DataFrame, n: int = 14) -> pl.Series:
    """ATR(n) — retourne une Series complète (compatible build_features).

    Lissé en **Wilder** (α = 1/n). Auparavant ``ewm_mean(span=n)``, soit
    α = 2/(n+1) : pour n=14, 0.133 au lieu de 0.071 — un ATR presque deux fois
    plus réactif que son nom ne l'annonce, l'équivalent d'un Wilder de période
    7,5. Le RSI voisin, lui, était déjà en α = 1/n, ce qui ne laissait guère de
    doute sur le caractère involontaire de l'écart.

    La correction a été précédée d'une mesure, pas décrétée : réoptimisation
    complète de chaque variante puis comparaison sur une fenêtre jamais vue par
    l'optimiseur (``scripts/compare_adx_smoothing.py``). Écart maximum 0.022 de
    score, de signe variable — la sur-réactivité n'était pas ce qui faisait
    vivre les stratégies concernées. Cf.
    docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter.
    """
    return atr_wilder(df, n)


def atr_series(df: pl.DataFrame, period: int = 14) -> pl.Series:
    """Alias explicite pour atr() → pl.Series."""
    return atr_wilder(df, period)


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

    **Lissage de Wilder (α = 1/n) aux quatre étages** — ATR, +DI, −DI et la
    ligne ADX elle-même. C'est la définition de l'ADX. Auparavant
    ``ewm_mean(span=n)`` partout, soit α = 2/(n+1) : mesuré sur BTC/USDC 1h,
    l'ADX moyen tombait de 35.4 à 28.2 après correction, et le verdict
    ``ADX ≥ 20`` changeait sur 21,6 % des barres. Cf.
    docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter et :func:`atr` pour la
    mesure qui a précédé la correction.
    """
    if len(df) < n * 2:
        z = pl.Series([0.0] * len(df))
        return z, z, z
    h    = df["high"]
    lo    = df["low"]
    up   = h.diff(1).clip(lower_bound=0)
    dn   = (-lo.diff(1)).clip(lower_bound=0)
    pdm  = up  * (up > dn).cast(pl.Float64)
    ndm  = dn  * (dn > up).cast(pl.Float64)

    a        = 1.0 / n
    atr14    = atr_wilder(df, n)
    atr_safe = atr14.clip(lower_bound=1e-10)

    pdi     = 100 * pdm.ewm_mean(alpha=a, adjust=False) / atr_safe
    ndi     = 100 * ndm.ewm_mean(alpha=a, adjust=False) / atr_safe
    sum_di  = (pdi + ndi).clip(lower_bound=1e-10)
    dx      = 100 * (pdi - ndi).abs() / sum_di

    adx_line = dx.ewm_mean(alpha=a, adjust=False).fill_null(0)
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
    # TR et ATR via Polars (pas de round-trip sur cette partie). Lissage de
    # Wilder comme partout ailleurs depuis §8ter — c'est aussi ce qu'utilise
    # ``ta.supertrend`` en Pine, dont ``mult`` est censé être comparable.
    atr_arr = atr_wilder(df, period).to_numpy().astype(np.float64)

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


# ══════════════════════════════════════════════════════════════════════════════
#  VWAP (rolling, ancré session, + bandes d'écart-type)
# ══════════════════════════════════════════════════════════════════════════════

def rolling_vwap(df: pl.DataFrame, n: int = 20) -> pl.Series:
    """VWAP glissant sur ``n`` barres (prix typique pondéré par le volume).
    Causal (fenêtre = barres passées + courante)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (tp * df["volume"]).rolling_sum(n)
    vv = df["volume"].rolling_sum(n).clip(lower_bound=1e-9)
    return pv / vv


def vwap_bands(df: pl.DataFrame, n: int = 20,
               k: float = 2.0) -> Tuple[pl.Series, pl.Series, pl.Series]:
    """(VWAP glissant, bande haute, bande basse) — bandes à ``k`` écarts-types
    du prix typique. Cibles/TP dynamiques et zones de sur-extension."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vw = rolling_vwap(df, n)
    sd = tp.rolling_std(n).fill_null(0.0)
    return vw, vw + k * sd, vw - k * sd


def session_vwap(df: pl.DataFrame) -> pl.Series:
    """VWAP ancré par session (jour UTC) — le standard day-trading. Se réinitialise
    à chaque nouvelle journée. Fallback cumulatif global si pas de colonne time."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].clip(lower_bound=0.0)
    if "time" in df.columns:
        day = df["time"].dt.truncate("1d")
        f = pl.DataFrame({"pv": tp * vol, "v": vol, "d": day})
        cpv = f.select(pl.col("pv").cum_sum().over("d")).to_series()
        cv = f.select(pl.col("v").cum_sum().over("d")).to_series().clip(lower_bound=1e-9)
        return cpv / cv
    cpv = (tp * vol).cum_sum()
    cv = vol.cum_sum().clip(lower_bound=1e-9)
    return cpv / cv


# ══════════════════════════════════════════════════════════════════════════════
#  CVD (Cumulative Volume Delta) — proxy OHLCV (multiplicateur money-flow)
# ══════════════════════════════════════════════════════════════════════════════

def cvd(df: pl.DataFrame) -> pl.Series:
    """Cumulative Volume Delta approximé depuis l'OHLCV (pas de données tick) :
    volume signé par la position de la clôture dans la bougie
    (``((C−L)−(H−C))/(H−L)`` ∈ [−1, 1]), cumulé. Une divergence prix↑/CVD↓
    signale une absorption (retournement probable)."""
    rng = (df["high"] - df["low"]).clip(lower_bound=1e-9)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return (mfm * df["volume"]).cum_sum()


# ══════════════════════════════════════════════════════════════════════════════
#  Choppiness Index — tendance (< 38.2) vs congestion (> 61.8)
# ══════════════════════════════════════════════════════════════════════════════

def choppiness(df: pl.DataFrame, n: int = 14) -> pl.Series:
    """Choppiness Index ∈ [0, 100] : > 61.8 = range/congestion, < 38.2 = tendance.
    ``100·log10(ΣTR / (max_H − min_L)) / log10(n)``."""
    tr_sum = _true_range(df).rolling_sum(n)
    rng = (df["high"].rolling_max(n) - df["low"].rolling_min(n)).clip(lower_bound=1e-9)
    return 100.0 * (tr_sum / rng).log10() / float(np.log10(n))


# ══════════════════════════════════════════════════════════════════════════════
#  Keltner Channels — EMA ± mult×ATR (cibles TP / sur-extension)
# ══════════════════════════════════════════════════════════════════════════════

def keltner(df: pl.DataFrame, n: int = 20, mult: float = 2.0,
            atr_n: int | None = None) -> Tuple[pl.Series, pl.Series, pl.Series]:
    """(médiane EMA, bande haute, bande basse) = EMA(close, n) ± mult×ATR."""
    mid = ema(df["close"], n)
    a = atr_wilder(df, atr_n or n)
    return mid, mid + mult * a, mid - mult * a


# ══════════════════════════════════════════════════════════════════════════════
#  Price action : pin bars, engulfing, VSA (no demand / no supply)
# ══════════════════════════════════════════════════════════════════════════════

def pin_bar(df: pl.DataFrame, wick_ratio: float = 2.0,
            body_max: float = 0.34) -> pl.Series:
    """{+1, 0, −1} : +1 marteau (mèche basse ≥ wick_ratio×corps, petit corps),
    −1 étoile filante (mèche haute dominante). Rejet à valider sur zone clé."""
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    c = df["close"].to_numpy()
    rng = np.maximum(h - lo, 1e-9)
    body = np.abs(c - o)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - lo
    small = body <= body_max * rng
    hammer = small & (lower >= wick_ratio * body) & (upper <= body)
    star = small & (upper >= wick_ratio * body) & (lower <= body)
    return pl.Series(hammer.astype(np.int8) - star.astype(np.int8))


def engulfing(df: pl.DataFrame) -> pl.Series:
    """{+1, 0, −1} : +1 avalement haussier (bougie verte englobant le corps
    rouge précédent), −1 avalement baissier."""
    o = df["open"].to_numpy()
    c = df["close"].to_numpy()
    out = np.zeros(len(c), dtype=np.int8)
    for i in range(1, len(c)):
        if c[i] > o[i] and c[i - 1] < o[i - 1] \
                and c[i] >= o[i - 1] and o[i] <= c[i - 1]:
            out[i] = 1
        elif c[i] < o[i] and c[i - 1] > o[i - 1] \
                and o[i] >= c[i - 1] and c[i] <= o[i - 1]:
            out[i] = -1
    return pl.Series(out)


def vsa_signal(df: pl.DataFrame, n: int = 20, spread_k: float = 0.6,
               vol_k: float = 0.7) -> pl.Series:
    """Volume Spread Analysis {+1, 0, −1} en congestion :
      +1 « No Supply » : bougie baissière à faible spread ET faible volume
         (les vendeurs manquent) → biais haussier ;
      −1 « No Demand » : bougie haussière à faible spread ET faible volume."""
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    c = df["close"].to_numpy()
    v = df["volume"].to_numpy()
    spread = h - lo
    avg_spread = pl.Series(spread).rolling_mean(n).fill_null(np.inf).to_numpy()
    avg_vol = pl.Series(v).rolling_mean(n).fill_null(np.inf).to_numpy()
    narrow = spread < spread_k * avg_spread
    lowvol = v < vol_k * avg_vol
    out = np.zeros(len(c), dtype=np.int8)
    out[narrow & lowvol & (c < o)] = 1     # no supply → haussier
    out[narrow & lowvol & (c > o)] = -1    # no demand → baissier
    return pl.Series(out)


def rsi_divergence_hidden(df: pl.DataFrame, period: int = 14,
                          lookback: int = 14) -> pl.Series:
    """Divergences CACHÉES (continuation) {+1, 0, −1}, complément de
    ``rsi_divergence`` (régulières) :
      +1  cachée haussière : le RSI inscrit un plus-bas mais le prix tient un
          plus-bas PLUS HAUT (higher low) → continuation de la hausse ;
      −1  cachée baissière : le RSI inscrit un plus-haut mais le prix tient un
          plus-haut PLUS BAS (lower high) → continuation de la baisse."""
    close = df["close"]
    r = rsi(close, period)
    hbull = ((r == r.rolling_min(lookback)) &
             (close > close.rolling_min(lookback) * 1.003))
    hbear = ((r == r.rolling_max(lookback)) &
             (close < close.rolling_max(lookback) * 0.997))
    return (hbull.cast(pl.Int8) - hbear.cast(pl.Int8)).fill_null(0)
