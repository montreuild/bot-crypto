"""La recette — objet de premier ordre décrivant COMMENT un modèle est fait.

Posé par ML-02 §3.1 et §5.5, jamais construit jusqu'ici : le mot « recette »
existait dans le code comme un simple alias du nom de stratégie. Conséquences
mesurées (docs/CONCEPTION_ARCHITECTURE_ML_UNIFIEE.md §1.4) :

- cinq stratégies consommant le même artefact ne pouvaient pas partager une
  entrée de registre — chacune republiait sous son propre nom ;
- ``opus_omnibus_v12`` ré-entraînait une recette strictement identique à celle
  de ``v11`` ;
- ``adx_threshold`` (``scoring_statistique_opus_v4/v5``) et ``lookahead``
  (``ml_dynamic_threshold``) sont balayés par l'optimiseur ET changent le
  modèle, mais n'entraient pas dans ``recipe_hash`` : deux essais produisaient
  des modèles différents sous la même clé.

Une recette est donc un **fichier versionné** (``recipes/*.yaml``) qui décrit
tout ce qui change le modèle, et rien d'autre. Ce qui n'est PAS dans la
recette : les seuils de décision, les setups, le sizing — ils appartiennent à
la stratégie, et deux stratégies aux seuils différents partagent légitimement
un modèle.

Frontière importante — **recette vs exploitation**. La recette déclare ses
conventions (labels, features, hyperparamètres, format de persistance) ; le
bloc ``gate:`` y fixe les valeurs par défaut du gate, mais un opérateur garde
la main via les clés ``gate_*`` du YAML de stratégie. Un arbitrage
d'exploitation ne doit jamais obliger à toucher au code d'une recette.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_RECIPES_DIR = "recipes"

#: Formats de persistance connus — doivent correspondre aux clés de
#: ``app.ml.predictor._BY_PERSISTENCE`` (+ ``proxy``). Verrouillé par test :
#: une recette qui déclare un format sans prédicteur serait inchargeable.
KNOWN_PERSISTENCE = ("lgbm_amp_dir_bundle", "lgbm_scaler", "lgbm_single", "proxy")


@dataclass(frozen=True)
class Recipe:
    """Description complète et immuable d'un modèle à produire.

    ``version`` est un entier bumpé À LA MAIN quand on veut repartir sur une
    nouvelle lignée d'artefacts sans changer de nom. Il entre dans le hash au
    même titre que les hyperparamètres : c'est le moyen explicite de dire
    « ce n'est plus le même modèle », y compris quand rien d'autre n'a bougé
    (changement de version de LightGBM, correction du builder de features…).
    """

    name: str
    version: int = 1
    features_catalog: str = "v4_polars@1"
    features_params: Dict[str, Any] = field(default_factory=dict)
    label_horizons: List[int] = field(default_factory=lambda: [1, 3, 6])
    amp_top_pct: float = 0.30
    #: Schéma de labellisation (``app.ml.labelling``). Déclaré et non déduit
    #: des têtes : deux recettes à tête unique peuvent viser des cibles
    #: différentes. Défaut = comportement historique (quantile d'amplitude).
    label_scheme: str = "amp_dir_quantile"
    heads: List[str] = field(default_factory=lambda: ["amp", "dir"])
    hp: Dict[str, Any] = field(default_factory=dict)
    window_bars: Dict[str, Any] = field(default_factory=dict)
    min_bars: int = 2000
    cadence_bars: int = 0
    gate: Dict[str, Any] = field(default_factory=dict)
    persistence: str = "lgbm_amp_dir_bundle"
    description: str = ""

    # ── Identité ─────────────────────────────────────────────────────────────
    def hash(self) -> str:
        """Empreinte de TOUT ce qui change le modèle produit.

        Inclut ``features_params`` — c'est la correction de la fracture (d) :
        balayer ``adx_threshold`` produit désormais des recettes distinctes,
        donc des lignées d'artefacts distinctes, au lieu d'écraser une clé
        unique. N'inclut PAS ``gate`` (décision d'exploitation : changer un
        seuil de promotion ne change pas le modèle) ni ``description``.
        """
        payload = {
            "name": self.name, "version": self.version,
            "features_catalog": self.features_catalog,
            "features_params": self.features_params,
            "label_horizons": sorted(int(h) for h in self.label_horizons),
            "amp_top_pct": round(float(self.amp_top_pct), 6),
            "label_scheme": self.label_scheme,
            "heads": sorted(self.heads),
            "hp": self.hp,
            "window_bars": self.window_bars,
            "min_bars": int(self.min_bars),
            "persistence": self.persistence,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    # ── Vues pour les consommateurs ──────────────────────────────────────────
    def gate_spec(self) -> Dict[str, Any]:
        """Conventions de scoring, au format attendu par ``GateConfig``."""
        spec: Dict[str, Any] = {
            "label_horizons": list(self.label_horizons),
            "amp_top_pct": float(self.amp_top_pct),
        }
        if "metric" in self.gate:
            spec["metric"] = self.gate["metric"]
        return spec

    def gate_params(self) -> Dict[str, Any]:
        """Défauts de gate sous forme de clés ``gate_*`` — surchargeables par
        le YAML de stratégie, qui a le dernier mot (cf. docstring module)."""
        out: Dict[str, Any] = {}
        for k in ("holdout_bars", "min_window_bars", "auc_floor", "epsilon"):
            if k in self.gate:
                out[f"gate_{k}"] = self.gate[k]
        if "metric" in self.gate:
            out["gate_metric"] = self.gate["metric"]
        return out

    def train_params(self) -> Dict[str, Any]:
        """Paramètres passés à l'entraînement (hyperparamètres + labels)."""
        p = dict(self.hp)
        p["label_horizons"] = list(self.label_horizons)
        p["amp_top_pct"] = float(self.amp_top_pct)
        p.update(self.features_params)
        return p

    def window_for(self, tf: str) -> Optional[int]:
        """Taille de fenêtre d'entraînement pour ce TF. ``None`` = tout
        l'historique fourni. Mesuré en 1h : plus n'est pas toujours mieux
        (cf. §1.5), d'où un réglage PAR TF plutôt qu'un global."""
        w = self.window_bars.get(tf, self.window_bars.get("default"))
        if w in (None, "max", "all"):
            return None
        return int(w)


# ─────────────────────────────────────────────────────────────────────────────
#  Chargement
# ─────────────────────────────────────────────────────────────────────────────
def _coerce(name: str, raw: Dict[str, Any]) -> Recipe:
    feats = raw.get("features") or {}
    labels = raw.get("labels") or {}
    window = raw.get("window") or {}
    persistence = str(raw.get("persistence", "lgbm_amp_dir_bundle"))
    if persistence not in KNOWN_PERSISTENCE:
        raise ValueError(
            f"recette {name!r} : persistence={persistence!r} inconnue "
            f"(attendu parmi {list(KNOWN_PERSISTENCE)})"
        )
    return Recipe(
        name=name,
        version=int(raw.get("version", 1)),
        features_catalog=str(feats.get("catalog", "v4_polars@1")),
        features_params=dict(feats.get("params") or {}),
        label_horizons=[int(h) for h in (labels.get("horizons") or [1, 3, 6])],
        amp_top_pct=float(labels.get("amp_top_pct", 0.30)),
        label_scheme=str(labels.get("scheme", "amp_dir_quantile")),
        heads=list(raw.get("heads") or ["amp", "dir"]),
        hp=dict(raw.get("hp") or {}),
        window_bars=dict(window.get("bars") or {}),
        min_bars=int(window.get("min_bars", 2000)),
        cadence_bars=int(window.get("cadence_bars", 0)),
        gate=dict(raw.get("gate") or {}),
        persistence=persistence,
        description=str(raw.get("description", "")),
    )


@functools.lru_cache(maxsize=64)
def load_recipe(name: str, recipes_dir: str = DEFAULT_RECIPES_DIR) -> Recipe:
    """Charge ``recipes/{name}.yaml``. Lève si absente ou invalide.

    Volontairement STRICT : une recette manquante est une erreur de
    configuration, pas un cas à traiter par un défaut silencieux. Un défaut
    ferait entraîner « quelque chose » sous un nom qui promet autre chose.
    """
    import yaml
    path = os.path.join(recipes_dir, f"{name}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"recette introuvable : {path} — les recettes disponibles sont "
            f"{available_recipes(recipes_dir)}"
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    declared = raw.get("recipe")
    if declared and declared != name:
        raise ValueError(
            f"{path} déclare recipe={declared!r} mais le fichier s'appelle {name!r} — "
            f"le nom de fichier est la clé du registre, il fait foi"
        )
    return _coerce(name, raw)


def available_recipes(recipes_dir: str = DEFAULT_RECIPES_DIR) -> List[str]:
    if not os.path.isdir(recipes_dir):
        return []
    return sorted(f[:-5] for f in os.listdir(recipes_dir) if f.endswith(".yaml"))


# ─────────────────────────────────────────────────────────────────────────────
#  Liaison stratégie → recette
# ─────────────────────────────────────────────────────────────────────────────
def strategy_models(strategy: Any, params: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Recettes consommées par ``strategy``, par rôle.

    Source de vérité, dans l'ordre : le bloc ``models:`` du YAML résolu
    (``params["models"]``), puis l'attribut de classe ``models``. Le YAML
    l'emporte — c'est ce qui permet de rebrancher une stratégie sur une autre
    recette sans toucher au code, et donc de remplacer une famille de fichiers
    par une famille de liaisons.
    """
    from_params = (params or {}).get("models")
    if isinstance(from_params, dict) and from_params:
        return {str(k): str(v) for k, v in from_params.items()}
    declared = getattr(strategy, "models", None) or {}
    return {str(k): str(v) for k, v in declared.items()}


def primary_recipe(strategy: Any, params: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Recette « principale » d'une stratégie — celle que le registre indexe
    et que le gate arbitre. Convention : le rôle ``signal``, sinon le premier
    déclaré. Une stratégie sans modèle retourne ``None``.
    """
    models = strategy_models(strategy, params)
    if not models:
        return None
    return models.get("signal") or next(iter(models.values()))
