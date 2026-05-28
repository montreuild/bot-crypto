"""Stratégie Scoring Statistique Opus V3 — LightGBM sur les mêmes features que V1/V2.

Différences clés par rapport à V1/V2 :
- V1/V2 : formule heuristique linéaire (approximation du rapport)
- V3 : modèle LightGBM entraîné inline sur les données réelles du backtest

Améliorations vs première version :
  1. Labels à seuil + lookahead multi-barres
     Au lieu de "close[i+1] > close[i]" (50/50 pur bruit), on labélise uniquement
     les barres où le prix a bougé de ≥ min_move_pct% dans les lookahead prochaines barres.
     Les barres sans mouvement significatif sont exclues du jeu d'entraînement.
     → filtre le bruit court terme, entraîne sur les vrais mouvements directionnels.

  2. Régime + indicateurs DI comme features additionnelles
     Le régime (Range/TU/TD/Choppy) et la force directionnelle DI (pdi-ndi)
     sont ajoutés en features numériques → le modèle apprend lui-même l'importance
     des régimes au lieu d'un filtre binaire.

  3. Hyperparamètres LightGBM renforcés
     num_leaves=31, reg_alpha/lambda, subsample — plus robuste, moins overfit.

Protocole d'entraînement :
  - Warmup : warmup_bars (défaut 600) pour la première fenêtre
  - Walk-forward léger : réentraînement toutes les retrain_every barres
  - Fenêtre glissante : min(n_disponible, warmup_bars*3) barres → garde le contexte récent
  - AUC cible : 0.58-0.68 (dataset limité inline vs 50k du rapport)
"""

import logging
import math
import os
import threading
from typing import Dict, Any, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategyML
from app.core.indicators import pre_val

logger = logging.getLogger(__name__)

REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3

REGIME_LABELS = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
}

# Features de base (colonnes _pre_* de precompute_df)
_BASE_COLS = [
    "_pre_atr_pct_r",
    "_pre_volstd20_r",
    "_pre_range_r",
    "_pre_body_abs_r",
    "_pre_upper_wick",
    "_pre_lower_wick",
    "_pre_body",
    "_pre_rsi14",
    "_pre_rsi_vel6",
    "_pre_rsi_vel12",
    "_pre_rsi_accel",
    "_pre_macd_hist_d1",
    "_pre_range_pos20",
    "_pre_volratio20",
    "_pre_adx14",
]

# Features additionnelles pour le régime (calculées inline)
# Noms symboliques — construits dans _build_features()
_ALL_FEATURE_NAMES = _BASE_COLS + [
    "regime_encoded",   # 0=Range, 0.33=TU, 0.67=TD, 1=Choppy
    "di_balance",       # (pdi - ndi) / 50  → biais directionnel normalisé
    "trend_strength",   # (sma20 - sma200) / sma200 → inclinaison tendance
    "atr_trend",        # atr_r vs sa propre moyenne 10 barres → expansion/contraction
]


def _detect_regime(adx: float, sma20: float, sma50: float,
                   sma100: float, sma200: float,
                   pdi: float = 0.0, ndi: float = 0.0,
                   adx_threshold: float = 20.0) -> int:
    if adx < adx_threshold:
        return REGIME_RANGE
    if sma20 > sma50 > sma100 > sma200 and pdi > ndi:
        return REGIME_TREND_UP
    if sma20 < sma50 < sma100 < sma200 and ndi > pdi:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


_REGIME_ENCODED = {
    REGIME_RANGE:    0.00,
    REGIME_TREND_UP: 0.33,
    REGIME_TREND_DN: 0.67,
    REGIME_CHOPPY:   1.00,
}


def _build_features(df: pl.DataFrame, adx_threshold: float = 20.0) -> Optional[np.ndarray]:
    """
    Construit la matrice de features complète (base + régime + DI + trend).
    Retourne None si les colonnes de base sont manquantes.
    """
    missing = [c for c in _BASE_COLS if c not in df.columns]
    if missing:
        return None

    n = len(df)
    try:
        base = df.select(_BASE_COLS).to_numpy().astype(np.float32)
        base = np.nan_to_num(base, nan=0.0, posinf=1.0, neginf=-1.0)
    except Exception:
        return None

    # Features additionnelles (régime, DI, trend)
    has_regime_cols = all(c in df.columns for c in (
        "_pre_adx14", "_pre_sma20", "_pre_sma50",
        "_pre_sma100", "_pre_sma200", "_pre_pdi14", "_pre_ndi14"
    ))

    if has_regime_cols:
        adx_a  = df["_pre_adx14"].to_numpy()
        sma20  = df["_pre_sma20"].to_numpy()
        sma50  = df["_pre_sma50"].to_numpy()
        sma100 = df["_pre_sma100"].to_numpy()
        sma200 = df["_pre_sma200"].to_numpy()
        pdi_a  = df["_pre_pdi14"].to_numpy()
        ndi_a  = df["_pre_ndi14"].to_numpy()

        regimes = np.array([
            _detect_regime(adx_a[i], sma20[i], sma50[i], sma100[i], sma200[i],
                           pdi_a[i], ndi_a[i], adx_threshold)
            for i in range(n)
        ], dtype=np.float32)
        regime_enc  = np.vectorize(_REGIME_ENCODED.get)(regimes.astype(int)).astype(np.float32)
        di_balance  = ((pdi_a - ndi_a) / 50.0).astype(np.float32)
        sma200_safe = np.where(sma200 > 0, sma200, 1.0)
        trend_str   = ((sma20 - sma200) / sma200_safe).astype(np.float32)

        # ATR trend: atr_pct_r rolling 10 barres (variation de volatilité)
        atr_r = base[:, _BASE_COLS.index("_pre_atr_pct_r")]
        atr_trend = np.zeros(n, dtype=np.float32)
        for i in range(10, n):
            atr_trend[i] = atr_r[i] - atr_r[max(0, i - 10):i].mean()

        extra = np.column_stack([regime_enc, di_balance, trend_str, atr_trend])
    else:
        extra = np.zeros((n, 4), dtype=np.float32)

    return np.column_stack([base, extra])


class Strategy(BaseStrategyML):
    name      = "scoring_statistique_opus_v3"
    model_dir = "models"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":  [15, 20, 25],
        "min_prob":       [0.55, 0.58, 0.60, 0.62],
        "lookahead":      [2, 3, 5],
        "min_move_pct":   [0.002, 0.003, 0.004],
        "retrain_every":  [300, 500, 800],
        "warmup_bars":    [500, 800, 1000],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._models:         Dict[str, Any] = {}
        self._scalers:        Dict[str, Any] = {}
        self._trained_tfs:    set = set()
        self._lock            = threading.Lock()
        self._call_cnt:       Dict[str, int] = {}
        self._last_retrain:   Dict[str, int] = {}
        self._managed_externally = False
        # Compatibilité API ML
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        # Metadata entraînement (affiché dans l'UI)
        self._train_meta:     Dict[str, dict]  = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("warmup_bars", 600)) + 20

    @property
    def is_trained(self) -> bool:
        return bool(self._trained_tfs)

    @property
    def managed_externally(self) -> bool:
        return self._managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool):
        self._managed_externally = v

    def reset_model(self) -> None:
        with self._lock:
            self._models.clear()
            self._scalers.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._train_meta.clear()
            self._last_retrain.clear()
            self._managed_externally = False
            self._best_auc = 0.0

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        p             = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold", 20.0))
        lookahead     = int(p.get("lookahead",      3))
        min_move_pct  = float(p.get("min_move_pct", 0.003))
        self._fit(df, adx_threshold=adx_threshold,
                  lookahead=lookahead, min_move_pct=min_move_pct)

    def _fit(self, df: pl.DataFrame, timeframe: str = "",
             adx_threshold: float = 20.0,
             lookahead: int = 3, min_move_pct: float = 0.003) -> None:
        """Interface attendue par auto_optimizer."""
        tf_key = timeframe or "default"
        self._train(df, tf_key, adx_threshold, lookahead, min_move_pct)

    # ── Cœur ML ───────────────────────────────────────────────────────────────

    def _train(self, df: pl.DataFrame, tf_key: str,
               adx_threshold: float = 20.0,
               lookahead: int = 3,
               min_move_pct: float = 0.003) -> bool:
        """
        Entraîne LightGBM avec labels à seuil + features régime.

        Labels (amélioration clé vs v1):
          y=1 si close[i+lookahead] > close[i] * (1 + min_move_pct)  → hausse réelle
          y=0 si close[i+lookahead] < close[i] * (1 - min_move_pct)  → baisse réelle
          Barres sans mouvement significatif → exclues du jeu d'entraînement

        Cela filtre le bruit des petits mouvements (<0.3%) et entraîne le modèle
        uniquement sur des signaux où un trade aurait une chance de sortir en profit.
        """
        try:
            import lightgbm as lgb
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            logger.error("[V3] pip install lightgbm scikit-learn")
            return False

        if len(df) < 100 + lookahead:
            return False

        X_all = _build_features(df, adx_threshold)
        if X_all is None:
            return False

        close   = df["close"].cast(pl.Float64).to_numpy()
        n       = len(close) - lookahead  # barres labélisables

        X_train = X_all[:n]
        c_now   = close[:n]
        c_ahead = close[lookahead:][:n]

        # Labels à seuil — filtre le bruit court terme
        up_thr   = c_now * (1.0 + min_move_pct)
        dn_thr   = c_now * (1.0 - min_move_pct)
        went_up  = c_ahead > up_thr
        went_dn  = c_ahead < dn_thr
        decisive = went_up | went_dn          # barres avec un mouvement réel

        n_decisive = decisive.sum()
        pct_kept   = 100.0 * n_decisive / max(n, 1)

        if n_decisive < 60:
            logger.debug(f"[V3] {tf_key}: trop peu de barres décisives ({n_decisive}), "
                         f"réduction min_move → next-bar fallback")
            # Fallback : next-bar direction sans seuil
            went_up  = c_ahead > c_now
            decisive = np.ones(n, dtype=bool)
            pct_kept = 100.0

        X_f = X_train[decisive].astype(np.float32)
        y_f = went_up[decisive].astype(np.int8)

        if len(X_f) < 60:
            return False

        scaler = StandardScaler()
        X_s    = scaler.fit_transform(X_f)

        # Pondération : équilibre hausse/baisse
        n_pos     = int(y_f.sum())
        n_neg     = len(y_f) - n_pos
        scale_pos = float(n_neg) / max(n_pos, 1)

        # Split temporel 80/20
        split = max(int(len(X_s) * 0.8), 40)
        if split >= len(X_s) - 10:
            split = len(X_s)

        import lightgbm as lgb
        params_lgb = {
            "objective":         "binary",
            "metric":            "auc",
            "num_leaves":        31,
            "learning_rate":     0.03,
            "min_child_samples": 20,
            "subsample":         0.7,
            "subsample_freq":    5,
            "colsample_bytree":  0.7,
            "reg_alpha":         0.1,
            "reg_lambda":        0.5,
            "scale_pos_weight":  scale_pos,
            "verbosity":         -1,
            "n_jobs":            1,
        }

        callbacks = [lgb.early_stopping(20, verbose=False), lgb.log_evaluation(-1)]

        if split < len(X_s):
            ds_train = lgb.Dataset(X_s[:split],  label=y_f[:split])
            ds_valid = lgb.Dataset(X_s[split:],  label=y_f[split:],
                                   reference=ds_train)
            valid_sets = [ds_valid]
        else:
            ds_train   = lgb.Dataset(X_s, label=y_f)
            valid_sets = [ds_train]
            callbacks  = [lgb.log_evaluation(-1)]

        try:
            booster = lgb.train(
                params_lgb, ds_train,
                num_boost_round=500,
                valid_sets=valid_sets,
                callbacks=callbacks,
            )
        except Exception as e:
            logger.warning(f"[V3] Entraînement échoué : {e}")
            return False

        auc = booster.best_score.get("valid_0", {}).get("auc", 0.0)

        with self._lock:
            self._models[tf_key]           = booster
            self._scalers[tf_key]          = scaler
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key]  = auc
            self._best_auc                 = auc
            self._train_meta[tf_key] = {
                "n_total":    n,
                "n_decisive": int(n_decisive),
                "pct_kept":   round(pct_kept, 1),
                "n_pos":      n_pos,
                "n_neg":      n_neg,
                "lookahead":  lookahead,
                "min_move":   min_move_pct,
                "auc":        round(auc, 4),
            }

        logger.info(
            f"[V3] {tf_key} entraîné : {len(X_f)} barres décisives / {n} total "
            f"({pct_kept:.0f}%) | AUC={auc:.3f} | "
            f"lookahead={lookahead} min_move={min_move_pct:.1%}"
        )
        return True

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    def save_model(self, path: str) -> None:
        import joblib
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        with self._lock:
            booster = self._models.get(tf_key)
            scaler  = self._scalers.get(tf_key)
            auc     = self._best_auc_per_tf.get(tf_key, 0.0)
            meta    = self._train_meta.get(tf_key, {})
        if booster is None:
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        joblib.dump({
            "booster":      booster,
            "scaler":       scaler,
            "feature_cols": _ALL_FEATURE_NAMES,
            "best_auc":     auc,
            "train_meta":   meta,
        }, path)
        logger.info(f"[V3] Modèle sauvegardé → {path} (AUC={auc:.3f})")

    def load_model(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        try:
            import joblib
            data = joblib.load(path)
            auc  = float(data.get("best_auc", 0.0))
            meta = data.get("train_meta", {})
            with self._lock:
                for key in (tf_key, "default"):
                    self._models[key]          = data["booster"]
                    self._scalers[key]         = data["scaler"]
                    self._best_auc_per_tf[key] = auc
                    self._trained_tfs.add(key)
                self._best_auc                = auc
                self._train_meta[tf_key]      = meta
            logger.info(f"[V3] Modèle chargé depuis {path} (AUC={auc:.3f})")
            return True
        except Exception as e:
            logger.warning(f"[V3] Chargement échoué {path}: {e}")
            return False

    # ── score() ───────────────────────────────────────────────────────────────

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p             = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold",  20.0))
        min_prob      = float(p.get("min_prob",        0.60))
        retrain_every = int(p.get("retrain_every",     500))
        warmup_bars   = int(p.get("warmup_bars",       600))
        lookahead     = int(p.get("lookahead",         3))
        min_move_pct  = float(p.get("min_move_pct",   0.003))

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)} < {self.min_bars_required(params)})")

        sym  = symbol or "default"
        cnt  = self._call_cnt.get(sym, 0) + 1
        self._call_cnt[sym] = cnt
        tf_key = sym

        # ── Walk-forward : entraînement initial puis périodique ────────────
        last       = self._last_retrain.get(tf_key, 0)
        need_train = (tf_key not in self._trained_tfs) or (cnt - last >= retrain_every)

        if need_train and not self._managed_externally:
            n_train  = min(len(df) - 1, warmup_bars * 3)
            train_df = df.slice(len(df) - n_train - 1, n_train)
            ok = self._train(train_df, tf_key, adx_threshold, lookahead, min_move_pct)
            if ok:
                self._last_retrain[tf_key] = cnt

        if tf_key not in self._trained_tfs:
            return self._none("Modèle non encore entraîné (warmup en cours)")

        # ── Régime courant ─────────────────────────────────────────────────
        c_now  = float(df["close"][-1] or 0.0)
        atr_v  = pre_val(df, "_pre_atr14")  or 0.0
        adx_v  = pre_val(df, "_pre_adx14")  or 0.0
        sma20  = pre_val(df, "_pre_sma20")  or pre_val(df, "_pre_ema20")  or c_now
        sma50  = pre_val(df, "_pre_sma50")  or pre_val(df, "_pre_ema50")  or c_now
        sma100 = pre_val(df, "_pre_sma100") or c_now
        sma200 = pre_val(df, "_pre_sma200") or pre_val(df, "_pre_ema200") or c_now
        pdi    = pre_val(df, "_pre_pdi14")  or 0.0
        ndi    = pre_val(df, "_pre_ndi14")  or 0.0

        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime     = _detect_regime(adx_v, sma20, sma50, sma100, sma200, pdi, ndi, adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        # Pas de signal en Range (modèle entraîné surtout sur tendances)
        if regime == REGIME_RANGE:
            return self._none(f"Range (ADX={adx_v:.0f})", regime=regime)

        # ── Prédiction ─────────────────────────────────────────────────────
        X_last = _build_features(df.slice(len(df) - 1, 1), adx_threshold)
        if X_last is None:
            return self._none("Features manquantes")

        with self._lock:
            booster = self._models.get(tf_key)
            scaler  = self._scalers.get(tf_key)

        if booster is None or scaler is None:
            return self._none("Modèle indisponible")

        try:
            prob_up = float(booster.predict(scaler.transform(X_last))[0])
        except Exception as e:
            logger.warning(f"[V3] Prédiction : {e}")
            return self._none("Erreur prédiction")

        dist_from_50 = abs(prob_up - 0.5)
        if dist_from_50 < (min_prob - 0.5):
            return self._none(
                f"prob={prob_up:.3f} < seuil {min_prob:.2f} | {regime_lbl}",
                prob_up=prob_up, regime=regime,
            )

        side = "long" if prob_up > 0.5 else "short"

        # Filtre cohérence régime/signal : pénalise les trades contre-tendance
        if regime == REGIME_TREND_DN and side == "long":
            size_fac = 0.4   # long en downtrend → risqué
        elif regime == REGIME_TREND_UP and side == "short":
            size_fac = 0.4   # short en uptrend → risqué
        elif regime == REGIME_TREND_DN and side == "short":
            size_fac = 1.0   # avec la tendance
        elif regime == REGIME_TREND_UP and side == "long":
            size_fac = 0.75  # avec la tendance
        else:
            size_fac = 0.5   # Choppy

        # Score : distance à 0.5 × factor de taille
        score = round(min(0.50 + dist_from_50 * 2.2, 0.94) * (size_fac * 0.4 + 0.6), 3)

        stop     = (c_now - 1.5 * atr_v) if side == "long" else (c_now + 1.5 * atr_v)
        rsi_v    = pre_val(df, "_pre_rsi14")     or 50.0
        auc_now  = self._best_auc_per_tf.get(tf_key, 0.0)
        meta     = self._train_meta.get(tf_key, {})

        return {
            "score":      score,
            "side":       side,
            "name":       self.name,
            "atr":        atr_v,
            "stop_hint":  round(stop, 2),
            "prob_up":    round(prob_up, 4),
            "regime":     regime,
            "regime_lbl": regime_lbl,
            "indicators": {
                "adx":        round(adx_v, 1),
                "rsi":        round(rsi_v, 1),
                "prob_up":    round(prob_up, 4),
                "auc":        round(auc_now, 3),
                "size_factor": size_fac,
                "n_decisive":  meta.get("n_decisive", 0),
                "pct_kept":    meta.get("pct_kept", 0),
            },
            "conditions": [
                f"Régime : {regime_lbl} (ADX={adx_v:.0f})",
                f"LightGBM P(hausse) = {prob_up:.3f} → {side.upper()} ✓",
                f"dist_50 = {dist_from_50:.3f} ≥ seuil {min_prob - 0.5:.3f}",
                f"Modèle AUC={auc_now:.3f} | taille {size_fac:.0%}",
                f"Labels décisifs : {meta.get('pct_kept', '?')}% "
                f"(lookahead={meta.get('lookahead', lookahead)}, "
                f"min_move={meta.get('min_move', min_move_pct):.1%})",
            ],
            "reason": (
                f"OpusV3-LGBM {side.upper()} | {regime_lbl} | "
                f"prob={prob_up:.3f} AUC={auc_now:.3f} "
                f"ADX={adx_v:.0f} RSI={rsi_v:.0f}"
            ),
        }

    def _none(self, reason: str = "", prob_up: float = 0.5, regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "prob_up":    round(prob_up, 4),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
