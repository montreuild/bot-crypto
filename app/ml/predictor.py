"""Contrat de prédicteur — « features → têtes de prédiction ».

Un **prédicteur** est l'objet runtime qui répond à la seule question que le
routing d'une stratégie pose au ML : *pour cette barre, que valent `p_event`
et `p_up` ?* Il ne sait rien des setups, des seuils ni du sizing ; le routing
ne sait rien du format de persistance, du catalogue de features ni du fait
qu'un modèle existe.

Pourquoi ce contrat (cf. docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §4.2) : la
manière d'obtenir une prédiction coûtait jusqu'ici **un fichier de stratégie
complet**. Un même routing existait en trois exemplaires — modèle figé, modèle
ré-entraîné, proxys d'indicateurs — alors que seule la source de prédiction
changeait. Le prédicteur est cette source, rendue interchangeable.

Quatre implémentations, toutes extraites de code existant :

===========================  ==========================================
``LgbmBundlePredictor``      bundle amplitude+direction V4 (3 fichiers)
``LgbmScalerPredictor``      format ``save_lgb_with_scaler`` (48 features)
``LgbmSinglePredictor``      un seul booster + meta (``ml_dynamic_threshold``)
``ProxyPredictor``           aucun artefact — proxys d'indicateurs
===========================  ==========================================

``ProxyPredictor`` est la ligne qui unifie tout : une variante « sans ML »
n'est pas une autre stratégie, c'est le même routing branché sur un prédicteur
qui ne consulte aucun artefact. Son implémentation est reprise **à
l'identique** des cinq variantes ``*_no_ml``, dont les fonctions de proxy
avaient un AST rigoureusement identique.

**Ce que le contrat ne normalise volontairement PAS** : le type de
``features``. Le catalogue V4 produit un ``pl.DataFrame`` de 462 colonnes ;
``scoring_statistique_opus_v4`` produit un ``np.ndarray`` de 48 colonnes ;
``ml_dynamic_threshold`` a le sien. Prédicteur et constructeur de features
viennent de la MÊME recette, donc ils s'accordent par construction. Imposer
une représentation unique obligerait à convertir dans les deux sens pour rien,
et effacerait le fait que ce sont des modèles différents — pas des variantes
d'un même modèle.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "Predictor", "LgbmBundlePredictor", "LgbmScalerPredictor",
    "LgbmSinglePredictor", "ProxyPredictor", "StatePredictor",
    "PROXY_SPEC", "build_predictor",
]


@runtime_checkable
class Predictor(Protocol):
    """Ce qu'une stratégie peut demander à sa source de prédiction.

    ``heads`` nomme les sorties disponibles (``("amp", "dir")`` pour les
    recettes omnibus, ``("dir",)`` pour ``ml_dynamic_threshold``). Une tête
    absente vaut ``None`` dans le dict retourné — jamais 0.5, qui ferait
    passer « pas de modèle » pour « le modèle hésite ».
    """

    heads: Tuple[str, ...]
    recipe: Optional[str]
    version_id: Optional[str]

    def predict_last(self, features: Any, tf: str) -> Dict[str, Optional[float]]:
        """Prédictions pour la DERNIÈRE barre (live, scan)."""
        ...

    def predict_series(self, features: Any, tf: str) -> Dict[str, Optional[np.ndarray]]:
        """Prédictions pour TOUTES les barres (scanner, backtest vectorisé)."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
#  1. Bundle amplitude + direction V4 (format majoritaire du dépôt)
# ─────────────────────────────────────────────────────────────────────────────
class LgbmBundlePredictor:
    """``{path}.amp.lgb`` + ``{path}.dir.lgb`` + ``{path}.meta.json``.

    Amplitude et direction peuvent porter des listes de features et des
    médianes DISTINCTES (le pack V4 le faisait) : ``dir_features``/
    ``dir_medians`` valent ``None`` quand elles sont partagées, cas courant.
    """

    heads: Tuple[str, ...] = ("amp", "dir")

    def __init__(self, bundle: Dict[str, Any], *, recipe: Optional[str] = None,
                 version_id: Optional[str] = None):
        self._b = bundle
        self.recipe = recipe
        self.version_id = version_id

    @classmethod
    def from_artifact(cls, path_prefix: str, *, recipe: Optional[str] = None,
                      version_id: Optional[str] = None) -> Optional["LgbmBundlePredictor"]:
        from app.ml.backend.persistence import load_amp_dir_bundle
        b = load_amp_dir_bundle(path_prefix)
        if b is None or b.get("amp_model") is None or not b.get("features"):
            return None
        return cls(b, recipe=recipe, version_id=version_id)

    def _resolve(self, head: str):
        b = self._b
        if head == "amp":
            return b.get("amp_model"), b.get("amp_cal"), b.get("features"), b.get("medians")
        feats = b.get("dir_features") if b.get("dir_features") is not None else b.get("features")
        meds = b.get("dir_medians") if b.get("dir_medians") is not None else b.get("medians")
        return b.get("dir_model"), b.get("dir_cal"), feats, meds

    def predict_series(self, features, tf: str) -> Dict[str, Optional[np.ndarray]]:
        from app.ml.backend.predictor import predict_batch_raw
        out: Dict[str, Optional[np.ndarray]] = {}
        for head in self.heads:
            model, cal, cols, meds = self._resolve(head)
            out[head] = predict_batch_raw(model, cal, cols, meds or {}, features)
        return out

    def predict_last(self, features, tf: str) -> Dict[str, Optional[float]]:
        series = self.predict_series(features.tail(1), tf)
        return {k: (float(v[-1]) if v is not None and len(v) else None)
                for k, v in series.items()}


# ─────────────────────────────────────────────────────────────────────────────
#  2. Format save_lgb_with_scaler — matrice de features déjà construite
# ─────────────────────────────────────────────────────────────────────────────
class LgbmScalerPredictor:
    """``scoring_statistique_opus_v4/v5`` : deux boosters, pas de liste de
    features sérialisée, entrée = ``np.ndarray`` (n, 48) produit par le
    ``_build_features(df, adx_threshold)`` de la recette.

    L'absence de liste de features dans le meta.json est ce qui faisait
    répondre ``unsupported_format`` au scorer générique : ici ce n'est plus un
    problème, puisque le prédicteur vient de la même recette que le
    constructeur de features et connaît donc l'ordre des colonnes.
    """

    heads: Tuple[str, ...] = ("amp", "dir")

    def __init__(self, amp_model, dir_model, *, recipe: Optional[str] = None,
                 version_id: Optional[str] = None):
        self._amp, self._dir = amp_model, dir_model
        self.recipe = recipe
        self.version_id = version_id

    @classmethod
    def from_artifact(cls, path_prefix: str, *, recipe: Optional[str] = None,
                      version_id: Optional[str] = None) -> Optional["LgbmScalerPredictor"]:
        from app.ml.backend.persistence import load_lgb_with_scaler
        payload = load_lgb_with_scaler(path_prefix)
        if payload is None or payload.get("amp_model") is None:
            return None
        return cls(payload["amp_model"], payload.get("dir_model"),
                   recipe=recipe, version_id=version_id)

    @staticmethod
    def _predict(model, X: np.ndarray) -> Optional[np.ndarray]:
        if model is None or X is None or len(X) == 0:
            return None
        try:
            Xc = np.where(np.isfinite(X), X, 0.0).astype(np.float64)
            return np.asarray(model.predict(Xc), dtype=float)
        except Exception as e:
            logger.warning(f"[Predictor] LgbmScaler : prédiction KO ({e})")
            return None

    def predict_series(self, features, tf: str) -> Dict[str, Optional[np.ndarray]]:
        X = np.asarray(features)
        return {"amp": self._predict(self._amp, X), "dir": self._predict(self._dir, X)}

    def predict_last(self, features, tf: str) -> Dict[str, Optional[float]]:
        X = np.asarray(features)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        series = self.predict_series(X[-1:], tf)
        return {k: (float(v[-1]) if v is not None and len(v) else None)
                for k, v in series.items()}


# ─────────────────────────────────────────────────────────────────────────────
#  3. Booster unique — ml_dynamic_threshold
# ─────────────────────────────────────────────────────────────────────────────
class LgbmSinglePredictor:
    """``{path}.lgb`` + ``{path}.meta.json`` (liste de features). Une seule
    tête : ``dir``. La recette n'a pas de notion d'amplitude — le dire par
    ``heads`` plutôt que de renvoyer un ``p_event`` fabriqué."""

    heads: Tuple[str, ...] = ("dir",)

    def __init__(self, booster, feature_cols, *, recipe: Optional[str] = None,
                 version_id: Optional[str] = None):
        self._booster = booster
        self._cols = list(feature_cols or [])
        self.recipe = recipe
        self.version_id = version_id

    @classmethod
    def from_artifact(cls, path_prefix: str, *, recipe: Optional[str] = None,
                      version_id: Optional[str] = None) -> Optional["LgbmSinglePredictor"]:
        lgb_path, meta_path = f"{path_prefix}.lgb", f"{path_prefix}.meta.json"
        if not (os.path.exists(lgb_path) and os.path.exists(meta_path)):
            return None
        try:
            import lightgbm as lgb
            booster = lgb.Booster(model_file=lgb_path)
            with open(meta_path, "r", encoding="utf-8") as f:
                cols = list(json.load(f).get("features") or [])
        except Exception as e:
            logger.warning(f"[Predictor] LgbmSingle : artefact illisible ({e})")
            return None
        return cls(booster, cols, recipe=recipe, version_id=version_id) if cols else None

    def predict_series(self, features, tf: str) -> Dict[str, Optional[np.ndarray]]:
        from app.ml.backend.predictor import predict_batch_raw
        return {"dir": predict_batch_raw(self._booster, None, self._cols, {}, features)}

    def predict_last(self, features, tf: str) -> Dict[str, Optional[float]]:
        series = self.predict_series(features.tail(1), tf)
        v = series.get("dir")
        return {"dir": float(v[-1]) if v is not None and len(v) else None}


# ─────────────────────────────────────────────────────────────────────────────
#  4. Proxys d'indicateurs — aucun artefact, aucun entraînement, aucun gate
# ─────────────────────────────────────────────────────────────────────────────
def _sig(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _tanh(x: float) -> float:
    return math.tanh(_clip(x, -30.0, 30.0))


def proxy_p_up(*, pdi, ndi, rsi, macd_hist, atr, roc, c, sma50,
               rsi_vel, range_pos, body, gain: float) -> float:
    """Substitut de ``P(hausse)`` à partir de scalaires d'indicateurs.

    Repris à l'identique des cinq variantes ``*_no_ml`` (AST vérifié
    identique dans les cinq) — aucun changement de pondération.
    """
    di    = _tanh((pdi - ndi) / 20.0)
    r     = _clip((rsi - 50.0) / 30.0, -1.0, 1.0)
    macd  = _tanh(macd_hist / (0.5 * atr + 1e-9))
    rocs  = _tanh(roc / 5.0)
    dist  = _tanh(((c - sma50) / sma50 * 15.0) if sma50 > 0 else 0.0)
    rvel  = _tanh(rsi_vel / 15.0)
    rpos  = _clip((range_pos - 0.5) * 2.0, -1.0, 1.0)
    bdy   = _tanh(body * 200.0)
    w = (1.0, 0.7, 0.8, 0.8, 0.8, 0.6, 0.6, 0.4)
    s = (di, r, macd, rocs, dist, rvel, rpos, bdy)
    return _sig(gain * (sum(wi * si for wi, si in zip(w, s)) / sum(w)))


def proxy_p_event(*, atr_pct_r, range_r, volstd_r, volr, adx, body_abs_r,
                  center: float, gain: float) -> float:
    """Substitut de ``P(mouvement ample)``. Repris à l'identique des ``*_no_ml``."""
    def _norm(x):  # ratio centré ~1.0 → [0,1] (0.7→0, 2.0→1)
        return _clip((x - 0.7) / 1.3, 0.0, 1.0)
    a = (_norm(atr_pct_r), _norm(range_r), _norm(volstd_r),
         _clip((volr - 1.0) / 1.5, 0.0, 1.0), _clip(adx / 40.0, 0.0, 1.0),
         _norm(body_abs_r))
    w = (1.0, 0.7, 0.7, 0.7, 0.6, 0.5)
    raw = sum(wi * ai for wi, ai in zip(w, a)) / sum(w)
    return _sig(gain * (raw - center))


#: Colonnes ``_pre_*`` lues par ``ProxyPredictor`` et valeur de repli de
#: chacune. Déclaratif pour que l'appelant sache quoi précalculer.
PROXY_SPEC: Dict[str, float] = {
    "_pre_rsi14": 50.0, "_pre_adx14": 0.0, "_pre_pdi14": 0.0, "_pre_ndi14": 0.0,
    "_pre_atr14": 0.0, "_pre_sma50": 0.0, "_pre_macd_hist": 0.0,
    "_pre_rsi_vel6": 0.0, "_pre_range_pos20": 0.5, "_pre_body": 0.0,
    "_pre_atr_pct_r": 1.0, "_pre_range_r": 1.0, "_pre_volstd20_r": 1.0,
    "_pre_volratio20": 1.0, "_pre_body_abs_r": 1.0, "_pre_sma20": 0.0,
}

# Repli SEULEMENT si l'appelant ne fournit rien. Les valeurs qui comptent sont
# celles de la stratégie consommatrice : les variantes ``*_no_ml`` n'ont pas
# toutes les mêmes, et les confondre change leurs signaux.
_PROXY_DEFAULTS = {"p_up_gain": 2.0, "p_event_center": 0.42, "p_event_gain": 3.0}


class ProxyPredictor:
    """Prédicteur SANS artefact : ``p_event``/``p_up`` dérivés d'indicateurs.

    ``recipe`` vaut ``None`` — rien à entraîner, rien à publier, rien à gater.
    C'est ce qui permet à une variante « sans ML » d'être une simple liaison
    (``models: {signal: "proxy:indicators"}``) au lieu d'un fichier de
    stratégie parallèle qui dérive silencieusement de son jumeau.

    ``features`` est le DataFrame OHLCV **décoré des colonnes ``_pre_*``**
    (cf. ``PROXY_SPEC``), pas un catalogue ML.
    """

    heads: Tuple[str, ...] = ("amp", "dir")
    recipe: Optional[str] = None
    version_id: Optional[str] = None

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        p = params or {}
        self.p_up_gain = float(p.get("p_up_gain", _PROXY_DEFAULTS["p_up_gain"]))
        self.p_event_center = float(p.get("p_event_center", _PROXY_DEFAULTS["p_event_center"]))
        self.p_event_gain = float(p.get("p_event_gain", _PROXY_DEFAULTS["p_event_gain"]))

    @staticmethod
    def _scalars(df) -> Dict[str, float]:
        from app.core.indicators import pre_val
        vals = {k: float(pre_val(df, k) or d) for k, d in PROXY_SPEC.items()}
        c = float(df["close"][-1] or 0.0)
        prev = float(df["close"][-15]) if len(df) > 15 else 0.0
        vals["close"] = c
        vals["roc"] = ((c / prev - 1.0) * 100.0) if prev > 0 else 0.0
        return vals

    def predict_last(self, features, tf: str) -> Dict[str, Optional[float]]:
        v = self._scalars(features)
        p_up = proxy_p_up(
            pdi=v["_pre_pdi14"], ndi=v["_pre_ndi14"], rsi=v["_pre_rsi14"],
            macd_hist=v["_pre_macd_hist"], atr=v["_pre_atr14"], roc=v["roc"],
            c=v["close"], sma50=v["_pre_sma50"], rsi_vel=v["_pre_rsi_vel6"],
            range_pos=v["_pre_range_pos20"], body=v["_pre_body"],
            gain=self.p_up_gain,
        )
        p_event = proxy_p_event(
            atr_pct_r=v["_pre_atr_pct_r"], range_r=v["_pre_range_r"],
            volstd_r=v["_pre_volstd20_r"], volr=v["_pre_volratio20"],
            adx=v["_pre_adx14"], body_abs_r=v["_pre_body_abs_r"],
            center=self.p_event_center, gain=self.p_event_gain,
        )
        return {"amp": p_event, "dir": p_up}

    def predict_series(self, features, tf: str) -> Dict[str, Optional[np.ndarray]]:
        """Non vectorisé : les proxys lisent des scalaires ``_pre_*`` de la
        dernière barre. Boucle explicite plutôt qu'un faux batch — le coût est
        assumé et visible, il n'y a pas de modèle à amortir ici."""
        n = len(features)
        amp = np.empty(n, dtype=float)
        dir_ = np.empty(n, dtype=float)
        for i in range(n):
            out = self.predict_last(features.head(i + 1), tf)
            amp[i], dir_[i] = out["amp"], out["dir"]
        return {"amp": amp, "dir": dir_}


# ─────────────────────────────────────────────────────────────────────────────
#  5. État en mémoire (mode inline) — le modèle n'est pas sur disque
# ─────────────────────────────────────────────────────────────────────────────
class StatePredictor:
    """Vue « prédicteur » d'un ``TrainState`` vivant (``MLBackend``).

    En ``ml_mode="inline"``, le modèle courant est en mémoire et change à
    chaque réentraînement : le routing doit voir la même interface que pour un
    artefact figé, sans savoir lequel des deux il consulte.
    """

    heads: Tuple[str, ...] = ("amp", "dir")

    def __init__(self, backend, *, recipe: Optional[str] = None):
        self._ml = backend
        self.recipe = recipe
        self.version_id = None

    def predict_last(self, features, tf: str) -> Dict[str, Optional[float]]:
        return {"amp": self._ml.predict_amplitude(features, tf),
                "dir": self._ml.predict_direction(features, tf)}

    def predict_series(self, features, tf: str) -> Dict[str, Optional[np.ndarray]]:
        return {"amp": self._ml.predict_series(features, tf, "amp"),
                "dir": self._ml.predict_series(features, tf, "dir")}


# ─────────────────────────────────────────────────────────────────────────────
#  Fabrique — la recette déclare son format, on ne le renifle plus
# ─────────────────────────────────────────────────────────────────────────────
#: ``persistence:`` de la recette → implémentation. C'est ce qui remplace le
#: reniflage de format (et donc ``unsupported_format``) : une recette DIT
#: comment elle persiste, on ne le devine plus depuis les fichiers présents.
class _FabriquePredictor(Protocol):
    """Ce que le registre attend d'une implémentation."""

    def from_artifact(self, path_prefix: str, *, recipe: str = ...,
                      version_id: str = ...) -> Any: ...


_BY_PERSISTENCE: Dict[str, Any] = {
    "lgbm_amp_dir_bundle": LgbmBundlePredictor,
    "lgbm_scaler": LgbmScalerPredictor,
    "lgbm_single": LgbmSinglePredictor,
}


def build_predictor(persistence: str, path_prefix: str, *,
                    recipe: Optional[str] = None,
                    version_id: Optional[str] = None,
                    params: Optional[Dict[str, Any]] = None) -> Optional[Predictor]:
    """Construit le prédicteur d'une recette depuis un artefact sur disque.

    ``persistence="proxy"`` ne lit rien et retourne toujours un prédicteur —
    c'est un mode sans artefact, pas un échec de chargement. Les autres
    retournent ``None`` si l'artefact est absent ou illisible : l'appelant
    décide (entraîner, ou renoncer), le prédicteur ne devine pas à sa place.
    """
    if persistence == "proxy":
        return ProxyPredictor(params)
    impl = _BY_PERSISTENCE.get(persistence)
    if impl is None:
        logger.error(
            f"[Predictor] persistence inconnue : {persistence!r} "
            f"(attendu parmi {sorted(_BY_PERSISTENCE)} ou 'proxy')"
        )
        return None
    return impl.from_artifact(path_prefix, recipe=recipe, version_id=version_id)
