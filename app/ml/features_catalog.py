"""Catalogues de features — le chaînon qui manquait à la recette.

``recipes/*.yaml`` déclare ``features.catalog`` (``v4_polars@1``, ``stat48@1``,
``dyn_threshold@1``) depuis l'étape B. Mais ce nom n'était **dispatché nulle
part** : il n'entrait que dans ``Recipe.hash()``. Autrement dit, la recette
disait quelles features elle voulait, et rien dans le dépôt ne savait les
construire à partir de cette déclaration — seule une classe ``Strategy`` le
savait, chacune à sa façon.

C'est la moitié écriture de l'asymétrie relevée par §2 de la conception :

    lecture   ``build_predictor(persistence, path)``   → piloté par la recette
    écriture  ``mod.Strategy().fit(df, …)``            → piloté par la stratégie

Ce module ferme cette moitié. Un catalogue est une fonction
``(df, **params) -> FeatureSet``, enregistrée sous le nom exact que porte la
recette. À partir de là, entraîner ne demande plus qu'une recette.

**Contrat uniforme : les features sont NOMMÉES.** Les trois constructeurs
existants ne s'accordaient pas — ``v4_polars`` et ``dyn_threshold`` rendent un
DataFrame à colonnes nommées, ``stat48`` un ``np.ndarray`` anonyme. Cette
anonymie n'est pas un détail de forme : c'est la cause exacte du
``unsupported_format`` qui empêche le gate de scorer les artefacts
``stat48_v4``/``stat48_v5`` (``app.ml.scoring.score_amp_dir_bundle`` a besoin
de la liste des features pour reconstruire la matrice d'entrée). Nommer les
colonnes de ``stat48`` dans l'ordre exact de leur construction est donc un
prérequis, pas un embellissement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureSet:
    """Sortie normalisée d'un catalogue.

    ``frame`` porte les colonnes de features ET les colonnes OHLCV/temps
    nécessaires à la labellisation (au minimum ``close``) : le labelleur
    travaille sur les MÊMES lignes que les features, sans réalignement à la
    charge de l'appelant — c'est là que se logent les décalages d'un indice.
    """

    frame: pl.DataFrame
    names: List[str]

    def __len__(self) -> int:
        return len(self.frame)

    def matrix(self, dtype=np.float32) -> np.ndarray:
        """Matrice ``(n, len(names))`` dans l'ordre déclaré par ``names``."""
        return self.frame.select(self.names).to_numpy().astype(dtype)


#: Constructeurs par nom de catalogue (valeur exacte de ``features.catalog``).
_CATALOGS: Dict[str, Callable[..., Optional[FeatureSet]]] = {}


def register_catalog(name: str, builder: Callable[..., Optional[FeatureSet]]) -> None:
    """Déclare un catalogue. Permet d'en brancher un hors dépôt sans toucher ici."""
    _CATALOGS[name.strip()] = builder


def available_catalogs() -> List[str]:
    return sorted(_CATALOGS)


def build(catalog: str, df: pl.DataFrame,
          params: Optional[Dict[str, Any]] = None) -> Optional[FeatureSet]:
    """Construit les features déclarées par ``catalog``.

    ``None`` si l'historique est trop court pour ce catalogue — c'est un
    résultat, pas une erreur : l'appelant décide (élargir la fenêtre, renoncer).
    Lève ``KeyError`` sur un catalogue inconnu : une recette qui déclare un
    catalogue inexistant est une erreur de configuration, pas un cas à traiter
    par un défaut silencieux (même parti pris que ``load_recipe``).
    """
    builder = _CATALOGS.get(str(catalog).strip())
    if builder is None:
        raise KeyError(
            f"Catalogue de features inconnu : {catalog!r} — connus : "
            f"{available_catalogs()}. Utilisez register_catalog() pour en "
            f"déclarer un autre."
        )
    return builder(df, **(params or {}))


# ─────────────────────────────────────────────────────────────────────────────
#  v4_polars@1 — catalogue V4 (~437 colonnes), déjà nommé
# ─────────────────────────────────────────────────────────────────────────────
def _build_v4_polars(df: pl.DataFrame, **_ignored) -> Optional[FeatureSet]:
    """Le catalogue historique. ``**_ignored`` : la recette peut porter des
    paramètres qui concernent les labels ou les HP, pas ce constructeur."""
    from app.ml.backend.features import build_features, select_feature_columns
    feats = build_features(df)
    if feats is None or len(feats) == 0:
        return None
    return FeatureSet(frame=feats, names=select_feature_columns(feats))


# ─────────────────────────────────────────────────────────────────────────────
#  stat48@1 — 56 colonnes construites en ndarray, désormais nommées
# ─────────────────────────────────────────────────────────────────────────────
def stat48_feature_names() -> List[str]:
    """Noms des colonnes de ``stat48``, dans l'ordre EXACT de construction de
    ``scoring_statistique_opus_v4._build_features``.

    L'ordre est le contrat : la matrice est empilée par ``np.column_stack``, un
    nom mal placé associerait silencieusement une importance à la mauvaise
    feature. Verrouillé par test contre la vraie sortie du constructeur.

    Note : la docstring d'origine annonce « 48 features » et les recettes
    s'appellent ``stat48_*``, mais le constructeur en produit **56** — le bloc
    BB (largeur + rang centile, 2 × 4 lags) a été ajouté sans que le décompte
    suive. Le nom des recettes est conservé : c'est une clé de registre, la
    renommer romprait la lignée d'artefacts pour une correction cosmétique.
    """
    from app.strategies.scoring_statistique_opus_v4 import (
        _AMP_BASE_COLS,
        _DIR_BASE_COLS,
        _LAGS,
    )
    names: List[str] = []
    for col in _AMP_BASE_COLS:                       # 4 × 4 lags = 16
        names += [f"{col.lstrip('_')}_lag{lag}" for lag in _LAGS]
    for lag in _LAGS:                                # 2 × 4 lags = 8
        names += [f"bb_width_lag{lag}", f"bb_rank100_lag{lag}"]
    for col in _DIR_BASE_COLS:                       # 7 × 4 lags = 28
        names += [f"{col.lstrip('_')}_lag{lag}" for lag in _LAGS]
    names += ["regime_norm", "di_bias", "trend_strength", "adx_norm"]   # 4
    return names


def _build_stat48(df: pl.DataFrame, adx_threshold: float = 20.0,
                  **_ignored) -> Optional[FeatureSet]:
    from app.strategies.scoring_statistique_opus_v4 import _build_features
    X = _build_features(df, adx_threshold=float(adx_threshold))
    if X is None or len(X) == 0:
        return None
    names = stat48_feature_names()
    if X.shape[1] != len(names):
        # Ne jamais deviner : des noms décalés fausseraient les importances et
        # la matrice rechargée à l'inférence.
        raise RuntimeError(
            f"stat48 : {X.shape[1]} colonnes construites pour {len(names)} noms "
            f"connus — stat48_feature_names() n'est plus aligné sur "
            f"_build_features()."
        )
    frame = pl.DataFrame({n: X[:, j] for j, n in enumerate(names)})
    # Colonnes de labellisation, alignées ligne à ligne avec les features.
    passthrough = [c for c in ("time", "close") if c in df.columns]
    if passthrough:
        frame = pl.concat([df.select(passthrough), frame], how="horizontal")
    return FeatureSet(frame=frame, names=names)


# ─────────────────────────────────────────────────────────────────────────────
#  dyn_threshold@1 — catalogue « propre » (~30 colonnes), déjà nommé
# ─────────────────────────────────────────────────────────────────────────────
def _build_dyn_threshold(df: pl.DataFrame, **_ignored) -> Optional[FeatureSet]:
    from app.strategies.ml_dynamic_threshold import compute_features
    feats = compute_features(df)
    if feats is None or len(feats) == 0:
        return None
    names = list(feats.columns)
    passthrough = [c for c in ("time", "close") if c in df.columns
                   and c not in feats.columns]
    frame = (pl.concat([df.select(passthrough), feats], how="horizontal")
             if passthrough else feats)
    return FeatureSet(frame=frame, names=names)


register_catalog("v4_polars@1", _build_v4_polars)
register_catalog("stat48@1", _build_stat48)
register_catalog("dyn_threshold@1", _build_dyn_threshold)


def from_matrix(catalog: str, X, frame: pl.DataFrame) -> FeatureSet:
    """Emballe une matrice DÉJÀ construite dans le contrat du catalogue.

    Sert aux appelants qui possèdent leur propre cache de features — le
    backtest en tête, dont ``prepare_for_backtest`` pré-calcule la matrice pour
    chaque valeur d'``adx_threshold`` du param_space. Sans ce point d'entrée,
    router leur entraînement par ``recipe_trainer`` reconstruirait les features
    à chaque réentraînement walk-forward : correction fonctionnelle payée d'une
    régression de performance sur la boucle chaude.

    ``frame`` fournit les colonnes de labellisation (``close``, ``time``) ;
    seules ses ``len(X)`` premières lignes sont retenues, la matrice faisant foi.
    """
    names = _NAMES_BY_CATALOG.get(str(catalog).strip())
    if names is None:
        raise KeyError(f"Catalogue {catalog!r} sans nommage statique — "
                       f"connus : {sorted(_NAMES_BY_CATALOG)}")
    cols = names()
    if X.shape[1] != len(cols):
        raise RuntimeError(f"{catalog} : {X.shape[1]} colonnes pour {len(cols)} noms")
    out = pl.DataFrame({n: X[:, j] for j, n in enumerate(cols)})
    passthrough = [c for c in ("time", "close") if c in frame.columns]
    if passthrough:
        out = pl.concat([frame.select(passthrough).head(len(X)), out], how="horizontal")
    return FeatureSet(frame=out, names=cols)


#: Catalogues dont les noms de colonnes sont connus sans construire la matrice.
#: Seul ``stat48`` en a besoin : les deux autres rendent déjà un frame nommé,
#: donc n'ont aucune raison de passer par ``from_matrix``.
_NAMES_BY_CATALOG: Dict[str, Callable[[], List[str]]] = {
    "stat48@1": stat48_feature_names,
}
