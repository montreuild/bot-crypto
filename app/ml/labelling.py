"""Schémas de labellisation — l'autre moitié de ce qu'une recette doit dire.

``app.ml.features_catalog`` rend constructibles les ENTRÉES d'un modèle depuis
la seule recette. Il manque la CIBLE : deux recettes du dépôt n'apprennent pas
la même chose, et rien ne le déclarait.

    ``amp_dir_quantile``  (v4_polars, stat48) — deux têtes. Amplitude : le
        rendement absolu dépasse-t-il le quantile ``1 - amp_top_pct`` ?
        Direction : le rendement est-il positif ? Agrégé sur ``horizons``.
    ``vol_adaptive_dir``  (dyn_threshold) — une seule tête, et un seuil
        ADAPTATIF à la volatilité réalisée plutôt qu'un quantile d'amplitude
        fixe. C'est précisément ce que la recette revendique (« la recette qui
        prouve que le scoring ne peut pas être universel ») ; le traiter comme
        les autres produirait un modèle qui n'est pas celui décrit.

Le schéma est déclaré (``labels.scheme:``) et non déduit des têtes : deux
recettes à tête unique peuvent viser des cibles différentes, et deviner
rejouerait exactement la confusion que la recette est censée lever.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

DEFAULT_SCHEME = "amp_dir_quantile"


@dataclass(frozen=True)
class Labels:
    """Cibles alignées sur les ``n`` PREMIÈRES lignes du jeu de features.

    ``n`` est toujours plus petit que le nombre de barres : labelliser la
    barre ``t`` demande de connaître ``t + h``, donc les dernières barres ne
    sont pas labellisables. L'appelant doit tronquer ses features à ``n`` —
    c'est là que se logent les décalages d'un indice, et la seule façon de ne
    pas se tromper est que le labelleur annonce lui-même sa longueur.
    """

    y: Dict[str, np.ndarray]
    n: int
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def heads(self) -> List[str]:
        return sorted(self.y)


_SCHEMES: Dict[str, Callable[..., Optional[Labels]]] = {}


def register_scheme(name: str, builder: Callable[..., Optional[Labels]]) -> None:
    _SCHEMES[name.strip()] = builder


def available_schemes() -> List[str]:
    return sorted(_SCHEMES)


def build(scheme: str, frame: pl.DataFrame,
          params: Optional[Dict[str, Any]] = None) -> Optional[Labels]:
    """Construit les cibles de ``scheme`` depuis ``frame`` (qui porte ``close``).

    Lève ``KeyError`` sur un schéma inconnu — même parti pris strict que
    ``load_recipe`` et ``features_catalog.build``.
    """
    builder = _SCHEMES.get(str(scheme).strip())
    if builder is None:
        raise KeyError(
            f"Schéma de labellisation inconnu : {scheme!r} — connus : "
            f"{available_schemes()}."
        )
    if "close" not in frame.columns:
        raise ValueError(
            "le frame de features ne porte pas 'close' : impossible de "
            "labelliser (cf. features_catalog.FeatureSet)"
        )
    return builder(frame, **(params or {}))


# ─────────────────────────────────────────────────────────────────────────────
#  amp_dir_quantile — deux têtes, quantile d'amplitude
# ─────────────────────────────────────────────────────────────────────────────
def _build_amp_dir_quantile(frame: pl.DataFrame,
                            label_horizons: Optional[List[int]] = None,
                            amp_top_pct: float = 0.30,
                            **_ignored) -> Optional[Labels]:
    from app.ml.backend.features import multi_horizon_labels, single_horizon_labels

    close = frame["close"].to_numpy().astype(np.float64)
    horizons = [int(h) for h in (label_horizons or [1])]
    if len(horizons) > 1:
        y_amp, y_dir, n, amp_thr, stats = multi_horizon_labels(
            close, horizons, float(amp_top_pct))
    else:
        y_amp, y_dir, n, amp_thr = single_horizon_labels(close, float(amp_top_pct))
        stats = {"horizons": horizons, "n_labels": int(n),
                 "amp_thr_pct": round(amp_thr * 100, 4)}
    if n <= 0:
        return None
    return Labels(y={"amp": y_amp, "dir": y_dir}, n=int(n), stats=dict(stats))


# ─────────────────────────────────────────────────────────────────────────────
#  vol_adaptive_dir — une tête, seuil adaptatif à la volatilité
# ─────────────────────────────────────────────────────────────────────────────
def _build_vol_adaptive_dir(frame: pl.DataFrame,
                            lookahead: int = 3,
                            vol_multiplier: float = 0.6,
                            **_ignored) -> Optional[Labels]:
    from app.strategies.ml_dynamic_threshold import compute_labels

    lookahead = int(lookahead)
    y = compute_labels(frame, lookahead=lookahead,
                       vol_multiplier=float(vol_multiplier))
    # ``2 × lookahead`` et non ``lookahead`` : c'est la troncature de
    # ml_dynamic_threshold._train, reprise telle quelle pour que la bascule ne
    # change pas le jeu d'entraînement. Le seuil est décalé d'une barre
    # (shift(1)) et la cible regarde ``lookahead`` barres en avant.
    n = max(0, len(frame) - 2 * lookahead)
    if n <= 0:
        return None
    y_arr = y[:n].fill_null(0).to_numpy().astype(np.int64)
    return Labels(y={"dir": y_arr}, n=n,
                  stats={"horizons": [lookahead], "n_labels": n,
                         "scheme": "vol_adaptive_dir",
                         "vol_multiplier": float(vol_multiplier)})


register_scheme("amp_dir_quantile", _build_amp_dir_quantile)
register_scheme("vol_adaptive_dir", _build_vol_adaptive_dir)
