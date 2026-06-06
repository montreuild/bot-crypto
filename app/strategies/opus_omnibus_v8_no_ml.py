"""Stratégie Opus Omnibus V8 — variante *sans ML* (``opus_omnibus_v8_no_ml``).

Équivalent à base d'indicateurs de :mod:`app.strategies.opus_omnibus_v8`. Le
squelette est strictement identique (mêmes features V4, même classification de
régime, mêmes 7 setups, même filtre horaire, même formule de score, mêmes
sorties anticipées) ; seules les **deux probabilités ML** ``p_event`` (amplitude)
et ``p_up`` (direction) sont remplacées par les proxys déterministes de
:mod:`app.strategies._no_ml_proxy`.

Conséquences :
  * aucun fichier modèle (.pkl) à charger, aucune dépendance LightGBM/sklearn ;
  * aucun entraînement ni ré-entraînement → coût CPU/maintenance quasi nul ;
  * comportement déterministe et inspectable (les sorties sont des combinaisons
    d'indicateurs documentées).

Le routing (setups V8, régime, exit_td_window, excès baissier) est **importé**
de ``opus_omnibus_v8`` pour rester automatiquement synchronisé avec la version
ML. Le pipeline de features V4 (pandas) et le cache feature_store sont partagés
(catalogue ``opus_v4_pandas`` v1) avec les variantes ML/V8.
"""

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import pre_val
from app.strategies.opus_stat_pretrained_v4 import (
    _FeatureBuilder,
    _detect_timeframe,
    _last_bar_hour_dow,
    _to_pandas_window,
    REGIME_LABELS,
)
from app.strategies.opus_omnibus_v8 import (
    _SUPPORTED_TFS,
    _EXIT_TD_WINDOW_BARS,
    _classify_regime,
    _regime_history_from_features,
    _exit_td_window_active,
    _apply_setup_overrides,
    _select_setup,
    _check_early_exit_v7,
)
from app.strategies._no_ml_proxy import compute_proxies, PROXY_PARAM_SPACE

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    """V8 OMNIBUS sans ML — proxys d'indicateurs au lieu des modèles V4."""

    name = "opus_omnibus_v8_no_ml"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
        # Seuils de setups (sous-ensemble impactant, identique à V8).
        "setup_signal_up_amp_min":        [0.45, 0.50, 0.55],
        "setup_signal_up_dir_min":        [0.55, 0.60, 0.65],
        "setup_short_td_high_amp_min":    [0.55, 0.60, 0.65],
        "setup_short_td_high_dir_max":    [0.25, 0.30, 0.35],
        "setup_short_td_amp_min":         [0.45, 0.50, 0.55],
        "setup_short_td_dir_max":         [0.35, 0.40, 0.45],
        "setup_long_choppy_amp_min":      [0.45, 0.50, 0.55],
        "setup_long_choppy_dir_min":      [0.55, 0.58, 0.62],
        "setup_short_choppy_amp_min":     [0.45, 0.50, 0.55],
        "setup_short_choppy_dir_max":     [0.38, 0.42, 0.46],
        "setup_long_range_strict_amp_min": [0.55, 0.60, 0.65],
        "setup_long_range_strict_dir_min": [0.55, 0.60, 0.65],
        "exit_td_window_bars":            [2, 3, 4],
        # Coefficients des proxys (optimisables).
        **PROXY_PARAM_SPACE,
    }
    fixed_params: Dict[str, Any] = {}

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
    }

    _FEATURE_BUILDER = _FeatureBuilder()

    def __init__(self):
        self._bt_features_pdf = None
        self._bt_features_len = 0

    def min_bars_required(self, params: dict = None) -> int:
        return 230

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule les features V4 (cache partagé avec V8 ML : opus_v4_pandas)."""
        try:
            from app.core.feature_store import cached_strategy_features
            _ohlcv = ("time", "open", "high", "low", "close", "volume")
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="opus_v4_pandas", version="1",
                builder=lambda pdf: self._FEATURE_BUILDER.build(
                    pdf[[c for c in _ohlcv if c in pdf.columns]]),
                in_kind="pandas", out_kind="pandas")
            self._bt_features_pdf = feats
            self._bt_features_len = len(df) if feats is not None else 0
            logger.info(
                f"[OmnibusV8-NoML] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(feats.shape[1] if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OmnibusV8-NoML] prepare_for_backtest KO : {e}")
            self._bt_features_pdf = None
            self._bt_features_len = 0

    def _features(self, df: pl.DataFrame):
        if self._bt_features_pdf is not None and len(df) <= self._bt_features_len:
            return self._bt_features_pdf.iloc[: len(df)]
        pdf = _to_pandas_window(df, n=max(260, self.min_bars_required() + 20))
        return self._FEATURE_BUILDER.build(pdf)

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required():
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        enable_hour_filter  = bool(p.get("enable_hour_filter", self._DEFAULTS["enable_hour_filter"]))
        active_hours_utc    = list(p.get("active_hours_utc",   self._DEFAULTS["active_hours_utc"]))
        active_days         = list(p.get("active_days",        self._DEFAULTS["active_days"]))
        adx_threshold       = float(p.get("adx_threshold",     self._DEFAULTS["adx_threshold"]))
        exit_td_window_bars = int(p.get("exit_td_window_bars", self._DEFAULTS["exit_td_window_bars"]))
        disable_trailing    = bool(p.get("disable_trailing",   self._DEFAULTS["disable_trailing"]))
        use_fixed_tp        = bool(p.get("use_fixed_tp",       self._DEFAULTS["use_fixed_tp"]))

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

        features = self._features(df)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.iloc[-1]
        atr_v    = float(last_row.get("ATR_14", 0.0) or 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime_history = _regime_history_from_features(
            features, n_last=max(exit_td_window_bars + 2, 5),
            adx_threshold=adx_threshold,
        )
        regime         = regime_history[-1]
        regime_lbl     = REGIME_LABELS[regime]
        exit_td_active = _exit_td_window_active(regime_history, exit_td_window_bars)

        # Proxys déterministes au lieu des modèles V4.
        p_event, p_up = compute_proxies(last_row, p)

        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold", 38.0))
        be_sma_pct = float(p.get("bearish_excess_sma_pct", 1.5))
        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False
        rsi_v = float(last_row.get("RSI_14", 50.0) or 50.0)
        rsi_excess = rsi_v < be_rsi_thr
        sma20_v = float(pre_val(df, "_pre_sma20") or 0.0)
        if sma20_v <= 0 and len(df) >= 20:
            sma20_v = float(df["close"].rolling_mean(20)[-1] or 0.0)
        price_below_sma20 = (c_now < sma20_v * (1.0 - be_sma_pct / 100.0)) if sma20_v > 0 else False
        bearish_excess = consec_red or rsi_excess or price_below_sma20

        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up, exit_td_active, bearish_excess)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl} p_event={p_event:.2f} "
                f"p_up={p_up:.2f} exit_td={exit_td_active} bearish_excess={bearish_excess}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        long_choppy_confluence = False
        if setup["name"] == "SIGNAL_UP":
            setup = dict(setup)
            setup["size_factor"] = float(setup.get("size_factor", 1.0)) * 1.5
            long_choppy_confluence = True

        side        = "long" if setup["direction"] == 1 else "short"
        sl_atr_mult = float(setup["sl_mult"])
        tp_atr_mult = float(setup["tp_mult"])
        max_bars    = int(setup["max_bars"])
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = max(0, 4 - int(setup["priority"])) * 0.04
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

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
            "bearish_excess":   bool(bearish_excess),
            "long_choppy_confluence": bool(long_choppy_confluence),
            "consec_red":       bool(consec_red),
            "rsi_excess":       bool(rsi_excess),
            "price_below_sma20": bool(price_below_sma20),
        }
        if use_fixed_tp:
            sig["tp_atr_mult"] = tp_atr_mult

        sig["indicators"] = {
            "adx":              round(float(last_row.get("ADX", 0.0) or 0.0), 1),
            "rsi":              round(rsi_v, 1),
            "sma20":            round(sma20_v, 4) if sma20_v > 0 else None,
            "bearish_excess":   bool(bearish_excess),
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
            "proxy":            True,
        }

        conditions = [
            f"Setup V8 (no-ML) retenu : {setup['name']} (priorité {setup['priority']})",
            f"Régime : {regime_lbl} | exit_td_window={exit_td_active}",
            f"P(événement) proxy={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse) proxy={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse) proxy={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse) proxy={p_up:.2f}"),
            f"Risque : SL {sl_atr_mult:.2f}×ATR | TP {tp_atr_mult:.2f}×ATR | max {max_bars} bougies",
            "Probabilités issues de proxys d'indicateurs (sans modèle ML)",
        ]
        if setup["name"] == "SIGNAL_UP":
            conditions.append(
                f"Excès baissier : rouge×2={consec_red} | RSI<{be_rsi_thr:.0f}={rsi_excess} | "
                f"Prix<SMA20-{be_sma_pct:.1f}%={price_below_sma20}"
            )
        sig["conditions"] = conditions
        sig["reason"] = (
            f"OmnibusV8-NoML {setup['name']} {side.upper()} | {regime_lbl} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} bearish_excess={bearish_excess}"
        )
        return sig

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        setup_name = position.get("setup")
        if not setup_name:
            ind = position.get("indicators") or {}
            setup_name = ind.get("setup")
        if not setup_name:
            return None
        if df is None or len(df) < self.min_bars_required():
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold  = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        dir_inv_short  = float(p.get("early_exit_dir_inv_short",  0.55))
        dir_inv_long   = float(p.get("early_exit_dir_inv_long",   0.45))
        dir_drop_range = float(p.get("early_exit_dir_drop_range", 0.40))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return None

        try:
            features = self._features(df)
            if features is None or len(features) == 0:
                return None
            last_row = features.iloc[-1]
            regime = _classify_regime(
                float(last_row.get("ADX", 0.0) or 0.0),
                int(last_row.get("MM_bullish_align", 0) or 0),
                int(last_row.get("MM_bearish_align", 0) or 0),
                adx_threshold,
            )
            _, p_up = compute_proxies(last_row, p)
        except Exception as e:
            logger.warning(f"[OmnibusV8-NoML] check_early_exit recompute KO : {e}")
            return None

        return _check_early_exit_v7(
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
