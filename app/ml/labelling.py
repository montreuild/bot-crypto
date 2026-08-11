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


# ─────────────────────────────────────────────────────────────────────────────
#  triple_barrier — trois têtes, dont la seule qui pose la question du trader
# ─────────────────────────────────────────────────────────────────────────────
def _atr_causal(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                periode: int = 14) -> np.ndarray:
    """ATR simple, calculé sans regarder en avant.

    Recalculé ici plutôt que lu dans le frame : la taille des barrières définit
    la CIBLE, et une cible ne doit pas dépendre du catalogue de features
    utilisé. Sinon la même recette produirait des labels différents selon les
    entrées, et deux modèles ne seraient plus comparables.
    """
    prev = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= periode:
        cumul = np.cumsum(np.insert(tr, 0, 0.0))
        out[periode - 1:] = (cumul[periode:] - cumul[:-periode]) / periode
    valides = out[np.isfinite(out) & (out > 0)]
    reserve = float(np.median(valides)) if valides.size else 1e-9
    return np.where(np.isfinite(out) & (out > 0), out, reserve)


def _build_triple_barrier(frame: pl.DataFrame,
                          label_horizons: Optional[List[int]] = None,
                          amp_top_pct: float = 0.30,
                          tb_tp_atr: float = 1.5,
                          tb_sl_atr: float = 1.0,
                          tb_max_bars: int = 12,
                          tb_atr_period: int = 14,
                          **_ignored) -> Optional[Labels]:
    """``amp`` et ``dir`` d'``amp_dir_quantile``, plus une tête ``tb``.

    POURQUOI UNE TROISIÈME TÊTE. ``dir`` demande « le rendement à t+h est-il
    positif ? ». La question est mal posée pour trader : elle ignore le CHEMIN.
    Un mouvement qui finit +2 % après être passé par −3 % est un gain pour
    ``dir`` et une position stoppée pour le bot. C'est une des explications du
    plafond de ``dir`` autour de 0.53 — on demande au modèle de prédire le
    signe d'un bruit, sur un horizon fixe, sans lui dire ce qui compte.

    ``tb`` demande « une position longue ouverte ici, avec ce stop et cette
    cible, aurait-elle touché la cible avant le stop ? ». C'est exactement la
    décision que prend la stratégie, et c'est une cible path-dependent : elle
    récompense les entrées dont le mouvement démarre proprement, pas seulement
    celles dont le point d'arrivée est favorable.

    BARRIÈRES EN ATR, pas en pourcentage : à ±1 % un stop est hors de portée en
    régime calme et touché à chaque bougie en régime agité — la cible mesurerait
    la volatilité plutôt que la qualité de l'entrée. En multiples d'ATR, la
    difficulté reste comparable d'un régime à l'autre.

    CONVENTION PESSIMISTE. Quand une même bougie touche la cible ET le stop,
    l'OHLC ne dit pas lequel est venu en premier. On tranche pour le STOP. Le
    parti pris inverse produirait des labels optimistes, donc un modèle qui
    promet un taux de réussite que l'exécution réelle ne tiendra pas — le genre
    d'erreur qui ne se voit qu'en production.

    La tête est BINAIRE : 1 si la cible est touchée en premier, 0 sinon (stop
    touché, ou aucune des deux avant la barrière verticale). Regrouper le stop
    et l'expiration est délibéré — les deux sont des trades qu'on n'aurait pas
    voulu prendre. ``stats`` détaille les trois issues pour que l'équilibre des
    classes reste visible.
    """
    besoin = {"high", "low", "close"}
    if not besoin.issubset(set(frame.columns)):
        logger.error(
            f"[labelling] triple_barrier exige {sorted(besoin)} ; le frame porte "
            f"{sorted(set(frame.columns) & besoin)} — un label de chemin ne peut "
            f"pas se construire sur les seules clôtures."
        )
        return None

    base = _build_amp_dir_quantile(frame, label_horizons, amp_top_pct)
    if base is None:
        return None

    high = frame["high"].to_numpy().astype(np.float64)
    low = frame["low"].to_numpy().astype(np.float64)
    close = frame["close"].to_numpy().astype(np.float64)
    N = len(close)
    H = max(1, int(tb_max_bars))

    atr = _atr_causal(high, low, close, int(tb_atr_period))
    cible = close + float(tb_tp_atr) * atr
    stop = close - float(tb_sl_atr) * atr

    #: Issue par barre : +1 cible touchée, −1 stop touché, 0 expiré.
    issue = np.zeros(N, dtype=np.int8)
    # Boucle sur le DÉCALAGE, pas sur les barres : H passes vectorisées au lieu
    # de N × H comparaisons scalaires.
    for j in range(1, H + 1):
        t = np.arange(N - j)
        libre = issue[t] == 0
        touche_stop = libre & (low[j:] <= stop[t])
        touche_cible = libre & (high[j:] >= cible[t]) & ~touche_stop
        issue[t[touche_cible]] = 1
        issue[t[touche_stop]] = -1
    y_tb = (issue == 1).astype(np.int8)

    # Une barre n'est labellisable que si ses H barres suivantes existent :
    # au-delà, « non résolu » voudrait dire « série finie », pas « expiré ».
    n = min(int(base.n), N - H)
    if n <= 0:
        logger.warning(f"[labelling] triple_barrier : n={n} après troncature "
                       f"(N={N}, H={H})")
        return None

    # Toutes les têtes tronquées à `n`, pas seulement `tb` : `Labels` promet des
    # cibles alignées, et `amp`/`dir` restent sinon plus longues que `n` parce
    # que leur horizon maximal est plus court que la barrière verticale. Le
    # trainer re-tronque de son côté, mais un contrat tenu par le seul appelant
    # n'est pas un contrat.
    y = {tete: arr[:n] for tete, arr in base.y.items()}
    y["tb"] = y_tb[:n].astype(np.int64)

    tete = issue[:n]
    stats = dict(base.stats)
    stats.update({
        "scheme": "triple_barrier",
        "n_labels": int(n),
        "tb_tp_atr": float(tb_tp_atr),
        "tb_sl_atr": float(tb_sl_atr),
        "tb_max_bars": H,
        "tb_pct_cible": round(100.0 * float((tete == 1).mean()), 2),
        "tb_pct_stop": round(100.0 * float((tete == -1).mean()), 2),
        "tb_pct_expire": round(100.0 * float((tete == 0).mean()), 2),
    })
    return Labels(y=y, n=int(n), stats=stats)


register_scheme("amp_dir_quantile", _build_amp_dir_quantile)
register_scheme("vol_adaptive_dir", _build_vol_adaptive_dir)
register_scheme("triple_barrier", _build_triple_barrier)
