"""Opus Omnibus V11 — variante *sans ML* : **preset de V11**, plus un fork.

``p_event`` et ``p_up`` viennent de proxys d'indicateurs (``ProxyPredictor``,
recette ``proxy_indicators``) au lieu de modèles LightGBM. Tout le reste — les
setups, leur sélection, les sorties anticipées, le sizing — est **le routing de
V11**, plus une copie.

**Pourquoi c'était un problème.** Ce fichier était un fork autonome de 548
lignes. Sur les 9 fonctions qu'il partageait avec V11, 8 avaient divergé : il
avait notamment perdu les conditions ``needs_bearish_excess`` et
``needs_rsi_below``, et fusionné le test ``regime == TREND_DN`` dans la garde
``exit_td``. Rien ne le signalait, et ses huit setups étaient pourtant déclarés
identiques à ceux de V11 — une correction du routing V11 n'arrivait jamais
jusqu'ici.

**Ce que la bascule change.** Elle NE conserve PAS le comportement du fork :
elle le réaligne sur V11. C'est l'objet même de l'opération — le fork était en
retard, pas volontairement différent — mais c'est un **changement de
comportement**, mesuré et documenté, pas une refactorisation neutre. Ampleur
mesurable avec ``scripts/compare_no_ml_preset.py``.

*Précision sur la CAUSE de cet écart*, obtenue après coup en ventilant la
mesure champ par champ. Les conditions de setup perdues (ci-dessus) sont
réelles, mais ce n'est pas ce qui domine le chiffre : sur 120 barres,
``p_event`` et ``p_up`` sont **identiques** (0 écart — les proxys sont les
mêmes des deux côtés) tandis que ``regime`` diffère **38 fois**. Le moteur du
changement est donc la **source de l'ADX** : le fork lisait ``_pre_adx14``
(lissage ``span=14``, α = 2/15), V11 lit l'``ADX`` du catalogue V4 (α = 1/14,
le lissage de Wilder). Les deux séries ne mesurent pas la même chose —
corrélation 0.75, verdict ``ADX ≥ 20`` différent sur 21,6 % des barres. Cf.
docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter.

Ce que le preset garde en propre : les gains des proxys, qui sont ses vrais
paramètres de réglage et n'ont aucun équivalent côté ML.
"""
from typing import Any, Dict, List, Optional, Tuple

import polars as pl

from app.ml.predictor import ProxyPredictor
from app.strategies.opus_omnibus_v11 import _SUPPORTED_TFS
from app.strategies.opus_omnibus_v11 import Strategy as _V11Strategy

_PROXY_KEYS = ("p_up_gain", "p_event_center", "p_event_gain")


class Strategy(_V11Strategy):
    """Routing V11, prédictions par proxys d'indicateurs."""

    name = "opus_omnibus_v11_no_ml"
    models: Dict[str, str] = {"signal": "proxy_indicators"}
    timeframes: List[str] = list(_SUPPORTED_TFS)

    #: Aucun artefact : ni entraînement, ni gate, ni warmup de modèle.
    uses_artifact: bool = False

    param_space: Dict[str, List] = {
        **_V11Strategy.param_space,
        # Gains des proxys — ce que CE preset a de propre à explorer.
        "p_up_gain":      [1.5, 2.0, 2.5],
        "p_event_gain":   [2.5, 3.0, 4.0],
        "p_event_center": [0.38, 0.42, 0.46],
    }
    # Rien à figer : sans artefact, aucun hyperparamètre d'entraînement n'a à
    # être soustrait à l'espace de recherche.
    fixed_params: Dict[str, Any] = {}

    _DEFAULTS = {
        **_V11Strategy._DEFAULTS,
        # Valeurs du fork d'origine — reprises telles quelles : ce sont les
        # réglages sous lesquels cette variante a été backtestée.
        "p_up_gain":      2.0,
        "p_event_center": 0.42,
        "p_event_gain":   3.0,
        "log_training":   False,
    }

    def min_bars_required(self, params: dict = None) -> int:
        """Sans entraînement, il ne faut que de quoi calculer les indicateurs —
        pas les milliers de barres d'un warmup de modèle."""
        return 260

    def _predict_heads(self, df: pl.DataFrame, features: pl.DataFrame,
                       tf: str, params: Dict[str, Any]
                       ) -> Tuple[Optional[float], Optional[float]]:
        """Les proxys lisent les colonnes ``_pre_*`` des bougies, pas le
        catalogue V4 — d'où le double passage du point d'injection."""
        cfg = {k: float((params or {}).get(k, self._DEFAULTS[k])) for k in _PROXY_KEYS}
        out = ProxyPredictor(cfg).predict_last(df, tf)
        return out.get("amp"), out.get("dir")
