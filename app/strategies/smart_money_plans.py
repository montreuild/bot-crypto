"""Méthodes score / trade_plans / early_exit de smart_money (ARCH-05 découpe)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core import smc

class _PlansMixin:
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = self._p(params)
        if len(df) < self.min_bars_required(params):
            return self._none("historique insuffisant")

        if self._cache_valid(df):
            sig = self._bt_signals.get(df.height - 1)
            return dict(sig) if sig else self._none("aucun setup SMC")

        win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
        res, aux = self._analyze_cached(win, p)
        i = len(win) - 1
        close = win["close"].to_numpy().astype(float)
        open_ = win["open"].to_numpy().astype(float)
        low   = win["low"].to_numpy().astype(float)
        high  = win["high"].to_numpy().astype(float)
        sig = self._signal_at(res, i, open_, high, low, close, aux, p)
        return sig if sig else self._none(
            f"aucun setup SMC (bias {res['bias']['label']})"
        )

    def trade_plans(self, df: pl.DataFrame, params: dict = None,
                    max_plans: int = 8) -> List[dict]:
        p = self._p(params)
        if len(df) < self.min_bars_required(params):
            return []
        win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
        res, aux = self._analyze_cached(win, p)
        i = len(win) - 1
        close = win["close"].to_numpy().astype(float)
        open_ = win["open"].to_numpy().astype(float)
        low   = win["low"].to_numpy().astype(float)
        high  = win["high"].to_numpy().astype(float)
        ema   = aux["ema"]
        atr   = float(res["_atr_arr"][i])
        price = float(close[i])
        if atr <= 0 or price <= 0:
            return []
        trend = int(res["_trend_arr"][i])
        pd_zone = smc.premium_discount_at(res, high, low, close, i) or {}
        zone = pd_zone.get("zone", "")
        long_ema_ok  = ema is None or price > float(ema[i])
        short_ema_ok = ema is None or price < float(ema[i])
        htf_mode = str(p.get("htf_filter", "off"))
        htf_t = int(aux["htf"][i]) if aux["htf"] is not None else 0
        long_htf_ok, short_htf_ok = self._htf_ok(htf_mode, htf_t)
        ext = aux.get("ext_trend")
        if ext is not None:
            et = int(ext[i])
            long_htf_ok = long_htf_ok and et >= 0
            short_htf_ok = short_htf_ok and et <= 0
        buf = float(p["sl_buffer_atr"]) * atr
        max_ob_age = int(p["ob_max_age"])
        plans: List[dict] = []

        times_arr = None
        if "time" in win.columns:
            try:
                times_arr = win["time"].dt.epoch(time_unit="s").to_list()
            except Exception:
                try:
                    times_arr = win["time"].to_list()
                except Exception:
                    times_arr = None

        def _plan_time(idx) -> Optional[int]:
            if times_arr is None or idx is None:
                return None
            k = int(idx)
            if not (0 <= k < len(times_arr)):
                return None
            v = times_arr[k]
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        def _add(plan: Optional[dict], status: str, trigger: str,
                 zone_lo=None, zone_hi=None, anchor=None):
            if plan is None:
                return
            entry = plan.get("entry")
            dist = (entry - price) / price * 100.0 if entry else 0.0
            plans.append({
                "status": status, "side": plan["side"], "setup": plan["setup"],
                "score_min": plan["score"],
                "entry": plan["entry"], "stop": plan["stop_hint"],
                "tp": plan["tp_hint"],
                "gain_pct": plan["indicators"]["gain_pct"],
                "rr": plan["indicators"]["rr"],
                "tp_source": plan["indicators"]["tp_source"],
                "distance_pct": round(dist, 3),
                "trigger": trigger, "reason": plan["reason"],
                "zone_low": zone_lo, "zone_high": zone_hi,
                "signal_time": _plan_time(anchor),
            })

        sig = self._signal_at(res, i, open_, high, low, close, aux, p)
        if sig is not None:
            sig = dict(sig)
            sig["entry"] = price
            _add(sig, "immediate",
                 "Déclenché sur la bougie courante — entrée au prochain open",
                 anchor=i)

        pending_zones = list(res["_all_obs"])
        if bool(p.get("use_rejection_blocks", False)):
            pending_zones += list(res["_all_rejections"])
        for ob in reversed(pending_zones):
            if ob["touched_at"] is not None or ob["invalidated_at"] is not None:
                continue
            if i - ob["created_at"] > max_ob_age:
                continue
            if ob["kind"] == "bullish" and price > ob["top"] \
                    and self._dir_gate("long", trend, zone, long_ema_ok, long_htf_ok):
                sc = 0.50 + 0.10 + (0.10 if ob.get("strength", 1) >= 2 else 0.0) \
                    + (0.10 if zone == "premium" else 0.0)
                plan = self._build_trade(
                    res, i, "long", float(ob["top"]),
                    float(ob["bottom"]) - buf, atr, p,
                    setup="OB_RETEST", score=round(min(sc, 1.0), 3),
                    detail=f"demande [{ob['bottom']:.6g}–{ob['top']:.6g}]",
                    trend=trend, zone=zone)
                if plan:
                    plan["entry"] = float(ob["top"])
                _add(plan, "pending",
                     f"Attendre le retour du prix dans la zone de demande "
                     f"[{ob['bottom']:.6g}–{ob['top']:.6g}] + bougie de rejet",
                     zone_lo=ob["bottom"], zone_hi=ob["top"],
                     anchor=ob["created_at"])
            elif ob["kind"] == "bearish" and price < ob["bottom"] \
                    and self._dir_gate("short", trend, zone, short_ema_ok, short_htf_ok):
                sc = 0.50 + 0.10 + (0.10 if ob.get("strength", 1) >= 2 else 0.0) \
                    + (0.10 if zone == "discount" else 0.0)
                plan = self._build_trade(
                    res, i, "short", float(ob["bottom"]),
                    float(ob["top"]) + buf, atr, p,
                    setup="OB_RETEST", score=round(min(sc, 1.0), 3),
                    detail=f"offre [{ob['bottom']:.6g}–{ob['top']:.6g}]",
                    trend=trend, zone=zone)
                if plan:
                    plan["entry"] = float(ob["bottom"])
                _add(plan, "pending",
                     f"Attendre le retour du prix dans la zone d'offre "
                     f"[{ob['bottom']:.6g}–{ob['top']:.6g}] + bougie de rejet",
                     zone_lo=ob["bottom"], zone_hi=ob["top"],
                     anchor=ob["created_at"])

        for pool in reversed(res["_all_pools"]):
            if pool["swept_at"] is not None or i - pool["formed_at"] > int(p["pool_max_age"]):
                continue
            lvl = float(pool["level"])
            if pool["kind"] == "sell_side" and lvl < price \
                    and self._dir_gate("long", trend, zone, long_ema_ok, long_htf_ok):
                sc = 0.50 + 0.10 + 0.10 + (0.10 if zone == "premium" else 0.0)
                plan = self._build_trade(
                    res, i, "long", lvl, lvl - 0.5 * atr - buf, atr, p,
                    setup="SWEEP_REVERSAL", score=round(min(sc, 1.0), 3),
                    detail=f"sweep pool {lvl:.6g}", trend=trend, zone=zone)
                if plan:
                    plan["entry"] = lvl
                _add(plan, "pending",
                     f"Attendre une mèche SOUS les equal lows {lvl:.6g} "
                     f"(×{len(pool['indices'])}) avec clôture au-dessus (rejet)",
                     zone_lo=pool["bottom"], zone_hi=pool["top"],
                     anchor=pool["formed_at"])
            elif pool["kind"] == "buy_side" and lvl > price \
                    and self._dir_gate("short", trend, zone, short_ema_ok, short_htf_ok):
                sc = 0.50 + 0.10 + 0.10 + (0.10 if zone == "discount" else 0.0)
                plan = self._build_trade(
                    res, i, "short", lvl, lvl + 0.5 * atr + buf, atr, p,
                    setup="SWEEP_REVERSAL", score=round(min(sc, 1.0), 3),
                    detail=f"sweep pool {lvl:.6g}", trend=trend, zone=zone)
                if plan:
                    plan["entry"] = lvl
                _add(plan, "pending",
                     f"Attendre une mèche AU-DESSUS des equal highs {lvl:.6g} "
                     f"(×{len(pool['indices'])}) avec clôture en dessous (rejet)",
                     zone_lo=pool["bottom"], zone_hi=pool["top"],
                     anchor=pool["formed_at"])

        plans.sort(key=lambda x: (x["status"] != "immediate",
                                  abs(x["distance_pct"])))
        return plans[:max_plans]

    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        p = self._p(params)
        ts_bars = int(p.get("time_stop_bars", 0) or 0)
        if bool(p.get("use_trailing", False)) and ts_bars > 0:
            bars_held = (df.height - 1) - int(position.get("bar", df.height))
            if bars_held >= ts_bars:
                ind = position.get("indicators") or {}
                risk_pct = float(ind.get("_risk_pct") or 0.0)
                if risk_pct <= 0:
                    st = position.get("_stop_trail") or []
                    e = float(position.get("entry") or 0.0)
                    if st and e > 0:
                        risk_pct = abs(e - float(st[0]["stop"])) / e * 100.0
                mfe = float(position.get("mfe", 0.0))
                if risk_pct > 0 and mfe < float(p.get("ts_profit_r", 1.0)) * risk_pct:
                    return "time_stop_stall"

        if not bool(p.get("choch_exit", True)):
            return None
        idx = df.height - 1
        direction = None
        if (self._bt_events_opposite is not None and self._bt_close_ref is not None
                and idx < len(self._bt_close_ref)
                and abs(float(df["close"][-1]) - self._bt_close_ref[idx]) < 1e-9):
            direction = self._bt_events_opposite.get(idx)
        else:
            win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
            res, _ = self._analyze_cached(win, p)
            last = len(win) - 1
            for ev in reversed(res["_all_struct_events"]):
                if ev["index"] < last:
                    break
                if ev["kind"] == "CHoCH":
                    direction = ev["direction"]
                    break
        if direction is None:
            return None
        side = position.get("side")
        if side == "long" and direction == "down":
            return "smc_choch_down"
        if side == "short" and direction == "up":
            return "smc_choch_up"
        return None
