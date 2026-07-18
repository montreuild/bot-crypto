"""Pré-calcul vectorisé des colonnes partagées ``_pre_*`` (cache process-wide).

Extrait de ``indicators.py`` (découpage V13) : ``precompute_df`` ajoute en une
passe les colonnes (_pre_rsi14, _pre_atr14, _pre_adx14, _pre_macd_hist,
_pre_ema*, _pre_volratio20…) lues ensuite en O(1) par les stratégies via
``pre_val``.
"""
import logging
import threading
from collections import OrderedDict

import polars as pl

_log = logging.getLogger(__name__)

from app.core.indicators_core import _true_range  # noqa: E402

# ── Cache des features pré-calculées (partagé entre jobs d'un même process) ──
# Le backtest et l'optimiseur appellent precompute_df() de façon répétée sur la
# MÊME plage (par TF) : 4 stratégies en parallèle sur le même df côté backtest,
# et N trials sur df_is/df_oos identiques côté optimiseur. On mémoïse le résultat
# par empreinte de la plage pour ne calculer les indicateurs qu'une seule fois.
# Borné à quelques entrées pour limiter la mémoire (plusieurs TF × IS/OOS).
_PRECOMPUTE_CACHE: "OrderedDict[tuple, pl.DataFrame]" = OrderedDict()
_PRECOMPUTE_LOCK = threading.Lock()
_PRECOMPUTE_MAXSIZE = 16


def _precompute_key(df: pl.DataFrame):
    """Empreinte bon marché d'une plage OHLCV (taille + bornes temporelles + dernier close)."""
    try:
        n = df.height
        if n == 0 or "time" not in df.columns:
            return None
        return (n, df.width, str(df["time"][0]), str(df["time"][-1]),
                float(df["close"][-1]))
    except Exception:
        return None




# ══════════════════════════════════════════════════════════════════════════════
#  Pré-calcul vectorisé — O(n) unique pour accélérer le backtest (~180×)
# ══════════════════════════════════════════════════════════════════════════════

def precompute_df(df: pl.DataFrame) -> pl.DataFrame:
    """Enveloppe mémoïsée de :func:`_precompute_df_impl`.

    - Idempotent : si le df porte déjà les colonnes ``_pre_*``, il est retourné tel quel.
    - Cache par empreinte de plage (voir ``_precompute_key``) partagé au sein du
      process, ce qui évite de recalculer les indicateurs pour chaque stratégie
      (backtest 4 threads) et pour chaque trial (optimiseur) sur une plage identique.
    """
    if "_pre_atr14" in df.columns:
        return df
    key = _precompute_key(df)
    if key is not None:
        with _PRECOMPUTE_LOCK:
            cached = _PRECOMPUTE_CACHE.get(key)
            if cached is not None:
                _PRECOMPUTE_CACHE.move_to_end(key)
                return cached
    result = _precompute_df_impl(df)
    if key is not None:
        with _PRECOMPUTE_LOCK:
            _PRECOMPUTE_CACHE[key] = result
            _PRECOMPUTE_CACHE.move_to_end(key)
            while len(_PRECOMPUTE_CACHE) > _PRECOMPUTE_MAXSIZE:
                _PRECOMPUTE_CACHE.popitem(last=False)
    return result


def _precompute_df_impl(df: pl.DataFrame) -> pl.DataFrame:
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
