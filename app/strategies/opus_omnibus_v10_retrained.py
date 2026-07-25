"""Opus Omnibus V10 ré-entraîné — **preset de V11**, plus un fichier autonome.

V10 et V11 portaient le même routing dans deux fichiers de 952 et 778 lignes.
Ce qui les séparait tient en trois réglages, tous exprimables en paramètres :

======================  ======================================================
``di_rescue: inf``      V10 classe le régime sur 3 conditions (ADX + alignement
                        SMA) ; V11 ajoute un rattrapage DI qui requalifie un
                        Choppy directionnel en Trend. ``di_rescue = inf`` rend
                        ce rattrapage inatteignable, donc restitue exactement
                        le classifieur de V10.
recette single-horizon  V10 labellise en ``t+1`` (``omnibus_v4_single``) ;
                        V11 agrège ``[1, 3, 6]``.
``calibrate``/``prune`` V10 ne calibrait ni n'élaguait.
======================  ======================================================

**Équivalence prouvée avant la fusion**, sur trois plans distincts :

1. *Classification de régime* — ``classify_regime(..., di_rescue=inf)`` égale
   l'ancien ``_classify_regime`` de V10 sur les 2 520 combinaisons du domaine
   (ADX × alignements × DI_diff × pente × BB rank), avec contre-épreuve :
   à ``di_rescue=10`` les deux divergent bien, donc le test discrimine.
2. *Prédictions* — 60 barres de décision réelles BTC/USDC 1h, ``p_event`` et
   ``p_up`` identiques à 1e-8, sur des valeurs qui varient (0.14 → 0.66).
3. *Sélection de setup* — 23 040 combinaisons (régime × p_event × p_up ×
   exit_td × excès baissier × RSI × ADX) : sélection identique dans 100 % des
   cas, dont 42,9 % où un setup se déclenche effectivement.

Cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §7.
"""
from typing import Any, Dict, List

from app.strategies.opus_omnibus_v11 import _SUPPORTED_TFS
from app.strategies.opus_omnibus_v11 import Strategy as _V11Strategy

# Rattrapage DI inatteignable ⇒ classifieur 3-conditions de V10. ``inf`` plutôt
# qu'un grand nombre arbitraire : l'intention (« désactivé ») est lisible, et
# aucune valeur de DI_diff ne peut le franchir par accident.
_DI_RESCUE_DISABLED = float("inf")


class Strategy(_V11Strategy):
    """Routing V11, réglages V10. Aucun code de décision propre."""

    name = "opus_omnibus_v10_retrained"
    models: Dict[str, str] = {"signal": "omnibus_v4_single"}
    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Espace de recherche V10 : plus large que celui de V11 sur les bornes
    # directionnelles (``dir_min``/``dir_max``), conservées ici parce qu'elles
    # définissent ce que CE preset explore.
    param_space: Dict[str, Any] = {
        "setup_signal_up_amp_min":          [0.45, 0.50, 0.55],
        "setup_signal_up_dir_min":          [0.55, 0.60, 0.65],
        "setup_short_td_high_amp_min":      [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":      [0.25, 0.30, 0.35],
        "setup_long_choppy_amp_min":        [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":        [0.55, 0.58, 0.62],
        "setup_short_choppy_amp_min":       [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":       [0.38, 0.42, 0.46],
        "setup_long_tu_amp_min":            [0.50, 0.55, 0.60],
        "setup_long_tu_dir_min":            [0.58, 0.62, 0.66],
        "setup_long_tu_needs_adx_above":    [22.0, 25.0, 28.0],
        "setup_long_range_strict_amp_min":  [0.55, 0.60, 0.65],
        "setup_long_range_light_amp_min":   [0.45, 0.50, 0.55],
        "exit_td_window_bars":              [2, 3, 4],
    }

    # Hyperparamètres d'entraînement figés (hors espace de recherche) : les
    # échantillonner invalide le cache d'entraînement process-wide entre les
    # trials de l'optimiseur — chaque trial repaierait l'intégralité des
    # retrains LightGBM walk-forward.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":     0.30,
        "label_horizons":  [1],              # doit rester aligné sur la recette
        "warmup_bars":     750,
        "retrain_every":   800,
        "n_estimators":    500,
        "num_leaves":      31,
        "learning_rate":   0.03,
        "di_rescue":       _DI_RESCUE_DISABLED,
        "calibrate":       False,
        "prune_features":  False,
    }

    _DEFAULTS = {
        **_V11Strategy._DEFAULTS,
        "label_horizons":  [1],
        "di_rescue":       _DI_RESCUE_DISABLED,
        "calibrate":       False,
        "prune_features":  False,
        "log_training":    False,
    }
