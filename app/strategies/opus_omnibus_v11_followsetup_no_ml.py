"""Opus Omnibus V11 FollowSetup — variante *sans ML*
(``opus_omnibus_v11_followsetup_no_ml``).

Équivalent à base d'indicateurs de
:mod:`app.strategies.opus_omnibus_v11_followsetup`. La philosophie FollowSetup est
conservée intégralement : pas de TP/trailing/timeout serré ; une position reste
ouverte tant que le setup actif pointe dans sa direction, et n'est clôturée que
lorsqu'un setup **opposé** est confirmé (anti-whipsaw : confirmation sur K
bougies, cooldown, score minimum, hystérésis). Seules les deux probabilités ML
``p_event`` et ``p_up`` sont remplacées par les proxys déterministes de
:mod:`app.strategies._no_ml_proxy`.

Aucun modèle entraîné, aucune dépendance LightGBM/sklearn. Le routing (setups
sans LONG_EXIT_TD, régime enrichi) est importé de
``opus_omnibus_v11_followsetup`` ; le pipeline de features V4 polars et le cache
``v4_polars`` v1 restent partagés.
"""

import datetime as _dt
import json
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import pre_val
from app.strategies.opus_omnibus_v11_followsetup import (
    _SUPPORTED_TFS,
    _build_features,
    _window_polars,
    _detect_timeframe,
    _last_regime,
    _apply_setup_overrides,
    _select_setup,
    REGIME_LABELS,
)
from app.strategies._no_ml_proxy import compute_proxies, PROXY_PARAM_SPACE

logger = logging.getLogger(__name__)

_FLIP_LOG_PATH = os.path.join("logs", "opus_omnibus_v11_followsetup_no_ml_flips.jsonl")


class Strategy(BaseStrategy):
    """V11 FollowSetup sans ML — sortie pilotée par le flip de setup, proxys d'indicateurs."""

    name = "opus_omnibus_v11_followsetup_no_ml"

    timeframes: List[str] = list(_SUPPORTED_TFS)

    param_space: Dict[str, Any] = {
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
        "di_rescue":             [8.0, 10.0, 14.0],
        "safety_sl_atr_mult":    [6.0, 8.0, 10.0, 15.0],
        "flip_confirm_bars":     [1, 2, 3, 4],
        "flip_cooldown_bars":    [0, 3, 5, 8],
        "flip_min_score":        [0.0, 0.50, 0.55, 0.60],
        "flip_hysteresis_margin": [0.0, 0.03, 0.05, 0.08],
        "max_bars_safety":       [100, 200, 400, 800],
        **PROXY_PARAM_SPACE,
    }
    fixed_params: Dict[str, Any] = {}

    _DEFAULTS = {
        "enable_hour_filter":  False,
        "active_hours_utc":    list(range(0, 24)),
        "active_days":         [0, 1, 2, 3, 4, 5, 6],
        "adx_threshold":       20.0,
        "safety_sl_atr_mult":  10.0,
        "disable_trailing":    True,
        "use_fixed_tp":        False,
        "signal_up_dynamic_risk": False,
        "bearish_excess_rsi_threshold": 38.0,
        "bearish_excess_sma_pct":        1.5,
        "di_rescue":           10.0,
        "log_flips":           True,
        "flip_confirm_bars":      2,
        "flip_cooldown_bars":     5,
        "flip_min_score":         0.55,
        "flip_hysteresis_margin": 0.05,
        "max_bars_safety":        200,
    }

    def __init__(self):
        self._bt_features: Optional[pl.DataFrame] = None
        self._bt_features_len: int = 0
        self._call_cnt: Dict[str, int] = {}
        self._last_flip_cnt: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        return 230

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            from app.core.feature_store import cached_strategy_features
            feats = cached_strategy_features(
                getattr(self, "_bt_symbol", None), getattr(self, "_bt_tf", None), df,
                name="v4_polars", version="1",
                builder=lambda w: _build_features(_window_polars(w, n=len(w))),
                in_kind="polars", out_kind="polars")
            self._bt_features = feats
            self._bt_features_len = len(df) if feats is not None else 0
            logger.info(
                f"[OmnibusV11-FollowSetup-NoML] backtest : features pré-calculées sur "
                f"{self._bt_features_len} bougies "
                f"({(len(feats.columns) if feats is not None else 0)} colonnes)"
            )
        except Exception as e:
            logger.warning(f"[OmnibusV11-FollowSetup-NoML] prepare_for_backtest KO : {e}")
            self._bt_features = None
            self._bt_features_len = 0

    def _features(self, df: pl.DataFrame) -> Optional[pl.DataFrame]:
        if self._bt_features is not None and len(df) <= self._bt_features_len:
            return self._bt_features.head(len(df))
        return _build_features(_window_polars(df, n=max(260, self.min_bars_required())))

    def _bearish_excess(self, df: pl.DataFrame, last_row: dict,
                        c_now: float, p: dict) -> bool:
        be_rsi_thr = float(p.get("bearish_excess_rsi_threshold",
                                 self._DEFAULTS["bearish_excess_rsi_threshold"]))
        be_sma_pct = float(p.get("bearish_excess_sma_pct",
                                 self._DEFAULTS["bearish_excess_sma_pct"]))
        if len(df) >= 2:
            consec_red = bool(
                float(df["close"][-1]) < float(df["open"][-1]) and
                float(df["close"][-2]) < float(df["open"][-2])
            )
        else:
            consec_red = False
        rsi_v = float(last_row.get("RSI_14") or 50.0)
        rsi_excess = rsi_v < be_rsi_thr
        sma20_v = float(last_row.get("SMA_20") or 0.0)
        price_below_sma20 = (
            c_now < sma20_v * (1.0 - be_sma_pct / 100.0)
        ) if sma20_v > 0 else False
        return consec_red or rsi_excess or price_below_sma20

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        if df is None or len(df) < self.min_bars_required():
            return self._none(f"Données insuffisantes ({len(df) if df is not None else 0})")

        p = (params or {}).get(self.name, {})
        adx_threshold    = float(p.get("adx_threshold",     self._DEFAULTS["adx_threshold"]))
        di_rescue        = float(p.get("di_rescue",         self._DEFAULTS["di_rescue"]))
        safety_sl_mult   = float(p.get("safety_sl_atr_mult", self._DEFAULTS["safety_sl_atr_mult"]))
        disable_trailing = bool(p.get("disable_trailing",   self._DEFAULTS["disable_trailing"]))
        cooldown_bars    = int(p.get("flip_cooldown_bars",  self._DEFAULTS["flip_cooldown_bars"]))
        max_bars_safety  = int(p.get("max_bars_safety",     self._DEFAULTS["max_bars_safety"]))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return self._none(f"Timeframe non supporté (détecté={tf})")

        cnt = self._call_cnt.get(tf, 0) + 1
        self._call_cnt[tf] = cnt

        # Cooldown post-flip : gel des entrées pendant N bougies après un flip.
        last_flip = self._last_flip_cnt.get(tf, -10**9)
        bars_since_flip = cnt - last_flip
        if cooldown_bars > 0 and bars_since_flip < cooldown_bars:
            return self._none(
                f"Cooldown post-flip ({bars_since_flip}/{cooldown_bars} bougies)"
            )

        features = self._features(df)
        if features is None or len(features) == 0:
            return self._none("Construction des features V4 impossible")

        last_row = features.row(-1, named=True)
        atr_v = float(last_row.get("ATR_14") or 0.0)
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = float(pre_val(df, "_pre_atr14") or 0.0)
        c_now = float(df["close"][-1] or 0.0)
        if c_now <= 0 or atr_v <= 0:
            return self._none("Prix ou ATR invalide")

        regime, regime_sub = _last_regime(features, adx_threshold, di_rescue)
        regime_lbl = REGIME_LABELS[regime]

        p_event, p_up = compute_proxies(last_row, p)

        rsi_v = float(last_row.get("RSI_14") or 50.0)
        adx_v = float(last_row.get("ADX") or 0.0)
        bearish_excess = self._bearish_excess(df, last_row, c_now, p)

        setups = _apply_setup_overrides(p)
        setup  = _select_setup(setups, regime, p_event, p_up,
                               bearish_excess, rsi_v, adx_v)
        if setup is None:
            return self._none(
                f"Aucun setup actif | regime={regime_lbl}({regime_sub}) "
                f"p_event={p_event:.2f} p_up={p_up:.2f} rsi={rsi_v:.1f} adx={adx_v:.1f}",
                p_event=p_event, p_up=p_up, regime=regime,
            )

        side        = "long" if setup["direction"] == 1 else "short"
        size_factor = float(setup.get("size_factor", 1.0))

        priority_bonus = max(0, 6 - int(setup["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        score_val      = round(min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3)

        sig: Dict[str, Any] = {
            "score":            score_val,
            "side":             side,
            "name":             self.name,
            "atr":              atr_v,
            "sl_atr_mult":      safety_sl_mult,
            "disable_trailing": disable_trailing,
            "size_factor":      size_factor,
            "exit_after_bars":  max_bars_safety,
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "regime_sub":       regime_sub,
            "tf_detected":      tf,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "setup_direction":  int(setup["direction"]),
            "bearish_excess":   bool(bearish_excess),
            "rsi":              round(rsi_v, 1),
            "adx":              round(adx_v, 1),
        }

        sig["indicators"] = {
            "adx":              round(adx_v, 1),
            "rsi":              round(rsi_v, 1),
            "p_event":          round(p_event, 4),
            "p_up":             round(p_up, 4),
            "regime":           regime,
            "regime_lbl":       regime_lbl,
            "regime_sub":       regime_sub,
            "setup":            setup["name"],
            "setup_priority":   int(setup["priority"]),
            "setup_direction":  int(setup["direction"]),
            "sl_mult":          safety_sl_mult,
            "proxy":            True,
        }
        sig["conditions"] = [
            f"Setup FollowSetup (no-ML) retenu : {setup['name']} (priorité {setup['priority']}, dir={side})",
            f"Régime : {regime_lbl} / {regime_sub} | ADX={adx_v:.1f}",
            f"P(événement) proxy={p_event:.2f} ≥ {setup['amp_min']:.2f} ✓",
            (f"P(hausse) proxy={p_up:.2f} < {setup['dir_max']:.2f} ✓"
             if setup.get("dir_max") is not None else
             f"P(hausse) proxy={p_up:.2f} > {setup['dir_min']:.2f} ✓"
             if setup.get("dir_min") is not None else
             f"P(hausse) proxy={p_up:.2f}"),
            f"Pas de TP/trailing — sortie sur flip de setup (confirm "
            f"{int(p.get('flip_confirm_bars', self._DEFAULTS['flip_confirm_bars']))} "
            f"bougies, cooldown {cooldown_bars}) | "
            f"SL safety {safety_sl_mult:.1f}×ATR | timeout {max_bars_safety} bougies",
            "Probabilités issues de proxys d'indicateurs (sans modèle ML)",
        ]
        sig["reason"] = (
            f"OmnibusV11-FollowSetup-NoML {setup['name']} {side.upper()} | "
            f"{regime_lbl}/{regime_sub} | tf={tf} | "
            f"P(event)={p_event:.2f} P(up)={p_up:.2f} ADX={adx_v:.1f}"
        )
        return sig

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        side = position.get("side")
        if side not in ("long", "short"):
            return None
        if df is None or len(df) < self.min_bars_required():
            return None

        p = (params or {}).get(self.name, {})
        adx_threshold     = float(p.get("adx_threshold", self._DEFAULTS["adx_threshold"]))
        di_rescue         = float(p.get("di_rescue",     self._DEFAULTS["di_rescue"]))
        confirm_bars      = int(p.get("flip_confirm_bars",
                                      self._DEFAULTS["flip_confirm_bars"]))
        flip_min_score    = float(p.get("flip_min_score",
                                        self._DEFAULTS["flip_min_score"]))
        hysteresis_margin = float(p.get("flip_hysteresis_margin",
                                        self._DEFAULTS["flip_hysteresis_margin"]))
        log_flips         = bool(p.get("log_flips", self._DEFAULTS["log_flips"]))

        tf = _detect_timeframe(df)
        if tf not in _SUPPORTED_TFS:
            return None

        try:
            features = self._features(df)
            if features is None or len(features) == 0:
                return None
            last_row = features.row(-1, named=True)
            c_now    = float(df["close"][-1] or 0.0)
            if c_now <= 0:
                return None

            regime, regime_sub = _last_regime(features, adx_threshold, di_rescue)
            p_event, p_up = compute_proxies(last_row, p)
            rsi_v = float(last_row.get("RSI_14") or 50.0)
            adx_v = float(last_row.get("ADX") or 0.0)
            bearish_excess = self._bearish_excess(df, last_row, c_now, p)

            setups = _apply_setup_overrides(p)
            current = _select_setup(setups, regime, p_event, p_up,
                                    bearish_excess, rsi_v, adx_v)
        except Exception as e:
            logger.warning(f"[OmnibusV11-FollowSetup-NoML] check_early_exit recompute KO : {e}")
            return None

        held_dir = 1 if side == "long" else -1

        if current is None or int(current["direction"]) == held_dir:
            position["_fs_opp_count"] = 0
            position["_fs_opp_setup"] = None
            return None

        if hysteresis_margin > 0.0:
            d_max = current.get("dir_max")
            d_min = current.get("dir_min")
            if d_max is not None and p_up >= (float(d_max) - hysteresis_margin):
                position["_fs_opp_count"] = 0
                position["_fs_opp_setup"] = None
                return None
            if d_min is not None and p_up <= (float(d_min) + hysteresis_margin):
                position["_fs_opp_count"] = 0
                position["_fs_opp_setup"] = None
                return None

        priority_bonus = max(0, 6 - int(current["priority"])) * 0.025
        confidence     = abs(p_up - 0.5) * 2.0
        new_score      = round(
            min(0.55 + p_event * confidence * 0.30 + priority_bonus, 0.94), 3
        )
        if new_score < flip_min_score:
            position["_fs_opp_count"] = 0
            position["_fs_opp_setup"] = None
            return None

        prev_setup = position.get("_fs_opp_setup")
        if prev_setup == current["name"]:
            cnt_opp = int(position.get("_fs_opp_count", 0)) + 1
        else:
            cnt_opp = 1
        position["_fs_opp_count"] = cnt_opp
        position["_fs_opp_setup"] = current["name"]

        if cnt_opp < max(1, confirm_bars):
            return None

        try:
            self._last_flip_cnt[tf] = self._call_cnt.get(tf, 0)
        except Exception:
            pass
        if log_flips:
            self._log_flip(
                tf=tf, position=position, new_setup=current, new_score=new_score,
                p_event=float(p_event), p_up=float(p_up), regime=regime,
                regime_sub=regime_sub, adx=adx_v, rsi=rsi_v, confirm_bars=int(cnt_opp),
            )
        return f"setup_flip_to_{current['name']}"

    def _log_flip(self, tf: str, position: dict, new_setup: dict,
                  new_score: float, p_event: float, p_up: float,
                  regime: int, regime_sub: str, adx: float, rsi: float,
                  confirm_bars: int) -> None:
        try:
            os.makedirs(os.path.dirname(_FLIP_LOG_PATH) or ".", exist_ok=True)
            record = {
                "ts":             _dt.datetime.utcnow().isoformat(),
                "strategy":       self.name,
                "tf":             tf,
                "symbol":         position.get("symbol"),
                "from_side":      position.get("side"),
                "from_setup":     position.get("setup"),
                "to_setup":       new_setup.get("name"),
                "to_direction":   int(new_setup.get("direction", 0)),
                "new_score":      round(float(new_score), 4),
                "p_event":        round(float(p_event), 4),
                "p_up":           round(float(p_up), 4),
                "regime":         regime,
                "regime_lbl":     REGIME_LABELS.get(regime, "?"),
                "regime_sub":     regime_sub,
                "adx":            round(float(adx), 2),
                "rsi":            round(float(rsi), 2),
                "confirm_bars":   int(confirm_bars),
            }
            with open(_FLIP_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[OmnibusV11-FollowSetup-NoML] log flip KO : {e}")

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
