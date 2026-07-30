"""Opus Omnibus V10 — variante *sans ML* : **preset de V11**, plus un fork.

Deux réglages séparent ce preset de ``opus_omnibus_v11_no_ml``, tous deux
déjà exprimables en paramètres :

======================  ======================================================
``di_rescue: inf``      V10 classe le régime sur 3 conditions (ADX + alignement
                        SMA) ; V11 ajoute un rattrapage DI qui requalifie un
                        Choppy directionnel en Trend. ``inf`` rend ce
                        rattrapage inatteignable, donc restitue exactement le
                        classifieur de V10 — même mécanisme, même preuve que
                        ``opus_omnibus_v10_retrained`` (2 520 combinaisons du
                        domaine, avec contre-épreuve à ``di_rescue=10``).
recette ``proxy``       ``p_event``/``p_up`` viennent de ``ProxyPredictor``
                        (colonnes ``_pre_*``) au lieu de LightGBM.
======================  ======================================================

Le routing lui-même n'a **rien** de propre : les 8 setups du fork sont, champ
par champ, les 8 premiers de ``opus_omnibus_v11._DEFAULT_SETUPS`` (V11 en
porte deux de plus, désactivés — ``enabled: False`` est le tout premier test
de ``_evaluate_setup``, donc inerte). ``_check_early_exit`` et
``_signal_up_dynamic_risk`` sont identiques aux fonctions de V11 aux noms de
setups désactivés près.

**Ce que la bascule change — et pourquoi.** 76,7 % des barres de décision sont
identiques au fork (120 barres BTC/USDC 1h, 2 changements de side/setup,
15 → 16 signaux). Le champ qui bouge n'est PAS celui qu'on attendrait :

===============  ==============================================================
``p_event``      identique — 0 écart sur 120
``p_up``         identique — 0 écart sur 120
``regime``       **28 écarts sur 120**
===============  ==============================================================

Les proxys sont donc rigoureusement les mêmes des deux côtés ; ce qui change
est la **source de l'ADX**. Le fork lisait ``_pre_adx14``
(``app.core.indicators_precompute``), V11 lit ``ADX`` du catalogue de features
V4 (``app.ml.backend.features``) — et ces deux séries ne mesurent pas la même
chose : corrélation 0.75, écart absolu moyen 9.5 points, et le verdict
``ADX ≥ 20`` diffère sur **21,6 %** des barres. Cause : le premier lisse en
``ewm_mean(span=14)`` (α = 2/15), le second en α = 1/14 — le lissage de
Wilder, seul conforme à la définition de l'ADX. Un ``span=14`` équivaut à un
Wilder de période 7,5.

La bascule fait donc passer cette variante d'un ADX deux fois trop réactif à
l'ADX correct. C'est un **changement de comportement assumé**, pas une
refactorisation neutre. L'incohérence elle-même est antérieure et dépasse ce
fichier : 17 stratégies lisent ``_pre_adx14`` (cf.
docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter).

Mesure reproductible : ``scripts/compare_no_ml_preset.py <fork.py>
opus_omnibus_v10_no_ml``.
"""
from typing import Any, Dict, List

from app.strategies.opus_omnibus_v11 import _SUPPORTED_TFS
from app.strategies.opus_omnibus_v11_no_ml import Strategy as _V11NoMLStrategy

# Rattrapage DI inatteignable ⇒ classifieur 3-conditions de V10. ``inf`` plutôt
# qu'un grand nombre arbitraire : l'intention (« désactivé ») est lisible, et
# aucune valeur de DI_diff ne peut le franchir par accident.
_DI_RESCUE_DISABLED = float("inf")


class Strategy(_V11NoMLStrategy):
    """Routing V11, régime V10, prédictions par proxys d'indicateurs."""

    name = "opus_omnibus_v10_no_ml"
    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Espace de recherche V10 : plus large que celui de V11 sur les bornes
    # directionnelles (``dir_min``/``dir_max``), conservées ici parce qu'elles
    # définissent ce que CE preset explore.
    param_space: Dict[str, Any] = {
        **_V11NoMLStrategy.param_space,
        "setup_signal_up_dir_min":         [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":     [0.25, 0.30, 0.35],
        "setup_long_choppy_dir_min":       [0.55, 0.58, 0.62],
        "setup_short_choppy_dir_max":      [0.38, 0.42, 0.46],
        "setup_long_tu_dir_min":           [0.58, 0.62, 0.66],
        "setup_long_tu_needs_adx_above":   [22.0, 25.0, 28.0],
    }

    _DEFAULTS = {
        **_V11NoMLStrategy._DEFAULTS,
        "di_rescue": _DI_RESCUE_DISABLED,
    }
