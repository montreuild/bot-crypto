"""IsotonicRegression native — implémentation sans sklearn.

Régression isotone (Pool Adjacent Violators Algorithm, PAV) avec support
de l'extrapolation (clip). Remplace ``sklearn.isotonic.IsotonicRegression``
pour éliminer la dépendance sklearn du repo.

L'algorithme :
  1. Trier les X par ordre croissant (avec gestion des ex aequo).
  2. Appliquer PAV : fusionner les blocs adjacents qui violent la contrainte
     monotone (croissante ici), en remplaçant leurs y par la moyenne pondérée.
  3. Construire la fonction stepwise : pour un nouveau X, retourner le y
     du bloc correspondant (interpolation linéaire entre les thresholds).
  4. ``out_of_bounds="clip"`` : clamp aux bornes [y_min, y_max] et aux
     extrêmes de X_thresholds_.

Référence : Barlow et al. (1972), "Statistical Inference under Order
Restrictions", Wiley.

Compatibilité avec l'API sklearn :
  - ``fit(X, y)`` — entraîne
  - ``predict(X)`` — prédit (array ou scalaire)
  - ``X_thresholds_``, ``Y_thresholds_`` — attributs publics (utilisés par
    ``MLBackend.persistence._isotonic_to_dict``)
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class IsotonicRegression:
    """Régression isotone native (PAV) — remplace sklearn.

    Parameters
    ----------
    y_min : float, optional
        Borne inférieure des prédictions (clamp). Si None, pas de clamp.
    y_max : float, optional
        Borne supérieure des prédictions (clamp). Si None, pas de clamp.
    increasing : bool, default True
        Si True, contrainte monotone croissante. Si False, décroissante.
    out_of_bounds : str, default "clip"
        Stratégie pour les X hors-bornes : "clip" (prolonger la dernière
        valeur) ou "raise" (lever une ValueError).
    """

    def __init__(self, y_min: Optional[float] = None,
                 y_max: Optional[float] = None,
                 increasing: bool = True,
                 out_of_bounds: str = "clip"):
        self.y_min = y_min
        self.y_max = y_max
        self.increasing = increasing
        self.out_of_bounds = out_of_bounds
        # Attributs fit (compat sklearn) :
        self.X_thresholds_: Optional[np.ndarray] = None
        self.Y_thresholds_: Optional[np.ndarray] = None
        self.X_min_: Optional[float] = None
        self.X_max_: Optional[float] = None

    def fit(self, X, y) -> "IsotonicRegression":
        """Ajuste la régression isotone sur (X, y).

        X et y sont 1D de même longueur. Les NaN/inf sont ignorés.
        """
        X = np.asarray(X, dtype=np.float64).ravel()
        y = np.asarray(y, dtype=np.float64).ravel()
        if len(X) != len(y):
            raise ValueError(f"X et y de tailles différentes : {len(X)} vs {len(y)}")

        # Filtrer NaN/inf.
        mask = np.isfinite(X) & np.isfinite(y)
        X = X[mask]
        y = y[mask]
        if len(X) < 2:
            # Pas assez de points : calibration triviale (identité).
            self.X_thresholds_ = np.array([X[0] if len(X) > 0 else 0.0])
            self.Y_thresholds_ = np.array([y[0] if len(y) > 0 else 0.0])
            self.X_min_ = float(self.X_thresholds_[0])
            self.X_max_ = float(self.X_thresholds_[0])
            return self

        # Trier par X croissant.
        order = np.argsort(X, kind="mergesort")
        X_sorted = X[order]
        y_sorted = y[order]

        # Pour "decreasing", inverser y puis appliquer increasing.
        if not self.increasing:
            y_sorted = -y_sorted

        # Pool Adjacent Violators (PAV) — algorithme O(n).
        # On maintient une pile de blocs (x_lo, x_hi, y_mean, weight).
        blocks = []  # chaque bloc = [y_sum, weight, x_start, x_end]
        for i in range(len(X_sorted)):
            cur_y = y_sorted[i]
            cur_x = X_sorted[i]
            blocks.append([cur_y, 1, cur_x, cur_x])
            # Fusionner si le bloc précédent a une moyenne > courante.
            while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                prev = blocks[-2]
                cur  = blocks[-1]
                prev[0] += cur[0]
                prev[1] += cur[1]
                prev[3] = cur[3]  # étendre x_end
                blocks.pop()

        # Construire les thresholds : un point par bloc (x_end, y_mean).
        x_thresh = np.array([b[3] for b in blocks], dtype=np.float64)
        y_mean   = np.array([b[0] / b[1] for b in blocks], dtype=np.float64)

        # Inverser la transformation si "decreasing".
        if not self.increasing:
            y_mean = -y_mean

        # Clamp optionnel.
        if self.y_min is not None:
            y_mean = np.maximum(y_mean, self.y_min)
        if self.y_max is not None:
            y_mean = np.minimum(y_mean, self.y_max)

        self.X_thresholds_ = x_thresh
        self.Y_thresholds_ = y_mean
        self.X_min_ = float(X_sorted[0])
        self.X_max_ = float(X_sorted[-1])
        return self

    def predict(self, X):
        """Prédit les y pour X (array ou scalaire).

        Interpolation linéaire entre les thresholds ; clip hors-bornes
        (si out_of_bounds="clip").
        """
        if self.X_thresholds_ is None or self.Y_thresholds_ is None:
            raise RuntimeError("IsotonicRegression non fittée — appeler fit() d'abord")

        X_arr = np.asarray(X, dtype=np.float64).ravel()
        out = np.interp(
            X_arr,
            self.X_thresholds_,
            self.Y_thresholds_,
            left=self.Y_thresholds_[0] if self.out_of_bounds == "clip" else np.nan,
            right=self.Y_thresholds_[-1] if self.out_of_bounds == "clip" else np.nan,
        )
        # np.interp ne clamp pas les y — on le fait manuellement.
        if self.y_min is not None:
            out = np.maximum(out, self.y_min)
        if self.y_max is not None:
            out = np.minimum(out, self.y_max)

        # Si input était scalaire, retourner scalaire.
        if np.isscalar(X) or (hasattr(X, "__len__") and len(X) == 1 and not isinstance(X, (list, tuple, np.ndarray))):
            return float(out[0])
        if isinstance(X, (list, tuple)):
            return out.tolist()
        if np.isscalar(X):
            return float(out[0])
        return out

    def __repr__(self) -> str:
        return (
            f"IsotonicRegression(y_min={self.y_min}, y_max={self.y_max}, "
            f"increasing={self.increasing}, out_of_bounds={self.out_of_bounds!r}, "
            f"fitted={self.X_thresholds_ is not None})"
        )
