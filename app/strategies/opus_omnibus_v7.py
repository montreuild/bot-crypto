"""Stratégie Opus Omnibus V7 (entraîné inline) — 6 setups sur modèles V4 entraînés.

Variante de ``opus_omnibus_v7_pretrained`` qui **entraîne son propre modèle**
au lieu de charger le pkl V4 embarqué. Conserve la logique V7 des 6 setups.

Améliorations V7 (idem ``opus_omnibus_v7_pretrained``) :
  - Nouveau setup SHORT_TD_HIGH (priorité 0, amp≥0.60, p_dir<0.30, size×1.5).
  - LONG_CHOPPY raffiné : p_dir>0.58, TP/SL serrés, max_bars=5.
  - SHORT_CHOPPY durci : p_dir<0.42, TP élargi.
  - Early exit LONG_CHOPPY assoupli : sort si p_dir<0.40 OU régime=TD.

Auto-portant : pas de dépendance vers ``opus_stat_retrained_v4`` ni vers
``opus_stat_pretrained_v4``. Chaque stratégie embarque sa propre copie du
pipeline (V4 features + LightGBM amp/dir). Cela duplique du code métier mais
permet de décommissionner indépendamment.

Optimisations mémoire :
  - Features castées en ``float32`` avant LightGBM.
  - ``max_bin=63`` + ``force_col_wise=True``.
  - ``gc.collect()`` explicite entre trainings.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import polars as pl

from app.core.indicators import (
    pre_val,
)
from app.core.indicators import (
    safe_num as _safe_num,
)
from app.engine.engine import BaseStrategyML
from app.ml.backend.features import _ewm_alpha_np as _bk_ewm_alpha_np
from app.ml.backend.features import build_features as _bk_build_features
from app.ml.backend.features import detect_timeframe as _bk_detect_timeframe
from app.ml.backend.features import impute_inplace as _bk_impute_inplace
from app.ml.backend.features import last_bar_hour_dow as _bk_last_bar_hour_dow
from app.ml.backend.features import select_feature_columns as _bk_select_feature_columns
from app.ml.backend.features import window_polars as _bk_window_polars
from app.ml.backend.mixin import MLBackendMixin

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers V4 partagés — implémentation unique dans app/ml/backend/features.py.
#  Ces fonctions étaient dupliquées à l'identique dans 4 stratégies (~300 lignes
#  chacune pour le seul ``_build_features``). Équivalence des sorties vérifiée
#  sur données réelles et cas dégénérés avant factorisation, et verrouillée par
#  tests/test_ml_helpers_shared.py (qui interdit une nouvelle copie locale).
#  Les noms préfixés restent exposés au niveau module : plusieurs consommateurs
#  historiques les importent directement depuis la stratégie.
# ─────────────────────────────────────────────────────────────────────────────
_build_features = _bk_build_features
_detect_timeframe = _bk_detect_timeframe
_window_polars = _bk_window_polars
_last_bar_hour_dow = _bk_last_bar_hour_dow
_select_feature_columns = _bk_select_feature_columns
_impute_inplace = _bk_impute_inplace
_ewm_alpha_np = _bk_ewm_alpha_np

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h", "4h", "1d")

# Codes de régime
REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3
REGIME_LABELS   = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
    -1:              "?",
}

_EXIT_TD_WINDOW_BARS = 3   # fenêtre LONG_EXIT_TD

_EXCLUDED_COLS = frozenset({
    "time", "open", "high", "low", "close", "volume",
    "log_ret", "OBV",
    "SMA_20", "SMA_50", "SMA_100", "SMA_200",
    "EMA_20", "EMA_50", "EMA_100", "EMA_200",
    "EMA_9", "EMA_21",
    "high_20", "low_20", "high_50", "low_50", "high_100", "low_100",
    "ATR_14",
})

_NUMERIC_DTYPES = (
    pl.Float32, pl.Float64,
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Routing V7 — setups + régime
# ─────────────────────────────────────────────────────────────────────────────

def _classify_regime(adx_val: float, bull: int, bear: int,
                     adx_threshold: float = 20.0) -> int:
    if adx_val < adx_threshold:
        return REGIME_RANGE
    if bull == 1:
        return REGIME_TREND_UP
    if bear == 1:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _regime_history(features_df: pl.DataFrame, n_last: int = 5,
                    adx_threshold: float = 20.0) -> List[int]:
    sub = features_df.tail(n_last)
    rows = sub.select(["ADX", "MM_bullish_align", "MM_bearish_align"]).rows()
    out: List[int] = []
    for adx_v, bull, bear in rows:
        out.append(_classify_regime(
            float(adx_v) if adx_v is not None else 0.0,
            int(bull)    if bull  is not None else 0,
            int(bear)    if bear  is not None else 0,
            adx_threshold,
        ))
    return out


def _exit_td_window_active(regimes: List[int], window_bars: int) -> bool:
    n = len(regimes)
    if n < 2:
        return False
    start = max(1, n - window_bars)
    for k in range(start, n):
        if regimes[k] != REGIME_TREND_DN and regimes[k - 1] == REGIME_TREND_DN:
            return True
    return False


_DEFAULT_SETUPS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "SHORT_TD_HIGH", "priority": 0, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "amp_min": 0.60, "dir_max": 0.30, "dir_min": None,
        "tp_mult": 1.4,  "sl_mult": 1.6,  "max_bars": 8,  "size_factor": 1.5,
    },
    {
        "name": "SHORT_TD",     "priority": 1, "direction": -1, "enabled": True,
        "regime": REGIME_TREND_DN, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": 0.40, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.6,  "max_bars": 8,  "size_factor": 1.0,
    },
    {
        "name": "LONG_CHOPPY",  "priority": 2, "direction":  1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": None, "dir_min": 0.58,
        "tp_mult": 0.9,  "sl_mult": 1.2,  "max_bars": 10,  "size_factor": 1.0,
    },
    {
        "name": "SHORT_CHOPPY", "priority": 2, "direction": -1, "enabled": True,
        "regime": REGIME_CHOPPY, "needs_exit_td_window": False,
        "amp_min": 0.50, "dir_max": 0.42, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.4,  "max_bars": 6,  "size_factor": 1.0,
    },
    {
        "name": "LONG_EXIT_TD", "priority": 3, "direction":  1, "enabled": True,
        "regime": None,  "needs_exit_td_window": True,
        "amp_min": 0.40, "dir_max": None, "dir_min": None,
        "tp_mult": 1.2,  "sl_mult": 1.5,  "max_bars": 8,  "size_factor": 1.0,
    },
    {
        "name": "LONG_RANGE_STRICT", "priority": 4, "direction":  1, "enabled": True,
        "regime": REGIME_RANGE, "needs_exit_td_window": False,
        "amp_min": 0.60, "dir_max": None, "dir_min": 0.60,
        "tp_mult": 0.8,  "sl_mult": 1.2,  "max_bars": 6,  "size_factor": 1.0,
    },
)


def _apply_setup_overrides(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    setups: List[Dict[str, Any]] = []
    for src in _DEFAULT_SETUPS:
        s = dict(src)
        prefix = f"setup_{s['name'].lower()}_"
        for field in ("priority", "direction", "amp_min", "dir_max", "dir_min",
                      "tp_mult", "sl_mult", "max_bars", "enabled", "size_factor"):
            key = prefix + field
            if key in p and p[key] is not None:
                s[field] = p[key]
        setups.append(s)
    return setups


def _evaluate_setup(setup: Dict[str, Any], regime: int, p_event: float, p_up: float,
                    exit_td_active: bool) -> bool:
    if not setup.get("enabled", True):
        return False
    if setup["regime"] is not None and regime != setup["regime"]:
        return False
    if setup["needs_exit_td_window"]:
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
    return True


def _select_setup(setups: List[Dict[str, Any]], regime: int, p_event: float,
                  p_up: float, exit_td_active: bool) -> Optional[Dict[str, Any]]:
    cands = [s for s in setups
             if _evaluate_setup(s, regime, p_event, p_up, exit_td_active)]
    if not cands:
        return None
    return min(cands, key=lambda s: s["priority"])


def _check_early_exit(setup_name: str, regime: int, p_up: float,
                      dir_inv_short: float = 0.55,
                      dir_inv_long: float = 0.40,
                      dir_drop_range: float = 0.40) -> Optional[str]:
    if setup_name in ("SHORT_TD_HIGH", "SHORT_TD"):
        if regime != REGIME_TREND_DN:
            return "regime_exit_TD"
        if p_up > dir_inv_short:
            return "p_dir_inversion"
    elif setup_name == "SHORT_CHOPPY":
        if regime != REGIME_CHOPPY:
            return "regime_exit_choppy"
        if p_up > 0.58:
            return "p_dir_inversion"
    elif setup_name == "LONG_CHOPPY":
        if p_up < dir_inv_long:
            return "p_dir_drop"
        if regime == REGIME_TREND_DN:
            return "to_TD"
    elif setup_name == "LONG_EXIT_TD":
        if regime == REGIME_TREND_DN:
            return "back_to_TD"
    elif setup_name == "LONG_RANGE_STRICT":
        if regime == REGIME_TREND_DN:
            return "regime_to_TD"
        if p_up < dir_drop_range:
            return "p_dir_drop"
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Strategy
# ─────────────────────────────────────────────────────────────────────────────

class Strategy(MLBackendMixin, BaseStrategyML):
    """OMNIBUS V7 — 6 setups avec routing par priorité, sur modèles V4
    entraînés inline (mêmes paramètres LightGBM que ``opus_stat_retrained_v4``,
    mais code dupliqué pour rester autonome)."""

    name      = "opus_omnibus_v7"
    # Recette(s) consommée(s) — surchargeable par le bloc `models:`
    # du YAML (cf. app.ml.recipe.strategy_models).
    models: Dict[str, str] = {"signal": "omnibus_v4_single"}
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        # SHORT_TD_HIGH
        "setup_short_td_high_amp_min":    [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":    [0.25, 0.30, 0.35],
        # SHORT_TD
        "setup_short_td_amp_min":         [0.45, 0.50, 0.55],
        "setup_short_td_dir_max":         [0.35, 0.40, 0.45],
        "setup_short_td_tp_mult":         [1.0, 1.2, 1.4],
        "setup_short_td_sl_mult":         [1.4, 1.6, 1.8],
        # SHORT_CHOPPY
        "setup_short_choppy_amp_min":     [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":     [0.38, 0.42, 0.46],
        # LONG_CHOPPY
        "setup_long_choppy_amp_min":      [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":      [0.55, 0.58, 0.62],
        # LONG_EXIT_TD
        "setup_long_exit_td_amp_min":     [0.35, 0.40, 0.45],
        "setup_long_exit_td_max_bars":    [4, 6, 8, 10],
        # LONG_RANGE_STRICT
        "setup_long_range_strict_amp_min":[0.55, 0.60, 0.65],
        "setup_long_range_strict_dir_min":[0.55, 0.60, 0.65],
        "exit_td_window_bars":            [2, 3, 4],
    }
    # Hyperparamètres d'entraînement figés (hors espace de recherche) : les
    # échantillonner invalide le cache d'entraînement process-wide entre les
    # trials de l'optimiseur — chaque trial repaye alors l'intégralité des
    # retrains LightGBM walk-forward (rédhibitoire sur 50k bougies). Valeurs
    # effectives : _DEFAULTS ; surchargables via le YAML stratégie.
    fixed_params: Dict[str, Any] = {
        "amp_top_pct":     0.30,
        "warmup_bars":     2000,
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
        # Entraînement
        "amp_top_pct":         0.30,
        "warmup_bars":         2000,
        "retrain_every":       800,
        "n_estimators":        500,
        "num_leaves":          31,
        "learning_rate":       0.03,
    }

    retrain_interval_h: int = 6

    # Recette omnibus_v4_single : t+1, sans calibration ni élagage.
    ml_calibrate = False
    ml_prune_features = False
    ml_multi_horizon = False

    def __init__(self):
        # Tout l'état ML vit dans le backend — cf. MLBackendMixin.
        MLBackendMixin.__init__(self)
        self._cancel_event = None

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        warmup = int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))
        return max(230, warmup + 30)

    # Entraînement avec cache process-wide (cf. app/core/train_cache.py) :
    # les retrains identiques (même fenêtre, mêmes hyperparams d'entraînement)
    # sont réutilisés entre les trials de l'optimiseur au lieu d'être relancés.
    _TRAIN_STATE_ATTRS = ('_amp_models', '_dir_models', '_feature_cols', '_medians', '_best_auc_per_tf', '_train_meta')
    _TRAIN_PARAM_KEYS  = ('amp_top_pct', 'n_estimators', 'num_leaves', 'learning_rate')

    # ── Sortie anticipée V7 ────────────────────────────────────────────────
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
        dir_inv_short  = float(p.get("early_exit_dir_inv_short",  0.55))
        dir_inv_long   = float(p.get("early_exit_dir_inv_long",   0.45))
        dir_drop_range = float(p.get("early_exit_dir_drop_range", 0.40))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS or tf not in self._trained_tfs:
            return None

        try:
            # Fast-path backtest : features pré-calculées.
            if self._bt_features is not None and len(df) <= self._bt_features_len:
                features = self._bt_features.head(len(df))
            else:
                features = _build_features(
                    _window_polars(df, n=max(260, self.min_bars_required(params)))
                )
            if features is None or len(features) == 0:
                return None
            last_row = features.row(-1, named=True)
            regime = _classify_regime(
                _safe_num(last_row.get("ADX"), 0.0),
                int(_safe_num(last_row.get("MM_bullish_align"), 0.0)),
                int(_safe_num(last_row.get("MM_bearish_align"), 0.0)),
                adx_threshold,
            )
            p_up = self.predict_direction(features, tf)
            if p_up is None:
                return None
        except Exception as e:
            logger.warning(f"[OmnibusV7-RT] check_early_exit recompute KO : {e}")
            return None

        return _check_early_exit(
            setup_name, regime, p_up,
            dir_inv_short=dir_inv_short,
            dir_inv_long=dir_inv_long,
            dir_drop_range=dir_drop_range,
        )

    # ── Score V7 ───────────────────────────────────────────────────────────
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

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        if enable_hour_filter:
            hour, dow = _last_bar_hour_dow(df)
            if hour is not None and dow is not None:
                if dow not in active_days:
                    return self._none(
                        f"Hors jours actifs (weekday={dow}, autorisés={active_days})"
                    )
                if hour not in active_hours_utc:
                    return self._none(
                        f"Hors session ({hour}h UTC, autorisées={active_hours_utc})"
                    )

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(
                f"Timeframe non supporté (détecté={tf}, attendus={_SUPPORTED_TFS})"
            )

        cnt = self._call_cnt.get(tf, 0) + 1
        self._call_cnt[tf] = cnt
        last       = self._last_retrain.get(tf, 0)
        need_train = (tf not in self._trained_tfs) or (cnt - last >= retrain_every)
        if need_train and not self._managed_externally:
            from app.core.train_cache import aligned_train_window
            n_train = min(len(df) - 1, warmup_bars * 2)
            train_df, self._bt_train_offset = aligned_train_window(
                df, retrain_every, n_train)
            ok = self._train(train_df, tf, p)
            self._bt_train_offset = None
            if ok:
                self._last_retrain[tf] = cnt

        if tf not in self._trained_tfs:
            return self._none("Modèle pas encore entraîné (warmup en cours)")

        # Fast-path backtest : features pré-calculées une fois.
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            features = self._bt_features.head(len(df))
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

        regime_history = _regime_history(
            features, n_last=max(exit_td_window_bars + 2, 5),
            adx_threshold=adx_threshold,
        )
        regime         = regime_history[-1]
        regime_lbl     = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regime_history, exit_td_window_bars)

        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")

        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up, exit_td_active)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} exit_td={exit_td_active}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side        = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = (5 - int(setup["priority"])) * 0.04
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        meta = self._train_meta.get(tf, {})

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
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":              round(_safe_num(last_row.get("ADX"), 0.0), 1),
            "rsi":              round(_safe_num(last_row.get("RSI_14"), 50.0), 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "exit_td_active":   bool(exit_td_active),
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "max_bars":         max_bars,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
            "n_features":       meta.get("n_features", 0),
        }
        sig["conditions"] = [
            f"Setup V7 retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime : {regime_lbl} | exit_td_window={exit_td_active}",
            f"P(événement)={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse)={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse)={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse)={p_up:.2f} (pas de seuil dir)"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | "
            f"max {max_bars} bougies",
            f"Modèle V4 entraîné inline / {tf} ({meta.get('n_features', 0)} features, "
            f"AUC amp={meta.get('auc_amp', 0):.2f} dir={meta.get('auc_dir', 0):.2f})",
        ]
        sig["reason"] = (
            f"OmnibusV7-RT {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f}"
        )
        return sig

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
