"""Stratégie Opus Omnibus V11 — V10 autonome + 4 améliorations ML.

V11 part du routing V10 (mêmes 8 setups, SIGNAL_UP dynamique, excès baissier) et
entraîne ses propres modèles LightGBM inline, mais introduit quatre améliorations
ciblant la qualité du signal et son analysabilité :

1. **Labellisation multi-horizon** — l'amplitude/direction ne sont plus calculées
   sur le seul rendement ``t+1`` mais agrégées sur plusieurs horizons
   (``label_horizons``, défaut [1, 3, 6]).

2. **Détection de régime enrichie** — la classification 4-régimes est conservée
   mais affinée : Choppy à forte directionnalité requalifié en Trend, Range
   sous-typé (squeeze / ouvert).

3. **Importance des features régulière** — après chaque entraînement, le gain
   LightGBM par feature est extrait, et (optionnel) les features à gain nul
   sont retirées du prochain cycle (``prune_features``).

4. **Calibration des probabilités** — les sorties brutes LightGBM sont
   recalibrées par régression isotone ajustée sur le set de validation.

Réfactoring : toute la mécanique ML (features V4, entraînement LightGBM,
inférence, persistance) est désormais déléguée à ``app.ml.backend.MLBackend``.
Cette stratégie ne porte plus que le routing V10 (setups, sélection,
early_exit, signal_up_dynamic_risk).
"""

import datetime as _dt
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import pre_val
from app.core.indicators import safe_num as _safe_num
from app.engine.engine import BaseStrategyML
from app.ml.backend import (
    FEATURES_CATALOG_NAME,
    FEATURES_CATALOG_VERSION,
    MLBackend,
    REGIME_CHOPPY,
    REGIME_LABELS,
    REGIME_RANGE,
    REGIME_TREND_DN,
    REGIME_TREND_UP,
    SUPPORTED_TFS,
    build_features as _build_features,
    classify_regime as _classify_regime_v11,
    detect_timeframe as _detect_timeframe,
    exit_td_window_active as _exit_td_window_active,
    last_bar_hour_dow as _last_bar_hour_dow,
    regime_history as _regime_history_v11,
    window_polars as _window_polars,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Alias publics (compatibilité : scanner_service et autres consommateurs
#  historiques importent ces symboles depuis opus_omnibus_v11).
# ─────────────────────────────────────────────────────────────────────────────
_SUPPORTED_TFS = SUPPORTED_TFS

_EXIT_TD_WINDOW_BARS = 3


# ── Setups OMNIBUS V10 ────────────────────────────────────────────────────────
_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "SIGNAL_UP", "priority": -1, "direction": 1, "enabled": True,
        "regime": None, "needs_exit_td_window": False, "needs_bearish_excess": True,
        "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 1.0, "sl_mult": 1.3, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
        "tp_mult": 1.4, "sl_mult": 1.6, "max_bars": 8, "size_factor": 1.5,
    },
    {
        "name": "LONG_CHOPPY", "priority": 2, "direction": 1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
        "tp_mult": 0.9, "sl_mult": 1.2, "max_bars": 10, "size_factor": 1.0,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.4, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "LONG_TU", "priority": 3, "direction": 1, "enabled": True,
        "regime": REGIME_TREND_UP, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": 25.0,
        "amp_min": 0.55, "dir_max": None, "dir_min": 0.62,
        "tp_mult": 1.4, "sl_mult": 1.1, "max_bars": 10, "size_factor": 1.0,
    },
    {
        "name": "LONG_EXIT_TD", "priority": 4, "direction": 1, "enabled": True,
        "regime": None, "needs_exit_td_window": True,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.40, "dir_max": None, "dir_min": None,
        "tp_mult": 1.2, "sl_mult": 1.5, "max_bars": 8, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 5, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8, "sl_mult": 1.2, "max_bars": 6, "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_LIGHT", "priority": 6, "direction": 1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "needs_bearish_excess": False, "needs_rsi_below": None, "needs_adx_above": None,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.55,
        "tp_mult": 0.7, "sl_mult": 1.0, "max_bars": 4, "size_factor": 0.6,
    },
)


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    setups: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "tp_mult", "sl_mult", "max_bars", "enabled", "size_factor",
                      "needs_bearish_excess", "needs_rsi_below", "needs_adx_above"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def _evaluate_setup(setup: Dict[str, Any],
                    regime: int, p_event: float, p_up: float,
                    exit_td_active: bool,
                    bearish_excess: bool = False,
                    rsi: float = 50.0,
                    adx: float = 0.0) -> bool:
    if not setup.get("enabled", True):
        return False
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    if setup.get("needs_exit_td_window", False):
        if not exit_td_active:
            return False
        if regime == REGIME_TREND_DN:
            return False
    if p_event < float(setup["amp_min"]):
        return False
    if setup["dir_max"] is not None and p_up >= float(setup["dir_max"]):
        return False
    if setup["dir_min"] is not None and p_up <= float(setup["dir_min"]):
        return False
    if setup.get("needs_bearish_excess", False) and not bearish_excess:
        return False
    if setup.get("needs_rsi_below") is not None and rsi >= float(setup["needs_rsi_below"]):
        return False
    if setup.get("needs_adx_above") is not None and adx < float(setup["needs_adx_above"]):
        return False
    return True


def _select_setup(setups: List[Dict[str, Any]],
                  regime: int, p_event: float, p_up: float,
                  exit_td_active: bool,
                  bearish_excess: bool = False,
                  rsi: float = 50.0,
                  adx: float = 0.0) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, exit_td_active,
                                bearish_excess, rsi, adx)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


def _check_early_exit(setup_name: str, regime: int, p_up: float,
                      dir_inv_short: float = 0.55,
                      dir_inv_long: float = 0.42,
                      dir_drop_range: float = 0.40) -> Optional[str]:
    if setup_name == "SIGNAL_UP":
        if p_up < dir_inv_long:
            return "p_dir_drop"
        if regime == REGIME_TREND_DN:
            return "to_TD"
    elif setup_name == "SHORT_TD_HIGH":
        if regime != REGIME_TREND_DN:
            return "regime_exit_TD"
        if p_up > dir_inv_short:
            return "p_dir_inversion"
    elif setup_name == "LONG_CHOPPY":
        if p_up < dir_inv_long:
            return "p_dir_drop"
        if regime == REGIME_TREND_DN:
            return "to_TD"
    elif setup_name == "SHORT_CHOPPY":
        if regime != REGIME_CHOPPY:
            return "regime_exit_choppy"
        if p_up > 0.58:
            return "p_dir_inversion"
    elif setup_name == "LONG_TU":
        if regime == REGIME_TREND_DN:
            return "to_TD"
        if p_up < dir_inv_long:
            return "p_dir_drop"
    elif setup_name == "LONG_EXIT_TD":
        if regime == REGIME_TREND_DN:
            return "back_to_TD"
    elif setup_name in ("LONG_RANGE_STRICT", "LONG_RANGE_LIGHT"):
        if regime == REGIME_TREND_DN:
            return "regime_to_TD"
        if p_up < dir_drop_range:
            return "p_dir_drop"
    return None


def _signal_up_dynamic_risk(regime: int) -> Tuple[float, float]:
    """size_factor et sl_mult adaptés au régime pour SIGNAL_UP (mean-reversion)."""
    if regime == REGIME_CHOPPY or regime == REGIME_RANGE:
        return 1.5, 1.3
    if regime == REGIME_TREND_DN:
        return 1.2, 1.1
    if regime == REGIME_TREND_UP:
        return 1.0, 1.0
    return 1.0, 1.3


logger = logging.getLogger(__name__)

_TRAIN_LOG_PATH = os.path.join("logs", "opus_omnibus_v11_train.jsonl")


# ─────────────────────────────────────────────────────────────────────────────
class Strategy(BaseStrategyML):
    """OMNIBUS V11 — routing V10 + multi-horizon, régime enrichi, importance,
    calibration. Modèles LightGBM entraînés inline via MLBackend."""

    name      = "opus_omnibus_v11"
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        # ── Setups V10 ──
        "setup_signal_up_amp_min":          [0.45, 0.50, 0.55],
        "setup_signal_up_dir_min":          [0.55, 0.60, 0.65],
        "setup_short_td_high_amp_min":      [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":      [0.25, 0.30, 0.35],
        "setup_long_choppy_amp_min":        [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":        [0.55, 0.58, 0.62],
        "setup_short_choppy_amp_min":       [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":       [0.38, 0.42, 0.46],
        "setup_long_tu_amp_min":            [0.50, 0.55, 0.60],
        "setup_long_tu_dir_min":            [0.58, 0.62, 0.66],
        "setup_long_range_strict_amp_min":  [0.55, 0.60, 0.65],
        "setup_long_range_light_amp_min":   [0.45, 0.50, 0.55],
        "exit_td_window_bars":              [2, 3, 4],
        # ── V11 ML (décision uniquement) ──
        "di_rescue":       [8.0, 10.0, 14.0],
    }
    # Hyperparamètres d'entraînement figés (hors espace de recherche).
    fixed_params: Dict[str, Any] = {
        "label_horizons":  [1, 3, 6],
        "calibrate":       True,
        "prune_features":  True,
        "amp_top_pct":     0.30,
        "warmup_bars":     750,
        "retrain_every":   800,
        "n_estimators":    500,
        "num_leaves":      31,
        "learning_rate":   0.03,
    }

    _DEFAULTS = {
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),
        "active_days":         [0, 1, 2, 3, 4],
        "adx_threshold":       20.0,
        "exit_td_window_bars": _EXIT_TD_WINDOW_BARS,
        "disable_trailing":    True,
        "use_fixed_tp":        True,
        "bearish_excess_rsi_threshold": 38.0,
        "bearish_excess_sma_pct":        1.5,
        "signal_up_dynamic_risk":        True,
        # V11 ML
        "label_horizons":   [1, 3, 6],
        "calibrate":        True,
        "prune_features":   True,
        "di_rescue":        10.0,
        "log_training":     True,
        "importance_top_n": 15,
        # Entraînement
        "amp_top_pct":      0.30,
        "warmup_bars":      750,
        "retrain_every":    800,
        "n_estimators":     500,
        "num_leaves":       31,
        "learning_rate":    0.03,
    }

    retrain_interval_h: int = 6

    def __init__(self):
        # Composition : tout le ML est délégué au backend générique.
        self.ml = MLBackend(
            name=self.name,
            model_dir=self.model_dir,
            calibrate=True,
            prune_features=True,
            multi_horizon=True,
        )

    # ── Compatibilité cached_train (les attributs état doivent être mutables
    #    sur la Strategy pour que train_cache puisse snapshot/restaurer).
    #    On délègue à self.ml.state via propriétés.
    _TRAIN_STATE_ATTRS = MLBackend._TRAIN_STATE_ATTRS
    _TRAIN_PARAM_KEYS  = MLBackend._TRAIN_PARAM_KEYS

    @property
    def _amp_models(self):       return self.ml.state.amp_models
    @property
    def _dir_models(self):       return self.ml.state.dir_models
    @property
    def _amp_cal(self):          return self.ml.state.amp_cal
    @property
    def _dir_cal(self):          return self.ml.state.dir_cal
    @property
    def _feature_cols(self):     return self.ml.state.feature_cols
    @property
    def _kept_features(self):    return self.ml.state.kept_features
    @property
    def _medians(self):          return self.ml.state.medians
    @property
    def _trained_tfs(self):      return self.ml.state.trained_tfs
    @property
    def _best_auc_per_tf(self):  return self.ml.state.best_auc_per_tf
    @property
    def _train_meta(self):       return self.ml.state.train_meta
    @property
    def _last_retrain(self):     return self.ml.state.last_retrain
    @property
    def _call_cnt(self):         return self.ml.state.call_cnt
    @property
    def _best_auc(self):         return self.ml.state.best_auc
    @_best_auc.setter
    def _best_auc(self, v):      self.ml.state.best_auc = float(v)
    @property
    def _managed_externally(self): return self.ml.state.managed_externally
    @_managed_externally.setter
    def _managed_externally(self, v): self.ml.state.managed_externally = bool(v)

    # Cache backtest (délègue au backend)
    @property
    def _bt_features(self):      return self.ml._bt_features
    @property
    def _bt_features_len(self):  return self.ml._bt_features_len
    @property
    def _bt_train_offset(self):  return self.ml._bt_train_offset
    @_bt_train_offset.setter
    def _bt_train_offset(self, v): self.ml._bt_train_offset = v

    @property
    def _lock(self): return self.ml._lock

    # ── Cycle de vie ML (délègue au backend) ───────────────────────────────
    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        self.ml.prepare_for_backtest(df, getattr(self, "_bt_symbol", None),
                                     getattr(self, "_bt_tf", None))

    @property
    def is_trained(self) -> bool:
        return self.ml.is_trained

    @property
    def managed_externally(self) -> bool:
        return self.ml.managed_externally

    @managed_externally.setter
    def managed_externally(self, v: bool) -> None:
        self.ml.managed_externally = v

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        warmup = int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))
        return max(230, warmup + 30)

    def reset_model(self) -> None:
        self.ml.reset_model()

    # ── Persistance (délègue au backend) ───────────────────────────────────
    def save_model(self, path: str) -> None:
        self.ml.save_model(path)
        # Log additionnel (compat logging V11).
        try:
            tf = self.ml._tf_from_path(path)
            auc = self.ml.best_auc_per_tf.get(tf, 0.0)
            logger.info(f"[OmnibusV11] Modèles sauvegardés → {path} (AUC={auc:.3f})")
        except Exception:
            pass

    def load_model(self, path: str) -> bool:
        ok = self.ml.load_model(path)
        if ok:
            tf = self.ml._tf_from_path(path)
            logger.info(f"[OmnibusV11] Modèle {tf} chargé depuis {path}")
        return ok

    def fit(self, df: pl.DataFrame, params: dict = None) -> None:
        # Préserve le chemin de log JSONL spécifique V11.
        p = (params or {}).get(self.name, {})
        if p.get("log_training", self._DEFAULTS["log_training"]):
            self._install_train_log_hook()
        self.ml.fit(df, params, defaults=self._DEFAULTS, strategy=self)

    _train_log_hook_installed: bool = False

    def _install_train_log_hook(self) -> None:
        """Hook one-shot pour écrire logs/opus_omnibus_v11_train.jsonl après train."""
        if self._train_log_hook_installed:
            return
        self._train_log_hook_installed = True
        # Le hook est appelé manuellement depuis _train_impl ci-dessous.

    # ── Entraînement (délègue au backend, préserve cached_train) ───────────
    def _train(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        from app.core.train_cache import cached_train
        ok = cached_train(self, df, tf_key, params, self._train_impl,
                          self._TRAIN_STATE_ATTRS, self._TRAIN_PARAM_KEYS)
        if ok and params.get("log_training", self._DEFAULTS["log_training"]):
            self._append_train_log(tf_key, self.ml.train_meta.get(tf_key, {}))
        return ok

    def _train_impl(self, df: pl.DataFrame, tf_key: str, params: dict) -> bool:
        # Délègue au backend (sans cached_train ici — déjà géré par _train).
        return self.ml._train_impl_wrapper(df, tf_key, params)

    def _append_train_log(self, tf_key: str, meta: dict) -> None:
        try:
            os.makedirs(os.path.dirname(_TRAIN_LOG_PATH) or ".", exist_ok=True)
            record = {"ts": _dt.datetime.utcnow().isoformat(), "strategy": self.name,
                      "tf": tf_key, **meta}
            with open(_TRAIN_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[OmnibusV11] log entraînement KO : {e}")

    # ── Prédictions (délègue au backend) ───────────────────────────────────
    def _predict(self, features_df: pl.DataFrame, tf: str, target: str) -> Optional[float]:
        return self.ml.predict_single(features_df, tf, target)

    def predict_amplitude(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self.ml.predict_amplitude(features_df, tf)

    def predict_direction(self, features_df: pl.DataFrame, tf: str) -> Optional[float]:
        return self.ml.predict_direction(features_df, tf)

    def _predict_series(self, features_df: pl.DataFrame, tf: str,
                        target: str) -> Optional[np.ndarray]:
        return self.ml.predict_series(features_df, tf, target)

    def predict(self, df: pl.DataFrame, params: dict = None) -> Dict[str, Any]:
        return self.score(df, params)

    # ── Score V11 (routing conservé) ───────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        enable_hour_filter  = bool(p.get("enable_hour_filter",  self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",    self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",         self._DEFAULTS["active_days"]))
        adx_threshold       = float(p.get("adx_threshold",      self._DEFAULTS["adx_threshold"]))
        exit_td_window_bars = int(p.get("exit_td_window_bars",  self._DEFAULTS["exit_td_window_bars"]))
        disable_trailing    = bool(p.get("disable_trailing",    self._DEFAULTS["disable_trailing"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",        self._DEFAULTS["use_fixed_tp"]))
        signal_up_dyn       = bool(p.get("signal_up_dynamic_risk", self._DEFAULTS["signal_up_dynamic_risk"]))
        di_rescue           = float(p.get("di_rescue",          self._DEFAULTS["di_rescue"]))

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        if enable_hour_filter:
            hour, dow = _last_bar_hour_dow(df)
            if hour is not None and dow is not None:
                if dow not in active_days:
                    return self._none(f"Hors jours actifs (weekday={dow})")
                if hour not in active_hours_utc:
                    return self._none(f"Hors session ({hour}h UTC)")

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(f"Timeframe non supporté (détecté={tf})")

        ml_state = self.ml.state
        cnt = ml_state.call_cnt.get(tf, 0) + 1
        ml_state.call_cnt[tf] = cnt
        last       = ml_state.last_retrain.get(tf, 0)
        need_train = (tf not in ml_state.trained_tfs) or (cnt - last >= retrain_every)
        if need_train and not ml_state.managed_externally:
            from app.core.train_cache import aligned_train_window
            n_train = min(len(df) - 1, warmup_bars * 2)
            train_df, self.ml._bt_train_offset = aligned_train_window(
                df, retrain_every, n_train)
            ok = self._train(train_df, tf, p)
            self.ml._bt_train_offset = None
            if ok:
                ml_state.last_retrain[tf] = cnt

        if tf not in ml_state.trained_tfs:
            return self._none("Modèle pas encore entraîné (warmup en cours)")

        bt_feats = self.ml._bt_features
        bt_len   = self.ml._bt_features_len
        if bt_feats is not None and len(df) <= bt_len:
            features = bt_feats.head(len(df))
        else:
            features = _build_features(_window_polars(df, n=max(260, self.min_bars_required(params))))
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v = _safe_num(last_row.get("ATR_14"), 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        # Régime enrichi V11.
        regimes, subs = _regime_history_v11(
            features, n_last=max(exit_td_window_bars + 2, 5),
            adx_threshold=adx_threshold, di_rescue=di_rescue,
        )
        regime     = regimes[-1]
        regime_sub = subs[-1]
        regime_lbl = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regimes, exit_td_window_bars)

        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")

        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma_pct = float(p.get("bearish_excess_sma_pct", 1.5))
        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False
        rsi_v = _safe_num(last_row.get("RSI_14"), 50.0)
        adx_v = _safe_num(last_row.get("ADX"), 0.0)
        rsi_excess = rsi_v < be_rsi_thr
        sma20_v = _safe_num(last_row.get("SMA_20"), 0.0)
        price_below_sma20 = (c_now < sma20_v * (1.0 - be_sma_pct / 100.0)) if sma20_v > 0 else False
        bearish_excess = consec_red or rsi_excess or price_below_sma20

        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up, exit_td_active,
                               bearish_excess, rsi_v, adx_v)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl}({regime_sub}) "
                f"p_event={p_event:.2f} p_up={p_up:.2f} rsi={rsi_v:.1f} adx={adx_v:.1f}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        signal_up_dyn_applied = False
        if setup["name"] == "SIGNAL_UP" and signal_up_dyn:
            setup = dict(setup)
            size_mult, sl_override = _signal_up_dynamic_risk(regime)
            setup["size_factor"] = float(setup.get("size_factor", 1.0)) * size_mult
            setup["sl_mult"]     = sl_override
            signal_up_dyn_applied = True

        side        = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self.ml.train_meta.get(tf, {})
        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "disable_trailing": disable_trailing,
            "size_factor":      size_factor,
            "exit_after_bars":  max_bars,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "regime_sub":       regime_sub,
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "bearish_excess":   bool(bearish_excess),
            "signal_up_dyn":    bool(signal_up_dyn_applied),
            "rsi":              round(rsi_v, 1),
            "adx":              round(adx_v, 1),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":            round(adx_v, 1),
            "rsi":            round(rsi_v, 1),
            "p_event":        round(p_event, 4),
            "p_up":           round(p_up, 4),
            "regime":         regime,
            "regime_lbl":     regime_lbl,
            "regime_sub":     regime_sub,
            "setup":          setup["name"],
            "setup_priority": int(setup["priority"]),
            "sl_mult":        sl_atr_mult,
            "tp_mult":        tp_atr_mult if use_fixed_tp else None,
            "max_bars":       max_bars,
            "auc_amp":        meta.get("auc_amp", 0.0),
            "auc_dir":        meta.get("auc_dir", 0.0),
            "calibrated":     meta.get("calibrated", False),
            "horizons":       meta.get("horizons"),
            "n_features":     meta.get("n_features", 0),
        }
        sig["conditions"] = [
            f"Setup V11 retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime enrichi : {regime_lbl} / {regime_sub} | ADX={adx_v:.1f} | exit_td={exit_td_active}",
            f"P(événement)={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓"
            + (" (calibrée)" if meta.get("calibrated") else ""),
            (f"P(hausse)={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse)={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse)={p_up:.2f}"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | max {max_bars} bougies",
            f"Modèle multi-horizon {meta.get('horizons')} / {tf} "
            f"({meta.get('n_features', 0)} feats, AUC amp={meta.get('auc_amp', 0):.2f} "
            f"dir={meta.get('auc_dir', 0):.2f})",
        ]
        if signal_up_dyn_applied:
            sig["conditions"].append(
                f"SIGNAL_UP dynamique : size×{size_factor:.2f} SL {sl_atr_mult:.2f}×ATR"
            )
        sig["reason"] = (
            f"OmnibusV11 {setup['name']} {side.upper()} | {regime_lbl}/{regime_sub} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}"
        )
        return sig

    # ── Sortie anticipée (routing V10) ────────────────────────────────────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        setup_name = position.get("setup")
        if not setup_name:
            ind = position.get("indicators") or {}
            setup_name = ind.get("setup")
        if not setup_name:
            return None
        if df is None or len(df) < self.min_bars_required(params):
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold  = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        di_rescue      = float(p.get("di_rescue",     self._DEFAULTS["di_rescue"]))
        dir_inv_short  = float(p.get("early_exit_dir_inv_short",  0.55))
        dir_inv_long   = float(p.get("early_exit_dir_inv_long",   0.42))
        dir_drop_range = float(p.get("early_exit_dir_drop_range", 0.40))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS or tf not in self.ml.state.trained_tfs:
            return None

        try:
            bt_feats = self.ml._bt_features
            bt_len   = self.ml._bt_features_len
            if bt_feats is not None and len(df) <= bt_len:
                features = bt_feats.head(len(df))
            else:
                features = _build_features(
                    _window_polars(df, n=max(260, self.min_bars_required(params)))
                )
            if features is None or len(features) == 0:
                return None
            regimes, _ = _regime_history_v11(
                features, n_last=2, adx_threshold=adx_threshold, di_rescue=di_rescue,
            )
            regime = regimes[-1]
            p_up = self.predict_direction(features, tf)
            if p_up is None:
                return None
        except Exception as e:
            logger.warning(f"[OmnibusV11] check_early_exit recompute KO : {e}")
            return None

        return _check_early_exit(
            setup_name, regime, p_up,
            dir_inv_short=dir_inv_short,
            dir_inv_long=dir_inv_long,
            dir_drop_range=dir_drop_range,
        )

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
