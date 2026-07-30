"""Fast Analyse & optimisation — screening rapide des indicateurs sur un OHLCV.

Pour une paire/timeframe donnée, teste chaque indicateur comme signal d'entrée
autonome (familles TENDANCE → sortie trailing, RETOUR-À-LA-MOYENNE → sortie
TP=moyenne), avec sensibilité aux frais (taker vs maker) et split IS/OOS. Sert
le cadre « Fast Analyse & optimisation » de la page Scanner (branché sur les
données déjà en cache — aucun fetch).
"""
from typing import Optional

import numpy as np
import polars as pl

import app.core.indicators as I
from app.core.config import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.core.indicators_core import atr_wilder


def _edge(cond) -> np.ndarray:
    a = np.asarray(cond, dtype=bool)
    out = np.zeros(len(a), dtype=np.int8)
    out[1:] = a[1:] & ~a[:-1]
    return out


def _smc_signals(df: pl.DataFrame):
    """Famille « smc » (SMC-09) : signaux du moteur SMC du bot lui-même —
    sweeps REJETÉS (achat après prise de liquidité sell-side, miroir short)
    et premiers retests d'order block. Edge-triggered par construction
    (une entrée par événement) ; TP = première cible de liquidité opposée
    (``smc.liquidity_targets_above/below``) — signal ignoré si aucune."""
    from app.core import smc as _smc
    n = len(df)
    res = _smc.analyze(df)
    c = df["close"].to_numpy()
    out = {}

    def _tp_for(i: int, direction: int):
        price = float(c[i])
        if direction > 0:
            lv = _smc.liquidity_targets_above(res, i, price)
            return lv[0] if lv else None
        lv = _smc.liquidity_targets_below(res, i, price)
        return lv[-1] if lv else None

    events = {
        "SMC sweep rejeté": [(ev["index"], 1 if ev["kind"] == "sell_side" else -1)
                             for ev in res["_all_sweeps"] if ev["rejected"]],
        "SMC OB retest":    [(ob["touched_at"], 1 if ob["kind"] == "bullish" else -1)
                             for ob in res["_all_obs"]
                             if ob["touched_at"] is not None
                             and (ob["invalidated_at"] is None
                                  or ob["invalidated_at"] > ob["touched_at"])],
    }
    for name, evs in events.items():
        s = np.zeros(n, dtype=np.int8)
        tp = np.full(n, np.nan)
        for i, direction in evs:
            if not (0 <= i < n):
                continue
            lvl = _tp_for(i, direction)
            if lvl is None:
                continue
            s[i] = direction
            tp[i] = lvl
        out[name] = (s, "smc", tp)
    return out


def build_signals(df: pl.DataFrame, scale: float = 1.0, include_smc: bool = True):
    """{nom: (entrees{+1,0,-1}, kind, tp_arr|None)} ; kind ∈ trend|mr|smc.

    ``scale`` (SMC-10) : facteur multiplicatif des périodes d'indicateurs
    (1.0 = défaut historique, byte-identique). La famille smc n'est incluse
    qu'à l'échelle 1.0 (le moteur SMC n'a pas de période à balayer)."""
    def P(x): return max(2, int(round(x * scale)))
    c = df["close"].to_numpy()
    e20 = I.ema(df["close"], P(20)).to_numpy()
    e50 = I.ema(df["close"], P(50)).to_numpy()
    e200 = I.ema(df["close"], P(200)).to_numpy()
    rsi = I.rsi(df["close"], P(14)).to_numpy()
    _, _, mh = (x.to_numpy() for x in I.macd(df["close"]))
    bbu, _, bbl = (x.to_numpy() for x in I.bollinger(df["close"], P(20), 2.0))
    adx_l, pdi, ndi = (x.to_numpy() for x in I.adx(df, P(14)))
    st_dir, _ = (x.to_numpy() for x in I.supertrend(df, P(10), 3.0))
    dcu, dcl = (x.to_numpy() for x in I.donchian(df, P(20)))
    chop = I.choppiness(df, P(14)).fill_null(50.0).to_numpy()
    bbm = I.bollinger(df["close"], P(20), 2.0)[1].to_numpy()
    vw, vwu, vwd = (x.to_numpy() for x in I.vwap_bands(df, P(20), 2.0))
    up = (c > e200) & (e20 > e50)
    dn = (c < e200) & (e20 < e50)
    trL = up & (adx_l > 22) & (pdi > ndi) & (chop < 55)
    trS = dn & (adx_l > 22) & (ndi > pdi) & (chop < 55)
    rng = chop > 55

    def sig(L, S):
        s = np.zeros(len(c), dtype=np.int8)
        s[_edge(L) == 1] = 1
        s[_edge(S) == 1] = -1
        return s

    out = {
        "TENDANCE EMA200 + long":   (sig(trL, np.zeros(len(c), bool)), "trend", None),
        "TENDANCE EMA200 align":    (sig(trL, trS), "trend", None),
        "TENDANCE SuperTrend flip": (sig(st_dir > 0, st_dir < 0), "trend", None),
        "TENDANCE EMA20/50 cross":  (sig(e20 > e50, e20 < e50), "trend", None),
        "TENDANCE MACD hist":       (sig(mh > 0, mh < 0), "trend", None),
        "TENDANCE Donchian break":  (sig(c > np.roll(dcu, 1), c < np.roll(dcl, 1)), "trend", None),
        "RETOUR Bollinger→mid":     (sig((c < bbl) & rng, (c > bbu) & rng), "mr", bbm),
        "RETOUR RSI30/70→mid":      (sig((rsi < 30) & rng, (rsi > 70) & rng), "mr", bbm),
        "RETOUR VWAP2σ→vwap":       (sig((c < vwd) & rng, (c > vwu) & rng), "mr", vw),
    }
    if include_smc and scale == 1.0:
        out.update(_smc_signals(df))
    return out


def _sim(df, sig, kind, tp_arr, lo, hi, fee, spread,
         trail_k=3.5, stop_k=1.5, tstop_trend=24, tstop_mr=16):
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    lw = df["low"].to_numpy()
    c = df["close"].to_numpy()
    atr = atr_wilder(df, 14).fill_null(0.0).to_numpy()
    pos = None
    trades = []
    for j in range(max(1, lo + 1), hi):
        if pos:
            hh, ll = float(h[j]), float(lw[j])
            side = pos["side"]
            bars = j - pos["bar"]
            ex = None
            if kind == "trend":
                fav = (hh - pos["entry"]) if side == "long" else (pos["entry"] - ll)
                if fav >= pos["risk"]:
                    pos["tr"] = True
                    a = float(atr[j]) or pos["risk"]
                    pos["sl"] = max(pos["sl"], hh - trail_k * a) if side == "long" \
                        else min(pos["sl"], ll + trail_k * a)
                hsl = (ll <= pos["sl"]) if side == "long" else (hh >= pos["sl"])
                if hsl:
                    ex = pos["sl"]
                elif bars >= tstop_trend and not pos.get("tr"):
                    ex = float(c[j])
            else:
                htp = (hh >= pos["tp"]) if side == "long" else (ll <= pos["tp"])
                hsl = (ll <= pos["sl"]) if side == "long" else (hh >= pos["sl"])
                if hsl:
                    ex = pos["sl"]
                elif htp:
                    ex = pos["tp"]
                elif bars >= tstop_mr:
                    ex = float(c[j])
            if ex is not None:
                trades.append(((ex - pos["entry"]) / pos["entry"] * 100
                               * (1 if side == "long" else -1)) - 2 * fee * 100)
                pos = None
        if pos is None and sig[j - 1] != 0:
            side = "long" if sig[j - 1] > 0 else "short"
            entry = float(o[j]) * (1 + spread if side == "long" else 1 - spread)
            a = float(atr[j - 1]) or 1e-9
            sl = entry - stop_k * a if side == "long" else entry + stop_k * a
            pos = {"side": side, "entry": entry, "sl": sl, "risk": stop_k * a, "bar": j}
            if kind in ("mr", "smc"):
                tp = float(tp_arr[j - 1])
                if (side == "long" and tp <= entry) or (side == "short" and tp >= entry):
                    pos = None
                    continue
                pos["tp"] = tp
    if pos:
        trades.append(((float(c[hi - 1]) - pos["entry"]) / pos["entry"] * 100
                       * (1 if pos["side"] == "long" else -1)) - 2 * fee * 100)
    return trades


def _stats(tr):
    if not tr:
        return {"n": 0, "pnl": 0.0, "pf": 0.0, "sharpe": 0.0, "wr": 0.0}
    a = np.array(tr)
    w = a[a > 0]
    gl = abs(a[a <= 0].sum())
    return {"n": len(tr), "pnl": round(float(a.sum()), 1),
            "pf": round(float(w.sum() / gl) if gl > 0 else 9.99, 2),
            "sharpe": round(float(a.mean() / a.std() * np.sqrt(len(a))) if a.std() > 0 else 0.0, 2),
            "wr": round(float((a > 0).mean() * 100), 1)}


def analyze(df: pl.DataFrame, taker: float = DEFAULT_TAKER_FEE,
            maker: float = DEFAULT_MAKER_FEE,
            oos_frac: float = 0.33, fee_grid: list = None,
            period_scales: tuple = None) -> dict:
    """Retourne {rows:[…], best:…} classés par PnL OOS maker décroissant.

    SMC-10 (opt-in, API par défaut inchangée) :
      - ``fee_grid``      : liste de (taker, maker) additionnels — chaque row
        gagne ``fee_grid: [{taker, maker, oos_taker, oos_maker}]`` (un edge
        visible seulement à un autre niveau de frais n'est plus manqué) ;
      - ``period_scales`` : échelles de période (ex. (0.5, 2.0)) — chaque
        signal indicateur est aussi testé avec ses périodes ×échelle
        (lignes « nom ×0.5 »)."""
    n = df.height
    if n < 260:
        return {"rows": [], "best": None, "error": "historique insuffisant (< 260 barres)"}
    split = int(n * (1 - oos_frac))
    spread = taker * 0.5
    all_signals = dict(build_signals(df))
    for sc in (period_scales or ()):
        if sc == 1.0:
            continue
        for nm, tpl in build_signals(df, scale=float(sc),
                                     include_smc=False).items():
            all_signals[f"{nm} ×{sc:g}"] = tpl
    rows = []
    for name, (sig, kind, tp) in all_signals.items():
        rec = {"signal": name, "kind": kind}
        for fee, tag in ((taker, "taker"), (maker, "maker")):
            rec[tag] = {"full": _stats(_sim(df, sig, kind, tp, 0, n, fee, spread)),
                        "oos": _stats(_sim(df, sig, kind, tp, split, n, fee, spread))}
        if fee_grid:
            rec["fee_grid"] = [
                {"taker": t, "maker": m,
                 "oos_taker": _stats(_sim(df, sig, kind, tp, split, n, t, t * 0.5)),
                 "oos_maker": _stats(_sim(df, sig, kind, tp, split, n, m, t * 0.5))}
                for t, m in fee_grid]
        rows.append(rec)
    rows.sort(key=lambda r: -r["maker"]["oos"]["pnl"])
    best: Optional[str] = None
    for r in rows:
        om, ot = r["maker"]["oos"], r["taker"]["oos"]
        if om["pnl"] > 0 and om["n"] >= 10 and om["pf"] > 1.0:
            best = r["signal"]
            r["edge"] = "taker+maker" if (ot["pnl"] > 0 and ot["pf"] > 1.0) else "maker"
            break
    return {"rows": rows, "best": best, "bars": n, "oos_bars": n - split}
