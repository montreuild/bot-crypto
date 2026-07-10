#!/usr/bin/env python3
"""Analyse & optimisation automatisée des indicateurs sur un jeu OHLCV quelconque
(crypto, action, forex…). Reproduit la démarche de la campagne smart_money /
trend_rider : screening INDIVIDUEL de chaque indicateur comme signal autonome,
familles TENDANCE et RETOUR-À-LA-MOYENNE, avec sensibilité aux frais (taker vs
maker) et split IS/OOS — pour répondre vite à « ce symbole a-t-il un edge
exploitable, et de quel type ? ».

Usage :
    python scripts/analyze_indicators.py data/ohlcv/BTC_USDC/4h.parquet
    python scripts/analyze_indicators.py AAPL.csv --tf 1d --taker 0.0005 --maker 0.0
    python scripts/analyze_indicators.py data.parquet --oos-frac 0.33 --json out.json

CSV attendu : colonnes open/high/low/close/volume (+ éventuellement date/time),
insensible à la casse ; les alias usuels (Open, Adj Close, Date…) sont mappés.
"""
import argparse
import json
import os
import sys

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app.core.indicators as I                       # noqa: E402
from app.core.indicators_core import atr_wilder       # noqa: E402


# ── Chargement OHLCV générique ───────────────────────────────────────────────
_ALIASES = {
    "open": "open", "o": "open",
    "high": "high", "h": "high",
    "low": "low", "l": "low",
    "close": "close", "c": "close", "adj close": "close", "adj_close": "close",
    "volume": "volume", "vol": "volume", "v": "volume",
    "date": "time", "datetime": "time", "timestamp": "time", "time": "time",
}


def load_ohlcv(path: str) -> pl.DataFrame:
    if path.lower().endswith((".parquet", ".pq")):
        df = pl.read_parquet(path)
    else:
        df = pl.read_csv(path, try_parse_dates=True)
    ren = {c: _ALIASES[c.strip().lower()] for c in df.columns
           if c.strip().lower() in _ALIASES}
    df = df.rename(ren)
    need = {"open", "high", "low", "close"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"Colonnes OHLC manquantes : {missing} (colonnes : {df.columns})")
    if "volume" not in df.columns:
        df = df.with_columns(pl.lit(1.0).alias("volume"))   # actions sans volume
    df = df.with_columns([pl.col(k).cast(pl.Float64) for k in
                          ("open", "high", "low", "close", "volume")])
    return df.drop_nulls(["open", "high", "low", "close"])


# ── Batterie de signaux (edge-triggered {+1,0,-1}) ───────────────────────────
def _edge(cond) -> np.ndarray:
    a = np.asarray(cond, dtype=bool)
    out = np.zeros(len(a), dtype=np.int8)
    out[1:] = a[1:] & ~a[:-1]
    return out


def build_signals(df: pl.DataFrame):
    """Retourne {nom: (entrees{+1,0,-1}, kind, tp_arr|None)} ; kind ∈ trend|mr."""
    c = df["close"].to_numpy()
    e20 = I.ema(df["close"], 20).to_numpy(); e50 = I.ema(df["close"], 50).to_numpy()
    e200 = I.ema(df["close"], 200).to_numpy()
    rsi = I.rsi(df["close"], 14).to_numpy()
    ml, ms, mh = (x.to_numpy() for x in I.macd(df["close"]))
    bbu, bbm, bbl = (x.to_numpy() for x in I.bollinger(df["close"], 20, 2.0))
    adx_l, pdi, ndi = (x.to_numpy() for x in I.adx(df, 14))
    st_dir, _ = (x.to_numpy() for x in I.supertrend(df, 10, 3.0))
    dcu, dcl = (x.to_numpy() for x in I.donchian(df, 20))
    chop = I.choppiness(df, 14).fill_null(50.0).to_numpy()
    vw, vwu, vwd = (x.to_numpy() for x in I.vwap_bands(df, 20, 2.0))
    up = (c > e200) & (e20 > e50); dn = (c < e200) & (e20 < e50)
    trend_reg_L = up & (adx_l > 22) & (pdi > ndi) & (chop < 55)
    trend_reg_S = dn & (adx_l > 22) & (ndi > pdi) & (chop < 55)
    rng = chop > 55

    def sig(L, S):
        s = np.zeros(len(c), dtype=np.int8)
        s[_edge(L) == 1] = 1; s[_edge(S) == 1] = -1
        return s

    out = {
        # ── Tendance (sortie trailing) ──
        "TREND EMA200-align+long":  (sig(trend_reg_L, np.zeros(len(c), bool)), "trend", None),
        "TREND EMA200-align":       (sig(trend_reg_L, trend_reg_S), "trend", None),
        "TREND SuperTrend flip":    (sig(st_dir > 0, st_dir < 0), "trend", None),
        "TREND EMA20/50 cross":     (sig(e20 > e50, e20 < e50), "trend", None),
        "TREND MACD hist":          (sig(mh > 0, mh < 0), "trend", None),
        "TREND Donchian breakout":  (sig(c > np.roll(dcu, 1), c < np.roll(dcl, 1)), "trend", None),
        # ── Retour à la moyenne (sortie TP=moyenne) ──
        "MR BollingerLow→mid":      (sig((c < bbl) & rng, (c > bbu) & rng), "mr", bbm),
        "MR RSI30/70→mid":          (sig((rsi < 30) & rng, (rsi > 70) & rng), "mr", bbm),
        "MR VWAP2σ→vwap":           (sig((c < vwd) & rng, (c > vwu) & rng), "mr", vw),
    }
    return out


# ── Simulateurs (position unique, entrée au next open) ───────────────────────
def _sim(df, sig, kind, tp_arr, lo, hi, fee, spread,
         trail_k=3.5, stop_k=1.5, tstop_trend=24, tstop_mr=16):
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    atr = atr_wilder(df, 14).fill_null(0.0).to_numpy()
    pos = None; trades = []
    for j in range(max(1, lo + 1), hi):
        if pos:
            hh, ll = float(h[j]), float(l[j]); side = pos["side"]; bars = j - pos["bar"]
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
            else:  # mr
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
        return dict(n=0, pnl=0.0, pf=0.0, sharpe=0.0, wr=0.0)
    a = np.array(tr); w = a[a > 0]; gl = abs(a[a <= 0].sum())
    return dict(n=len(tr), pnl=round(float(a.sum()), 1),
                pf=round(float(w.sum() / gl) if gl > 0 else 9.99, 2),
                sharpe=round(float(a.mean() / a.std() * np.sqrt(len(a))) if a.std() > 0 else 0.0, 2),
                wr=round(float((a > 0).mean() * 100), 1))


# ── Orchestration ────────────────────────────────────────────────────────────
def analyze(df, taker, maker, oos_frac):
    n = df.height; split = int(n * (1 - oos_frac))
    spread = taker * 0.5
    rows = []
    for name, (sig, kind, tp) in build_signals(df).items():
        rec = {"signal": name, "kind": kind}
        for fee, ftag in ((taker, "taker"), (maker, "maker")):
            full = _stats(_sim(df, sig, kind, tp, 0, n, fee, spread))
            oos = _stats(_sim(df, sig, kind, tp, split, n, fee, spread))
            rec[ftag] = {"full": full, "oos": oos}
        rows.append(rec)
    rows.sort(key=lambda r: -r["maker"]["oos"]["pnl"])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", help="chemin OHLCV (.parquet ou .csv)")
    ap.add_argument("--tf", default="?", help="label du timeframe (affichage)")
    ap.add_argument("--taker", type=float, default=0.001, help="frais taker (défaut 0.001)")
    ap.add_argument("--maker", type=float, default=0.0004, help="frais maker (défaut 0.0004)")
    ap.add_argument("--oos-frac", type=float, default=0.33, help="fraction OOS finale (défaut 0.33)")
    ap.add_argument("--json", default=None, help="sauvegarde des résultats en JSON")
    a = ap.parse_args()

    df = load_ohlcv(a.data)
    rows = analyze(df, a.taker, a.maker, a.oos_frac)

    sym = os.path.basename(a.data)
    print(f"\n═══ {sym}  (tf={a.tf}, {df.height} barres, OOS={int(a.oos_frac*100)}%, "
          f"taker={a.taker:.4f} maker={a.maker:.4f}) ═══")
    print(f"{'signal':26s} {'kind':5s} │ {'FULL maker Σ/PF':>16s} │ "
          f"{'OOS taker Σ/PF':>16s} │ {'OOS maker Σ/PF':>16s}")
    print("─" * 92)
    best = None
    for r in rows:
        fm, ot, om = r["maker"]["full"], r["taker"]["oos"], r["maker"]["oos"]
        flag = ""
        if om["pnl"] > 0 and om["n"] >= 10 and om["pf"] > 1.0:
            flag = "  ✔ edge (maker)"
            if ot["pnl"] > 0 and ot["pf"] > 1.0:
                flag = "  ✔✔ edge (taker+maker)"
        if flag and best is None:
            best = r["signal"]
        print(f"{r['signal']:26s} {r['kind']:5s} │ {fm['pnl']:8.1f} {fm['pf']:5.2f}"
              f"     │ {ot['pnl']:8.1f} {ot['pf']:5.2f}     │ "
              f"{om['pnl']:8.1f} {om['pf']:5.2f}{flag}")
    print("─" * 92)
    if best:
        print(f"Verdict : edge OOS significatif (n≥10, PF>1) = « {best} »")
    else:
        pos = [r for r in rows if r["maker"]["oos"]["pnl"] > 0
               and r["maker"]["oos"]["pf"] > 1.0]
        if pos:
            b = pos[0]
            print(f"Verdict : pas d'edge SIGNIFICATIF ; meilleur candidat marginal = "
                  f"« {b['signal']} » (OOS maker PF {b['maker']['oos']['pf']}, "
                  f"n={b['maker']['oos']['n']} — trop peu de trades).")
        else:
            print("Verdict : AUCUN edge OOS positif — symbole/TF non exploitable "
                  "avec ces indicateurs.")

    if a.json:
        with open(a.json, "w") as f:
            json.dump({"symbol": sym, "tf": a.tf, "bars": df.height, "rows": rows}, f, indent=2)
        print(f"→ résultats écrits dans {a.json}")


if __name__ == "__main__":
    main()
