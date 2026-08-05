"""Stratégie Smart Money Concepts (SMC) — façade ARCH-05.

Corps découpé :
  - smart_money_params.py  : PARAM_SPACE / FIXED_PARAMS
  - smart_money_aux.py     : _AnalysisMixin (_build_aux, prepare_for_backtest…)
  - smart_money_plans.py   : _PlansMixin (score, trade_plans, check_early_exit)
  - smart_money_signals.py : _signal_at / _build_trade (ARCH-008)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.indicators_core import ema as _ema_series
from app.core.indicators_core import volume_ratio as _vol_ratio
from app.engine.engine import BaseStrategy
from app.strategies import smart_money_signals as _sm_signals
from app.strategies.smart_money_aux import _AnalysisMixin
from app.strategies.smart_money_params import FIXED_PARAMS, PARAM_SPACE
from app.strategies.smart_money_plans import _PlansMixin

logger = logging.getLogger(__name__)


class Strategy(_AnalysisMixin, _PlansMixin, BaseStrategy):
    name = "smart_money"

    # Aligné sur trading.timeframes actifs (pas de 2h : non collecté / non activé).
    # 15m/30m/1h supportés via configs strategies/smart_money.yaml (optimizer_results).
    timeframes: List[str] = ["15m", "30m", "1h", "4h", "1d"]
    warmup_bars: int = 260

    param_space: Dict[str, List] = PARAM_SPACE
    fixed_params: Dict[str, Any] = FIXED_PARAMS

    def __init__(self):
        self._bt_signals: Optional[Dict[int, dict]] = None
        self._bt_events_opposite: Optional[Dict[int, str]] = None
        self._bt_close_ref: Optional[np.ndarray] = None
        self._ana_key: Optional[tuple] = None
        self._ana_res: Optional[dict] = None
        self._ana_aux: Optional[dict] = None

    def _p(self, params: dict = None) -> Dict[str, Any]:
        p = dict(self.fixed_params)
        for k, v in ((params or {}).get(self.name, {}) or {}).items():
            if k in p and v is not None:
                p[k] = v
        return p

    @staticmethod
    def _smc_params(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "swing_left":    int(p["swing_len"]),
            "swing_right":   int(p["swing_len"]),
            "eq_tol_atr":    float(p["eq_tol_atr"]),
            "disp_body_atr": float(p["disp_body_atr"]),
            "ob_lookback":   int(p["ob_lookback"]),
            "fvg_min_atr":   float(p["fvg_min_atr"]),
            "rb_wick_atr":   float(p.get("rb_wick_atr", 0.5)),
        }

    _signal_at = _sm_signals._signal_at
    _build_trade = _sm_signals._build_trade

    @staticmethod
    def _htf_ok(htf_mode: str, htf_t: int) -> tuple:
        long_ok = (htf_mode == "off") \
            or (htf_mode == "soft" and htf_t >= 0) \
            or (htf_mode == "strict" and htf_t == 1)
        short_ok = (htf_mode == "off") \
            or (htf_mode == "soft" and htf_t <= 0) \
            or (htf_mode == "strict" and htf_t == -1)
        return long_ok, short_ok

    @staticmethod
    def _dir_gate(side: str, trend: int, zone: str,
                  ema_ok: bool, htf_ok: bool) -> bool:
        if side == "long":
            return trend == 1 and zone != "discount" and ema_ok and htf_ok
        return trend == -1 and zone != "premium" and ema_ok and htf_ok

    @staticmethod
    def _choch_index_arrays(res: dict) -> tuple:
        cached = res.get("_choch_idx")
        if cached is None:
            evs = res["_all_struct_events"]
            cd = np.array(sorted(e["index"] for e in evs
                                 if e["kind"] == "CHoCH" and e["direction"] == "down"),
                          dtype=np.int64)
            cu = np.array(sorted(e["index"] for e in evs
                                 if e["kind"] == "CHoCH" and e["direction"] == "up"),
                          dtype=np.int64)
            cached = (cd, cu)
            res["_choch_idx"] = cached
        return cached

    @staticmethod
    def _vol_ratio_arr(df: pl.DataFrame) -> np.ndarray:
        return _vol_ratio(df, 20).fill_null(0.0).to_numpy().astype(float)

    @staticmethod
    def _ema_arr(df: pl.DataFrame, length: int) -> Optional[np.ndarray]:
        if length <= 0:
            return None
        return _ema_series(df["close"], length).to_numpy().astype(float)

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
