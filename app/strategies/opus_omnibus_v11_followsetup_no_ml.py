"""Opus Omnibus V11 FollowSetup — variante *sans ML* : **preset**, plus un fork.

``opus_omnibus_v11_followsetup`` tient une position tant que le setup actif
pointe dans sa direction et ne la ferme que sur setup **opposé** confirmé
(anti-whipsaw : confirmation sur K bougies, cooldown, score minimum,
hystérésis) — pas de TP, pas de trailing, pas de timeout serré. Ce fichier
reprend ce routing à l'identique en remplaçant seulement la source de
``p_event``/``p_up`` : ``ProxyPredictor`` (recette ``proxy_indicators``, lue
sur les colonnes ``_pre_*``) au lieu des modèles LightGBM.

Il n'y avait **rien d'autre** à conserver du fork de 495 lignes : ses 7 setups
sont, champ par champ, ceux de son jumeau ML, et ses ``_DEFAULTS`` de routing
(seuils de flip, cooldown, SL de sécurité, filtre horaire désactivé) sont
identiques. Seuls les gains des proxys lui appartiennent en propre.

**Ce que la bascule change.** Comme les autres variantes ``_no_ml``, le
réalignement fait passer la mesure de régime de ``_pre_adx14`` (lissé en
``span=14``, α = 2/15) à l'``ADX`` du catalogue V4 (α = 1/14, lissage de
Wilder) : le verdict ``ADX ≥ 20`` diffère sur 21,6 % des barres. Changement de
comportement assumé — vers l'ADX conforme à sa définition. Cf.
docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §8ter.

**Note de séquencement.** Le chantier §7 (fusion omnibus complète) prévoit
d'absorber ``opus_omnibus_v11_followsetup`` dans ``opus_omnibus_v11`` sous la
forme d'un mode ``follow_setup``. Ce preset suivra alors son parent sans
changer : il ne déclare que des valeurs, aucun routing.
"""
from typing import Any, Dict, List, Optional, Tuple

import polars as pl

from app.ml.predictor import ProxyPredictor
from app.strategies.opus_omnibus_v11_followsetup import _SUPPORTED_TFS
from app.strategies.opus_omnibus_v11_followsetup import (
    Strategy as _FollowSetupStrategy,
)

_PROXY_KEYS = ("p_up_gain", "p_event_center", "p_event_gain")


class Strategy(_FollowSetupStrategy):
    """Routing FollowSetup, prédictions par proxys d'indicateurs."""

    name = "opus_omnibus_v11_followsetup_no_ml"
    models: Dict[str, str] = {"signal": "proxy_indicators"}
    timeframes: List[str] = list(_SUPPORTED_TFS)

    #: Aucun artefact : ni entraînement, ni gate, ni warmup de modèle.
    uses_artifact: bool = False

    param_space: Dict[str, Any] = {
        **_FollowSetupStrategy.param_space,
        # Bornes directionnelles explorées par le fork, absentes de l'espace
        # de son jumeau ML (qui n'optimise que les seuils d'amplitude).
        "setup_signal_up_dir_min":     [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max": [0.25, 0.30, 0.35],
        "setup_long_choppy_dir_min":   [0.55, 0.58, 0.62],
        "setup_short_choppy_dir_max":  [0.38, 0.42, 0.46],
        "setup_long_tu_dir_min":       [0.58, 0.62, 0.66],
        # Gains des proxys — ce que CE preset a de propre à explorer.
        "p_up_gain":      [1.5, 2.0, 2.5],
        "p_event_gain":   [2.5, 3.0, 4.0],
        "p_event_center": [0.38, 0.42, 0.46],
    }
    # Rien à figer : sans artefact, aucun hyperparamètre d'entraînement n'a à
    # être soustrait à l'espace de recherche.
    fixed_params: Dict[str, Any] = {}

    _DEFAULTS = {
        **_FollowSetupStrategy._DEFAULTS,
        # Valeurs du fork d'origine — ce sont les réglages sous lesquels cette
        # variante a été backtestée.
        "p_up_gain":      2.0,
        "p_event_center": 0.42,
        "p_event_gain":   3.0,
        "log_training":   False,
    }

    def min_bars_required(self, params: dict | None = None) -> int:
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
