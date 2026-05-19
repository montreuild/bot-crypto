"""Stratégie Opus Omnibus V7 (entraîné inline) — 6 setups sur modèles V4 entraînés.

Variante de ``opus_omnibus_v6_pretrained`` qui **entraîne son propre modèle**
au lieu de charger le pkl V4 embarqué. Conserve la logique V7 des 6 setups.

Améliorations V7 (idem ``opus_omnibus_v6_pretrained``) :
  - Nouveau setup SHORT_TD_HIGH (priorité 0, amp≥0.60, p_dir<0.30, size×1.5).
  - LONG_CHOPPY raffiné : p_dir>0.58, TP/SL serrés, max_bars=5.
  - SHORT_CHOPPY durci : p_dir<0.42, TP élargi.
  - Early exit LONG_CHOPPY assoupli : sort si p_dir<0.40 OU régime=TD.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.indicators import build_v4_features, pre_val
from app.strategies.opus_stat_retrained_v4 import (
    Strategy as _OpusRetrained,
    _window_polars,
)
from app.strategies.opus_v4_common import (
    detect_timeframe as _detect_timeframe,
    last_bar_hour_dow as _last_bar_hour_dow,
    classify_regime as _classify_regime,
    regime_history_from_features as _regime_history_from_features,
    exit_td_window_active as _exit_td_window_active,
    apply_setup_overrides as _apply_setup_overrides,
    select_setup as _select_setup,
    check_early_exit_v7 as _check_early_exit_v6,
    EXIT_TD_WINDOW_BARS as _EXIT_TD_WINDOW_BARS,
    REGIME_LABELS, REGIME_TREND_DN,
)

logger = logging.getLogger(__name__)

_SUPPORTED_TFS = ("15m", "30m", "1h")


class Strategy(_OpusRetrained):
    """V7 OMNIBUS — 6 setups avec routing par priorité, sur modèles V4
    entraînés inline (même pipeline LightGBM que ``opus_stat_retrained_v4``)."""

    name      = "opus_omnibus_v6"
    model_dir = "models"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    # Espaces d'optimisation : seuils V6.1 setups + hyperparams d'entraînement V4
    param_space: Dict[str, Any] = {
        # SHORT_TD_HIGH (V7)
        "setup_short_td_high_amp_min":    [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":    [0.25, 0.30, 0.35],
        # SHORT_TD
        "setup_short_td_amp_min":         [0.45, 0.50, 0.55],
        "setup_short_td_dir_max":         [0.35, 0.40, 0.45],
        "setup_short_td_tp_mult":         [1.0, 1.2, 1.4],
        "setup_short_td_sl_mult":         [1.4, 1.6, 1.8],
        # SHORT_CHOPPY (V7 : seuil durci)
        "setup_short_choppy_amp_min":     [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":     [0.38, 0.42, 0.46],
        # LONG_CHOPPY (V7 : seuil raffiné)
        "setup_long_choppy_amp_min":      [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":      [0.55, 0.58, 0.62],
        # LONG_EXIT_TD
        "setup_long_exit_td_amp_min":     [0.35, 0.40, 0.45],
        "setup_long_exit_td_max_bars":    [4, 6, 8, 10],
        # LONG_RANGE_STRICT
        "setup_long_range_strict_amp_min":[0.55, 0.60, 0.65],
        "setup_long_range_strict_dir_min":[0.55, 0.60, 0.65],
        "exit_td_window_bars":            [2, 3, 4],
        # Entraînement V4 (hérité)
        "amp_top_pct":     [0.25, 0.30, 0.35],
        "warmup_bars":     [1000, 2000, 3000],
        "retrain_every":   [500, 800, 1500],
        "n_estimators":    [200, 300, 500],
        "num_leaves":      [15, 31, 63],
        "learning_rate":   [0.02, 0.03, 0.05],
    }
    fixed_params: Dict[str, Any] = {}

    # Hérite des défauts d'entraînement + ajoute les défauts V6.1 spécifiques.
    _DEFAULTS = {
        **_OpusRetrained._DEFAULTS,
        # V6.1 — surcharge le routing décision
        "enable_hour_filter":  True,
        "active_hours_utc":    list(range(13, 21)),
        "active_days":         [0, 1, 2, 3, 4],
        "adx_threshold":       20.0,
        "exit_td_window_bars": _EXIT_TD_WINDOW_BARS,
        "disable_trailing":    True,   # V6.1 → SL fixe
        "use_fixed_tp":        True,
    }

    # ── Sortie anticipée V6.1 (réplique de should_exit_early) ──────────────
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
            features = build_v4_features(
                _window_polars(df, n=max(260, self.min_bars_required(params)))
            )
            if features is None or len(features) == 0:
                return None
            last_row = features.row(-1, named=True)
            regime = _classify_regime(
                float(last_row.get("ADX") or 0.0),
                int(last_row.get("MM_bullish_align") or 0),
                int(last_row.get("MM_bearish_align") or 0),
                adx_threshold,
            )
            p_up = self.predict_direction(features, tf)
            if p_up is None:
                return None
        except Exception as e:
            logger.warning(f"[OmnibusV7-RT] check_early_exit recompute KO : {e}")
            return None

        return _check_early_exit_v6(
            setup_name, regime, p_up,
            dir_inv_short=dir_inv_short,
            dir_inv_long=dir_inv_long,
            dir_drop_range=dir_drop_range,
        )

    # ── Score (remplace la règle V4 par le sélecteur 5-setups V6.1) ────────
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

        # 1. Filtre temporel V4
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

        # 2. Détection TF
        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(
                f"Timeframe non supporté (détecté={tf}, attendus={_SUPPORTED_TFS})"
            )

        # 3. Walk-forward inline (hérité de _OpusRetrained._train)
        cnt = self._call_cnt.get(tf, 0) + 1
        self._call_cnt[tf] = cnt
        last       = self._last_retrain.get(tf, 0)
        need_train = (tf not in self._trained_tfs) or (cnt - last >= retrain_every)
        if need_train and not self._managed_externally:
            n_train  = min(len(df) - 1, warmup_bars * 2)
            train_df = df.slice(len(df) - n_train - 1, n_train)
            if self._train(train_df, tf, p):
                self._last_retrain[tf] = cnt

        if tf not in self._trained_tfs:
            return self._none("Modèle pas encore entraîné (warmup en cours)")

        # 4. Features + ATR
        features = build_v4_features(
            _window_polars(df, n=max(260, self.min_bars_required(params)))
        )
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v    = float(last_row.get("ATR_14") or 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now    = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        # 5. Régime + exit_td_window
        regime_history = _regime_history_from_features(
            features, n_last=max(exit_td_window_bars + 2, 5),
            adx_threshold=adx_threshold,
        )
        regime         = regime_history[-1]
        regime_lbl     = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regime_history, exit_td_window_bars)

        # 6. Prédictions (modèles entraînés inline, méthodes héritées)
        p_event = self.predict_amplitude(features, tf)
        p_up    = self.predict_direction(features, tf)
        if p_event is None or p_up is None:
            return self._none(f"Modèle {tf} indisponible")

        # 7. Sélection du setup
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
            "adx":              round(float(last_row.get("ADX") or 0.0), 1),
            "rsi":              round(float(last_row.get("RSI_14") or 50.0), 1),
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
