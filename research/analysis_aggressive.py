"""Analyse ciblée « agressive » : les setups de GROS MOUVEMENT battent-ils les frais ?

Pour une stratégie agressive, ce qui compte n'est pas le hit-rate moyen mais la
capacité à capter de grosses continuations (queue droite) nettes de frais.
On mesure, par TF (15m/30m/1h/4h) :
  • breakout (Donchian) + surge de volume + expansion d'ATR → forward returns
    NETS de coût round-trip, P(net>0), queue droite (p90), et MFE/MAE.
  • effet d'un filtre d'alignement HTF (tendance EMA200 du même TF).
  • étude « let-it-run » : jusqu'où courent les gagnants (MFE) → calibre trailing.

NB : 15m ne couvre que ~1.5 an récent, 30m ~2.9 ans → couverture de régimes
limitée sur bas TF (pas de bear 2022). Validation bear → 1h/4h/1d.

Usage : python research/analysis_aggressive.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis_btc import enrich  # réutilise les helpers  # noqa: E402

UPLOAD = "/root/.claude/uploads/6ee1d036-9dde-49c8-ba23-22b93133ed2a"
FILES = {
    "15m": f"{UPLOAD}/e4172c1f-15m.parquet",
    "30m": f"{UPLOAD}/eac34547-30m.parquet",
    "1h":  f"{UPLOAD}/7591e47a-1h.parquet",
    "4h":  f"{UPLOAD}/2e2ebbb8-4h.parquet",
}
# Coût round-trip réaliste : taker 0.1%×2 + spread 0.05%×2 = 0.30 %
RC = 0.003
HORIZONS = {"15m": (8, 16, 32), "30m": (6, 12, 24), "1h": (4, 8, 16), "4h": (3, 6, 12)}


def _load(tf):
    df = pd.read_parquet(FILES[tf]).sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"])
    return enrich(df)


def fwd_stats(d, cond, k, rc=RC, side=1):
    fwd = (d["close"].shift(-k) / d["close"] - 1) * side
    m = cond & fwd.notna()
    f = fwd[m].values
    if len(f) < 30:
        return None
    net = f - rc
    return {"n": len(f), "p_net": (net > 0).mean() * 100, "mean": f.mean() * 100,
            "net_mean": net.mean() * 100, "p90": np.percentile(f, 90) * 100,
            "p10": np.percentile(f, 10) * 100}


def mfe_mae(d, cond, k, side=1):
    """MFE/MAE moyennes sur k barres après le signal (en %)."""
    idx = np.where(cond.values)[0]
    idx = idx[idx + k < len(d)]
    if len(idx) < 30:
        return None
    hi = d["high"].values
    lo = d["low"].values
    cl = d["close"].values
    mfes, maes = [], []
    for i in idx:
        entry = cl[i]
        win_hi = hi[i + 1:i + 1 + k]
        win_lo = lo[i + 1:i + 1 + k]
        if side == 1:
            mfes.append((win_hi.max() - entry) / entry * 100)
            maes.append((win_lo.min() - entry) / entry * 100)
        else:
            mfes.append((entry - win_lo.min()) / entry * 100)
            maes.append((entry - win_hi.max()) / entry * 100)
    return {"mfe": float(np.mean(mfes)), "mae": float(np.mean(maes)),
            "mfe_p75": float(np.percentile(mfes, 75))}


def main():
    print("=" * 92)
    print("ANALYSE AGRESSIVE — setups de gros mouvement, NETS de frais (RC=%.2f%%)" % (RC * 100))
    print("=" * 92)
    for tf in FILES:
        d = _load(tf)
        d = d.dropna(subset=["atr14", "sma200", "ema200", "adx14"]).reset_index(drop=True)
        khi = HORIZONS[tf]
        # Features de "burst"
        don_hi = d["high"].rolling(20).max().shift(1)
        don_lo = d["low"].rolling(20).min().shift(1)
        volr = d["volume"] / d["volume"].rolling(20).mean()
        atr_rising = d["atr14"] > d["atr14"].shift(3)
        htf_up = (d["close"] > d["ema200"]) & (d["ema200"] > d["ema200"].shift(20))
        htf_dn = (d["close"] < d["ema200"]) & (d["ema200"] < d["ema200"].shift(20))

        brk_up = (d["close"] > don_hi)
        brk_up_q = brk_up & (volr > 1.5) & atr_rising
        brk_up_qh = brk_up_q & htf_up
        brk_dn = (d["close"] < don_lo)
        brk_dn_qh = brk_dn & (volr > 1.5) & atr_rising & htf_dn

        print(f"\n{'─'*92}\n[{tf}]  {len(d)} bougies · {d['time'].iloc[0].date()} → "
              f"{d['time'].iloc[-1].date()}  · horizons {khi}")
        print(f"  {'setup':<34}{'k':>4}{'n':>7}{'P(net>0)%':>10}{'moy%':>8}"
              f"{'net%':>8}{'p90%':>8}  (queue droite)")
        rows = [
            ("Breakout Donchian20 (brut)", brk_up, 1),
            ("Breakout + vol×1.5 + ATR↑", brk_up_q, 1),
            ("Breakout + vol + ATR↑ + HTF↑", brk_up_qh, 1),
            ("Breakdown + vol + ATR↑ + HTF↓ (short)", brk_dn_qh, -1),
        ]
        for name, cond, side in rows:
            cond = cond.fillna(False)
            for k in khi:
                s = fwd_stats(d, cond, k, side=side)
                if s is None:
                    continue
                flag = "  <<< net+" if s["net_mean"] > 0 and s["p_net"] > 50 else ""
                print(f"  {name:<34}{k:>4}{s['n']:>7}{s['p_net']:>10.1f}"
                      f"{s['mean']:>8.2f}{s['net_mean']:>+8.2f}{s['p90']:>8.2f}{flag}")
        # Étude let-it-run (MFE/MAE) sur le meilleur setup qualité+HTF
        km = khi[-1]
        mm = mfe_mae(d, brk_up_qh.fillna(False), km, 1)
        if mm:
            rr = mm["mfe"] / abs(mm["mae"]) if mm["mae"] != 0 else 0
            print(f"  → let-it-run (Breakout+HTF, {km} barres) : "
                  f"MFE moy={mm['mfe']:+.2f}%  MAE moy={mm['mae']:+.2f}%  "
                  f"MFE p75={mm['mfe_p75']:+.2f}%  ratio MFE/MAE={rr:.2f}")
    print("\n" + "=" * 92)
    print("LECTURE : un setup agressif est viable si net% > 0 ET la queue droite (p90)")
    print("  >> frais → on coupe vite (stop serré ~MAE) et on laisse courir (trailing ~MFE).")
    print("  Le filtre HTF doit augmenter net% / p90. Bas TF = plus de signaux mais frais")
    print("  proportionnellement plus lourds → exiger vol-surge + expansion + alignement.")


if __name__ == "__main__":
    main()
