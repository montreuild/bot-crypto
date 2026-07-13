"""Timeframes — SOURCE UNIQUE de vérité (V4-A : ARCH-08 + DEAD-04).

Avant ce module, les mêmes tables étaient dupliquées dans 8+ fichiers avec des
périmètres divergents : ``_HTF_MAP`` (app/live/utils), ``_TF_SECS``
(position_mixin), ``_TF_MS`` (ohlcv_cache), ``_TF_SEC``/``_HTF_SEC_MAP``
(vizion, smart_money) et quatre ``_TF_MINUTES`` (backtest, optimizer, replay,
oos_tracker — 9 clés pour les deux premiers, 12 pour les deux autres).

Ce module est dans app/core : importable par TOUTES les couches (strategies,
engine, live, api) sans créer d'inversion de dépendance.
"""
from typing import Dict


def tf_to_seconds(tf: str) -> int:
    """Convertit un libellé de timeframe (« 4h », « 30m », « 1d ») en secondes.
    Retourne 0 si le libellé est invalide (comportement historique de
    ``smart_money._tf_to_sec``)."""
    try:
        return int(tf[:-1]) * {"m": 60, "h": 3600, "d": 86400}[tf[-1]]
    except (ValueError, IndexError, KeyError):
        return 0


# Durée des timeframes supportés, en secondes (table canonique).
TF_SECONDS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400,
}

# Dérivées — ne JAMAIS redéfinir localement (elles divergeaient déjà).
TF_MINUTES: Dict[str, int] = {tf: s // 60 for tf, s in TF_SECONDS.items()}
TF_MS: Dict[str, int] = {tf: s * 1000 for tf, s in TF_SECONDS.items()}

# Timeframe → timeframe SUPÉRIEUR pour le biais/l'analyse HTF.
# (Historiquement « source unique de vérité » déclarée dans app/live/utils,
# mais recalculée localement par vizion/smart_money — désormais ici.)
HTF_MAP: Dict[str, str] = {
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

# Secondes LTF → secondes HTF (consommé par smc.htf_analysis/htf_trend_series
# via les stratégies). Construit par parsing pour couvrir TOUTES les entrées de
# HTF_MAP (y compris « 6m », absente de TF_SECONDS) — reproduit exactement la
# table de smart_money ; celle de vizion (qui excluait 6m/8h) gagne deux
# entrées inertes (aucun TF de trading ne les utilise).
HTF_SECONDS_MAP: Dict[int, int] = {
    tf_to_seconds(k): tf_to_seconds(v)
    for k, v in HTF_MAP.items()
    if tf_to_seconds(k) and tf_to_seconds(v)
}
