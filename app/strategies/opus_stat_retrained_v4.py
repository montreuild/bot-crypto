"""Stratégie Opus Stat Retrained V4 — pipeline V4 entraîné inline (polars/numpy).

Variante de ``opus_stat_pretrained_v4`` qui **entraîne son propre modèle** au
lieu de charger le pkl embarqué. Méthodologie V4 :

  1. Features V4 (~100 indicateurs × lags 1/3/6/12) construites par
     ``app.ml.backend.features.build_features`` (polars/numpy).
  2. Labellisation amplitude (``|ret_t+1| > quantile``) + direction (``ret > 0``).
  3. Split chronologique 80/20.
  4. Deux LightGBM (amp + dir) entraînés en mode ``binary`` avec early-stopping.
  5. Imputation des NaN par les médianes du **train**.
  6. Persistance par TF via ``save_model`` / ``load_model``.
  7. Walk-forward : réentraînement périodique inline.

Optimisations mémoire :
  - Features castées en ``float32`` avant passage à LightGBM (~2× moins de RAM).
  - LightGBM ``max_bin=63`` + ``force_col_wise=True`` (~4× moins d'histogrammes).
  - ``gc.collect()`` explicite entre trainings pour éviter l'accumulation des
    boosters précédents en backtest (cause typique du ``bad allocation``).

Helpers V4 partagés : le pipeline de features (``_build_features`` et ses
satellites) vivait en copie locale ici — ~390 lignes identiques à celles de
trois stratégies soeurres ET à ``app.ml.backend.features``, l'implémentation
de référence. Les copies sont désormais des alias du backend (équivalence des
sorties vérifiée sur données réelles avant factorisation, verrouillée par
``tests/test_ml_helpers_shared.py``). Le ROUTING (setups, seuils, gestion du
risque) reste entièrement local : c'est lui qui différencie la stratégie.
"""

import logging
from typing import Any, Dict, List

import numpy as np
import polars as pl

from app.core.indicators import (
    pre_val,
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

# Codes / labels de régime (alignés sur ``app.engine.risk`` V4)
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

# Multiplicateur de taille par heure UTC — dérivé du lift empirique horaire
# mesuré sur ~50k bougies 15m (heatmap jour×heure de l'analyse V4 recouvrée) :
# mult(h) = lift(h) / lift_max(=2.43 à 14h), plancher 0.2. Cf.
# opus_stat_pretrained_v4.py (même constante, dupliquée — stratégies
# auto-portantes par convention de ce module, cf. docstring).
_HOUR_LIFT_15M = {
    0: 0.44, 1: 1.09, 2: 0.66, 3: 0.62, 4: 0.47, 5: 0.54, 6: 0.64, 7: 0.65,
    8: 0.82, 9: 0.90, 10: 0.91, 11: 0.89, 12: 0.87, 13: 1.49, 14: 2.43,
    15: 2.28, 16: 1.80, 17: 1.62, 18: 1.30, 19: 1.10, 20: 0.94, 21: 0.79,
    22: 0.67, 23: 0.56,
}
_HOUR_SIZE_MULT_FLOOR = 0.20
_HOUR_LIFT_MAX = max(_HOUR_LIFT_15M.values())
_HOUR_SIZE_MULT = {
    h: max(_HOUR_SIZE_MULT_FLOOR, lift / _HOUR_LIFT_MAX)
    for h, lift in _HOUR_LIFT_15M.items()
}

# Colonnes exclues du jeu de features (raw OHLCV + MM brutes non-stationnaires)
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


class Strategy(MLBackendMixin, BaseStrategyML):
    """Stratégie ML — pipeline V4 entraîné inline (modèles persistés par TF)."""

    name      = "opus_stat_retrained_v4"
    # Recette(s) consommée(s) — surchargeable par le bloc `models:`
    # du YAML (cf. app.ml.recipe.strategy_models).
    models: Dict[str, str] = {"signal": "omnibus_v4_single"}
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        "thresh_amp_td":    [0.40, 0.45, 0.50, 0.55, 0.60],
        "thresh_dir_td":    [0.05, 0.08, 0.10, 0.12, 0.15],
        "thresh_amp_other": [0.50, 0.55, 0.60, 0.65, 0.70],
        "thresh_dir_other": [0.10, 0.13, 0.15, 0.18, 0.20],
        "sl_atr_mult_td":    [1.5, 1.75, 1.8, 2.0, 2.25],
        "sl_atr_mult_other": [1.0, 1.25, 1.5, 1.75],
        "tp_atr_mult_td":    [1.0, 1.2, 1.4, 1.6],
        "tp_atr_mult_other": [0.8, 1.0, 1.2, 1.4],
        "max_hold_bars":     [1, 2, 4, 6, 8],
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
        # Sizing gradué par heure UTC (indépendant du filtre binaire ci-dessus,
        # cf. _HOUR_SIZE_MULT) — désactivable pour retrouver un sizing plat.
        "enable_hour_sizing":  True,
        "use_fixed_tp":        True,
        "disable_trailing":    True,
        "use_exit_after_bars": False,
        "sl_atr_mult_td":      1.8,
        "sl_atr_mult_other":   1.5,
        "tp_atr_mult_td":      1.2,
        "tp_atr_mult_other":   1.0,
        "use_kelly_sizing":    True,
        "kelly_size_other":    0.5,
        "min_confidence":      0.2,
        "amp_top_pct":         0.30,
        "warmup_bars":         2000,
        "retrain_every":       800,
        "n_estimators":        500,
        "num_leaves":          31,
        "learning_rate":       0.03,
    }

    retrain_interval_h: int = 6

    # Cette recette ne calibre ni n'élague, et labellise en t+1 — cf.
    # recipes/omnibus_v4_single.yaml, qui reste la source de vérité.
    ml_calibrate = False
    ml_prune_features = False
    ml_multi_horizon = False

    def __init__(self):
        # Tout l'état ML (modèles, médianes, compteurs, cache de backtest, lock)
        # vit désormais dans le backend — cf. MLBackendMixin.
        MLBackendMixin.__init__(self)
        self._cancel_event = None

    def min_bars_required(self, params: dict | None = None) -> int:
        p = (params or {}).get(self.name, {})
        warmup = int(p.get("warmup_bars", self._DEFAULTS["warmup_bars"]))
        return max(230, warmup + 30)

    # Entraînement avec cache process-wide (cf. app/core/train_cache.py) :
    # les retrains identiques (même fenêtre, mêmes hyperparams d'entraînement)
    # sont réutilisés entre les trials de l'optimiseur au lieu d'être relancés.
    _TRAIN_STATE_ATTRS = ('_amp_models', '_dir_models', '_feature_cols', '_medians', '_best_auc_per_tf', '_train_meta')
    _TRAIN_PARAM_KEYS  = ('amp_top_pct', 'n_estimators', 'num_leaves', 'learning_rate')

    # ── Score ──────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict | None = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        thresh_amp_td    = float(p.get("thresh_amp_td",    0.50))
        thresh_dir_td    = float(p.get("thresh_dir_td",    0.10))
        thresh_amp_other = float(p.get("thresh_amp_other", 0.55))
        thresh_dir_other = float(p.get("thresh_dir_other", 0.13))
        adx_threshold    = float(p.get("adx_threshold",    20.0))
        max_hold_bars    = int(p.get("max_hold_bars",      4))

        sl_atr_mult_td    = float(p.get("sl_atr_mult_td",    self._DEFAULTS["sl_atr_mult_td"]))
        sl_atr_mult_other = float(p.get("sl_atr_mult_other", self._DEFAULTS["sl_atr_mult_other"]))
        tp_atr_mult_td    = float(p.get("tp_atr_mult_td",    self._DEFAULTS["tp_atr_mult_td"]))
        tp_atr_mult_other = float(p.get("tp_atr_mult_other", self._DEFAULTS["tp_atr_mult_other"]))
        if "sl_atr_mult" in p:
            sl_atr_mult_td = sl_atr_mult_other = float(p["sl_atr_mult"])
        if "tp_atr_mult" in p:
            tp_atr_mult_td = tp_atr_mult_other = float(p["tp_atr_mult"])

        use_kelly_sizing = bool(p.get("use_kelly_sizing", self._DEFAULTS["use_kelly_sizing"]))
        kelly_size_other = float(p.get("kelly_size_other", self._DEFAULTS["kelly_size_other"]))
        min_confidence   = float(p.get("min_confidence",   self._DEFAULTS["min_confidence"]))

        enable_hour_filter  = bool(p.get("enable_hour_filter",  self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",    self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",         self._DEFAULTS["active_days"]))
        enable_hour_sizing  = bool(p.get("enable_hour_sizing",  self._DEFAULTS["enable_hour_sizing"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",        self._DEFAULTS["use_fixed_tp"]))
        disable_trailing    = bool(p.get("disable_trailing",    self._DEFAULTS["disable_trailing"]))
        use_exit_after_bars = bool(p.get("use_exit_after_bars", self._DEFAULTS["use_exit_after_bars"]))

        warmup_bars   = int(p.get("warmup_bars",   self._DEFAULTS["warmup_bars"]))
        retrain_every = int(p.get("retrain_every", self._DEFAULTS["retrain_every"]))

        # Heure/jour de la dernière bougie — calculés inconditionnellement :
        # le filtre binaire (skip total hors session) et le sizing gradué
        # (_HOUR_SIZE_MULT, appliqué plus bas) sont deux leviers indépendants.
        hour, dow = _last_bar_hour_dow(df)
        if enable_hour_filter and hour is not None and dow is not None:
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
        atr_v = float(last_row.get("ATR_14") or 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        adx_v = float(last_row.get("ADX") or 0.0)
        bull  = int(last_row.get("MM_bullish_align") or 0)
        bear  = int(last_row.get("MM_bearish_align") or 0)
        if adx_v < adx_threshold:
            regime = REGIME_RANGE
        elif bull == 1:
            regime = REGIME_TREND_UP
        elif bear == 1:
            regime = REGIME_TREND_DN
        else:
            regime = REGIME_CHOPPY
        regime_lbl = REGIME_LABELS[regime]

        if regime == REGIME_TREND_UP:
            return self._none("Trend Up : aucun edge (AUC dir ≈ 0.50)", regime=regime)

        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")
        dir_dist = abs(p_up - 0.5)

        if regime == REGIME_TREND_DN:
            amp_thresh, dir_thresh = thresh_amp_td, thresh_dir_td
            sl_atr_mult, tp_atr_mult = sl_atr_mult_td, tp_atr_mult_td
            regime_size_fac = 1.0
        else:
            amp_thresh, dir_thresh = thresh_amp_other, thresh_dir_other
            sl_atr_mult, tp_atr_mult = sl_atr_mult_other, tp_atr_mult_other
            regime_size_fac = kelly_size_other

        if p_event < amp_thresh:
            return self._none(
                f"P(event)={p_event:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )
        if dir_dist < dir_thresh:
            return self._none(
                f"|P(up)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side = "long" if p_up > 0.5 else "short"

        confidence = dir_dist * 2.0
        if use_kelly_sizing:
            size_factor = min(1.0, max(0.0, regime_size_fac * max(confidence, min_confidence)))
        else:
            size_factor = regime_size_fac

        # Sizing gradué par heure UTC (indépendant du filtre binaire) — un
        # signal à 14h (lift ×2.43, mult=1.0) garde sa taille pleine ; le même
        # signal à 19h (lift ×1.10, mult≈0.45) ou hors fenêtre si le filtre
        # est désactivé est réduit en proportion du lift empirique mesuré.
        hour_size_mult = 1.0
        if enable_hour_sizing and hour is not None:
            hour_size_mult = _HOUR_SIZE_MULT.get(hour, 1.0)
            size_factor = min(1.0, max(0.0, size_factor * hour_size_mult))

        score_val = round(min(0.55 + p_event * confidence * 0.39, 0.94), 3)
        meta      = self._train_meta.get(tf, {})

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      sl_atr_mult,
            "size_factor":      round(size_factor, 4),
            "disable_trailing": disable_trailing,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "tf_detected":      tf,
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult
        if use_exit_after_bars:
            sig["exit_after_bars"] = max_hold_bars
        if not disable_trailing:
            sig["trail_override"] = {
                "trail_wide":  max(1.0, sl_atr_mult),
                "trail_tight": max(0.5, tp_atr_mult * 0.5),
                "breakeven_r": 0.8,
                "lock_r":      max(1.0, tp_atr_mult),
                "tight_r":     max(1.5, tp_atr_mult * 1.5),
                "grace_bars":  1,
            }

        exit_desc = [f"SL fixe = entry ∓ {sl_atr_mult:.2f}×ATR"]
        if use_fixed_tp:
            exit_desc.append(f"TP fixe = entry ± {tp_atr_mult:.2f}×ATR")
        exit_desc.append("trailing désactivé" if disable_trailing else "trailing actif")
        if use_exit_after_bars:
            exit_desc.append(f"sortie après {max_hold_bars} barres max")

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(float(last_row.get("RSI_14") or 50.0), 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "dir_dist":         round(dir_dist, 4),
            "confidence":       round(confidence, 4),
            "size_factor":      round(size_factor, 4),
            "regime_size_fac":  regime_size_fac,
            "hour_size_mult":   round(hour_size_mult, 3),
            "sl_mult":          sl_atr_mult,
            "tp_mult":          tp_atr_mult if use_fixed_tp else None,
            "use_fixed_tp":     use_fixed_tp,
            "use_kelly_sizing": use_kelly_sizing,
            "disable_trailing": disable_trailing,
            "auc_amp":          meta.get("auc_amp", 0.0),
            "auc_dir":          meta.get("auc_dir", 0.0),
            "n_features":       meta.get("n_features", 0),
            "amp_thr_pct":      meta.get("amp_thr_pct", 0.0),
        }
        sig["conditions"] = [
            f"Modèle V4 entraîné inline / {tf} ({meta.get('n_features', 0)} features, "
            f"AUC amp={meta.get('auc_amp', 0):.2f} dir={meta.get('auc_dir', 0):.2f})",
            f"Régime : {regime_lbl} (ADX={adx_v:.0f}) — autorisé ✓",
            f"P(événement)={p_event:.2f} ≥ {amp_thresh:.2f} ✓",
            f"P(hausse)={p_up:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR (régime {regime_lbl})",
            f"Sizing : régime ×{regime_size_fac:.2f} × confidence {confidence:.2f} "
            f"= size_factor {size_factor:.2f}" if use_kelly_sizing
            else f"Sizing : ×{regime_size_fac:.2f} (Kelly désactivé)",
            f"Sizing horaire : {hour}h UTC → ×{hour_size_mult:.2f} "
            f"(lift empirique {_HOUR_LIFT_15M.get(hour, 1.0):.2f}× la moyenne)"
            if enable_hour_sizing and hour is not None else "Sizing horaire désactivé",
            f"Sortie : {' + '.join(exit_desc)}",
        ]
        sig["reason"] = (
            f"OpusV4-RT {side.upper()} | {regime_lbl} | tf={tf} | "
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
