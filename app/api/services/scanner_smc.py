"""Payloads SMC du scanner : overlay graphique et rejeu (DETTE-04c).

Extraits de `scanner_service.py` (781 lignes). Les deux constructeurs partagent
la même analyse — c'est ce qui les met dans le même module plutôt que chacun
dans le sien.
"""
import logging

logger = logging.getLogger(__name__)


# ── Analyse Smart Money Concepts (overlay graphique scanner/replay) ─────────
def build_smc_payload(cfg: dict, df, symbol: str, tf: str) -> dict:
    """Analyse SMC complète (structure, liquidité, OB/FVG, sessions, HTF,
    signal smart_money) — payload brut prêt pour _clean/JSONResponse."""
    from app.core import smc
    from app.core.param_resolution import resolve_strategy_params
    from app.strategies.smart_money import Strategy as _SMCStrategy

    # Overlay optimizer_results du timeframe (même résolution que le live et
    # le backtest) → l'UI reflète la config RÉELLEMENT tradée par le bot.
    resolved_params = {"smart_money":
                       resolve_strategy_params(cfg, tf, symbol).get("smart_money", {})}
    # Une seule analyse partagée par la sérialisation ET par score/trade_plans
    # (via le cache d'instance de la stratégie) : len(df) ≤ 3000 = max_window,
    # donc la fenêtre de la stratégie == df.
    strat = _SMCStrategy()
    p_full = strat._p(resolved_params)
    res, res_aux = strat._analyze_cached(df, p_full)

    times = df["time"].dt.epoch(time_unit="s").to_list()
    n = len(df)
    last_t = int(times[-1])

    def _t(idx, default=None):
        try:
            return int(times[int(idx)])
        except (TypeError, ValueError, IndexError):
            return default

    # ── Zones (rectangles top/bottom bornés dans le temps) ───────────────
    order_blocks = [{
        "kind": ob["kind"], "top": ob["top"], "bottom": ob["bottom"],
        "time_start": _t(ob["index"]),
        "time_end":   _t(ob["invalidated_at"], last_t),
        "status": ("invalidated" if ob["invalidated_at"] is not None
                   else "touched" if ob["touched_at"] is not None
                   else "fresh"),
        "strength": ob["strength"],
    } for ob in res["order_blocks"][-14:]]

    pools = [{
        "kind": pool["kind"], "level": pool["level"],
        "top": pool["top"], "bottom": pool["bottom"],
        "time_start": _t(min(pool["indices"])),
        "time_end":   _t(pool["swept_at"], last_t),
        "status": "swept" if pool["swept_at"] is not None else "active",
        "n_touches": len(pool["indices"]),
    } for pool in res["liquidity_pools"][-16:]]

    fvgs = [{
        "kind": fv["kind"], "top": fv["top"], "bottom": fv["bottom"],
        "time_start": _t(fv["index"]),
        "time_end":   _t(fv["filled_at"], last_t),
        "status": ("filled" if fv["filled_at"] is not None
                   else "mitigated" if fv["mitigated_at"] is not None
                   else "open"),
    } for fv in res["fvgs"][-14:]]

    voids = [{
        "kind": vd["kind"], "top": vd["top"], "bottom": vd["bottom"],
        "time_start": _t(vd["start_index"]),
        "time_end":   _t(vd["filled_at"], last_t),
        "status": ("filled" if vd["filled_at"] is not None
                   else "mitigated" if vd["mitigated_at"] is not None
                   else "open"),
    } for vd in res["liquidity_voids"][-12:]]

    breakers = [{
        "kind": brk["kind"], "top": brk["top"], "bottom": brk["bottom"],
        "time_start": _t(brk["created_at"]),
        "time_end":   _t(brk["invalidated_at"], last_t),
        "status": ("invalidated" if brk["invalidated_at"] is not None
                   else "touched" if brk["touched_at"] is not None
                   else "fresh"),
    } for brk in res["breakers"][-10:]]

    rejections = [{
        "kind": rb["kind"], "top": rb["top"], "bottom": rb["bottom"],
        "time_start": _t(rb["index"]),
        "time_end":   _t(rb["invalidated_at"], last_t),
        "status": ("invalidated" if rb["invalidated_at"] is not None
                   else "touched" if rb["touched_at"] is not None
                   else "fresh"),
    } for rb in res["rejection_blocks"][-10:]]

    # Volume profile (POC / HVN / LVN), session courante et biais HTF
    import numpy as _np
    _h = df["high"].to_numpy().astype(float)
    _l = df["low"].to_numpy().astype(float)
    _c = df["close"].to_numpy().astype(float)
    _v = df["volume"].to_numpy().astype(float)
    vp = smc.volume_profile(_h, _l, _c, _v, n - 1,
                            lookback=int(p_full.get("vp_lookback", 240)),
                            n_bins=int(p_full.get("vp_bins", 40)))
    vprofile = None
    if vp:
        vprofile = {"poc": round(vp["poc"], 8),
                    "hvns": [round(x, 8) for x in vp["hvns"][:8]],
                    "lvns": [round(x, 8) for x in vp["lvns"][:8]]}
    last_epoch = int(times[-1])
    kz_arr = smc.killzone_flags(_np.array([last_epoch], dtype=_np.int64))
    session = {"name": smc.session_label(last_epoch),
               "in_killzone": bool(kz_arr[0])}
    # Biais HTF : réutilise l'analyse de l'aux si disponible (mêmes params
    # swing que le signal), sinon calcule avec _smc_params (cohérence).
    htf_meta = (res_aux or {}).get("htf_meta")
    if htf_meta is None:
        from app.core.timeframes import HTF_SECONDS_MAP as _HTF_SEC_MAP
        _, htf_meta = smc.htf_trend_series(
            df, _SMCStrategy._smc_params(p_full),
            mult=int(p_full.get("htf_mult", 4)), htf_sec_map=_HTF_SEC_MAP)
    htf_bias = {
        "trend": htf_meta["trend"],
        "label": {1: "haussier", -1: "baissier", 0: "neutre"}[htf_meta["trend"]],
        "n_htf": htf_meta["n_htf"],
    }

    # Zigzag de structure (peaks/troughs) + projection de cycle
    structure_line = [{
        "time": _t(pt["index"]), "price": pt["price"],
        "kind": pt["kind"], "label": pt["label"],
    } for pt in res["structure_line"][-40:] if _t(pt["index"]) is not None]
    cycle = None
    if res["cycle"]:
        cy = res["cycle"]
        cycle = {
            "phase": cy["phase"], "boundary": cy["boundary"],
            "target": cy["target"], "progress": cy["progress"],
            "from_time": _t(cy["from_index"]),
            "from_price": cy["from_price"],
        }

    # ── Markers (structure + sweeps + swings labellisés) ─────────────────
    markers = []
    for ev in res["structure_events"][-30:]:
        markers.append({
            "time": _t(ev["index"]), "type": ev["kind"],
            "direction": ev["direction"], "level": ev["level"],
        })
    for ev in res["sweeps"][-20:]:
        markers.append({
            "time": _t(ev["index"]), "type": "SWEEP",
            "direction": "down" if ev["kind"] == "sell_side" else "up",
            "level": ev["level"], "rejected": ev["rejected"],
            "source": ev["source"],
        })
    swing_marks = [{
        "time": _t(sw["index"]), "type": "SWING",
        "label": sw["label"], "price": sw["price"], "kind": sw["kind"],
    } for sw in res["swings"][-24:] if sw["label"]]

    # ── Trendlines + canal ────────────────────────────────────────────────
    trendlines = [{
        "kind": t["kind"],
        "time1": _t(t["x1"]), "y1": t["y1"],
        "time2": _t(t["x2"]), "y2": t["y2"],
    } for t in res["trendlines"]]
    channel = None
    if res["channel"]:
        ch = res["channel"]
        channel = {
            "time_start": _t(ch["start_index"]),
            "time_end":   _t(ch["end_index"]),
            "mid_start":  ch["mid_start"], "mid_end": ch["mid_end"],
            "half_width": ch["half_width"],
        }

    # ── Signal courant + plans de trade de la stratégie smart_money ──────
    # Réutilisent le cache d'analyse de `strat` (fenêtre == df) et les mêmes
    # params résolus par TF → aucune analyse redondante, cohérence UI/live.
    signal = None
    trade_plans = []
    try:
        trade_plans = strat.trade_plans(df, resolved_params)
        sig = strat.score(df, resolved_params)
        if sig.get("side") not in (None, "none"):
            signal = {
                "side":   sig["side"],
                "score":  sig.get("score"),
                "setup":  sig.get("setup"),
                "entry":  round(float(df["close"][-1]), 8),
                "stop":   sig.get("stop_hint"),
                "tp":     sig.get("tp_hint"),
                "reason": sig.get("reason", ""),
                **{k: (sig.get("indicators") or {}).get(k)
                   for k in ("gain_pct", "rr", "pd_zone", "tp_source")},
            }
        else:
            signal = {"side": "none", "reason": sig.get("reason", "")}
    except Exception as e:
        logger.warning(f"[smc] signal stratégie KO : {e}")

    # Bougies pour le chart (Smart Graph) — sans ceci l'UI n'affiche rien.
    ohlcv = {
        "time":   [int(t) for t in times],
        "open":   [round(float(v), 8) for v in df["open"].to_list()],
        "high":   [round(float(v), 8) for v in df["high"].to_list()],
        "low":    [round(float(v), 8) for v in df["low"].to_list()],
        "close":  [round(float(v), 8) for v in df["close"].to_list()],
        "volume": [round(float(v), 4) for v in df["volume"].to_list()],
    }

    return {
        "symbol": symbol, "timeframe": tf, "n_bars": n,
        "ohlcv": ohlcv,
        "bias": res["bias"],
        "premium_discount": res["premium_discount"],
        "order_blocks": order_blocks,
        "liquidity_pools": pools,
        "fvgs": fvgs,
        "liquidity_voids": voids,
        "breakers": breakers,
        "rejection_blocks": rejections,
        "volume_profile": vprofile,
        "session": session,
        "htf_bias": htf_bias,
        "structure_line": structure_line,
        "cycle": cycle,
        "markers": markers,
        "swing_labels": swing_marks,
        "trendlines": trendlines,
        "channel": channel,
        "signal": signal,
        "trade_plans": trade_plans,
    }


# ── Smart replay : payload complet pour rejeu bougie par bougie ─────────────
def build_smc_replay_payload(cfg: dict, df, symbol: str, tf: str) -> dict:
    """Payload de rejeu Smart Money : moteur causal + trades du VRAI
    Backtester avec les paramètres résolus du TF/symbole."""
    from app.core import smc
    from app.core.param_resolution import resolve_strategy_params
    from app.engine.backtest import Backtester as _Backtester
    from app.engine.engine import Engine as _Engine
    from app.strategies.smart_money import Strategy as _SMCStrategy

    n = len(df)
    times = df["time"].dt.epoch(time_unit="s").to_list()

    # Paramètres résolus (base YAML + overlay optimizer_results du TF/symbole)
    resolved = resolve_strategy_params(cfg, tf, symbol)
    p_strat  = {**_SMCStrategy.fixed_params,
                **{k: v for k, v in (resolved.get("smart_money") or {}).items()
                   if v is not None}}
    from app.core.timeframes import HTF_SECONDS_MAP as _HTF_SEC_MAP
    res = smc.analyze(df, _SMCStrategy._smc_params(p_strat))
    htf_arr, htf_meta = smc.htf_trend_series(
        df, _SMCStrategy._smc_params(p_strat),
        mult=int(p_strat.get("htf_mult", 4)), htf_sec_map=_HTF_SEC_MAP)

    # Trades réels : Backtester (mêmes coûts/priorités que le backtest)
    eng = _Engine()
    eng.register(_SMCStrategy(), silent=True)
    bt_res = _Backtester(eng, cfg).run(df, symbol, timeframe=tf)
    def _trade_row(t: dict) -> dict:
        entry = t.get("entry")
        stop = t.get("stop")
        tp = t.get("take_profit")
        side = t.get("side")
        ind = t.get("indicators") or {}
        # Gain espéré / RR depuis le plan (indicateurs signal) ou calcul entry/SL/TP
        gain_pct = ind.get("gain_pct")
        rr = ind.get("rr")
        if gain_pct is None and entry and tp and entry > 0:
            try:
                gain_pct = abs(float(tp) - float(entry)) / float(entry) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                gain_pct = None
        if rr is None and entry and stop and tp:
            try:
                risk = abs(float(entry) - float(stop))
                reward = abs(float(tp) - float(entry))
                rr = (reward / risk) if risk > 0 else None
            except (TypeError, ValueError, ZeroDivisionError):
                rr = None
        bar = t.get("bar")
        # bar = index d'entrée côté backtester (souvent i+1) → clamp sur times
        signal_time = None
        if bar is not None and times:
            try:
                bi = int(bar)
                # position["bar"] = i+1 à l'ouverture → time de la bougie d'entrée
                idx = bi - 1 if bi >= 1 else bi
                idx = max(0, min(idx, len(times) - 1))
                signal_time = int(times[idx])
            except (TypeError, ValueError, IndexError):
                signal_time = None
        if signal_time is None and t.get("entry_time") is not None:
            # repli : ISO / string horodatée
            et = t.get("entry_time")
            try:
                if et is not None and hasattr(et, "timestamp"):
                    signal_time = int(et.timestamp())
                else:
                    from datetime import datetime
                    s = str(et).replace("Z", "+00:00")
                    signal_time = int(datetime.fromisoformat(s).timestamp())
            except Exception:
                signal_time = None
        score = t.get("score")
        return {
            "entry_bar": bar,
            "exit_bar": t.get("exit_bar"),
            "side": side,
            "setup": t.get("setup"),
            "entry": entry,
            "stop": stop,
            "tp": tp,
            "exit": t.get("exit"),
            "exit_reason": t.get("exit_reason"),
            "pnl": round(t.get("pnl") or 0.0, 4),
            "pnl_pct": t.get("pnl_pct"),
            "score": score,
            "score_min": score,
            "reason": t.get("reason", ""),
            "signal_time": signal_time,
            "gain_pct": round(float(gain_pct), 3) if gain_pct is not None else None,
            "rr": round(float(rr), 2) if rr is not None else None,
            "distance_pct": None,  # non applicable une fois le trade clôturé
        }

    trades = [_trade_row(t) for t in bt_res.trades]

    candles = [{
        "time": int(times[i]),
        "open":  round(float(df["open"][i]), 8),
        "high":  round(float(df["high"][i]), 8),
        "low":   round(float(df["low"][i]), 8),
        "close": round(float(df["close"][i]), 8),
    } for i in range(n)]

    def _swings():
        return [{"index": s["index"], "price": s["price"], "kind": s["kind"],
                 "label": s["label"], "confirmed_at": s["confirmed_at"],
                 "swept_at": s["swept_at"]} for s in res["_all_swings"]]

    def _pools():
        return [{"kind": x["kind"], "level": x["level"], "top": x["top"],
                 "bottom": x["bottom"], "formed_at": x["formed_at"],
                 "start_index": min(x["indices"]),
                 "n_touches": len(x["indices"]),
                 "swept_at": x["swept_at"]} for x in res["_all_pools"]]

    def _zones(lst, start_key="index"):
        return [{"kind": x["kind"], "top": x["top"], "bottom": x["bottom"],
                 "index": x[start_key], "created_at": x["created_at"],
                 "touched_at": x["touched_at"],
                 "invalidated_at": x["invalidated_at"],
                 "strength": x.get("strength", 1)} for x in lst]

    # Format colonnes (Smart Replay UI lit `ohlcv`) + liste d'objets (`candles`).
    ohlcv = {
        "time":   [int(times[i]) for i in range(n)],
        "open":   [round(float(df["open"][i]), 8) for i in range(n)],
        "high":   [round(float(df["high"][i]), 8) for i in range(n)],
        "low":    [round(float(df["low"][i]), 8) for i in range(n)],
        "close":  [round(float(df["close"][i]), 8) for i in range(n)],
        "volume": [round(float(df["volume"][i]), 4) for i in range(n)],
    }

    payload = {
        "symbol": symbol, "timeframe": tf, "n_bars": n,
        "start_index": max(260, int(_SMCStrategy.warmup_bars)),
        "ohlcv": ohlcv,
        "candles": candles,
        "swings": _swings(),
        "struct_events": res["_all_struct_events"],
        "sweeps": res["_all_sweeps"],
        "pools": _pools(),
        "order_blocks": _zones(res["_all_obs"]),
        "breakers": _zones(res["_all_breakers"]),
        "rejections": _zones(res["_all_rejections"]),
        "fvgs": [{"kind": x["kind"], "top": x["top"], "bottom": x["bottom"],
                  "index": x["index"], "mitigated_at": x["mitigated_at"],
                  "filled_at": x["filled_at"]} for x in res["_all_fvgs"]],
        "voids": [{"kind": x["kind"], "top": x["top"], "bottom": x["bottom"],
                   "start_index": x["start_index"], "end_index": x["end_index"],
                   "filled_at": x["filled_at"]} for x in res["_all_voids"]],
        "trend": [int(v) for v in res["_trend_arr"]],
        "htf_trend": [int(v) for v in htf_arr],
        "trades": trades,
        "params_used": {k: p_strat.get(k) for k in
                        ("min_score", "min_rr", "min_gain_pct", "htf_filter",
                         "sl_buffer_atr", "use_rejection_blocks", "kz_filter",
                         "kz_bonus", "amd_bonus", "vp_confluence")},
    }
    return payload


