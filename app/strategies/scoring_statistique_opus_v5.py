"""Stratégie Scoring Statistique Opus V5 — V4 + Trend Up + trailing multi-barre.

V5 = V4 améliorée pour le trading réel (vs V4 reproduit la mesure académique du rapport).

Différences clés par rapport à V4 :

  1. **Trade en Trend Up activé** (avec seuils stricts)
     Le rapport §6.4 déconseille TU (AUC dir ≈ 0.50). MAIS §3.5 documente un
     léger biais haussier (up_share 51-52%) → on autorise TU avec:
       - Seuils plus stricts qu'en Trend Down
       - Taille réduite (×0.5 par défaut)
       - Préférence longs (pénalise les shorts en TU)

  2. **Exploitation du clustering (§3.1)**
     Le rapport documente 50-70% de clustering : quand un mouvement démarre,
     les barres suivantes vont souvent dans le même sens. V4 sort à la barre
     suivante et gaspille cet edge. V5 :
       - SL initial = 1.5×ATR (rapport §6.6)
       - Trailing stop progressif (breakeven → lock → tight)
       - Max hold = 8 barres (forcé si pas de mouvement, évite drift)

  3. **Conséquences attendues vs V4**
     - WR plus bas (trailing stop coupe parfois sur du bruit)
     - Gain moyen plus élevé (laisse courir les vagues de clustering)
     - Profit Factor théoriquement supérieur si clustering > 50%

Méthodologie ML héritée du rapport (inchangée vs V4) :
  - Deux modèles LightGBM (amplitude + direction)
  - Features avec lags 1/3/6/12
  - Régime ADX + alignement SMA + confirmation DI
  - Multiplicateur horaire (§6.5)
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.indicators import pre_val
from app.engine.engine import BaseStrategyML

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

_LAGS = (1, 3, 6, 12)

_AMP_BASE_COLS = [
    "_pre_atr_pct_r",
    "_pre_volstd20_r",
    "_pre_range_r",
    "_pre_body_abs_r",
]

_DIR_BASE_COLS = [
    "_pre_rsi14",
    "_pre_body",
    "_pre_upper_wick",
    "_pre_lower_wick",
    "_pre_macd_hist",
    "_pre_volratio20",
    "_pre_range_pos20",
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


def _bb_width(close: np.ndarray, period: int = 20) -> np.ndarray:
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(close)
    out = np.zeros(n, dtype=np.float32)
    if n < period:
        return out
    windows = sliding_window_view(close.astype(np.float64), period)
    sma = windows.mean(axis=1)
    std = windows.std(axis=1)
    out[period - 1:] = (4.0 * std / np.maximum(sma, 1e-9)).astype(np.float32)
    return out


def _build_features(df: pl.DataFrame, adx_threshold: float = 20.0) -> Optional[np.ndarray]:
    """48 features : 16 amp + 8 BB + 28 dir + 4 régime, avec lags 1/3/6/12."""
    missing = [c for c in _AMP_BASE_COLS + _DIR_BASE_COLS if c not in df.columns]
    if missing:
        return None

    n = len(df)
    cols: List[np.ndarray] = []

    for col in _AMP_BASE_COLS:
        arr = df[col].cast(pl.Float32).to_numpy()
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        for lag in _LAGS:
            lagged = np.concatenate([np.full(lag, arr[0] if n > 0 else 0.0), arr[:-lag]])
            cols.append(lagged.astype(np.float32))

    close = df["close"].cast(pl.Float32).to_numpy()
    bb_w  = _bb_width(close, 20)
    from numpy.lib.stride_tricks import sliding_window_view
    bb_rank = np.zeros(n, dtype=np.float32)
    if n >= 101:
        windows = sliding_window_view(bb_w, 100)          # (n-99, 100)
        bb_rank[100:] = (bb_w[100:, np.newaxis] > windows[:n - 100]).sum(axis=1) / 100.0
    for lag in _LAGS:
        cols.append(np.concatenate([np.zeros(lag, dtype=np.float32), bb_w[:-lag]]))
        cols.append(np.concatenate([np.zeros(lag, dtype=np.float32), bb_rank[:-lag]]))

    for col in _DIR_BASE_COLS:
        arr = df[col].cast(pl.Float32).to_numpy()
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        for lag in _LAGS:
            cols.append(np.concatenate([np.full(lag, arr[0] if n > 0 else 0.0), arr[:-lag]]).astype(np.float32))

    has_regime = all(c in df.columns for c in
                     ("_pre_adx14", "_pre_sma20", "_pre_sma50",
                      "_pre_sma100", "_pre_sma200", "_pre_pdi14", "_pre_ndi14"))
    if has_regime:
        adx_a = df["_pre_adx14"].to_numpy().astype(np.float32)
        s20   = df["_pre_sma20"].to_numpy().astype(np.float32)
        s50   = df["_pre_sma50"].to_numpy().astype(np.float32)
        s100  = df["_pre_sma100"].to_numpy().astype(np.float32)
        s200  = df["_pre_sma200"].to_numpy().astype(np.float32)
        pdi_a = df["_pre_pdi14"].to_numpy().astype(np.float32)
        ndi_a = df["_pre_ndi14"].to_numpy().astype(np.float32)

        is_range = adx_a < adx_threshold
        is_tu    = (~is_range) & (s20 > s50) & (s50 > s100) & (s100 > s200) & (pdi_a > ndi_a)
        is_td    = (~is_range) & (~is_tu) & (s20 < s50) & (s50 < s100) & (s100 < s200) & (ndi_a > pdi_a)
        regimes  = np.where(is_range, REGIME_RANGE,
                   np.where(is_tu, REGIME_TREND_UP,
                   np.where(is_td, REGIME_TREND_DN, REGIME_CHOPPY))).astype(np.float32)
        s200_safe = np.where(s200 > 0, s200, 1.0)
        cols.append(regimes / 3.0)
        cols.append((pdi_a - ndi_a) / 50.0)
        cols.append((s20 - s200) / s200_safe)
        cols.append(adx_a / 50.0)
    else:
        for _ in range(4):
            cols.append(np.zeros(n, dtype=np.float32))

    X = np.column_stack(cols)
    return np.nan_to_num(X, nan=0.0, posinf=1.0, neginf=-1.0)


def _hour_multiplier(hour: int) -> float:
    if 13 <= hour <= 17:
        return 1.0
    if 8  <= hour <= 12:
        return 0.7
    if 0  <= hour <= 5:
        return 0.3
    return 0.5


def _get_hour(df: pl.DataFrame) -> int:
    try:
        ts = df["time"][-1]
        if hasattr(ts, "hour"):
            return ts.hour
        import datetime
        return datetime.datetime.utcfromtimestamp(int(ts) / 1_000_000).hour
    except Exception:
        return 12


class Strategy(BaseStrategyML):
    name      = "scoring_statistique_opus_v5"
    model_dir = "models"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":     [15, 20, 25],
        # Trend Down — seuils élargis vers le bas (AUC réelle < 0.87 sur petit dataset)
        "amp_thresh_td":     [0.35, 0.40, 0.45, 0.50, 0.55],
        "dir_dist_td":       [0.03, 0.05, 0.08, 0.10, 0.12],
        # Trend Up — seuils stricts (rapport déconseille mais on tente)
        "amp_thresh_tu":     [0.45, 0.55, 0.60, 0.65],
        "dir_dist_tu":       [0.08, 0.12, 0.15, 0.18, 0.20],
        # Range/Choppy
        "amp_thresh_other":  [0.40, 0.50, 0.55, 0.60, 0.65],
        "dir_dist_other":    [0.05, 0.08, 0.12, 0.15, 0.18],
        # Exploitation clustering
        "max_hold_bars":     [4, 6, 8, 12],
        "trail_wide":        [1.0, 1.5, 2.0],
        "breakeven_r":       [0.4, 0.5, 0.7],
    }

    # Hyperparamètres d'entraînement figés (hors espace de recherche) pour
    # borner le coût des trials de l'optimiseur sur de longues fenêtres.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":   0.30,
        "warmup_bars":   2000,
        "retrain_every": 800,
    }

    def __init__(self):
        self._amp_models:  Dict[str, Any] = {}
        self._dir_models:  Dict[str, Any] = {}
        self._scalers:     Dict[str, Any] = {}
        self._trained_tfs: set            = set()
        self._lock         = threading.Lock()
        self._call_cnt:    Dict[str, int] = {}
        self._last_retrain:Dict[str, int] = {}
        self._managed_externally          = False
        self._best_auc:        float            = 0.0
        self._best_auc_per_tf: Dict[str, float] = {}
        self._train_meta:      Dict[str, dict]  = {}
        # Cache backtest : voir scoring_statistique_opus_v4 pour la motivation.
        self._bt_features_cache: Dict[float, np.ndarray] = {}
        self._bt_features_len: int = 0
        self._bt_full_df: Optional[pl.DataFrame] = None  # df complet du backtest (FeatureStore)

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les features pour toute la fenêtre du backtest.

        Le cache est par ``adx_threshold`` (paramètre qui influence le
        calcul du régime). En optim on pré-peuple toutes les valeurs du
        ``param_space`` pour que ``_train`` puisse lire le cache au lieu
        de rebuilder à chaque trial.
        """
        try:
            self._bt_features_cache.clear()
            self._bt_features_len = len(df)
            self._bt_full_df = df  # conservé pour des (re)builds alignés sur la fenêtre complète
            seeds = list(self.param_space.get("adx_threshold", [])) + [20.0]
            seen = set()
            for raw in seeds:
                key = round(float(raw), 6)
                if key in seen:
                    continue
                seen.add(key)
                X = self._build_full_features(key)
                if X is not None:
                    self._bt_features_cache[key] = X
            logger.info(
                f"[OpusV5-Stat] backtest : cache features prêt sur {len(df)} bougies "
                f"({len(self._bt_features_cache)} seuil(s) adx_threshold pré-calculés)"
            )
        except Exception as e:
            logger.warning(f"[OpusV5-Stat] prepare_for_backtest KO : {e}")
            self._bt_features_cache.clear()
            self._bt_features_len = 0
            self._bt_full_df = None

    def _build_full_features(self, adx_threshold: float) -> Optional[np.ndarray]:
        """Construit X (n, 48) sur TOUTE la fenêtre de backtest via le catalogue
        FeatureStore (un provider par ``adx_threshold``). ``_build_features`` exige
        les colonnes ``_pre_*`` : on les précalcule dans le builder
        (``precompute_df`` est idempotent). Retourne ``None`` hors backtest."""
        df = self._bt_full_df
        if df is None:
            return None
        from app.core.feature_store import cached_strategy_features
        from app.core.indicators import precompute_df as _pc
        key = round(float(adx_threshold), 6)
        return cached_strategy_features(
            getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
            name=f"{self.name}_adx{key}", version="1",
            builder=lambda w, t=adx_threshold: _build_features(_pc(w), t),
            in_kind="polars", out_kind="numpy")

    def _get_or_build_features(self, df: pl.DataFrame,
                               adx_threshold: float) -> Optional[np.ndarray]:
        """Retourne ``X[:len(df)]`` aligné sur ``df`` (cf. v4 pour le détail du
        correctif d'alignement : (re)build si cache absent ou plus court)."""
        if self._bt_features_len == 0 or len(df) > self._bt_features_len:
            feat_window = df.slice(max(0, len(df) - 250), min(250, len(df)))
            return _build_features(feat_window, adx_threshold)
        key = round(float(adx_threshold), 6)
        X = self._bt_features_cache.get(key)
        if X is None or len(X) < len(df):
            X = self._build_full_features(adx_threshold)
            if X is None:
                return None
            self._bt_features_cache[key] = X
        return X[: len(df)]

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("warmup_bars", 2000)) + max(_LAGS) + 20

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
            self._amp_models.clear()
            self._dir_models.clear()
            self._scalers.clear()
            self._trained_tfs.clear()
            self._best_auc_per_tf.clear()
            self._train_meta.clear()
            self._last_retrain.clear()
            self._managed_externally = False
            self._best_auc = 0.0
        self._bt_features_cache.clear()
        self._bt_features_len = 0
        self._bt_full_df = None

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        p             = (params or {}).get(self.name, {})
        adx_threshold = float(p.get("adx_threshold", 20.0))
        amp_top_pct   = float(p.get("amp_top_pct",   0.30))
        self._fit(df, adx_threshold=adx_threshold, amp_top_pct=amp_top_pct)

    def _fit(self, df: pl.DataFrame, timeframe: str = "",
             adx_threshold: float = 20.0, amp_top_pct: float = 0.30) -> None:
        tf_key = timeframe or "default"
        self._train(df, tf_key, adx_threshold, amp_top_pct)

    # ── Entraînement (identique V4) ─────────────────────────────────────────

    def _train(self, df: pl.DataFrame, tf_key: str,
               adx_threshold: float = 20.0, amp_top_pct: float = 0.30) -> bool:
        try:
            import lightgbm as lgb
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            logger.error("[V5] pip install lightgbm scikit-learn")
            return False

        if len(df) < 200:
            return False

        # Réutilise le cache backtest si présent (pré-peuplé par
        # ``prepare_for_backtest`` pour toutes les valeurs d'adx_threshold
        # du param_space en optim).
        X = self._get_or_build_features(df, adx_threshold)
        if X is None:
            return False

        close   = df["close"].cast(pl.Float64).to_numpy()
        # Aligne strictement features et labels : X peut être plus court que
        # len(close)-1 (warmup / cache de features). Sans borner n par len(X),
        # le jeu de validation X_s[split:n] et y_amp[split:n] divergent en taille
        # et LightGBM lève « Length of labels differs from the length of #data ».
        n       = min(len(X), len(close) - 1)
        if n < 150:
            return False
        X_train = X[:n]
        ret_t1  = (close[1:n + 1] - close[:n]) / np.maximum(close[:n], 1e-9)
        abs_ret = np.abs(ret_t1)

        amp_thr = np.quantile(abs_ret, 1.0 - amp_top_pct)
        y_amp   = (abs_ret >= amp_thr).astype(np.int8)
        y_dir   = (ret_t1 > 0).astype(np.int8)

        split = max(int(n * 0.8), 100)
        if split >= n - 50:
            split = n - 50

        scaler = StandardScaler()
        # Colonne de features entièrement NaN → 0/0 dans le calcul de variance de
        # StandardScaler → RuntimeWarning « invalid value encountered in divide »
        # (bénin, bruyant). Neutralisé localement, valeurs inchangées.
        with np.errstate(invalid="ignore", divide="ignore"):
            X_s    = scaler.fit_transform(X_train)

        params_lgb = {
            "objective":         "binary",
            "metric":            "auc",
            "num_leaves":        31,
            "learning_rate":     0.03,
            "min_child_samples": 20,
            "subsample":         0.8,
            "subsample_freq":    5,
            "colsample_bytree":  0.8,
            "reg_alpha":         0.1,
            "reg_lambda":        0.5,
            "verbosity":         -1,
            "n_jobs":            1,
        }
        # Fix : callbacks instanciés frais dans chaque lgb.train (early_stopping
        # est stateful, sa réutilisation entre amp et dir cassait le 2e modèle).

        # Modèle amplitude
        ds_train_amp = lgb.Dataset(X_s[:split],  label=y_amp[:split])
        ds_valid_amp = lgb.Dataset(X_s[split:n], label=y_amp[split:n], reference=ds_train_amp)
        try:
            booster_amp = lgb.train(
                {**params_lgb,
                 "scale_pos_weight": (y_amp[:split] == 0).sum() / max((y_amp[:split] == 1).sum(), 1)},
                ds_train_amp, num_boost_round=300,
                valid_sets=[ds_valid_amp],
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(-1)],
            )
        except Exception as e:
            logger.warning(f"[V5] Amp échoué : {e}")
            return False

        auc_amp = booster_amp.best_score.get("valid_0", {}).get("auc", 0.0)

        # Modèle direction
        ds_train_dir = lgb.Dataset(X_s[:split],  label=y_dir[:split])
        ds_valid_dir = lgb.Dataset(X_s[split:n], label=y_dir[split:n], reference=ds_train_dir)
        try:
            booster_dir = lgb.train(
                {**params_lgb,
                 "scale_pos_weight": (y_dir[:split] == 0).sum() / max((y_dir[:split] == 1).sum(), 1)},
                ds_train_dir, num_boost_round=300,
                valid_sets=[ds_valid_dir],
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(-1)],
            )
        except Exception as e:
            logger.warning(f"[V5] Dir échoué : {e}")
            return False

        auc_dir      = booster_dir.best_score.get("valid_0", {}).get("auc", 0.0)
        auc_combined = (auc_amp + auc_dir) / 2.0

        with self._lock:
            self._amp_models[tf_key]      = booster_amp
            self._dir_models[tf_key]      = booster_dir
            self._scalers[tf_key]         = scaler
            self._trained_tfs.add(tf_key)
            self._best_auc_per_tf[tf_key] = auc_combined
            self._best_auc                = auc_combined
            self._train_meta[tf_key] = {
                "n_train":     int(split),
                "n_valid":     int(n - split),
                "auc_amp":     round(float(auc_amp), 4),
                "auc_dir":     round(float(auc_dir), 4),
                "amp_thr_pct": round(float(amp_thr) * 100, 3),
            }

        logger.info(
            f"[V5] {tf_key} : {split} train / {n - split} val | "
            f"AUC amp={auc_amp:.3f} dir={auc_dir:.3f}"
        )
        return True

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    def save_model(self, path: str) -> None:
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        with self._lock:
            amp  = self._amp_models.get(tf_key)
            dir_ = self._dir_models.get(tf_key)
            sc   = self._scalers.get(tf_key)
            auc  = self._best_auc_per_tf.get(tf_key, 0.0)
            meta = self._train_meta.get(tf_key, {})
        if amp is None or dir_ is None:
            return
        from app.ml.backend.persistence import save_lgb_with_scaler
        if save_lgb_with_scaler(amp, dir_, sc, path, tf_key, auc, meta):
            logger.info(f"[V5] Modèles sauvegardés → {path} (AUC={auc:.3f})")

    def load_model(self, path: str) -> bool:
        tf_key = os.path.splitext(os.path.basename(path))[0].rsplit("_", 1)[-1]
        from app.ml.backend.persistence import load_lgb_with_scaler
        data = load_lgb_with_scaler(path)
        if data is None:
            return False
        try:
            with self._lock:
                for key in (tf_key, "default"):
                    self._amp_models[key]      = data["amp_model"]
                    self._dir_models[key]      = data["dir_model"]
                    self._scalers[key]         = data.get("scaler")
                    self._best_auc_per_tf[key] = float(data.get("best_auc", 0.0))
                    self._train_meta[key]      = data.get("train_meta", {})
                    self._trained_tfs.add(key)
                self._best_auc = float(data.get("best_auc", 0.0))
            logger.info(f"[V5] Modèles chargés depuis {path}")
            return True
        except Exception as e:
            logger.warning(f"[V5] Chargement échoué {path}: {e}")
            return False

    # ── score() — règle V5 : 4 régimes + trailing multi-barre ─────────────

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p              = (params or {}).get(self.name, {})
        adx_threshold  = float(p.get("adx_threshold",    20.0))
        # Trend Down (rapport §6.4)
        amp_thresh_td  = float(p.get("amp_thresh_td",    0.50))
        dir_dist_td    = float(p.get("dir_dist_td",      0.10))
        # Trend Up (ajout V5, seuils stricts)
        amp_thresh_tu  = float(p.get("amp_thresh_tu",    0.60))
        dir_dist_tu    = float(p.get("dir_dist_tu",      0.18))
        # Range / Choppy
        amp_thresh_oth = float(p.get("amp_thresh_other", 0.60))
        dir_dist_oth   = float(p.get("dir_dist_other",   0.15))
        amp_top_pct    = float(p.get("amp_top_pct",      0.30))
        warmup_bars    = int(p.get("warmup_bars",        2000))
        retrain_every  = int(p.get("retrain_every",      800))
        # Exploitation clustering (§3.1)
        max_hold_bars  = int(p.get("max_hold_bars",      8))
        trail_wide     = float(p.get("trail_wide",       1.5))
        breakeven_r    = float(p.get("breakeven_r",      0.5))
        lock_r         = float(p.get("lock_r",           1.5))
        tight_r        = float(p.get("tight_r",          2.5))
        trail_tight    = float(p.get("trail_tight",      0.5))
        grace_bars     = int(p.get("grace_bars",         2))

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        sym  = symbol or "default"
        cnt  = self._call_cnt.get(sym, 0) + 1
        self._call_cnt[sym] = cnt
        tf_key = sym

        # Walk-forward
        last       = self._last_retrain.get(tf_key, 0)
        need_train = (tf_key not in self._trained_tfs) or (cnt - last >= retrain_every)

        if need_train and not self._managed_externally:
            n_train  = min(len(df) - 1, warmup_bars * 2)
            train_df = df.slice(len(df) - n_train - 1, n_train)
            if self._train(train_df, tf_key, adx_threshold, amp_top_pct):
                self._last_retrain[tf_key] = cnt

        if tf_key not in self._trained_tfs:
            return self._none("Modèle non encore entraîné")

        # ── État courant ───────────────────────────────────────────────────
        c_now  = float(df["close"][-1] or 0.0)
        atr_v  = pre_val(df, "_pre_atr14")  or 0.0
        adx_v  = pre_val(df, "_pre_adx14")  or 0.0
        sma20  = pre_val(df, "_pre_sma20")  or c_now
        sma50  = pre_val(df, "_pre_sma50")  or c_now
        sma100 = pre_val(df, "_pre_sma100") or c_now
        sma200 = pre_val(df, "_pre_sma200") or c_now
        pdi    = pre_val(df, "_pre_pdi14")  or 0.0
        ndi    = pre_val(df, "_pre_ndi14")  or 0.0

        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime     = _detect_regime(adx_v, sma20, sma50, sma100, sma200, pdi, ndi, adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        # ── Prédictions ────────────────────────────────────────────────────
        X_full = self._get_or_build_features(df, adx_threshold)
        if X_full is None or len(X_full) == 0:
            return self._none("Features manquantes")
        X_last = X_full[-1:]

        with self._lock:
            amp_m  = self._amp_models.get(tf_key)
            dir_m  = self._dir_models.get(tf_key)
            scaler = self._scalers.get(tf_key)

        if amp_m is None or dir_m is None or scaler is None:
            return self._none("Modèle indisponible")

        try:
            X_s     = scaler.transform(X_last)
            p_event = float(amp_m.predict(X_s)[0])
            p_up    = float(dir_m.predict(X_s)[0])
        except Exception as e:
            logger.warning(f"[V5] Prédiction : {e}")
            return self._none("Erreur prédiction")

        dir_dist = abs(p_up - 0.5)

        # ── Règle V5 : 4 régimes (vs V4 qui exclut TU) ─────────────────────
        if regime == REGIME_TREND_DN:
            amp_thresh = amp_thresh_td
            dir_thresh = dir_dist_td
            size_fac   = 1.0
        elif regime == REGIME_TREND_UP:
            # AJOUT V5 — rapport §6.4 déconseille mais §3.5 documente bias haussier
            amp_thresh = amp_thresh_tu     # 0.60 (vs 0.50 TD)
            dir_thresh = dir_dist_tu       # 0.18 (vs 0.10 TD)
            size_fac   = 0.5
        else:  # Range ou Choppy
            amp_thresh = amp_thresh_oth
            dir_thresh = dir_dist_oth
            size_fac   = 0.5

        if p_event < amp_thresh:
            return self._none(
                f"P(événement)={p_event:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )
        if dir_dist < dir_thresh:
            return self._none(
                f"|P(hausse)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side = "long" if p_up > 0.5 else "short"

        # Pénalité shorts en Trend Up (rapport §3.5 : biais haussier 51-52%)
        if regime == REGIME_TREND_UP and side == "short":
            size_fac *= 0.5  # double pénalité : déjà 0.5, devient 0.25

        # Multiplicateur horaire
        hour     = _get_hour(df)
        hour_fac = _hour_multiplier(hour)
        size_fac *= hour_fac

        confidence = dir_dist * 2.0
        score      = round(min(0.55 + p_event * confidence * 0.39, 0.94), 3)

        meta    = self._train_meta.get(tf_key, {})
        auc_amp = meta.get("auc_amp", 0.0)
        auc_dir = meta.get("auc_dir", 0.0)

        # ── EXIT : trailing multi-barre + max hold ─────────────────────────
        # Le rapport §3.1 documente 50-70% de clustering : on laisse courir
        # plusieurs barres, mais avec un trailing serré qui coupe rapidement
        # quand le clustering s'arrête.
        stop_init = (c_now - 1.5 * atr_v) if side == "long" else (c_now + 1.5 * atr_v)
        trail_override = {
            "trail_wide":  trail_wide,    # 1.5×ATR initial (rapport §6.6)
            "grace_bars":  grace_bars,
            "breakeven_r": breakeven_r,   # 0.5 = breakeven dès 0.5×ATR profit
            "lock_r":      lock_r,        # 1.5 = lock à 1.5×ATR profit
            "tight_r":     tight_r,       # 2.5 = très serré à 2.5×ATR
            "trail_tight": trail_tight,   # 0.5×ATR phase finale
        }

        return {
            "score":           score,
            "side":            side,
            "name":            self.name,
            "atr":             atr_v,
            "stop_hint":       round(stop_init, 2),
            # Multi-barre : exit_after_bars = max hold (vs 1 en V4)
            "exit_after_bars": max_hold_bars,
            # Trailing stop progressif pour exploiter le clustering
            "trail_override":  trail_override,
            "p_event":         round(p_event, 4),
            "p_up":            round(p_up, 4),
            "regime":          regime,
            "regime_lbl":      regime_lbl,
            "indicators": {
                "adx":         round(adx_v, 1),
                "rsi":         round(pre_val(df, "_pre_rsi14") or 50.0, 1),
                "p_event":     round(p_event, 4),
                "p_up":        round(p_up, 4),
                "dir_dist":    round(dir_dist, 4),
                "hour":        hour,
                "hour_mult":   hour_fac,
                "size_factor": size_fac,
                "auc_amp":     auc_amp,
                "auc_dir":     auc_dir,
                "max_hold":    max_hold_bars,
            },
            "conditions": [
                f"Régime : {regime_lbl} (ADX={adx_v:.0f})",
                f"P(événement) = {p_event:.2f} ≥ seuil {amp_thresh:.2f} ✓",
                f"P(hausse) = {p_up:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
                f"Heure {hour}h UTC ×{hour_fac:.1f} | Taille {size_fac:.0%}",
                f"AUC modèle : amp={auc_amp:.2f} dir={auc_dir:.2f}",
                f"Exit : max {max_hold_bars} barres + trailing "
                f"({trail_wide:.1f}×ATR initial)",
                "Clustering exploité : §3.1 (50-70% des événements)",
            ],
            "reason": (
                f"OpusV5 {side.upper()} | {regime_lbl} | "
                f"P(event)={p_event:.2f} P(up)={p_up:.2f} | "
                f"max_hold={max_hold_bars}b"
            ),
        }

    def _none(self, reason: str = "", p_event: float = 0.0, p_up: float = 0.5,
              regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "p_event":    round(p_event, 4),
            "p_up":       round(p_up, 4),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
