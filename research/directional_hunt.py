"""Chasse à l'edge DIRECTIONNEL : peut-on donner une probabilité de direction ?

Approche honnête : la direction non-conditionnelle à court terme est ~martingale
(efficience). Mais des poches CONDITIONNELLES, théoriquement fondées, existent.
On les mesure une à une (P(up|condition) vs baseline, z-score binomial), puis on
quantifie l'AUC directionnel atteignable en COMBINANT les meilleures (régression
logistique, split train/test honnête).

Batterie (toutes causales, info connue à la clôture de t, on prédit le signe du
rendement forward sur k barres) :
  1. Reversal de capitulation (bougie panique + volume) — Lo-MacKinlay/Lehmann
  2. Fade d'euphorie (bougie extrême haussière + volume)
  3. Momentum/reversal conditionné au VOLUME (continuation si vol fort)
  4. Momentum time-series (tendance alignée) — Moskowitz-Ooi-Pedersen
  5. Streaks de signe (N bougies consécutives)
  6. Saisonnalité : heure UTC (cycle funding 0/8/16), jour de semaine
  7. Position dans le range / survente en tendance
  8. Divergence prix/volume

Usage : python research/directional_hunt.py
"""
from __future__ import annotations
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_btc import load, enrich, ema  # noqa: E402

TFS = ("1h", "4h", "1d")
HZ = {"1h": (6, 24), "4h": (3, 6), "1d": (1, 3)}


def zscore(p_hat, p0, n):
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    return (p_hat - p0) / np.sqrt(p0 * (1 - p0) / n)


def edge_dir(d, cond, k):
    """P(up | cond) sur k barres + z-score vs baseline. side neutre (on mesure UP)."""
    fwd = np.sign(d["close"].shift(-k) / d["close"] - 1)
    base = (fwd[fwd.notna()] > 0).mean()
    m = cond & fwd.notna()
    f = fwd[m]
    if len(f) < 50:
        return None
    p = (f > 0).mean()
    return {"n": int(len(f)), "p_up": p * 100, "base": base * 100,
            "edge": (p - base) * 100, "z": zscore(p, base, len(f))}


def run_battery(tf):
    d = enrich(load(tf)).dropna(subset=["atr14", "ema50", "ema200", "adx14", "rsi14"]).reset_index(drop=True)
    d["dt"] = pd.to_datetime(d["time"])
    ret = d["ret"]
    sig = np.sign(ret)
    vol_std = ret.rolling(50).std()
    volr = d["volume"] / d["volume"].rolling(20).mean()
    up_tr = (d["close"] > d["ema50"]) & (d["ema50"] > d["ema200"])
    dn_tr = (d["close"] < d["ema50"]) & (d["ema50"] < d["ema200"])
    rng_lookback = 50
    roll_hi = d["high"].rolling(rng_lookback).max()
    roll_lo = d["low"].rolling(rng_lookback).min()
    range_pos = (d["close"] - roll_lo) / (roll_hi - roll_lo).replace(0, np.nan)

    print(f"\n{'═'*94}\n[{tf}]  {len(d)} bougies · horizons {HZ[tf]}")
    print(f"  {'condition directionnelle':<46}{'k':>3}{'n':>7}{'P(up)%':>8}"
          f"{'base%':>7}{'edge':>7}{'z':>7}")
    rows = []

    def show(name, cond, signed=False):
        for k in HZ[tf]:
            r = edge_dir(d, cond.fillna(False), k)
            if r is None:
                continue
            flag = "  <<<" if abs(r["z"]) >= 3 else ("  <" if abs(r["z"]) >= 2 else "")
            print(f"  {name:<46}{k:>3}{r['n']:>7}{r['p_up']:>8.1f}{r['base']:>7.1f}"
                  f"{r['edge']:>+7.1f}{r['z']:>+7.1f}{flag}")
            rows.append((name, k, r["edge"], r["z"], r["n"]))

    # 1. Reversal de capitulation
    show("1.Capitulation (r<-2σ + vol>2×)", (ret < -2 * vol_std) & (volr > 2.0))
    show("1b.Capitulation forte (r<-3σ)", (ret < -3 * vol_std) & (volr > 1.5))
    # 2. Fade euphorie
    show("2.Euphorie (r>+2σ + vol>2×)", (ret > 2 * vol_std) & (volr > 2.0))
    # 3. Momentum/reversal conditionné volume
    show("3a.UP bar + VOL fort (vol>2×)", (sig > 0) & (volr > 2.0))
    show("3b.UP bar + VOL faible (vol<0.7×)", (sig > 0) & (volr < 0.7))
    show("3c.DOWN bar + VOL fort (vol>2×)", (sig < 0) & (volr > 2.0))
    # 4. Momentum time-series (tendance alignée)
    show("4a.Trend up établi (c>ema50>ema200)", up_tr)
    show("4b.Trend down établi", dn_tr)
    # 5. Streaks
    show("5a.3 bougies DOWN consécutives", (sig < 0) & (sig.shift(1) < 0) & (sig.shift(2) < 0))
    show("5b.3 bougies UP consécutives", (sig > 0) & (sig.shift(1) > 0) & (sig.shift(2) > 0))
    # 6. Saisonnalité
    h = d["dt"].dt.hour
    show("6a.Heure funding (0/8/16 UTC)", h.isin([0, 8, 16]))
    show("6b.Lundi (dow=0)", d["dt"].dt.dayofweek == 0)
    show("6c.Weekend (sam/dim)", d["dt"].dt.dayofweek >= 5)
    # 7. Position range / survente
    show("7a.Survente RSI<30 en uptrend", (d["rsi14"] < 35) & up_tr)
    show("7b.Bas de range (range_pos<0.15)", range_pos < 0.15)
    show("7c.Haut de range (range_pos>0.85)", range_pos > 0.85)
    # 8. Divergence prix/volume
    show("8.Hausse sur volume faible", (sig > 0) & (volr < 0.8) & up_tr)
    return d, rows


def logistic_direction(d, tf):
    """Régression logistique (numpy) sur features causales → AUC directionnel OOS.
    Quantifie honnêtement la prédictibilité de la direction en combinant les edges."""
    k = HZ[tf][0]
    ret = d["ret"]
    vol_std = ret.rolling(50).std()
    volr = (d["volume"] / d["volume"].rolling(20).mean()).fillna(1.0)
    up_tr = ((d["close"] > d["ema50"]) & (d["ema50"] > d["ema200"])).astype(float)
    dn_tr = ((d["close"] < d["ema50"]) & (d["ema50"] < d["ema200"])).astype(float)
    roll_lo = d["low"].rolling(50).min(); roll_hi = d["high"].rolling(50).max()
    range_pos = ((d["close"] - roll_lo) / (roll_hi - roll_lo).replace(0, np.nan)).fillna(0.5)
    h = pd.to_datetime(d["time"]).dt.hour
    feats = pd.DataFrame({
        "capitulation": ((ret < -2 * vol_std) & (volr > 2)).astype(float),
        "euphoria": ((ret > 2 * vol_std) & (volr > 2)).astype(float),
        "up_vol_strong": ((np.sign(ret) > 0) & (volr > 2)).astype(float),
        "trend_up": up_tr, "trend_dn": dn_tr,
        "rsi": (d["rsi14"] / 100.0).fillna(0.5),
        "range_pos": range_pos,
        "mom3": np.sign(ret).rolling(3).sum().fillna(0) / 3.0,
        "volr": np.clip(volr, 0, 5) / 5.0,
        "adx": (d["adx14"] / 50.0).clip(0, 1).fillna(0),
        "fund_hour": h.isin([0, 8, 16]).astype(float),
    })
    y = (d["close"].shift(-k) / d["close"] - 1)
    valid = feats.notna().all(axis=1) & y.notna()
    X = feats[valid].values
    yb = (y[valid].values > 0).astype(float)
    n = len(X)
    if n < 1000:
        return None
    # standardisation
    mu, sd = X.mean(0), X.std(0) + 1e-9
    X = (X - mu) / sd
    split = int(n * 0.7)
    Xtr, ytr, Xte, yte = X[:split], yb[:split], X[split:], yb[split:]
    # logistic GD
    w = np.zeros(X.shape[1]); b = 0.0; lr = 0.1
    for _ in range(2000):
        z = Xtr @ w + b
        p = 1 / (1 + np.exp(-z))
        g = p - ytr
        w -= lr * (Xtr.T @ g / len(ytr) + 1e-3 * w)
        b -= lr * g.mean()
    # AUC OOS (via rang de Mann-Whitney)
    pte = 1 / (1 + np.exp(-(Xte @ w + b)))
    pos = pte[yte == 1]; neg = pte[yte == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty_like(order, dtype=float); ranks[order] = np.arange(1, len(order) + 1)
    auc = (ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    # importance (poids absolus standardisés)
    imp = sorted(zip(feats.columns, w), key=lambda x: -abs(x[1]))[:5]
    return {"auc_oos": auc, "n_test": len(yte), "base": yte.mean() * 100,
            "top": imp}


def main():
    print("=" * 94)
    print("CHASSE À L'EDGE DIRECTIONNEL — P(up|condition) & AUC directionnel OOS combiné")
    print("=" * 94)
    all_rows = {}
    for tf in TFS:
        d, rows = run_battery(tf)
        lg = logistic_direction(d, tf)
        if lg:
            print(f"\n  ▶ Modèle logistique combiné [{tf}] — AUC directionnel OOS = "
                  f"{lg['auc_oos']:.4f}  (0.50=hasard) · base P(up)={lg['base']:.1f}% · "
                  f"n_test={lg['n_test']}")
            print(f"    top features : " +
                  ", ".join(f"{k}({w:+.2f})" for k, w in lg["top"]))
        all_rows[tf] = rows
    # Synthèse : meilleurs edges (|z|>=3, robustes)
    print("\n" + "=" * 94)
    print("EDGES DIRECTIONNELS ROBUSTES (|z| ≥ 3) — les seuls statistiquement sérieux :")
    found = False
    for tf in TFS:
        for name, k, edge, z, n in sorted(all_rows[tf], key=lambda x: -abs(x[3])):
            if abs(z) >= 3:
                found = True
                sens = "UP" if edge > 0 else "DOWN"
                print(f"   [{tf}] {name:<46} k={k} → biais {sens} "
                      f"(edge {edge:+.1f}pt, z={z:+.1f}, n={n})")
    if not found:
        print("   (aucun |z|≥3 — voir |z|≥2 ci-dessus)")


if __name__ == "__main__":
    main()
