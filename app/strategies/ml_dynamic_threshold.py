"""Stratégie ML à seuil dynamique — labels adaptatifs à la volatilité, filtre régime ADX.

Refonte phase6-sklearn-removal : remplace le pipeline sklearn (StandardScaler +
RandomForestClassifier/LogisticRegression) par LightGBM natif via MLBackend.
L'objectif est d'éliminer totalement la dépendance sklearn du repo.

Conservation de la logique métier :
  - **Features** : ``compute_features`` (~30 features Polars) — inchangé.
  - **Labels dynamiques** : ``compute_labels`` (seuil = volatilité_20p × sqrt(lookahead) ×
    vol_multiplier) — inchangé.
  - **Décision** : filtre ADX + seuils ``proba_long`` / ``proba_short`` — inchangé.

Changement technique :
  - **Modèle** : LightGBM booster natif (``lgb.train``) au lieu de sklearn Pipeline.
    LightGBM n'a pas besoin de StandardScaler (les arbres sont invariants aux
    transformations monotones des features).
  - **Random search** : hyperparams LightGBM natifs (``num_leaves``, ``learning_rate``,
    ``min_data_in_leaf``, ``feature_fraction``, ``bagging_fraction``) au lieu des
    hyperparams sklearn (``n_estimators``, ``max_depth``, ``min_samples_leaf``).
  - **Cross-validation** : TimeSeriesSplit manuel (sans sklearn) — split walk-forward,
    AUC calculé via ``lightgbm.Booster.predict`` + formule directe.
  - **Persistance** : format natif LGB + JSON via ``MLBackend.persistence`` (RCE-safe).

Avantages :
  - Pas de dépendance sklearn (10 Mo + scipy).
  - Pas de StandardScaler (LightGBM gère les features brutes).
  - Format natif RCE-safe (pas de pickle).
  - Réutilise MLBackend (features + persistence).
"""

import logging
import math
import os
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import adx as _adx
from app.core.indicators import atr_series, rsi
from app.engine.engine import BaseStrategyML

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Feature Engineering — identique à l'ancienne version (Polars)
# ═══════════════════════════════════════════════════════════════
def compute_features(df: pl.DataFrame) -> pl.DataFrame:
    """Construit 30+ features techniques depuis OHLCV.

    Inclut log-returns, VWAP rolling, micro-structure des bougies,
    divergence RSI/prix — plus robustes que les simples pct_change.
    """
    if len(df) < 100:
        return pl.DataFrame()

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]
    open_p = df["open"]

    # ── Log-Returns ────────────────────────────────────────────
    log_ret_1  = (close / close.shift(1).clip(lower_bound=1e-9)).log(math.e)
    log_ret_3  = (close / close.shift(3).clip(lower_bound=1e-9)).log(math.e)
    log_ret_5  = (close / close.shift(5).clip(lower_bound=1e-9)).log(math.e)
    log_ret_10 = (close / close.shift(10).clip(lower_bound=1e-9)).log(math.e)
    log_ret_20 = (close / close.shift(20).clip(lower_bound=1e-9)).log(math.e)

    # ── Rolling VWAP ───────────────────────────────────────────
    typical_price = (high + low + close) / 3
    vwap_20 = ((typical_price * volume).rolling_mean(20) /
               volume.rolling_mean(20).clip(lower_bound=1e-9))
    vwap_50 = ((typical_price * volume).rolling_mean(50) /
               volume.rolling_mean(50).clip(lower_bound=1e-9))
    vwap_dist_20 = (close - vwap_20) / vwap_20.clip(lower_bound=1e-9)
    vwap_dist_50 = (close - vwap_50) / vwap_50.clip(lower_bound=1e-9)

    # ── Micro-structure ────────────────────────────────────────
    body_size   = (close - open_p).abs()
    upper_wick  = high - pl.Series(np.maximum(open_p.to_numpy(), close.to_numpy()))
    lower_wick  = pl.Series(np.minimum(open_p.to_numpy(), close.to_numpy())) - low
    total_range = (high - low).clip(lower_bound=1e-9)
    body_to_range    = body_size   / total_range
    upper_wick_ratio = upper_wick  / total_range
    lower_wick_ratio = lower_wick  / total_range

    # ── RSI & divergence ───────────────────────────────────────
    rsi_14_s    = rsi(close, 14)
    rsi_14      = rsi_14_s / 100.0
    price_slope = close.diff(5)
    rsi_slope   = rsi_14_s.diff(5)
    rsi_divergence = price_slope.sign() * rsi_slope.sign()

    # ── EMAs relatifs au prix ──────────────────────────────────
    ema_rels = {}
    for s in [8, 13, 21, 50, 100]:
        ema = close.ewm_mean(span=s, adjust=False)
        ema_rels[f"ema{s}_rel"] = (close - ema) / close.clip(lower_bound=1e-9)

    # ── Croisements EMA ───────────────────────────────────────
    ema8  = close.ewm_mean(span=8,  adjust=False)
    ema21 = close.ewm_mean(span=21, adjust=False)
    ema50 = close.ewm_mean(span=50, adjust=False)
    cross_8_21  = (ema8  - ema21) / close.clip(lower_bound=1e-9)
    cross_21_50 = (ema21 - ema50) / close.clip(lower_bound=1e-9)

    # ── MACD ──────────────────────────────────────────────────
    ema12 = close.ewm_mean(span=12, adjust=False)
    ema26 = close.ewm_mean(span=26, adjust=False)
    macd  = ema12 - ema26
    sig_  = macd.ewm_mean(span=9, adjust=False)
    macd_hist_norm = (macd - sig_) / close.clip(lower_bound=1e-9)
    macd_norm      = macd / close.clip(lower_bound=1e-9)

    # ── ATR relatif ───────────────────────────────────────────
    atr_rels = {}
    for period in [7, 14]:
        atr_s = atr_series(df, period)
        atr_rels[f"atr{period}_rel"] = atr_s / close.clip(lower_bound=1e-9)

    # ── Bollinger %B ──────────────────────────────────────────
    bb_feats = {}
    for period in [10, 20]:
        sma = close.rolling_mean(period)
        std = close.rolling_std(period).clip(lower_bound=1e-9)
        bb_feats[f"bb_pct_{period}"]   = (close - (sma - 2*std)) / (4 * std)
        bb_feats[f"bb_width_{period}"] = (4 * std) / sma.clip(lower_bound=1e-9)

    # ── Momentum ──────────────────────────────────────────────
    mom_5  = close / close.shift(5).clip(lower_bound=1e-9) - 1
    mom_20 = close / close.shift(20).clip(lower_bound=1e-9) - 1

    # ── Volume ────────────────────────────────────────────────
    vol_ma        = volume.rolling_mean(20).clip(lower_bound=1e-9)
    vol_ratio_s   = volume / vol_ma
    vol_trend_5   = vol_ma.pct_change(5)
    c_arr = close.to_numpy()
    v_arr = volume.to_numpy()
    win   = 10
    n     = len(c_arr)
    corr_arr = np.full(n, np.nan)
    if n >= win:
        from numpy.lib.stride_tricks import sliding_window_view
        c_wins = sliding_window_view(c_arr, win)
        v_wins = sliding_window_view(v_arr, win)
        c_mean = c_wins.mean(axis=1)
        v_mean = v_wins.mean(axis=1)
        cov    = ((c_wins - c_mean[:, None]) * (v_wins - v_mean[:, None])).mean(axis=1)
        std_c  = c_wins.std(axis=1)
        std_v  = v_wins.std(axis=1)
        valid  = (std_c > 0) & (std_v > 0)
        corr_valid = np.where(valid, cov / (std_c * std_v + 1e-9), 0.0)
        corr_arr[win - 1:] = corr_valid
    vol_price_corr = pl.Series(corr_arr)

    # ── Volatilité réalisée ───────────────────────────────────
    ret_pct   = close.pct_change(1)
    vol_real_5  = ret_pct.rolling_std(5)
    vol_real_20 = ret_pct.rolling_std(20)
    vol_ratio_rv = vol_real_5 / vol_real_20.clip(lower_bound=1e-9)

    # ── ADX ───────────────────────────────────────────────────
    adx_14 = _adx(df, 14)[0]

    # ── High/Low range normalisé ──────────────────────────────
    hl_range  = (high - low) / close.clip(lower_bound=1e-9)
    close_pos = (close - low) / (high - low + 1e-9)

    feat_dict: Dict[str, pl.Series] = {
        "log_ret_1":        log_ret_1,
        "log_ret_3":        log_ret_3,
        "log_ret_5":        log_ret_5,
        "log_ret_10":       log_ret_10,
        "log_ret_20":       log_ret_20,
        "vwap_dist_20":     vwap_dist_20,
        "vwap_dist_50":     vwap_dist_50,
        "body_to_range":    body_to_range,
        "upper_wick_ratio": upper_wick_ratio,
        "lower_wick_ratio": lower_wick_ratio,
        "rsi_14":           rsi_14,
        "rsi_divergence":   rsi_divergence,
        **ema_rels,
        "cross_8_21":       cross_8_21,
        "cross_21_50":      cross_21_50,
        "macd_hist_norm":   macd_hist_norm,
        "macd_norm":        macd_norm,
        **atr_rels,
        **bb_feats,
        "mom_5":            mom_5,
        "mom_20":           mom_20,
        "vol_ratio":        vol_ratio_s,
        "vol_trend_5":      vol_trend_5,
        "vol_price_corr":   vol_price_corr,
        "vol_real_5":       vol_real_5,
        "vol_real_20":      vol_real_20,
        "vol_ratio_rv":     vol_ratio_rv,
        "adx_14":           adx_14,
        "hl_range":         hl_range,
        "close_pos":        close_pos,
    }

    result = pl.DataFrame(feat_dict)
    result = result.with_columns([
        pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite()).then(None).otherwise(pl.col(c)).alias(c)
        for c in result.columns
    ]).fill_null(0.0)
    return result


# ═══════════════════════════════════════════════════════════════
#  Labeling DYNAMIQUE — seuil adaptatif basé sur la volatilité
# ═══════════════════════════════════════════════════════════════
def compute_labels(df: pl.DataFrame, lookahead: int = 3,
                   vol_multiplier: float = 0.6) -> pl.Series:
    """Seuil adaptatif : seuil = volatilité_20p × sqrt(lookahead) × vol_multiplier.

    Le seuil est décalé d'une barre (shift(1)) pour éviter le lookahead bias.
    """
    close    = df["close"]
    log_ret  = (close / close.shift(1).clip(lower_bound=1e-9)).log(math.e)
    volatility         = log_ret.rolling_std(20)
    dynamic_threshold  = volatility.shift(1) * math.sqrt(lookahead) * vol_multiplier
    future = (close.shift(-lookahead) / close.clip(lower_bound=1e-9)).log(math.e)
    return (future > dynamic_threshold).cast(pl.Int32)


# ═══════════════════════════════════════════════════════════════
#  Espace de recherche LightGBM (remplace sklearn hyperparams)
# ═══════════════════════════════════════════════════════════════
LGB_PARAM_SPACE: Dict[str, List] = {
    "num_leaves":        [15, 31, 63],
    "learning_rate":     [0.05, 0.1, 0.2],
    "min_data_in_leaf":  [5, 10, 20],
    "feature_fraction":  [0.7, 0.85, 1.0],
    "bagging_fraction":  [0.7, 0.85, 1.0],
    "lambda_l1":         [0.0, 0.1, 1.0],
    "lambda_l2":         [0.0, 0.1, 1.0],
}


def _auc_score(y_true: np.ndarray, y_proba: np.ndarray) -> float:
    """Calcule l'AUC sans sklearn — formule Mann-Whitney U.

    AUC = (sum des ranks positifs - n_pos*(n_pos+1)/2) / (n_pos * n_neg).
    Robuste aux ex aequo via la moyenne des ranks.
    """
    y_true = np.asarray(y_true).astype(int)
    y_proba = np.asarray(y_proba, dtype=float)
    n_pos = int((y_true == 1).sum())
    n_neg = int((y_true == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return 0.5
    # Ranks (1-indexed), moyenne pour les ex aequo.
    order = np.argsort(y_proba, kind="mergesort")
    ranks = np.empty(len(y_proba), dtype=float)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and y_proba[order[j + 1]] == y_proba[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # moyenne des positions 1-indexed
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    sum_pos = float(ranks[y_true == 1].sum())
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _time_series_split(n: int, n_splits: int) -> List[Tuple[np.ndarray, np.ndarray]]:
    """TimeSeriesSplit manuel (sans sklearn).

    Retourne une liste de (train_idx, test_idx) pour walk-forward CV.
    Chaque test fold a une taille fixe de n // (n_splits + 1).
    """
    if n_splits < 2:
        return []
    test_size = n // (n_splits + 1)
    if test_size < 1:
        return []
    splits = []
    for i in range(n_splits):
        train_end = n - (n_splits - i) * test_size
        train_idx = np.arange(0, train_end)
        test_idx  = np.arange(train_end, train_end + test_size)
        if len(train_idx) > 0 and len(test_idx) > 0:
            splits.append((train_idx, test_idx))
    return splits


def _train_lgbm(X: np.ndarray, y: np.ndarray, params: Dict[str, Any]) -> Any:
    """Entraîne un booster LightGBM natif (sans sklearn wrapper)."""
    import lightgbm as lgb
    spw = float((y == 0).sum()) / max(int((y == 1).sum()), 1)
    common = {
        "objective":       "binary",
        "metric":          "auc",
        "verbosity":       -1,
        "num_leaves":      int(params.get("num_leaves", 31)),
        "learning_rate":   float(params.get("learning_rate", 0.1)),
        "min_data_in_leaf": int(params.get("min_data_in_leaf", 20)),
        "feature_fraction": float(params.get("feature_fraction", 0.85)),
        "bagging_fraction": float(params.get("bagging_fraction", 0.85)),
        "bagging_freq":     int(params.get("bagging_freq", 5)),
        "lambda_l1":        float(params.get("lambda_l1", 0.0)),
        "lambda_l2":        float(params.get("lambda_l2", 0.0)),
        "scale_pos_weight": spw,
        "n_jobs":           1,
    }
    ds = lgb.Dataset(X, label=y, free_raw_data=False)
    booster = lgb.train(
        common, ds,
        num_boost_round=200,
        callbacks=[lgb.log_evaluation(-1)],
    )
    return booster


def random_search_hyperparams(
    X: np.ndarray, y: np.ndarray,
    n_trials: int = 20,
    cv_folds: int = 5,
    seed: int = 42,
    cancel_event=None,
) -> Tuple[dict, float, List[dict]]:
    """Random Search avec TimeSeriesSplit manuel (sans sklearn).

    Retourne (best_params, best_auc, all_results).
    """
    rng = random.Random(seed)

    # Adapter le nombre de folds aux données.
    min_class_count = int(np.bincount(y.astype(int)).min()) if len(y) > 0 else 0
    max_folds_by_class = max(2, min_class_count // 3)
    max_folds_by_size  = max(2, len(y) // 20)
    actual_folds = min(cv_folds, 5, max_folds_by_class, max_folds_by_size)
    if actual_folds < 2:
        return {}, 0.0, []

    splits = _time_series_split(len(y), actual_folds)
    if not splits:
        return {}, 0.0, []

    best_params: Dict[str, Any] = {}
    best_score  = -1.0
    all_results: List[dict] = []

    logger.info(f"[ml_dynamic_threshold] Random Search LGBM — {n_trials} essais, {actual_folds} folds")

    for trial in range(n_trials):
        if cancel_event is not None and cancel_event.is_set():
            logger.info("[ml_dynamic_threshold] Entraînement interrompu (annulation backtest)")
            break
        params = {k: rng.choice(v) for k, v in LGB_PARAM_SPACE.items()}
        try:
            fold_aucs = []
            for train_idx, test_idx in splits:
                X_tr, y_tr = X[train_idx], y[train_idx]
                X_te, y_te = X[test_idx],  y[test_idx]
                if len(np.unique(y_tr)) < 2 or len(np.unique(y_te)) < 2:
                    continue
                booster = _train_lgbm(X_tr, y_tr, params)
                proba = booster.predict(X_te)
                fold_aucs.append(_auc_score(y_te, proba))
            if not fold_aucs:
                continue
            mean_auc = float(np.mean(fold_aucs))
            std_auc  = float(np.std(fold_aucs))
            all_results.append({"params": params, "auc": mean_auc, "std": std_auc})
            if mean_auc > best_score:
                best_score, best_params = mean_auc, params
                logger.info(
                    f"  [ml_dynamic_threshold][Trial {trial+1:02d}] Nouveau meilleur AUC={mean_auc:.4f}"
                    f"±{std_auc:.4f} | {params}"
                )
        except Exception as e:
            logger.debug(f"  [Trial {trial+1:02d}] KO : {e}")

    logger.info(f"[ml_dynamic_threshold] Meilleur AUC={best_score:.4f} | {best_params}")
    all_results.sort(key=lambda x: -x["auc"])
    return best_params, best_score, all_results


# ═══════════════════════════════════════════════════════════════
#  Détection automatique du timeframe
# ═══════════════════════════════════════════════════════════════
_SECS_TO_TF: Dict[int, str] = {
    60: "1m", 180: "3m", 300: "5m", 900: "15m",
    1800: "30m", 3600: "1h", 14400: "4h", 86400: "1d",
}

def _detect_tf(df: pl.DataFrame) -> str:
    """Infère le timeframe depuis l'intervalle médian entre les barres."""
    if "time" not in df.columns or len(df) < 2:
        return "unknown"
    try:
        med_s = float(
            df["time"].tail(64).diff().drop_nulls()
            .dt.total_microseconds().median()
        ) / 1e6
    except Exception as e:
        logger.debug(f"[MLDynThreshold] infer timeframe KO : {e}")
        return "unknown"
    if not med_s or med_s <= 0:
        return "unknown"
    closest = min(_SECS_TO_TF, key=lambda k: abs(k - med_s))
    if abs(closest - med_s) <= max(closest * 0.15, 5):
        return _SECS_TO_TF[closest]
    return f"custom_{int(med_s)}s"


# ═══════════════════════════════════════════════════════════════
#  Classe Strategy — interface compatible moteur
# ═══════════════════════════════════════════════════════════════
class MLDynamicThresholdStrategy(BaseStrategyML):
    """Stratégie ML à seuil dynamique — LightGBM natif (sans sklearn).

    Interface identique aux autres stratégies v6 : méthode score() retourne
    un dict {score, side, name, reason, conditions, indicators}.

    Modes de réentraînement :
    - managed_externally=False (défaut, backtest) : réentraînement inline toutes les
      `retrain_every` itérations, dans le thread courant.
    - managed_externally=True (live) : le LiveTrader planifie fit() en arrière-plan.
    """
    name = "ml_dynamic_threshold"
    # Recette(s) consommée(s) — surchargeable par le bloc `models:`
    # du YAML (cf. app.ml.recipe.strategy_models).
    models: Dict[str, str] = {"signal": "dyn_threshold_v1"}
    retrain_interval_h: int = 6
    model_dir: str = "models"

    timeframes: List[str] = ["5m", "15m", "1h"]

    # Le `model_type` est conservé pour compat config (YAML) mais n'a plus
    # d'effet — on utilise toujours LightGBM. Si `model_type` était
    # "logistic_regression", on loggue un info au premier fit.
    param_space: Dict[str, List] = {
        "lookahead":      [2, 3, 5],
        "vol_multiplier": [0.4, 0.6, 0.8],
        "adx_min":        [15.0, 20.0, 25.0],
        "proba_long":     [0.55, 0.60, 0.65],
        "proba_short":    [0.35, 0.40, 0.45],
    }

    fixed_params: Dict[str, Any] = {
        "n_trials":      8,
        "min_train":     150,
        "retrain_every": 50,
        "hyper_search_every": 20,
        "max_train_window": 6000,
    }

    @classmethod
    def score_holdout(cls, path_prefix: str, holdout_df, *,
                      gate_cfg: Any = None, params: dict = None) -> Dict[str, Any]:
        """Scorer dédié — le format de CETTE recette diffère du bundle V4 sur
        les trois plans à la fois :

          - **persistance** : un seul ``{path}.lgb`` (+ ``.meta.json``), pas
            de paire amplitude/direction ;
          - **features** : ``compute_features`` (~30 colonnes : VWAP,
            micro-structure, volatilité réalisée…), pas le catalogue V4 ;
          - **labels** : ``compute_labels`` — hausse au-delà d'un seuil
            *adaptatif à la volatilité*, pas un quantile d'amplitude fixe.

        Le scorer par défaut ne pouvait donc rien en tirer et le gate
        rapportait un trompeur « labels mono-classe / holdout dégénéré ».
        Retourne ``{"n": …, "auc_dir": …}`` — cohérent avec ``gate_spec``.

        classmethod : ne touche jamais l'état ML de l'instance courante (on
        score un artefact sur disque, souvent le SORTANT, pas ce qui est en
        mémoire).
        """
        import json

        from app.ml.scoring import rank_auc

        lgb_path, meta_path = f"{path_prefix}.lgb", f"{path_prefix}.meta.json"
        if not (os.path.exists(lgb_path) and os.path.exists(meta_path)):
            return {}
        try:
            import lightgbm as lgb
            booster = lgb.Booster(model_file=lgb_path)
            with open(meta_path, "r", encoding="utf-8") as f:
                feat_cols = list(json.load(f).get("features") or [])
        except Exception as e:
            logger.warning(f"[{cls.name}] score_holdout : artefact illisible ({e})")
            return {}
        if not feat_cols:
            return {}

        p = params or {}
        lookahead = int(p.get("lookahead", 3))
        vol_multiplier = float(p.get("vol_multiplier", 0.6))

        feats = compute_features(holdout_df)
        if feats is None or len(feats) == 0:
            return {}
        labels = compute_labels(holdout_df, lookahead, vol_multiplier)

        # Même troncature qu'à l'entraînement (_fit_impl) : les dernières
        # barres n'ont pas de futur observable dans le holdout.
        n_valid = max(0, len(holdout_df) - 2 * lookahead)
        if n_valid < 30:
            return {}
        y = labels[:n_valid].fill_null(0).to_numpy().astype(np.int64)

        missing = [c for c in feat_cols if c not in feats.columns]
        if missing:
            logger.warning(
                f"[{cls.name}] score_holdout : {len(missing)} features absentes "
                f"du holdout (ex. {missing[:3]}) — artefact incompatible"
            )
            return {}
        X = np.nan_to_num(
            feats[:n_valid].select(feat_cols).to_numpy().astype(np.float64),
            nan=0.0, posinf=1.0, neginf=-1.0,
        )
        try:
            proba = booster.predict(X)
        except Exception as e:
            logger.warning(f"[{cls.name}] score_holdout : prédiction KO ({e})")
            return {}
        return {"n": int(n_valid), "auc_dir": rank_auc(y, np.asarray(proba, dtype=float))}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        min_train = int(p.get("min_train", self.fixed_params["min_train"]))
        return min_train + 50

    def __init__(self,
                 lookahead:      int   = 3,
                 vol_multiplier: float = 0.6,
                 adx_min:        float = 20.0,
                 proba_long:     float = 0.60,
                 proba_short:    float = 0.40,
                 n_trials:       int   = 8,
                 min_train:      int   = 150,
                 retrain_every:  int   = 50,
                 hyper_search_every: int = 20,
                 max_train_window:   int = 6000,
                 # Compat : model_type ignoré (toujours LightGBM).
                 model_type:     str   = "lightgbm"):

        self.model_type     = "lightgbm"  # forcé ; model_type param ignoré
        self.lookahead      = lookahead
        self.vol_multiplier = vol_multiplier
        self.adx_min        = adx_min
        self.proba_long     = proba_long
        self.proba_short    = proba_short
        self.n_trials       = n_trials
        self.min_train      = min_train
        self.retrain_every  = retrain_every
        self.hyper_search_every = hyper_search_every
        self.max_train_window   = max_train_window
        self._fit_count_per_tf: Dict[str, int] = {}
        self.disabled_timeframes: List[str] = []

        # ── Stockage multi-TF : un booster LightGBM natif par TF ──────────
        self._boosters:        Dict[str, Any]       = {}   # tf → lgb.Booster
        self._best_auc_per_tf: Dict[str, float]     = {}
        self._best_params_per_tf: Dict[str, dict]   = {}
        self._feature_cols_per_tf: Dict[str, List]  = {}
        self._call_count_per_tf: Dict[str, int]     = {}
        self._trained_tfs:      set                 = set()

        # Compat rétrograde
        self._best_auc:     float    = 0.0
        self._best_params:  dict     = {}
        self._feature_cols: List[str] = []

        self._model_lock:   threading.RLock = threading.RLock()
        self.managed_externally: bool = False
        self._cancel_event: Optional[threading.Event] = None
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0
        self._bt_train_offset: Optional[int] = None

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les ~30 features pour toute la fenêtre du backtest."""
        try:
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="ml_dyn_threshold", version="1",
                builder=lambda w: compute_features(w),
                in_kind="polars", out_kind="polars", include_time=False)
            if feats is not None and len(feats) > 0:
                self._bt_features = feats
                self._bt_features_len = len(df)
                logger.info(
                    f"[MLDynThr] backtest : features pré-calculées sur "
                    f"{self._bt_features_len} bougies ({len(feats.columns)} colonnes)"
                )
        except Exception as e:
            logger.warning(f"[MLDynThr] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    def _get_or_build_features(self, df: pl.DataFrame, offset: int = None) -> pl.DataFrame:
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            if offset is not None and offset + len(df) <= self._bt_features_len:
                return self._bt_features.slice(offset, len(df))
            return self._bt_features.head(len(df))
        return compute_features(df)

    # ── Interface principale ───────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None, df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if params:
            p = params.get(self.name, {})
            if p:
                if "disabled_timeframes" in p:
                    self.disabled_timeframes = list(p["disabled_timeframes"])
                if not self._trained_tfs:
                    for k, v in p.items():
                        if k != "disabled_timeframes" and hasattr(self, k):
                            setattr(self, k, v)
        return self._signal(df)

    def _signal(self, df: pl.DataFrame) -> Dict[str, Any]:
        if self._cancel_event is not None and self._cancel_event.is_set():
            return self._no_signal()
        if len(df) < self.min_train + 20:
            return self._no_signal()

        tf = _detect_tf(df)
        if self.disabled_timeframes and tf in self.disabled_timeframes:
            return self._no_signal()
        cnt = self._call_count_per_tf.get(tf, 0) + 1
        self._call_count_per_tf[tf] = cnt

        if tf not in self._trained_tfs:
            if self.managed_externally:
                return self._no_signal()
            self._fit(df, tf)
        elif not self.managed_externally and cnt % self.retrain_every == 0:
            self._fit(df, tf)

        if tf not in self._trained_tfs:
            return self._no_signal()

        return self._predict(df, tf)

    # ── Entraînement ──────────────────────────────────────────
    _TRAIN_STATE_ATTRS = ('_boosters', '_best_params_per_tf', '_best_auc_per_tf',
                          '_feature_cols_per_tf')
    _TRAIN_PARAM_KEYS  = ('lookahead', 'vol_multiplier', 'n_trials',
                          'max_train_window', '_ran_search', '_prev_p')

    def _fit(self, df: pl.DataFrame, tf: str = "") -> None:
        tf_key = tf or _detect_tf(df)
        fit_no = self._fit_count_per_tf.get(tf_key, 0)
        self._fit_count_per_tf[tf_key] = fit_no + 1
        hyper_every = max(int(self.hyper_search_every or 1), 1)
        prev_p = self._best_params_per_tf.get(tf_key)
        ran_search = not (prev_p and fit_no % hyper_every != 0)

        from app.core.train_cache import aligned_train_window, cached_train
        n_train = int(self.max_train_window or 6000) + 2 * int(self.lookahead) + 100
        train_df, offset = aligned_train_window(df, self.retrain_every, n_train)
        self._bt_train_offset = offset

        cache_params = {
            "lookahead":         self.lookahead,
            "vol_multiplier":    self.vol_multiplier,
            "n_trials":          self.n_trials,
            "max_train_window":  self.max_train_window,
            "_ran_search":       ran_search,
            "_prev_p":           None if ran_search else prev_p,
        }
        cached_train(self, train_df, tf_key, cache_params, self._fit_impl,
                    self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS)
        self._bt_train_offset = None

    def _fit_impl(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        ran_search = bool(params.get("_ran_search"))
        prev_p     = params.get("_prev_p")
        try:
            feats  = self._get_or_build_features(df, offset=self._bt_train_offset)
            labels = compute_labels(df, self.lookahead, self.vol_multiplier)

            n       = len(df)
            n_valid = max(0, n - 2 * self.lookahead)
            X       = feats[:n_valid].to_numpy()
            y       = labels[:n_valid].fill_null(0).to_numpy().astype(np.int64)

            max_win = int(self.max_train_window or 0)
            if max_win > 0 and len(X) > max_win:
                X = X[-max_win:]
                y = y[-max_win:]

            # Sanitisation NaN/inf (LightGBM gère mais on évite les extrêmes).
            X = np.nan_to_num(X.astype(np.float64), nan=0.0, posinf=1.0, neginf=-1.0)

            if len(np.unique(y)) < 2:
                logger.warning(
                    f"[{self.name}] Fit annulé : une seule classe dans les labels."
                )
                return False

            class_counts = np.bincount(y)
            if class_counts.min() < 10:
                logger.warning(
                    f"[{self.name}] Fit annulé : classe minoritaire trop petite "
                    f"({class_counts.min()} exemples)."
                )
                return False

            if not ran_search:
                best_p, best_auc = prev_p, self._best_auc_per_tf.get(tf_key, 0.0)
            else:
                _hs_cap = 3000
                X_hs, y_hs = (X[-_hs_cap:], y[-_hs_cap:]) if len(X) > _hs_cap else (X, y)
                best_p, best_auc, _ = random_search_hyperparams(
                    X_hs, y_hs, self.n_trials,
                    cancel_event=self._cancel_event,
                )

            if self._cancel_event is not None and self._cancel_event.is_set():
                logger.info(f"[{self.name}] Entraînement annulé après random search")
                return False

            if not best_p:
                logger.warning(
                    f"[{self.name}] Random search sans essai valide — "
                    f"repli sur hyperparamètres par défaut LightGBM"
                )
                best_p, best_auc = {}, 0.0

            # Entraînement final sur toute la fenêtre avec les meilleurs params.
            booster = _train_lgbm(X, y, best_p)

            with self._model_lock:
                self._boosters[tf_key]            = booster
                self._best_params_per_tf[tf_key] = best_p
                self._best_auc_per_tf[tf_key]    = best_auc
                self._feature_cols_per_tf[tf_key] = list(feats.columns)
                self._trained_tfs.add(tf_key)
                self._best_auc     = best_auc
                self._best_params  = best_p
                self._feature_cols = list(feats.columns)

            # Validation IS/OOS si random search a tourné.
            try:
                split = int(len(X) * 0.8)
                if ran_search and split > 20 and len(X) - split > 10:
                    booster_oos = _train_lgbm(X[:split], y[:split], best_p)
                    proba_oos = booster_oos.predict(X[split:])
                    oos_auc   = float(_auc_score(y[split:], proba_oos))
                    ratio     = best_auc / max(oos_auc, 1e-9)
                    status    = "✅ robuste" if ratio < 1.3 else "⚠️ surapprentissage probable"
                    logger.info(
                        f"[{self.name}/{tf_key}] IS AUC={best_auc:.4f} | OOS AUC={oos_auc:.4f} "
                        f"| Ratio IS/OOS={ratio:.2f} — {status}"
                    )
            except Exception as oe:
                logger.debug(f"[{self.name}/{tf_key}] IS/OOS check KO : {oe}")

            logger.info(
                f"[{self.name}/{tf_key}] Modèle LightGBM entraîné — AUC={best_auc:.4f} | n={len(X)}"
            )
            return True
        except Exception as e:
            logger.error(f"[{self.name}] Erreur entraînement : {e}")
            return False

    # ── Prédiction ────────────────────────────────────────────
    def _predict(self, df: pl.DataFrame, tf: str = "") -> Dict[str, Any]:
        tf = tf or _detect_tf(df)
        try:
            feats = self._get_or_build_features(df)
            if len(feats) == 0:
                return self._no_signal()

            adx_val = float(_adx(df.tail(300), 14)[0][-1])
            auc_tf  = self._best_auc_per_tf.get(tf, self._best_auc)
            if adx_val < self.adx_min:
                return {
                    "score":      0.0,
                    "side":       "none",
                    "name":       self.name,
                    "reason":     f"Filtre régime : ADX={adx_val:.1f} < {self.adx_min} [{tf}]",
                    "conditions": [f"ADX insuffisant ({adx_val:.1f})"],
                    "indicators": {"adx": round(adx_val, 2), "auc": round(auc_tf, 4), "tf": tf},
                }

            X_last = np.nan_to_num(
                feats[-1:].to_numpy().astype(np.float64),
                nan=0.0, posinf=1.0, neginf=-1.0,
            )
            with self._model_lock:
                booster = self._boosters.get(tf)
            if booster is None:
                return self._no_signal()
            # LightGBM natif : booster.predict retourne la proba directe (équivalent
            # de predict_proba()[:, 1] du wrapper sklearn).
            proba  = float(booster.predict(X_last)[0])
            close  = float(df["close"][-1])

            if proba >= self.proba_long:
                side  = "long"
                score = 0.5 + (proba - self.proba_long) / max(1.0 - self.proba_long, 1e-9) * 0.5
            elif proba <= self.proba_short:
                side  = "short"
                score = 0.5 + (self.proba_short - proba) / max(self.proba_short, 1e-9) * 0.5
            else:
                return self._no_signal()

            score = round(min(score, 0.99), 3)

            indicators = {
                "proba_up":      round(proba, 4),
                "model":         "lightgbm",
                "auc":           round(auc_tf, 4),
                "adx":           round(adx_val, 2),
                "tf":            tf,
                "n_features":    len(self._feature_cols_per_tf.get(tf, self._feature_cols)),
                "lookahead":     self.lookahead,
                "vol_multiplier":self.vol_multiplier,
                "close":         close,
            }

            return {
                "score":      score,
                "side":       side,
                "name":       self.name,
                "reason":     (
                    f"ML-DynThreshold lightgbm/{tf} — proba_up={proba:.3f} "
                    f"(long≥{self.proba_long}, short≤{self.proba_short}, ADX={adx_val:.1f})"
                ),
                "conditions": [
                    f"Proba hausse : {proba:.3f}",
                    f"Modèle : lightgbm [{tf}]",
                    f"AUC cross-val : {auc_tf:.4f}",
                    f"Lookahead : {self.lookahead} bougies",
                    f"Seuil dynamique (vol_mult={self.vol_multiplier})",
                    f"ADX : {adx_val:.1f}",
                ],
                "indicators": indicators,
            }

        except Exception as e:
            logger.error(f"[{self.name}] Erreur prédiction : {e}")
            return self._no_signal()

    @staticmethod
    def _no_signal() -> Dict[str, Any]:
        return {
            "score":      0.0,
            "side":       "none",
            "name":       "ml_dynamic_threshold",
            "reason":     "Pas de signal ML",
            "conditions": [],
            "indicators": {},
        }

    # ── Contrat BaseStrategyML ─────────────────────────────────────────────

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        """Entraîne le modèle pour le TF détecté depuis df."""
        if params:
            p = params.get(self.name, {})
            for k, v in p.items():
                if hasattr(self, k):
                    setattr(self, k, v)
        tf = _detect_tf(df)
        self._fit(df, tf)

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        """Génère un signal sans réentraîner (modèle déjà chargé en mémoire)."""
        return self._predict(df)

    def save_model(self, path: str) -> None:
        """Persiste le booster LightGBM natif + métadonnées JSON (RCE-safe).

        Format : {path}.lgb (booster natif) + {path}.meta.json (features, auc, params).
        Plus de pickle/joblib — élimine le risque RCE.
        """
        tf = os.path.basename(path).rsplit("_", 1)[-1].split(".")[0]
        with self._model_lock:
            # Repli sur l'unique booster entraîné : ``fit()`` indexe sur le TF
            # INFÉRÉ des bougies (``_detect_tf``), qui peut retomber sur
            # "unknown"/"custom_Ns" là où ``save_model`` déduit le TF du nom de
            # fichier. Sans repli l'écriture était un no-op silencieux, et
            # l'échec ne se voyait qu'au « artefacts absents » du registre.
            key = tf if tf in self._boosters else (
                next(iter(self._boosters)) if len(self._boosters) == 1 else tf)
            booster   = self._boosters.get(key)
            best_p    = self._best_params_per_tf.get(key, {})
            auc       = self._best_auc_per_tf.get(key, 0.0)
            feat_cols = self._feature_cols_per_tf.get(key, [])
        if booster is None:
            logger.warning(
                f"[{self.name}] save_model({path}) : aucun booster à persister "
                f"(TF demandé={tf!r}, clés disponibles={sorted(self._boosters)})"
            )
            return
        try:
            import json
            lgb_path  = f"{path}.lgb"
            meta_path = f"{path}.meta.json"
            os.makedirs(os.path.dirname(lgb_path) or ".", exist_ok=True)
            booster.save_model(lgb_path)
            payload = {
                "tf":          tf,
                "features":    list(feat_cols),
                "best_params": dict(best_p),
                "best_auc":    float(auc),
                "model_type":  "lightgbm",
                "format_version": 1,
                "source":      "ml_dynamic_threshold (LightGBM natif)",
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            logger.info(f"[{self.name}/{tf}] Modèle LightGBM sauvegardé → {lgb_path}")
        except Exception as e:
            logger.warning(f"[{self.name}/{tf}] Sauvegarde modèle KO : {e}")

    def load_model(self, path: str) -> bool:
        """Charge le booster LightGBM natif (``{path}.lgb`` + ``.meta.json``)."""
        tf = os.path.basename(path).rsplit("_", 1)[-1].split(".")[0]
        try:
            import json

            import lightgbm as lgb
            lgb_path  = f"{path}.lgb"
            meta_path = f"{path}.meta.json"

            if os.path.exists(lgb_path) and os.path.exists(meta_path):
                booster = lgb.Booster(model_file=lgb_path)
                with open(meta_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                auc = float(payload.get("best_auc", 0.0))
                with self._model_lock:
                    self._boosters[tf]           = booster
                    self._best_params_per_tf[tf] = dict(payload.get("best_params") or {})
                    self._best_auc_per_tf[tf]    = auc
                    self._feature_cols_per_tf[tf] = list(payload.get("features") or [])
                    self._trained_tfs.add(tf)
                    self._best_auc     = auc
                    self._best_params  = dict(payload.get("best_params") or {})
                    self._feature_cols = list(payload.get("features") or [])
                logger.info(f"[{self.name}/{tf}] Modèle LightGBM chargé (natif) — AUC={auc:.4f}")
                return True

            return False
        except Exception as e:
            logger.warning(f"[{self.name}/{tf}] Chargement modèle KO : {e}")
            return False

    def reset_model(self) -> None:
        """Réinitialise tous les modèles (walk-forward backtest, tests)."""
        with self._model_lock:
            self._boosters.clear()
            self._best_auc_per_tf.clear()
            self._best_params_per_tf.clear()
            self._feature_cols_per_tf.clear()
            self._call_count_per_tf.clear()
            self._trained_tfs.clear()
            self._best_auc     = 0.0
            self._best_params  = {}
            self._feature_cols = []
        self._bt_features = None
        self._bt_features_len = 0

    @property
    def is_trained(self) -> bool:
        return len(self._trained_tfs) > 0


# Alias Engine
class Strategy(MLDynamicThresholdStrategy):
    pass
