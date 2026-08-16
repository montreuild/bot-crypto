"""MLBackend.trainer — entraînement LightGBM (amp + dir) avec calibration et pruning.

Extrait la logique d'entraînement commune aux stratégies Opus V11/V12,
opus_stat_retrained_v4 et variantes. Le trainer supporte :

- Labellisation multi-horizon (V11+) ou single-horizon (V4 retrained simple).
- Calibration isotone optionnelle sur le set de validation.
- Pruning de features optionnel (garde les features à gain > 0).
- Cache process-wide via `app.core.train_cache.cached_train` : la clé de cache
  est basée sur `type(strategy).__module__` + fenêtre + hyperparams — le
  trainer reçoit donc la `strategy` en paramètre pour préserver le cache.

Le trainer ne persiste PAS les modèles — c'est le rôle de `persistence.py`.
"""
from __future__ import annotations

import gc
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.ml.backend.features import (
    REGIME_LABELS,
    build_features,
    impute_inplace,
    multi_horizon_labels,
    regime_history,
    select_feature_columns,
    single_horizon_labels,
    window_polars,
)
from app.ml.scoring import _rankdata_average, rank_auc

logger = logging.getLogger(__name__)


# AUC par rang (Mann-Whitney) et rangs moyens : implémentation unique dans
# ``app.ml.scoring`` (module feuille — n'importe rien de ``app.ml.backend`` au
# niveau module, donc pas de cycle). Alias locaux pour la lisibilité du reste
# du fichier.
_rank_auc = rank_auc
_rankdata = _rankdata_average


def _regime_key(label: str) -> str:
    """"Trend Down" -> "trend_down" — clé stable pour les dicts de diagnostic."""
    return label.lower().replace(" ", "_")


def _auc_dir_by_regime(regimes: List[int], y_dir_valid: np.ndarray,
                       raw_dir_valid: np.ndarray) -> Dict[str, Any]:
    """AUC direction ventilée par régime sur le SET DE VALIDATION.

    Répond à la question ML-02 laissée ouverte par la purge des seuils
    direction de l'optimiseur (commit d6eb9db) : l'AUC direction *globale*
    mesurée (~0.53-0.54, quasi hasard) mélange 4 régimes dont la
    prévisibilité diffère fortement d'après l'analyse V4 externe recouvrée
    (AUC ≈0.86-0.88 en Trend Down, ≈0.50 en Trend Up) — jamais vérifié sur
    les modèles PROPRES de V11 (entraînement multi-horizon, features et
    labels différents du pkl V4 autonome). Cette fonction calcule l'AUC par
    régime sur repli identique afin de trancher avec des chiffres V11 réels
    plutôt que par analogie.
    """
    regimes_arr = np.asarray(regimes[:len(y_dir_valid)])
    out: Dict[str, Any] = {}
    for code, label in REGIME_LABELS.items():
        if code < 0:
            continue
        mask = regimes_arr == code
        n = int(mask.sum())
        if n < 15:
            out[_regime_key(label)] = {"n": n, "auc": None}
            continue
        auc = _rank_auc(y_dir_valid[mask], raw_dir_valid[mask])
        out[_regime_key(label)] = {"n": n, "auc": auc}
    return out


# ── Importance des features PAR RÉGIME (attributions LightGBM) ───────────────
# L'importance "gain" d'un booster est GLOBALE : elle agrège tous les
# échantillons et ne peut pas dire si le modèle s'appuie sur des features
# différentes selon le régime de marché. ``predict(..., pred_contrib=True)``
# donne en revanche une attribution PAR ÉCHANTILLON (valeurs de Shapley, une
# colonne par feature + une colonne de valeur de base) : moyenner
# |contribution| à l'intérieur de chaque bucket de régime produit une
# importance conditionnelle au régime, exactement ce qu'il faut pour tester
# l'hypothèse « le modèle direction lit d'autres signaux en Trend Down ».
_CONTRIB_MAX_SAMPLES_PER_REGIME = 2000


def _feature_importance_by_regime(booster, X_valid: np.ndarray,
                                  feature_cols: List[str],
                                  regimes: List[int],
                                  top_n: int = 15,
                                  seed: int = 0) -> Dict[str, Any]:
    """Importance des features ventilée par régime, via ``pred_contrib``.

    Retourne ``{regime: {"n": …, "top": [{"feature", "contrib"}…]}}``.
    Échantillonné à ``_CONTRIB_MAX_SAMPLES_PER_REGIME`` par régime : la
    matrice d'attributions est dense en ``(n, n_features+1)`` (≈ 7 Mo par
    régime à 2000×438 en float64), donc bornée explicitement plutôt que
    laissée croître avec la fenêtre d'entraînement.
    """
    regimes_arr = np.asarray(regimes[:len(X_valid)])
    rng = np.random.RandomState(seed)
    out: Dict[str, Any] = {}
    for code, label in REGIME_LABELS.items():
        if code < 0:
            continue
        key = _regime_key(label)
        idx = np.flatnonzero(regimes_arr == code)
        if len(idx) < 15:
            out[key] = {"n": int(len(idx)), "top": []}
            continue
        if len(idx) > _CONTRIB_MAX_SAMPLES_PER_REGIME:
            idx = rng.choice(idx, _CONTRIB_MAX_SAMPLES_PER_REGIME, replace=False)
        try:
            contrib = np.asarray(booster.predict(X_valid[idx], pred_contrib=True))
        except Exception as e:
            logger.debug(f"[MLBackend] pred_contrib KO ({label}) : {e}")
            out[key] = {"n": int(len(idx)), "top": []}
            continue
        # Dernière colonne = valeur de base (biais), pas une feature.
        mean_abs = np.abs(contrib[:, :-1]).mean(axis=0)
        order = np.argsort(-mean_abs)[:top_n]
        out[key] = {
            "n": int(len(idx)),
            "top": [{"feature": feature_cols[j], "contrib": round(float(mean_abs[j]), 6)}
                    for j in order],
            "_full": mean_abs,   # retiré avant sérialisation (cf. _regime_similarity)
        }
    return out


def _spearman(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    """Corrélation de rang de Spearman (Pearson sur les rangs) — sans scipy."""
    if len(a) != len(b) or len(a) < 3:
        return None
    ra, rb = _rankdata(np.asarray(a, float)), _rankdata(np.asarray(b, float))
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denom = float(np.sqrt((ra ** 2).sum() * (rb ** 2).sum()))
    return float((ra * rb).sum() / denom) if denom > 0 else None


def _regime_similarity(by_regime: Dict[str, Any], top_n: int = 15) -> Dict[str, Any]:
    """Compare les importances entre régimes deux à deux.

    Répond à « y a-t-il une corrélation ? » avec deux angles complémentaires :

      - ``spearman`` sur le VECTEUR COMPLET d'importances — proche de 1 = le
        modèle hiérarchise les features de la même façon dans les deux
        régimes (donc pas de spécialisation) ;
      - ``top_overlap`` = |intersection| / top_n sur les top-N — plus
        interprétable en pratique (« combien de features en commun en tête »).
    """
    keys = [k for k, v in by_regime.items() if v.get("top")]
    out: Dict[str, Any] = {}
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            va, vb = by_regime[ka], by_regime[kb]
            fa = {f["feature"] for f in va["top"][:top_n]}
            fb = {f["feature"] for f in vb["top"][:top_n]}
            entry: Dict[str, Any] = {
                "top_overlap": round(len(fa & fb) / max(len(fa | set()), 1), 3),
            }
            if va.get("_full") is not None and vb.get("_full") is not None:
                rho = _spearman(va["_full"], vb["_full"])
                entry["spearman"] = round(rho, 4) if rho is not None else None
            out[f"{ka}__vs__{kb}"] = entry
    return out


# ── Configuration d'entraînement ────────────────────────────────────────────
class TrainConfig:
    """Hyperparamètres d'entraînement LightGBM (figés, hors param_space).

    Ces valeurs sont les mêmes pour toutes les stratégies qui utilisent le
    MLBackend — elles font partie de la clé de cache process-wide. Les
    échantillonner invaliderait le cache et ferait repayer chaque trial de
    l'optimiseur l'intégralité des retrains walk-forward (rédhibitoire sur
    50k bougies).
    """

    def __init__(self,
                 amp_top_pct: float = 0.30,
                 n_estimators: int = 500,
                 num_leaves: int = 31,
                 learning_rate: float = 0.03,
                 label_horizons: Optional[List[int]] = None,
                 calibrate: bool = True,
                 prune_features: bool = True,
                 log_training: bool = True,
                 importance_top_n: int = 15,
                 warmup_bars: int = 750,
                 retrain_every: int = 800,
                 min_bars: int = 230,
                 adx_threshold: float = 20.0,
                 di_rescue: float = 10.0):
        self.amp_top_pct      = float(amp_top_pct)
        self.n_estimators     = int(n_estimators)
        self.num_leaves       = int(num_leaves)
        self.learning_rate    = float(learning_rate)
        self.label_horizons   = list(label_horizons or [1, 3, 6])
        self.calibrate        = bool(calibrate)
        self.prune_features   = bool(prune_features)
        self.log_training     = bool(log_training)
        self.importance_top_n = int(importance_top_n)
        self.warmup_bars      = int(warmup_bars)
        self.retrain_every    = int(retrain_every)
        self.min_bars         = int(min_bars)
        # Servent UNIQUEMENT à la ventilation par régime de l'AUC direction
        # sur le set de validation (cf. _auc_dir_by_regime) — pas au routing
        # de setups lui-même (géré par la stratégie appelante, ex. V11
        # score()). Défauts alignés sur opus_omnibus_v11._DEFAULTS pour que
        # la ventilation reflète le MÊME découpage de régime que le routing
        # réel, sans dupliquer un flag redondant si l'appelant en a déjà un.
        self.adx_threshold    = float(adx_threshold)
        self.di_rescue        = float(di_rescue)

    @classmethod
    def from_params(cls, params: Dict[str, Any], defaults: Dict[str, Any]) -> "TrainConfig":
        """Construit la config depuis un dict de params + défauts."""
        g = lambda k, d: params.get(k, defaults.get(k, d))  # noqa: E731
        return cls(
            amp_top_pct      = float(g("amp_top_pct", 0.30)),
            n_estimators     = int(g("n_estimators", 500)),
            num_leaves       = int(g("num_leaves", 31)),
            learning_rate    = float(g("learning_rate", 0.03)),
            label_horizons   = list(g("label_horizons", [1, 3, 6])),
            calibrate        = bool(g("calibrate", True)),
            prune_features   = bool(g("prune_features", True)),
            log_training     = bool(g("log_training", True)),
            importance_top_n = int(g("importance_top_n", 15)),
            warmup_bars      = int(g("warmup_bars", 750)),
            retrain_every    = int(g("retrain_every", 800)),
            min_bars         = int(g("min_bars", 230)),
            adx_threshold    = float(g("adx_threshold", 20.0)),
            di_rescue        = float(g("di_rescue", 10.0)),
        )


# ── État d'entraînement (mutable, porté par le backend) ─────────────────────
class TrainState:
    """Conteneur pour l'état ML mutable, accédé sous lock."""

    __slots__ = (
        "amp_models", "dir_models", "amp_cal", "dir_cal",
        "feature_cols", "kept_features", "medians",
        "trained_tfs", "best_auc_per_tf", "train_meta",
        "last_retrain", "call_cnt", "best_auc", "managed_externally",
    )

    def __init__(self):
        self.amp_models:   Dict[str, Any]              = {}
        self.dir_models:   Dict[str, Any]              = {}
        self.amp_cal:      Dict[str, Any]              = {}
        self.dir_cal:      Dict[str, Any]              = {}
        self.feature_cols: Dict[str, List[str]]        = {}
        self.kept_features: Dict[str, List[str]]       = {}
        self.medians:      Dict[str, Dict[str, float]] = {}
        self.trained_tfs:  set                         = set()
        self.best_auc_per_tf: Dict[str, float]         = {}
        self.train_meta:   Dict[str, dict]             = {}
        self.last_retrain: Dict[str, int]              = {}
        self.call_cnt:     Dict[str, int]              = {}
        self.best_auc:     float                       = 0.0
        self.managed_externally: bool                  = False

    def reset(self) -> None:
        self.amp_models.clear()
        self.dir_models.clear()
        self.amp_cal.clear()
        self.dir_cal.clear()
        self.feature_cols.clear()
        self.kept_features.clear()
        self.medians.clear()
        self.trained_tfs.clear()
        self.best_auc_per_tf.clear()
        self.train_meta.clear()
        self.last_retrain.clear()
        self.call_cnt.clear()
        self.best_auc = 0.0
        self.managed_externally = False

    # Attributs à sauvegarder/restaurer pour le cache process-wide.
    STATE_ATTRS: Tuple[str, ...] = (
        "amp_models", "dir_models", "amp_cal", "dir_cal", "feature_cols",
        "kept_features", "medians", "best_auc_per_tf", "train_meta",
    )

    # Clés de params qui invalident le cache si elles changent.
    PARAM_KEYS: Tuple[str, ...] = (
        "amp_top_pct", "n_estimators", "num_leaves", "learning_rate",
        "label_horizons", "calibrate", "prune_features",
    )


# ── Trainer ─────────────────────────────────────────────────────────────────
def train(state: TrainState, lock, df: pl.DataFrame, tf_key: str,
          params: Dict[str, Any], defaults: Dict[str, Any],
          bt_features: Optional[pl.DataFrame] = None,
          bt_features_len: int = 0,
          bt_train_offset: Optional[int] = None) -> bool:
    """Entraîne les 2 boosters (amp + dir) pour un TF donné.

    Args:
        state: état ML mutable (verrouillé par `lock`).
        lock:  threading.Lock pour sérialiser les mutations de `state`.
        df:    DataFrame OHLCV polars (fenêtre d'entraînement).
        tf_key: timeframe (ex. "1h").
        params: paramètres de la stratégie (peut contenir des overrides).
        defaults: defaults de la stratégie (_DEFAULTS).
        bt_features: cache de features pré-calculé pour backtest (optionnel).
        bt_features_len: longueur du cache backtest.
        bt_train_offset: offset de la fenêtre d'entraînement dans le cache.

    Returns:
        True si l'entraînement a réussi, False sinon.
    """
    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("[MLBackend] lightgbm requis : pip install lightgbm")
        return False

    cfg = TrainConfig.from_params(params, defaults)

    n_keep = max(2200, len(df))
    # Réutilise le cache backtest si dispo. ``bt_train_offset`` repère la
    # position de la fenêtre d'entraînement dans la fenêtre complète.
    _off = int(bt_train_offset or 0)
    if (bt_features is not None and bt_features_len > 0 and
            _off + len(df) <= bt_features_len):
        feats = bt_features.slice(_off, len(df))
    else:
        feats = build_features(window_polars(df, n=n_keep))
    if feats is None or len(feats) < 250:
        logger.warning(f"[MLBackend] {tf_key} : données insuffisantes")
        return False

    feature_cols = select_feature_columns(feats)
    # Pruning : ne garder que les features non nulles du cycle précédent.
    if cfg.prune_features and state.kept_features.get(tf_key):
        kept = [c for c in feature_cols if c in set(state.kept_features[tf_key])]
        if len(kept) >= 50:
            feature_cols = kept
    if not feature_cols:
        logger.warning(f"[MLBackend] {tf_key} : aucune feature exploitable")
        return False

    from app.ml.splitting import chrono_split, label_embargo

    close = feats["close"].to_numpy().astype(np.float64)
    multi = bool(cfg.label_horizons and len(cfg.label_horizons) > 1)
    horizons = list(cfg.label_horizons) if multi else [1]
    # Le découpage est planifié AVANT la labellisation : le seuil d'amplitude
    # ne doit voir que les lignes d'entraînement (#8), et il faut donc savoir
    # où elles s'arrêtent. ``n`` est déterministe — len(close) moins l'horizon
    # maximal — donc le plan peut être établi sans les labels.
    n_prevu = len(close) - label_embargo(horizons)
    plan = chrono_split(n_prevu, horizons)
    if plan is None:
        logger.warning(f"[MLBackend] {tf_key} : split impossible (n={n_prevu})")
        return False

    if multi:
        y_amp, y_dir, n, amp_thr, lbl_stats = multi_horizon_labels(
            close, cfg.label_horizons, cfg.amp_top_pct, thr_upto=plan.train,
        )
    else:
        y_amp, y_dir, n, amp_thr = single_horizon_labels(
            close, cfg.amp_top_pct, thr_upto=plan.train)
        lbl_stats = {"horizons": [1], "n_labels": int(n),
                     "amp_thr_pct": round(amp_thr * 100, 4),
                     "amp_thr_fit_n": int(plan.train)}
    if n < 200:
        logger.warning(f"[MLBackend] {tf_key} : pas assez de barres labélisables (n={n})")
        return False
    if n != plan.n:                       # défensif : la géométrie a bougé
        plan = chrono_split(n, horizons)
        if plan is None:
            logger.warning(f"[MLBackend] {tf_key} : split impossible (n={n})")
            return False
    lbl_stats["embargo"] = int(plan.embargo)

    X_full = feats.head(n).select(feature_cols).to_numpy().astype(np.float32)
    split, train_end = plan.split, plan.train

    medians: Dict[str, float] = {}
    for j, col in enumerate(feature_cols):
        col_train = X_full[:train_end, j]
        mask = np.isfinite(col_train)
        medians[col] = float(np.median(col_train[mask])) if mask.any() else 0.0

    # Embargo : les ``plan.embargo`` dernières lignes d'entraînement portent un
    # label construit sur des barres de la validation. Elles sont retirées de
    # l'entraînement, pas de la validation — l'AUC reste mesurée sur le même
    # échantillon qu'avant (cf. app/ml/splitting.py).
    X_train = X_full[:train_end].copy()
    X_valid = X_full[split:n].copy()
    del X_full
    impute_inplace(X_train, feature_cols, medians)
    impute_inplace(X_valid, feature_cols, medians)

    if len(np.unique(y_amp[:train_end])) < 2 or len(np.unique(y_dir[:train_end])) < 2:
        from app.core.log_throttle import log_throttled
        log_throttled(logger, f"mlbackend:monoclass:{tf_key}",
                      f"[MLBackend] {tf_key} : labels mono-classe, fit ignoré")
        return False

    from app.ml.threads import lgb_threads
    common = dict(
        objective="binary", metric="auc",
        num_leaves=cfg.num_leaves, learning_rate=cfg.learning_rate,
        min_child_samples=20, subsample=0.8, subsample_freq=5,
        colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=0.5,
        max_bin=63, force_col_wise=True, verbosity=-1,
        # 1 dans un worker d'optimisation, plusieurs cœurs en entraînement
        # autonome — le modèle produit est le même (cf. app/ml/threads.py).
        n_jobs=lgb_threads(),
    )

    boosters: Dict[str, Any] = {}
    aucs: Dict[str, float] = {}
    cal_err: Dict[str, float] = {}
    calibrators: Dict[str, Any] = {}
    importances: Dict[str, List[tuple]] = {}
    raw_va_by_target: Dict[str, np.ndarray] = {}

    for target, y in (("amp", y_amp), ("dir", y_dir)):
        spw = (y[:train_end] == 0).sum() / max((y[:train_end] == 1).sum(), 1)
        ds_tr = lgb.Dataset(X_train, label=y[:train_end], feature_name=feature_cols,
                            free_raw_data=False)
        ds_va = lgb.Dataset(X_valid, label=y[split:n], reference=ds_tr,
                            feature_name=feature_cols, free_raw_data=False)
        try:
            try:
                booster = lgb.train(
                    {**common, "scale_pos_weight": spw},
                    ds_tr, num_boost_round=cfg.n_estimators, valid_sets=[ds_va],
                    callbacks=[lgb.early_stopping(20, verbose=False),
                               lgb.log_evaluation(-1)],
                )
            except Exception as e:
                logger.warning(f"[MLBackend] {tf_key} : entraînement {target} KO ({e})")
                return False
            aucs[target] = float(booster.best_score.get("valid_0", {}).get("auc", 0.0))
            boosters[target] = booster

            # Prédictions brutes de validation — réutilisées par la calibration
            # ET par la ventilation d'AUC direction par régime (ci-dessous).
            raw_va = booster.predict(X_valid)
            raw_va_by_target[target] = raw_va

            # Calibration isotone sur le set de validation.
            if cfg.calibrate:
                try:
                    from app.ml.backend.isotonic import IsotonicRegression
                    y_va = y[split:n]
                    if len(np.unique(y_va)) >= 2:
                        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                        iso.fit(raw_va, y_va)
                        cal_va = iso.predict(raw_va)
                        cal_err[target] = round(float(np.mean(np.abs(cal_va - y_va))), 4)
                        calibrators[target] = iso
                except Exception as ce:
                    logger.debug(f"[MLBackend] {tf_key} calibration {target} KO : {ce}")

            # Importance des features (gain).
            try:
                gains = booster.feature_importance(importance_type="gain")
                pairs = sorted(zip(feature_cols, gains), key=lambda kv: -kv[1])
                importances[target] = [(c, float(g)) for c, g in pairs]
            except Exception:
                importances[target] = []
        finally:
            del ds_tr, ds_va
            gc.collect()

    del X_train

    # ── Diagnostics conditionnels au régime (set de validation) ─────────────
    # N'affectent ni l'entraînement ni le routing — purement loggés/persistés,
    # pour trancher la question ML-02 laissée ouverte par la purge des seuils
    # direction de l'optimiseur (d6eb9db) : l'AUC direction GLOBALE (~0.53)
    # mélange 4 régimes dont la prévisibilité pourrait différer fortement.
    auc_dir_by_regime: Dict[str, Any] = {}
    fi_by_regime: Dict[str, Any] = {}
    regime_similarity: Dict[str, Any] = {}
    try:
        n_valid_n = n - split
        regimes, _ = regime_history(feats.head(n), n_last=n_valid_n,
                                    adx_threshold=cfg.adx_threshold, di_rescue=cfg.di_rescue)
        y_dir_valid = y_dir[split:n]
        raw_dir_valid = raw_va_by_target.get("dir")
        if raw_dir_valid is not None and len(regimes) == len(y_dir_valid):
            auc_dir_by_regime = _auc_dir_by_regime(regimes, y_dir_valid, raw_dir_valid)
        # Importance des features par régime (pred_contrib) + similarité entre
        # régimes : le modèle direction s'appuie-t-il sur d'AUTRES features
        # selon le régime, ou hiérarchise-t-il les mêmes partout ?
        if boosters.get("dir") is not None and len(regimes) == len(X_valid):
            fi_by_regime = _feature_importance_by_regime(
                boosters["dir"], X_valid, feature_cols, regimes,
                top_n=cfg.importance_top_n,
            )
            regime_similarity = _regime_similarity(fi_by_regime, top_n=cfg.importance_top_n)
            # ``_full`` (vecteur d'importances complet) sert au Spearman
            # ci-dessus mais n'a pas à être sérialisé dans meta.json.
            for v in fi_by_regime.values():
                v.pop("_full", None)
    except Exception as e:
        logger.debug(f"[MLBackend] {tf_key} : diagnostics par régime KO : {e}")

    del X_valid

    # Pruning : features avec gain > 0 sur au moins un des deux modèles.
    kept_set = set()
    for tgt in ("amp", "dir"):
        for c, g in importances.get(tgt, []):
            if g > 0:
                kept_set.add(c)
    kept_features = [c for c in feature_cols if c in kept_set]

    auc_combined = (aucs.get("amp", 0.0) + aucs.get("dir", 0.0)) / 2.0
    top_feats = {
        tgt: [c for c, _ in importances.get(tgt, [])[:cfg.importance_top_n]]
        for tgt in ("amp", "dir")
    }
    # Nom + gain (pas seulement le nom) — pour l'affichage "Top features avec
    # importance" de la page Modèles (E7). top_features_amp/dir (noms seuls,
    # ci-dessous) restent inchangés pour compat des lecteurs existants (logs).
    feature_importance = {
        tgt: [{"feature": c, "gain": round(float(g), 2)}
             for c, g in importances.get(tgt, [])[:cfg.importance_top_n]]
        for tgt in ("amp", "dir")
    }
    meta = {
        "n_train":      int(train_end),
        "n_valid":      int(n - split),
        "embargo":      int(plan.embargo),
        "n_features":   len(feature_cols),
        "auc_amp":      round(aucs.get("amp", 0.0), 4),
        "auc_dir":      round(aucs.get("dir", 0.0), 4),
        "auc_dir_by_regime": auc_dir_by_regime,
        "feature_importance_dir_by_regime": fi_by_regime,
        "regime_feature_similarity": regime_similarity,
        "amp_thr_pct":  round(float(amp_thr) * 100, 4),
        "amp_top_pct":  cfg.amp_top_pct,
        "horizons":     lbl_stats.get("horizons"),
        "label_stats":  lbl_stats,
        "calibrated":   bool(calibrators),
        "cal_err":      cal_err,
        "n_kept_features": len(kept_features),
        "feature_importance_amp": feature_importance["amp"],
        "feature_importance_dir": feature_importance["dir"],
        "top_features_amp": top_feats["amp"],
        "top_features_dir": top_feats["dir"],
    }

    with lock:
        state.amp_models[tf_key]    = boosters["amp"]
        state.dir_models[tf_key]    = boosters["dir"]
        state.amp_cal[tf_key]       = calibrators.get("amp")
        state.dir_cal[tf_key]       = calibrators.get("dir")
        state.feature_cols[tf_key]  = feature_cols
        state.kept_features[tf_key] = kept_features
        state.medians[tf_key]       = medians
        state.trained_tfs.add(tf_key)
        state.best_auc_per_tf[tf_key] = auc_combined
        state.best_auc = max(state.best_auc, auc_combined)
        state.train_meta[tf_key]    = meta
    gc.collect()

    regime_auc_str = ", ".join(
        f"{lbl}={v['auc']:.3f}(n={v['n']})" if v.get("auc") is not None else f"{lbl}=n/a(n={v['n']})"
        for lbl, v in auc_dir_by_regime.items()
    )
    logger.info(
        f"[MLBackend] {tf_key} entraîné : {split} train / {n - split} val | "
        f"{len(feature_cols)} feats (gardées {len(kept_features)}) | "
        f"AUC amp={aucs.get('amp', 0):.3f} dir={aucs.get('dir', 0):.3f} | "
        f"horizons={meta['horizons']} | calib={meta['calibrated']} cal_err={cal_err} | "
        f"top_dir={top_feats['dir'][:5]} | AUC dir/régime : {regime_auc_str}"
    )
    return True
