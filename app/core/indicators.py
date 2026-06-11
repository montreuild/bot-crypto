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
from app.core.indicators_core import (            # noqa: F401
    _true_range, ema, sma, rsi, macd, bollinger, bb_squeeze,
    atr, atr_series, atr_val, adx, adx_val,
    volume_ratio, vol_ratio, obv, donchian, supertrend,
    stochastic, roc, green_ratio, rsi_divergence, trend_duration,
)
from app.core.indicators_causal import (          # noqa: F401
    _causal_prefix_index, supertrend_last, macd_hist_last3, ema_window,
)
from app.core.indicators_market import (          # noqa: F401
    market_structure, htf_trend, detect_regime_full, detect_regime,
    build_features, support_resistance_levels,
    nearest_support, nearest_resistance,
    bars_since_cross, rolling_slope, hurst_exponent, rolling_hurst,
    rolling_rank_pct, bearish_excess_series,
)
from app.core.indicators_precompute import (      # noqa: F401
    _PRECOMPUTE_CACHE, _PRECOMPUTE_LOCK, _PRECOMPUTE_MAXSIZE,
    _precompute_key, precompute_df, precompute, _precompute_df_impl, pre_val,
)
