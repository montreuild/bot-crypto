"""Opus Omnibus V8 — variante *sans ML* : **preset de V11**, plus un fork.

V8 précède la lignée V10/V11 : il porte 7 setups (dont ``SHORT_TD``, que V10 a
abandonné), sans ``LONG_TU`` ni ``LONG_RANGE_LIGHT``, avec des priorités
décalées et un seuil d'inversion directionnelle plus tolérant. Rien de tout
cela n'est du *code* propre à V8 — ce sont des **valeurs**, et V11 les expose
déjà toutes en paramètres. D'où ce preset, sans routing local.

Table de correspondance, écrite avant le code :

=========================  ====================================================
``di_rescue: inf``         V8 classe le régime sur 3 conditions (ADX +
                           alignement SMA), comme V10 : ``inf`` rend
                           inatteignable le rattrapage DI ajouté par V11.
``SHORT_TD`` réactivé      V11 le porte désactivé avec les valeurs de V9
                           (amp 0.55 / dir_max 0.35). V8 l'utilisait à
                           0.50 / 0.40, en priorité 1 — d'où les quatre
                           surcharges.
``LONG_TU`` désactivé      N'existe pas dans V8 (introduit par V9/V10).
``LONG_RANGE_LIGHT``       Idem — introduit par V10.
désactivé
priorités décalées         V8 : ``LONG_EXIT_TD`` 3, ``LONG_RANGE_STRICT`` 4.
                           V11 : 4 et 5, parce que ``LONG_TU`` s'est inséré
                           en 3. Sans ``LONG_TU``, il faut refermer le trou —
                           l'ordre relatif compte, pas les valeurs absolues.
``dir_inv_long: 0.45``     Seuil de sortie anticipée sur inversion
                           directionnelle. V10/V11 l'ont resserré à 0.42.
``signal_up_dynamic_risk`` V8 n'ajustait pas le sizing de ``SIGNAL_UP`` selon
``: False``                le régime — mécanisme introduit par V10.
recette ``proxy``          ``p_event``/``p_up`` viennent de ``ProxyPredictor``.
=========================  ====================================================

**Ce que la bascule change.** Comme pour ``opus_omnibus_v10_no_ml``, le
réalignement fait passer cette variante de ``_pre_adx14`` (lissé en
``span=14``, α = 2/15) à l'``ADX`` du catalogue V4 (α = 1/14, lissage de
Wilder) : deux séries qui ne mesurent pas la même chose — le verdict
``ADX ≥ 20`` diffère sur 21,6 % des barres. C'est un **changement de
comportement assumé**, vers l'ADX conforme à sa définition. Cf.
docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter, et
``scripts/compare_no_ml_preset.py <fork.py> opus_omnibus_v8_no_ml`` pour la
mesure.
"""
from typing import Any, Dict, List

from app.strategies.opus_omnibus_v11 import _SUPPORTED_TFS
from app.strategies.opus_omnibus_v11_no_ml import Strategy as _V11NoMLStrategy

_DI_RESCUE_DISABLED = float("inf")


class Strategy(_V11NoMLStrategy):
    """Routing V11, réglages V8, prédictions par proxys d'indicateurs."""

    name = "opus_omnibus_v8_no_ml"
    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        **_V11NoMLStrategy.param_space,
        # Bornes directionnelles : V8 les explorait, V11 les a retirées de son
        # espace au profit des seuls seuils d'amplitude.
        "setup_signal_up_dir_min":         [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":     [0.25, 0.30, 0.35],
        "setup_short_td_amp_min":          [0.45, 0.50, 0.55],
        "setup_short_td_dir_max":          [0.35, 0.40, 0.45],
        "setup_long_choppy_dir_min":       [0.55, 0.58, 0.62],
        "setup_short_choppy_dir_max":      [0.38, 0.42, 0.46],
        "setup_long_range_strict_dir_min": [0.55, 0.60, 0.65],
    }

    _DEFAULTS = {
        **_V11NoMLStrategy._DEFAULTS,
        "di_rescue":                        _DI_RESCUE_DISABLED,
        "signal_up_dynamic_risk":           False,
        "early_exit_dir_inv_long":          0.45,
        # ── Catalogue de setups V8 ──────────────────────────────────────────
        "setup_short_td_enabled":           True,
        "setup_short_td_priority":          1,
        "setup_short_td_amp_min":           0.50,
        "setup_short_td_dir_max":           0.40,
        "setup_long_tu_enabled":            False,
        "setup_long_range_light_enabled":   False,
        "setup_long_exit_td_priority":      3,
        "setup_long_range_strict_priority": 4,
    }
