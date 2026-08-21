"""Indicateurs techniques — façade de compatibilité.

Découpage V13 du monolithe (1030 lignes) en modules thématiques :
  - ``indicators_core.py``       : primitives (EMA/SMA/RSI/MACD/BB/ATR/ADX/
                                   volume/Donchian/SuperTrend/stochastique/ROC…)
  - ``indicators_causal.py``     : réutilisation causale fenêtre ↔ df complet
                                   (supertrend_last, macd_hist_last3, ema_window)
  - ``indicators_market.py``     : structure de marché, régimes, niveaux S/R,
                                   statistiques roulantes (Hurst, slope, rank…)
  - ``indicators_precompute.py`` : colonnes partagées ``_pre_*`` (precompute_df,
                                   pre_val) + cache process-wide

Tous les noms historiques restent importables depuis ``app.core.indicators``.
"""
from app.core.indicators_causal import (  # noqa: F401
    _causal_prefix_index,
    ema_window,
    htf_trend_ema_series,
    macd_hist_last3,
    supertrend_last,
)
from app.core.indicators_core import (  # noqa: F401
    _true_range,
    adx,
    adx_val,
    atr,
    atr_series,
    atr_val,
    bb_squeeze,
    bollinger,
    choppiness,
    cvd,
    donchian,
    ema,
    engulfing,
    green_ratio,
    keltner,
    macd,
    num,
    obv,
    pin_bar,
    roc,
    rolling_vwap,
    rsi,
    rsi_divergence,
    rsi_divergence_hidden,
    safe_num,
    session_vwap,
    sma,
    stochastic,
    supertrend,
    trend_duration,
    vol_ratio,
    volume_ratio,
    vsa_signal,
    vwap_bands,
)
from app.core.indicators_market import (  # noqa: F401
    bars_since_cross,
    bearish_excess_series,
    build_features,
    detect_regime,
    detect_regime_full,
    htf_trend,
    hurst_exponent,
    market_structure,
    nearest_resistance,
    nearest_support,
    rolling_hurst,
    rolling_rank_pct,
    rolling_slope,
    support_resistance_levels,
)
from app.core.indicators_precompute import (  # noqa: F401
    _PRECOMPUTE_CACHE,
    _PRECOMPUTE_LOCK,
    _PRECOMPUTE_MAXSIZE,
    _precompute_df_impl,
    _precompute_key,
    pre_val,
    precompute,
    precompute_df,
)
