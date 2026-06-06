"""Proxys déterministes (sans ML) pour ``p_event`` / ``p_up``.

Les stratégies *Opus Omnibus* (V8, V10, V11, V11-FollowSetup) reposent toutes sur
le même squelette de routing : un jeu de « setups » sélectionnés selon le régime
de marché et **deux probabilités** produites par des modèles LightGBM/sklearn :

  * ``p_event`` — P(amplitude) : probabilité qu'un mouvement de forte amplitude
    survienne dans les prochaines bougies (label = |rendement| dans le top
    ``amp_top_pct`` des quantiles, donc taux de base ≈ 0.30).
  * ``p_up`` — P(direction) : probabilité que le rendement moyen futur soit
    positif (centré sur 0.5 ; haussier > 0.5, baissier < 0.5).

Ces deux modèles sont coûteux à entraîner et à maintenir. Ce module fournit des
**équivalents purement à base d'indicateurs** : à partir de la dernière ligne de
features déjà calculée par le pipeline V4 (mêmes colonnes en pandas et polars),
on recompose deux scores dans ``[0, 1]`` qui imitent la sémantique des sorties
ML. Tout le reste de la chaîne (détection de régime, setups, scoring, sorties
anticipées) est réutilisé tel quel par les variantes ``_no_ml`` — seul l'appel au
modèle est remplacé par ces fonctions.

Conception
----------
* ``p_up`` : moyenne pondérée de sous-signaux directionnels normalisés dans
  ``[-1, 1]`` (DI_diff, RSI, ROC, pente SMA20, distance SMA50, ratio de bougies
  vertes, position MACD), passée dans une sigmoïde centrée sur 0.5.
* ``p_event`` : moyenne pondérée de sous-signaux d'amplitude normalisés dans
  ``[0, 1]`` (rang de largeur des bandes de Bollinger, surcroît de volume, force
  ADX, expansion/squeeze BB, magnitude du ROC récent), recentrée puis passée
  dans une sigmoïde — le centre décale la probabilité de base pour coller au
  taux d'événements ≈ 0.30 du label ML.

Tous les coefficients (échelles, poids, gains, centres) sont paramétrables via un
dict ``proxy_cfg`` pour permettre l'optimisation, avec des valeurs par défaut
raisonnables calibrées sur les ordres de grandeur des features V4.
"""

import math
from typing import Any, Dict, Mapping, Optional


# ── Valeurs par défaut des coefficients du proxy ──────────────────────────────
DEFAULT_PROXY_CFG: Dict[str, float] = {
    # Échelles de normalisation des sous-signaux directionnels.
    "dir_di_scale":    20.0,    # tanh(DI_diff / di_scale)
    "dir_rsi_scale":   30.0,    # (RSI-50) / rsi_scale, clip [-1,1]
    "dir_roc_scale":    5.0,    # tanh(ROC_14 / roc_scale)
    "dir_slope_scale": 1500.0,  # tanh(slope_SMA20 * slope_scale)
    "dir_dist_scale":  15.0,    # tanh(dist_SMA50 * dist_scale)
    # Poids des sous-signaux directionnels.
    "dir_w_di":     1.0,
    "dir_w_rsi":    0.7,
    "dir_w_roc":    0.8,
    "dir_w_slope":  1.0,
    "dir_w_dist":   0.8,
    "dir_w_green":  0.5,
    "dir_w_macd":   0.6,
    # Gain de la sigmoïde directionnelle.
    "dir_gain":     2.0,

    # Échelles des sous-signaux d'amplitude.
    "amp_volr_scale":  1.5,     # clip((vol_ratio-1)/volr_scale, 0, 1)
    "amp_adx_scale":   40.0,    # clip(ADX/adx_scale, 0, 1)
    "amp_roc_scale":   4.0,     # clip(|ROC_7|/roc_scale, 0, 1)
    # Poids des sous-signaux d'amplitude.
    "amp_w_bbrank":     1.0,
    "amp_w_volr":       0.8,
    "amp_w_adx":        0.7,
    "amp_w_expansion":  0.6,
    "amp_w_squeeze":    0.4,
    "amp_w_roc":        0.7,
    # Recentrage + gain de la sigmoïde d'amplitude (center ≈ taux de base).
    "amp_center":   0.40,
    "amp_gain":     3.2,
}


def default_proxy_cfg() -> Dict[str, float]:
    """Retourne une copie des coefficients par défaut (mutable sans effet de bord)."""
    return dict(DEFAULT_PROXY_CFG)


def resolve_proxy_cfg(params: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    """Fusionne les surcharges ``proxy_*`` issues des params stratégie.

    Les clés acceptées sont celles de :data:`DEFAULT_PROXY_CFG`. Toute autre clé
    est ignorée silencieusement (robustesse vis-à-vis du YAML).
    """
    cfg = default_proxy_cfg()
    if params:
        for k, v in params.items():
            if k in cfg and v is not None:
                try:
                    cfg[k] = float(v)
                except (TypeError, ValueError):
                    continue
    return cfg


def _f(row: Any, key: str, default: float = 0.0) -> float:
    """Lecture robuste d'une feature (pandas Series ou dict polars) en float fini."""
    try:
        v = row.get(key, default)
    except Exception:
        v = default
    if v is None:
        return default
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return v


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _tanh(x: float) -> float:
    # math.tanh sature proprement ; bornage défensif contre les overflows.
    return math.tanh(_clip(x, -30.0, 30.0))


def direction_proxy(row: Any, cfg: Dict[str, float]) -> float:
    """Score directionnel dans ``[0, 1]`` (0.5 = neutre), équivalent de ``p_up``."""
    di    = _tanh(_f(row, "DI_diff") / max(cfg["dir_di_scale"], 1e-9))
    rsi   = _clip((_f(row, "RSI_14", 50.0) - 50.0) / max(cfg["dir_rsi_scale"], 1e-9), -1.0, 1.0)
    roc   = _tanh(_f(row, "ROC_14") / max(cfg["dir_roc_scale"], 1e-9))
    slope = _tanh(_f(row, "slope_SMA20") * cfg["dir_slope_scale"])
    dist  = _tanh(_f(row, "dist_SMA50") * cfg["dir_dist_scale"])
    green = _clip((_f(row, "green_ratio_10", 0.5) - 0.5) * 2.0, -1.0, 1.0)
    # Position MACD : binaire (au-dessus/en-dessous de sa ligne de signal).
    macd  = 1.0 if _f(row, "MACD_above_signal") >= 0.5 else -1.0

    weights = (
        cfg["dir_w_di"], cfg["dir_w_rsi"], cfg["dir_w_roc"], cfg["dir_w_slope"],
        cfg["dir_w_dist"], cfg["dir_w_green"], cfg["dir_w_macd"],
    )
    signals = (di, rsi, roc, slope, dist, green, macd)
    wsum = sum(weights)
    if wsum <= 0:
        return 0.5
    s = sum(w * x for w, x in zip(weights, signals)) / wsum
    return _sigmoid(cfg["dir_gain"] * s)


def amplitude_proxy(row: Any, cfg: Dict[str, float]) -> float:
    """Score d'amplitude dans ``[0, 1]``, équivalent de ``p_event``."""
    bbrank    = _clip(_f(row, "BB_width_rank100"), 0.0, 1.0)
    volr      = _clip((_f(row, "vol_ratio", 1.0) - 1.0) / max(cfg["amp_volr_scale"], 1e-9), 0.0, 1.0)
    adx       = _clip(_f(row, "ADX") / max(cfg["amp_adx_scale"], 1e-9), 0.0, 1.0)
    expansion = 1.0 if _f(row, "BB_expansion") >= 0.5 else 0.0
    squeeze   = 1.0 if _f(row, "BB_squeeze") >= 0.5 else 0.0
    roc_amp   = _clip(abs(_f(row, "ROC_7")) / max(cfg["amp_roc_scale"], 1e-9), 0.0, 1.0)

    weights = (
        cfg["amp_w_bbrank"], cfg["amp_w_volr"], cfg["amp_w_adx"],
        cfg["amp_w_expansion"], cfg["amp_w_squeeze"], cfg["amp_w_roc"],
    )
    signals = (bbrank, volr, adx, expansion, squeeze, roc_amp)
    wsum = sum(weights)
    if wsum <= 0:
        return 0.0
    raw = sum(w * x for w, x in zip(weights, signals)) / wsum
    return _sigmoid(cfg["amp_gain"] * (raw - cfg["amp_center"]))


def compute_proxies(row: Any, params: Optional[Mapping[str, Any]] = None) -> tuple:
    """Retourne ``(p_event, p_up)`` calculés depuis la dernière ligne de features.

    ``row`` peut être une ``pandas.Series`` (pipeline V4 pandas) ou un dict issu
    de ``polars.DataFrame.row(-1, named=True)`` (pipeline V4 polars) : l'accès se
    fait uniquement via ``.get`` sur des colonnes communes aux deux.
    """
    cfg = resolve_proxy_cfg(params)
    p_up    = direction_proxy(row, cfg)
    p_event = amplitude_proxy(row, cfg)
    return p_event, p_up


# Clés proxy exposables dans les ``param_space`` des stratégies _no_ml pour
# permettre l'optimisation des coefficients sans toucher au code.
PROXY_PARAM_SPACE: Dict[str, list] = {
    "dir_gain":   [1.5, 2.0, 2.5, 3.0],
    "amp_gain":   [2.5, 3.2, 4.0],
    "amp_center": [0.35, 0.40, 0.45],
}
