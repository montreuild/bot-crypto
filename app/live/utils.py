"""
Utilitaires partagés pour la couche live trading.

Ce module regroupe les fonctions et constantes transversales utilisées par
live_trader, signal_pipeline et ohlcv_cache, évitant les imports circulaires.
"""
import math
from typing import Dict


# ---------------------------------------------------------------------------
# Sérialisation JSON sûre
# ---------------------------------------------------------------------------

def _safe_float(v, fallback=None):
    """Convertit nan/inf en fallback pour garantir la sérialisation JSON."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return fallback
        return f
    except (TypeError, ValueError):
        return fallback


def _sanitize(obj):
    """Parcourt récursivement un dict/list et remplace les float nan/inf par None."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        return _safe_float(obj)
    return obj


# ---------------------------------------------------------------------------
# Fusion des paramètres de stratégie
# ---------------------------------------------------------------------------

def _merge_params(base: dict, optimized: dict) -> dict:
    """
    Fusionne les params de config (base) avec les params optimisés.

    - base     : cfg["strategy_params"] complet — ex. {"trend": {...}, "supertrend_macd": {...}}
    - optimized: params d'un entry _active_per_tf — ex. {"trend": {"adx_min": 25, ...}}

    Résultat : dict complet avec toutes les stratégies, params optimisés écrasant le base.
    Les valeurs NaN/None sont remplacées par les valeurs base ou les défauts.
    """
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for strat_key, strat_params in optimized.items():
        if not isinstance(strat_params, dict):
            continue
        base_for_strat = dict(merged.get(strat_key, {}))
        for k, v in strat_params.items():
            if v is None:
                continue
            try:
                if isinstance(v, float) and math.isnan(v):
                    continue
            except (TypeError, ValueError):
                pass
            base_for_strat[k] = v
        merged[strat_key] = base_for_strat
    return merged


# ---------------------------------------------------------------------------
# Mapping timeframe → higher timeframe (filtre de tendance HTF)
# Source unique de vérité — importé par live_trader et signal_pipeline.
# ---------------------------------------------------------------------------

_HTF_MAP: Dict[str, str] = {
    "1m":  "15m",
    "3m":  "30m",
    "5m":  "1h",
    "6m":  "1h",
    "15m": "4h",
    "30m": "4h",
    "1h":  "4h",
    "2h":  "1d",
    "4h":  "1d",
    "6h":  "1d",
    "8h":  "1d",
    "12h": "1d",
    "1d":  "1d",
}
