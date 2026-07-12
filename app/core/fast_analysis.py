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


def build_signals(df: pl.DataFrame):
    """{nom: (entrees{+1,0,-1}, kind, tp_arr|None)} ; kind ∈ trend|mr."""
    c = df["close"].to_numpy()
    e20 = I.ema(df["close"], 20).to_numpy(); e50 = I.ema(df["close"], 50).to_numpy()
    e200 = I.ema(df["close"], 200).to_numpy()
    rsi = I.rsi(df["close"], 14).to_numpy()
    _, _, mh = (x.to_numpy() for x in I.macd(df["close"]))
    bbu, _, bbl = (x.to_numpy() for x in I.bollinger(df["close"], 20, 2.0))
    adx_l, pdi, ndi = (x.to_numpy() for x in I.adx(df, 14))
    st_dir, _ = (x.to_numpy() for x in I.supertrend(df, 10, 3.0))
    dcu, dcl = (x.to_numpy() for x in I.donchian(df, 20))
    chop = I.choppiness(df, 14).fill_null(50.0).to_numpy()
    bbm = I.bollinger(df["close"], 20, 2.0)[1].to_numpy()
    vw, vwu, vwd = (x.to_numpy() for x in I.vwap_bands(df, 20, 2.0))
    up = (c > e200) & (e20 > e50); dn = (c < e200) & (e20 < e50)
    trL = up & (adx_l > 22) & (pdi > ndi) & (chop < 55)
    trS = dn & (adx_l > 22) & (ndi > pdi) & (chop < 55)
    rng = chop > 55

    def sig(L, S):
        s = np.zeros(len(c), dtype=np.int8)
        s[_edge(L) == 1] = 1; s[_edge(S) == 1] = -1
        return s

    return {
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


def _sim(df, sig, kind, tp_arr, lo, hi, fee, spread,
         trail_k=3.5, stop_k=1.5, tstop_trend=24, tstop_mr=16):
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    lw = df["low"].to_numpy(); c = df["close"].to_numpy()
    atr = atr_wilder(df, 14).fill_null(0.0).to_numpy()
    pos = None; trades = []
    for j in range(max(1, lo + 1), hi):
        if pos:
            hh, ll = float(h[j]), float(lw[j]); side = pos["side"]; bars = j - pos["bar"]
            ex = None
            if kind == "trend":
                fav = (hh - pos["entry"]) if side == "long" else (pos["entry"] - ll)
                if fav >= pos["risk"]:
                    pos["tr"] = True; a = float(atr[j]) or pos["risk"]
                    pos["sl"] = max(pos["sl"], hh - trail_k * a) if side == "long" \
                        else min(pos["sl"], ll + trail_k * a)
                hsl = (ll <= pos["sl"]) if side == "long" else (hh >= pos["sl"])
                if hsl: ex = pos["sl"]
                elif bars >= tstop_trend and not pos.get("tr"): ex = float(c[j])
            else:
                htp = (hh >= pos["tp"]) if side == "long" else (ll <= pos["tp"])
                hsl = (ll <= pos["sl"]) if side == "long" else (hh >= pos["sl"])
                if hsl: ex = pos["sl"]
                elif htp: ex = pos["tp"]
                elif bars >= tstop_mr: ex = float(c[j])
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
            if kind == "mr":
                tp = float(tp_arr[j - 1])
                if (side == "long" and tp <= entry) or (side == "short" and tp >= entry):
                    pos = None; continue
                pos["tp"] = tp
    if pos:
        trades.append(((float(c[hi - 1]) - pos["entry"]) / pos["entry"] * 100
                       * (1 if pos["side"] == "long" else -1)) - 2 * fee * 100)
    return trades


def _stats(tr):
    if not tr:
        return {"n": 0, "pnl": 0.0, "pf": 0.0, "sharpe": 0.0, "wr": 0.0}
    a = np.array(tr); w = a[a > 0]; gl = abs(a[a <= 0].sum())
    return {"n": len(tr), "pnl": round(float(a.sum()), 1),
            "pf": round(float(w.sum() / gl) if gl > 0 else 9.99, 2),
            "sharpe": round(float(a.mean() / a.std() * np.sqrt(len(a))) if a.std() > 0 else 0.0, 2),
            "wr": round(float((a > 0).mean() * 100), 1)}


def analyze(df: pl.DataFrame, taker: float = DEFAULT_TAKER_FEE,
            maker: float = DEFAULT_MAKER_FEE,
            oos_frac: float = 0.33) -> dict:
    """Retourne {rows:[…], best:…} classés par PnL OOS maker décroissant."""
    n = df.height
    if n < 260:
        return {"rows": [], "best": None, "error": "historique insuffisant (< 260 barres)"}
    split = int(n * (1 - oos_frac)); spread = taker * 0.5
    rows = []
    for name, (sig, kind, tp) in build_signals(df).items():
        rec = {"signal": name, "kind": kind}
        for fee, tag in ((taker, "taker"), (maker, "maker")):
            rec[tag] = {"full": _stats(_sim(df, sig, kind, tp, 0, n, fee, spread)),
                        "oos": _stats(_sim(df, sig, kind, tp, split, n, fee, spread))}
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
