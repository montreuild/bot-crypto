"""Méthodes d'analyse / backtest de smart_money (ARCH-05 découpe)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import polars as pl

from app.core import ict, smc
from app.core.indicators_core import choppiness as _choppiness
from app.core.indicators_core import engulfing as _engulfing
from app.core.indicators_core import pin_bar as _pin_bar
from app.core.timeframes import HTF_SECONDS_MAP as _HTF_SEC_MAP

logger = logging.getLogger(__name__)

class _AnalysisMixin:
    def _build_aux(self, win: pl.DataFrame, p: Dict[str, Any],
                   res: dict) -> Dict[str, Any]:
        """Séries auxiliaires par barre (toutes causales) consommées par
        ``_signal_at`` : volume ratio, EMA de biais, killzones, biais HTF et
        compression AMD (range des ``amd_bars`` barres PRÉCÉDENTES ≤ k×ATR)."""
        aux: Dict[str, Any] = {
            "volr": self._vol_ratio_arr(win),
            "ema":  self._ema_arr(win, int(p["ema_filter_len"])),
            "h": win["high"].to_numpy().astype(float),
            "l": win["low"].to_numpy().astype(float),
            "c": win["close"].to_numpy().astype(float),
            "v": win["volume"].to_numpy().astype(float),
        }
        if (p.get("kz_bonus") or p.get("kz_filter")) and "time" in win.columns:
            ep = win["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
            aux["kz"] = smc.killzone_flags(ep)
        else:
            aux["kz"] = None
        if str(p.get("htf_filter", "off")) != "off":
            aux["htf"], aux["htf_meta"] = smc.htf_trend_series(
                win, self._smc_params(p), mult=int(p.get("htf_mult", 4)),
                htf_sec_map=_HTF_SEC_MAP)
        else:
            aux["htf"], aux["htf_meta"] = None, None
        if p.get("amd_bonus"):
            if bool(p.get("amd_session_anchored", False)) and "time" in win.columns:
                ep = win["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
                hours = (ep // 3600) % 24
                day = ep // 86400
                kzf = smc.killzone_flags(ep).astype(bool)
                atr_arr = res["_atr_arr"]
                k = float(p["amd_range_atr"])
                comp = np.zeros(len(win), dtype=bool)
                starts = np.flatnonzero(np.diff(day, prepend=day[0] - 1))
                ends = np.append(starts[1:], len(win))
                for s, e in zip(starts, ends):
                    mask = hours[s:e] < 7
                    if not mask.any():
                        continue
                    rng = float(aux["h"][s:e][mask].max()
                                - aux["l"][s:e][mask].min())
                    for idx in range(s, e):
                        if (hours[idx] >= 7 and kzf[idx]
                                and rng <= k * float(atr_arr[idx])):
                            comp[idx] = True
                aux["comp"] = comp
            else:
                m = int(p["amd_bars"])
                hi = win["high"].rolling_max(m).shift(1)
                lo = win["low"].rolling_min(m).shift(1)
                rng = (hi - lo).fill_null(float("inf")).to_numpy().astype(float)
                aux["comp"] = rng <= float(p["amd_range_atr"]) * res["_atr_arr"]
        else:
            aux["comp"] = None
        if bool(p.get("ext_structure_filter", False)):
            ext_sp = dict(self._smc_params(p))
            L = int(p.get("ext_swing_len", 8))
            ext_sp["swing_left"] = ext_sp["swing_right"] = L
            aux["ext_trend"] = smc.analyze(win, ext_sp)["_trend_arr"]
        else:
            aux["ext_trend"] = None
        if float(p.get("chop_filter_max", 0.0)) > 0:
            aux["chop"] = _choppiness(win, int(p.get("chop_len", 14))) \
                .fill_null(50.0).to_numpy().astype(float)
        else:
            aux["chop"] = None
        if bool(p.get("candle_bonus", False)):
            aux["pin"] = _pin_bar(win).to_numpy().astype(np.int8)
            aux["eng"] = _engulfing(win).to_numpy().astype(np.int8)
        else:
            aux["pin"] = aux["eng"] = None
        if (bool(p.get("smt_bonus", False)) or bool(p.get("smt_filter", False))) \
                and str(p.get("smt_correlate_path", "")):
            aux["smt"] = smc.smt_series(win, str(p["smt_correlate_path"]),
                                        int(p.get("smt_lookback", 20)))
        else:
            aux["smt"] = None
        if p.get("use_calendar_liquidity", False) and "time" in win.columns:
            aux["cal"] = smc.calendar_liquidity_levels(win)
        else:
            aux["cal"] = None
        if (p.get("judas_bonus") or p.get("judas_filter")) and "time" in win.columns:
            ep = win["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
            aux["judas"] = ict.judas_swing(
                aux["h"], aux["l"], aux["c"], ep,
                open_hour=int(p.get("judas_open_hour", 8)),
                window=int(p.get("judas_window", 3)),
                lookback=int(p.get("judas_lookback", 12)))
        else:
            aux["judas"] = None
        if (p.get("sb_bonus") or p.get("sb_filter")) and "time" in win.columns:
            ep = win["time"].dt.epoch(time_unit="s").to_numpy().astype(np.int64)
            aux["sb"] = ict.silver_bullet_flags(ep)
        else:
            aux["sb"] = None
        return aux

    @staticmethod
    def _pkey(p: Dict[str, Any]) -> tuple:
        return tuple(sorted((k, str(v)) for k, v in p.items()))

    def _analyze_cached(self, win: pl.DataFrame, p: Dict[str, Any]):
        ts = None
        if "time" in win.columns and win.height:
            try:
                ts = int(win["time"][-1].timestamp())
            except (AttributeError, TypeError):
                ts = str(win["time"][-1])
        key = (win.height, ts, self._pkey(p)) if ts is not None else None
        if key is not None and key == self._ana_key and self._ana_res is not None:
            return self._ana_res, self._ana_aux
        res = smc.analyze(win, self._smc_params(p))
        aux = self._build_aux(win, p, res)
        if key is not None:
            self._ana_key, self._ana_res, self._ana_aux = key, res, aux
        return res, aux

    def min_bars_required(self, params: dict = None) -> int:
        return 220

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            params = getattr(self, "_bt_params", None)
            p = self._p(params)
            res = smc.analyze(df, self._smc_params(p))
            close = df["close"].to_numpy().astype(float)
            open_ = df["open"].to_numpy().astype(float)
            low   = df["low"].to_numpy().astype(float)
            high  = df["high"].to_numpy().astype(float)
            aux   = self._build_aux(df, p, res)

            signals: Dict[int, dict] = {}
            event_bars = sorted(
                {ev["index"] for ev in res["_all_sweeps"] if ev["rejected"]} |
                {ob["touched_at"] for ob in res["_all_obs"]
                 if ob["touched_at"] is not None} |
                {brk["touched_at"] for brk in res["_all_breakers"]
                 if brk["touched_at"] is not None} |
                {rb["touched_at"] for rb in res["_all_rejections"]
                 if rb["touched_at"] is not None}
            )
            for i in event_bars:
                sig = self._signal_at(res, i, open_, high, low, close, aux, p)
                if sig is not None:
                    signals[i] = sig

            opposite: Dict[int, str] = {}
            for ev in res["_all_struct_events"]:
                if ev["kind"] == "CHoCH":
                    opposite[ev["index"]] = ev["direction"]

            self._bt_signals = signals
            self._bt_events_opposite = opposite
            self._bt_close_ref = close
        except Exception as e:
            logger.warning(f"[smart_money] prepare_for_backtest KO : {e}")
            self._bt_signals = None
            self._bt_events_opposite = None
            self._bt_close_ref = None

    def _cache_valid(self, df: pl.DataFrame) -> bool:
        if self._bt_signals is None or self._bt_close_ref is None:
            return False
        idx = df.height - 1
        ref = self._bt_close_ref
        return 0 <= idx < len(ref) and abs(float(df["close"][-1]) - ref[idx]) < 1e-9
