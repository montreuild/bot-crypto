"""Opus Omnibus V7 — **preset de V11**, plus un fichier autonome.

V7 est la plus ancienne génération omnibus encore vivante : 6 setups, régime
classé sur 3 conditions, labellisation ``t+1``. Rien de tout cela n'est du
*code* propre à V7 — ce sont des **valeurs**, et V11 porte déjà le catalogue
complet dont les 6 setups de V7 sont un sous-ensemble.

Table de correspondance, écrite avant le code (protocole §7) :

=========================  ====================================================
``di_rescue: inf``         V7 classe le régime sur ADX + alignement SMA seuls ;
                           V11 ajoute un rattrapage DI. ``inf`` le rend
                           inatteignable, donc restitue le classifieur de V7.
``SHORT_TD`` réactivé      V11 le porte désactivé aux valeurs V9
                           (amp 0.55 / dir_max 0.35, priorité 7). V7
                           l'utilisait à 0.50 / 0.40 en priorité 1.
``SIGNAL_UP`` désactivé    Introduit par V8 — n'existe pas dans V7.
``LONG_TU`` désactivé      Introduit par V9/V10.
``LONG_RANGE_LIGHT``       Introduit par V10.
désactivé
priorités décalées         V7 : ``LONG_EXIT_TD`` 3, ``LONG_RANGE_STRICT`` 4.
                           V11 : 4 et 5, ``LONG_TU`` s'étant inséré en 3.
                           Sans lui, il faut refermer le trou.
recette single-horizon     ``omnibus_v4_single`` — déjà celle de V7 avant la
                           bascule, donc aucun changement de modèle.
``warmup_bars: 2000``      V7 demandait une fenêtre plus large que V11 (750).
=========================  ====================================================

**Équivalence de sélection prouvée avant la fusion.** Sur les 7 744
combinaisons du domaine de décision (régime × ``p_event`` × ``p_up`` ×
fenêtre ``exit_td`` × excès baissier × RSI × ADX), le catalogue V11 surchargé
par les réglages ci-dessus sélectionne **le même setup dans 100 % des cas**,
dont 36,9 % où un setup se déclenche effectivement. Contre-épreuve : sans les
surcharges, les deux catalogues divergent sur 892 combinaisons — le test
discrimine donc bien, il ne conclut pas à l'identité par construction.

Ce que la bascule ne change pas : l'entraînement. V7 composait déjà
``MLBackendMixin`` et la recette ``omnibus_v4_single`` (étape C) — le modèle
produit est le même objet, par le même chemin.

Ce qui reste **non fusionné** de la famille omnibus, et pourquoi :
``opus_omnibus_v11_followsetup`` (mécanique de sortie différente — position
tenue jusqu'au setup opposé confirmé, sans TP ni timeout : c'est du code, pas
des valeurs) et ``opus_omnibus_v12`` (déjà une sous-classe de V11 : une
composition avec ``ml_dynamic_threshold``, pas une génération à fusionner).
Cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §7.3.
"""
from typing import Any, Dict, List

from app.strategies.opus_omnibus_v11 import _SUPPORTED_TFS
from app.strategies.opus_omnibus_v11 import Strategy as _V11Strategy

_DI_RESCUE_DISABLED = float("inf")


class Strategy(_V11Strategy):
    """Routing V11, catalogue et réglages V7. Aucun code de décision propre."""

    name = "opus_omnibus_v7"
    models: Dict[str, str] = {"signal": "omnibus_v4_single"}
    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Drapeaux du backend alignés sur la recette single-horizon : hérités de
    # V11, ils auraient annoncé multi-horizon calibré alors que
    # ``omnibus_v4_single`` dit l'inverse. Ce ne sont qu'un repli — les
    # ``_DEFAULTS`` gagnent à l'entraînement — mais un repli qui ment est pire
    # que pas de repli.
    ml_calibrate = False
    ml_prune_features = False
    ml_multi_horizon = False

    param_space: Dict[str, Any] = {
        # SHORT_TD_HIGH
        "setup_short_td_high_amp_min":     [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":     [0.25, 0.30, 0.35],
        # SHORT_TD — ressuscité par ce preset, donc de nouveau optimisable.
        "setup_short_td_amp_min":          [0.45, 0.50, 0.55],
        "setup_short_td_dir_max":          [0.35, 0.40, 0.45],
        "setup_short_td_tp_mult":          [1.0, 1.2, 1.4],
        "setup_short_td_sl_mult":          [1.4, 1.6, 1.8],
        # SHORT_CHOPPY / LONG_CHOPPY
        "setup_short_choppy_amp_min":      [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":      [0.38, 0.42, 0.46],
        "setup_long_choppy_amp_min":       [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":       [0.55, 0.58, 0.62],
        # LONG_EXIT_TD
        "setup_long_exit_td_amp_min":      [0.35, 0.40, 0.45],
        "setup_long_exit_td_max_bars":     [4, 6, 8, 10],
        # LONG_RANGE_STRICT
        "setup_long_range_strict_amp_min": [0.55, 0.60, 0.65],
        "setup_long_range_strict_dir_min": [0.55, 0.60, 0.65],
        "exit_td_window_bars":             [2, 3, 4],
    }

    # Hyperparamètres d'entraînement figés (hors espace de recherche) : les
    # échantillonner invalide le cache d'entraînement process-wide entre les
    # trials de l'optimiseur.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":    0.30,
        "label_horizons": [1],          # doit rester aligné sur la recette
        "warmup_bars":    2000,
        "retrain_every":  800,
        "n_estimators":   500,
        "num_leaves":     31,
        "learning_rate":  0.03,
        "di_rescue":      _DI_RESCUE_DISABLED,
        "calibrate":      False,
        "prune_features": False,
    }

    _DEFAULTS = {
        **_V11Strategy._DEFAULTS,
        "warmup_bars":     2000,
        "label_horizons":  [1],
        "di_rescue":       _DI_RESCUE_DISABLED,
        "calibrate":       False,
        "prune_features":  False,
        "log_training":    False,
        # ── Catalogue de setups V7 ──────────────────────────────────────────
        "setup_short_td_enabled":           True,
        "setup_short_td_priority":          1,
        "setup_short_td_amp_min":           0.50,
        "setup_short_td_dir_max":           0.40,
        "setup_signal_up_enabled":          False,
        "setup_long_tu_enabled":            False,
        "setup_long_range_light_enabled":   False,
        "setup_long_exit_td_priority":      3,
        "setup_long_range_strict_priority": 4,
    }
