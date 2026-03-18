"""Shim de compatibilité — tous les indicateurs sont désormais dans app.core.indicators.

Ce fichier ne doit plus être modifié directement.
Importer depuis app.core.indicators dans tout nouveau code.
"""
# noqa: F401 — ré-exports intentionnels
from app.core.indicators import (
    _true_range,
    rsi,
    atr_val as atr,           # les stratégies attendent atr() → float
    atr_series,
    adx_val as adx,           # les stratégies attendent adx() → float
    macd,
    supertrend,
    bb_squeeze,
    market_structure,
    vol_ratio,
    htf_trend,
    precompute_df,
    pre_val,
    support_resistance_levels,
    nearest_support,
    nearest_resistance,
    stochastic,
)
