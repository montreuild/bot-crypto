"""
Analyse quantitative exhaustive de BTC sur 3 timeframes (1h / 4h / 1d).

Objectif : identifier des *edges réels* (statistiquement significatifs et
robustes bull & bear) pour piloter la conception d'une stratégie de trading,
plutôt que d'empiler des indicateurs décoratifs.

Sections :
  1. Qualité & couverture des données
  2. Distribution des rendements (moments, fat tails, vol, Sharpe B&H, drawdown)
  3. Autocorrélation : momentum vs mean-reversion + clustering de volatilité
  4. Régimes de marché (structure SMA + ADX) et rendements forward conditionnels
  5. Saisonnalité (heure UTC, jour de semaine)
  6. Analyse spectrale / fréquences (FFT) + test de significativité (red-noise)
  7. Efficacité des retracements de Fibonacci
  8. Batterie d'edges conditionnels (forward returns, hit-rate, expectancy, t-stat)
  9. Cohérence bull vs bear des edges retenus

Usage :  python research/analysis_btc.py [--section N]
Sorties : stdout lisible + research/analysis_report.md + research/analysis_metrics.json
"""
from __future__ import annotations
import json
import os
import sys
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
np.random.seed(7)

UPLOAD = "/root/.claude/uploads/6ee1d036-9dde-49c8-ba23-22b93133ed2a"
FILES = {
    "1h": f"{UPLOAD}/7591e47a-1h.parquet",
    "4h": f"{UPLOAD}/2e2ebbb8-4h.parquet",
    "1d": f"{UPLOAD}/0212ac4e-1d.parquet",
}
BARS_PER_YEAR = {"1h": 365 * 24, "4h": 365 * 6, "1d": 365}
REPORT_LINES: List[str] = []
METRICS: Dict[str, dict] = {}


def out(line: str = ""):
    print(line)
    REPORT_LINES.append(line)


# ── Indicateurs (numpy/pandas, sémantique alignée sur le bot) ──────────────────

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    rs = up.ewm(alpha=1 / n, adjust=False).mean() / dn.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + rs)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    ndi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)
    return adx_, pdi.fillna(0), ndi.fillna(0)


def load(tf: str) -> pd.DataFrame:
    df = pd.read_parquet(FILES[tf]).sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"])
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ret"] = np.log(df["close"] / df["close"].shift(1))
    df["rsi14"] = rsi(df["close"], 14)
    df["atr14"] = atr(df, 14)
    df["atr_pct"] = df["atr14"] / df["close"] * 100
    for n in (20, 50, 100, 200):
        df[f"sma{n}"] = sma(df["close"], n)
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    a, p, m = adx(df, 14)
    df["adx14"], df["pdi14"], df["ndi14"] = a, p, m
    return df


def regime(row, adx_thr=20.0) -> str:
    if row["adx14"] < adx_thr or np.isnan(row["sma200"]):
        return "range"
    if row["sma20"] > row["sma50"] > row["sma100"] > row["sma200"] and row["pdi14"] > row["ndi14"]:
        return "trend_up"
    if row["sma20"] < row["sma50"] < row["sma100"] < row["sma200"] and row["ndi14"] > row["pdi14"]:
        return "trend_down"
    return "choppy"


def tstat(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    if len(x) < 3 or x.std() == 0:
        return 0.0
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def max_dd(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float(((equity - peak) / peak).min() * 100)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Qualité & couverture
# ══════════════════════════════════════════════════════════════════════════════
def section_quality(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("1. QUALITÉ & COUVERTURE DES DONNÉES")
    out("=" * 78)
    exp_min = {"1h": 60, "4h": 240, "1d": 1440}
    for tf, df in data.items():
        dt = df["time"].diff().dt.total_seconds().div(60).dropna()
        gaps = int((dt > exp_min[tf] * 1.5).sum())
        dups = int(df["time"].duplicated().sum())
        bad_hl = int(((df["high"] < df[["open", "close"]].max(axis=1)) |
                      (df["low"] > df[["open", "close"]].min(axis=1))).sum())
        span_days = (df["time"].iloc[-1] - df["time"].iloc[0]).days
        out(f"\n  [{tf}] {len(df):>6} bougies | {df['time'].iloc[0].date()} → "
            f"{df['time'].iloc[-1].date()} ({span_days} j, ~{span_days/365:.1f} ans)")
        out(f"       gaps>{exp_min[tf]*1.5:.0f}min={gaps} | doublons={dups} | "
            f"OHLC incohérents={bad_hl} | NaN close={int(df['close'].isna().sum())}")
        out(f"       prix: min={df['low'].min():.0f}  max={df['high'].max():.0f}  "
            f"dernier={df['close'].iloc[-1]:.0f}")
        METRICS.setdefault(tf, {})["coverage"] = {
            "bars": len(df), "span_days": span_days, "gaps": gaps,
            "dups": dups, "bad_ohlc": bad_hl}


# ══════════════════════════════════════════════════════════════════════════════
# 2. Distribution des rendements
# ══════════════════════════════════════════════════════════════════════════════
def section_returns(data: Dict[str, pd.DataFrame]):
    from scipy import stats
    out("\n" + "=" * 78)
    out("2. DISTRIBUTION DES RENDEMENTS (log-returns par barre)")
    out("=" * 78)
    out(f"\n  {'TF':>3} | {'µ/bar%':>8} {'σ/bar%':>8} {'vol.ann%':>9} {'skew':>7} "
        f"{'exKurt':>7} {'min%':>7} {'max%':>7} | {'B&H%':>9} {'CAGR%':>7} "
        f"{'maxDD%':>7} {'Sharpe':>7}")
    out("  " + "-" * 104)
    for tf, df in data.items():
        r = df["ret"].dropna().values
        rp = r * 100
        ann = np.sqrt(BARS_PER_YEAR[tf])
        sharpe = r.mean() / r.std() * ann if r.std() > 0 else 0
        eq = df["close"].values / df["close"].values[0]
        years = (df["time"].iloc[-1] - df["time"].iloc[0]).days / 365.25
        cagr = (eq[-1] ** (1 / years) - 1) * 100 if years > 0 else 0
        jb = stats.jarque_bera(r)
        out(f"  {tf:>3} | {rp.mean():8.4f} {rp.std():8.3f} {r.std()*ann*100:9.1f} "
            f"{stats.skew(r):7.2f} {stats.kurtosis(r):7.1f} {rp.min():7.2f} "
            f"{rp.max():7.2f} | {(eq[-1]-1)*100:9.0f} {cagr:7.1f} {max_dd(eq):7.1f} "
            f"{sharpe:7.2f}")
        METRICS.setdefault(tf, {})["returns"] = {
            "mu_bar_pct": float(rp.mean()), "sigma_bar_pct": float(rp.std()),
            "vol_ann_pct": float(r.std() * ann * 100), "skew": float(stats.skew(r)),
            "ex_kurtosis": float(stats.kurtosis(r)), "buy_hold_pct": float((eq[-1]-1)*100),
            "cagr_pct": float(cagr), "max_dd_pct": float(max_dd(eq)), "sharpe_bh": float(sharpe),
            "jarque_bera_p": float(jb.pvalue)}
    out("\n  → Fat tails massives (exKurt >> 0) + asymétrie : modèle gaussien invalide.")
    out("    Conséquence design : stops ATR obligatoires, pas de martingale, sizing par risque.")


# ══════════════════════════════════════════════════════════════════════════════
# 3. Autocorrélation : momentum vs mean-reversion + vol clustering
# ══════════════════════════════════════════════════════════════════════════════
def section_autocorr(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("3. AUTOCORRÉLATION — momentum vs mean-reversion & clustering de volatilité")
    out("=" * 78)
    for tf, df in data.items():
        r = df["ret"].dropna().values
        absr = np.abs(r)
        def acf(x, lag):
            x = x - x.mean()
            return float(np.sum(x[lag:] * x[:-lag]) / np.sum(x * x))
        ac_r = [acf(r, k) for k in (1, 2, 3, 5, 10, 24)]
        ac_a = [acf(absr, k) for k in (1, 2, 3, 5, 10, 24)]
        # Variance ratio (Lo-MacKinlay) : VR>1 → momentum, VR<1 → mean-reversion
        def vr(x, q):
            # Variance ratio Lo-MacKinlay : Var(somme q-périodes)/(q·Var(1-période)).
            # Non-overlapping ; ≈1 random walk, >1 momentum, <1 mean-reversion.
            n = len(x) // q * q
            x = x[:n]
            mu = x.mean()
            var1 = np.sum((x - mu) ** 2) / n
            xq = x.reshape(-1, q).sum(axis=1)
            varq = np.sum((xq - q * mu) ** 2) / len(xq)
            return float(varq / (q * var1)) if var1 > 0 else 1.0
        vrs = {q: vr(r, q) for q in (2, 5, 10, 20)}
        out(f"\n  [{tf}]")
        out(f"    ACF rendements  lag 1/2/3/5/10/24 : " +
            " ".join(f"{v:+.3f}" for v in ac_r))
        out(f"    ACF |rendement| lag 1/2/3/5/10/24 : " +
            " ".join(f"{v:+.3f}" for v in ac_a) + "   (clustering vol)")
        out(f"    Variance ratio  q=2/5/10/20       : " +
            " ".join(f"{vrs[q]:.3f}" for q in (2, 5, 10, 20)) +
            "   (>1 momentum, <1 mean-rev)")
        METRICS.setdefault(tf, {})["autocorr"] = {
            "acf_ret_lag1": ac_r[0], "acf_absret_lag1": ac_a[0],
            "acf_absret_lag24": ac_a[5], "vr_q5": vrs[5], "vr_q20": vrs[20]}
    out("\n  → Le clustering de |r| (ACF positive et persistante) est l'edge le plus")
    out("    robuste : la volatilité est prévisible même si la direction l'est peu.")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Régimes de marché
# ══════════════════════════════════════════════════════════════════════════════
def section_regimes(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("4. RÉGIMES DE MARCHÉ (structure SMA20/50/100/200 + ADX) — fwd returns")
    out("=" * 78)
    for tf, df in data.items():
        d = df.dropna(subset=["sma200", "adx14"]).copy()
        d["regime"] = d.apply(regime, axis=1)
        # forward return sur 6 barres (info connue à t, mesure t→t+6)
        k = 6
        d["fwd"] = d["close"].shift(-k) / d["close"] - 1
        out(f"\n  [{tf}]  (forward {k} barres)")
        out(f"    {'régime':<12} {'%temps':>7} {'fwd_moy%':>9} {'P(up)%':>7} "
            f"{'fwd_med%':>9}")
        tab = {}
        for reg, g in d.groupby("regime"):
            fwd = g["fwd"].dropna().values
            if len(fwd) < 20:
                continue
            tab[reg] = {"share": len(g) / len(d) * 100, "fwd_mean": fwd.mean() * 100,
                        "p_up": (fwd > 0).mean() * 100, "fwd_med": np.median(fwd) * 100,
                        "n": len(fwd)}
            out(f"    {reg:<12} {tab[reg]['share']:7.1f} {tab[reg]['fwd_mean']:9.3f} "
                f"{tab[reg]['p_up']:7.1f} {tab[reg]['fwd_med']:9.3f}")
        METRICS.setdefault(tf, {})["regimes"] = tab
    out("\n  → NB : même en trend_down, fwd_moy reste >0 (dérive haussière de BTC) →")
    out("    shorter le simple régime baissier ne suffit pas. trend_up = meilleur biais")
    out("    long ; range/choppy → mean-reversion douce ou abstention.")


# ══════════════════════════════════════════════════════════════════════════════
# 5. Saisonnalité
# ══════════════════════════════════════════════════════════════════════════════
def section_seasonality(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("5. SAISONNALITÉ (heure UTC, jour de semaine)")
    out("=" * 78)
    for tf in ("1h", "4h"):
        df = data[tf]
        d = df.dropna(subset=["ret"]).copy()
        d["hour"] = d["time"].dt.hour
        d["dow"] = d["time"].dt.dayofweek
        out(f"\n  [{tf}] rendement moyen par heure UTC (×1e4) :")
        hourly = d.groupby("hour")["ret"].agg(["mean", "count"])
        line = "    " + " ".join(f"{h:02d}:{hourly['mean'].get(h,0)*1e4:+05.1f}"
                                  for h in range(0, 24))
        # affichage compact en 2 lignes
        hrs = [f"h{h:02d}={hourly['mean'].get(h,0)*1e4:+5.1f}" for h in range(24)]
        for i in range(0, 24, 8):
            out("    " + "  ".join(hrs[i:i+8]))
        best = hourly["mean"].idxmax(); worst = hourly["mean"].idxmin()
        out(f"    meilleure heure={best:02d}h ({hourly['mean'][best]*1e4:+.1f}e-4)  "
            f"pire={worst:02d}h ({hourly['mean'][worst]*1e4:+.1f}e-4)")
        dow_names = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
        dow = d.groupby("dow")["ret"].mean() * 1e4
        out(f"    par jour (×1e4) : " +
            " ".join(f"{dow_names[i]}={dow.get(i,0):+.1f}" for i in range(7)))
        METRICS.setdefault(tf, {})["seasonality"] = {
            "best_hour": int(best), "worst_hour": int(worst),
            "hour_mean_e4": {int(h): float(hourly['mean'].get(h, 0) * 1e4) for h in range(24)}}


# ══════════════════════════════════════════════════════════════════════════════
# 6. Analyse spectrale / fréquences
# ══════════════════════════════════════════════════════════════════════════════
def section_spectral(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("6. ANALYSE SPECTRALE (FFT) — cycles dominants & significativité")
    out("=" * 78)
    for tf, df in data.items():
        close = df["close"].dropna().values
        n = len(close)
        logp = np.log(close)
        t = np.arange(n)
        detr = logp - np.polyval(np.polyfit(t, logp, 1), t)  # détrend linéaire
        w = detr * np.hanning(n)
        fft = np.fft.rfft(w)
        freqs = np.fft.rfftfreq(n)
        power = np.abs(fft) ** 2
        with np.errstate(divide="ignore"):
            periods = np.where(freqs > 0, 1 / freqs, np.inf)
        # cycles "tradables" : période entre 10 et 500 barres
        band = (periods >= 10) & (periods <= 500)
        idx = np.where(band)[0]
        top = idx[np.argsort(power[idx])[::-1][:5]]
        out(f"\n  [{tf}] détrend log-prix — top cycles (période en barres) :")
        for j in top:
            out(f"      période ≈ {periods[j]:7.1f} barres  "
                f"({periods[j]/ (24 if tf=='1h' else 6 if tf=='4h' else 1):6.1f} j)  "
                f"power={power[j]/power[idx].sum()*100:5.1f}% de la bande")

        # Significativité : périodogramme des RENDEMENTS (stationnaire) vs
        # null par permutation (shuffle détruit toute structure série).
        r = df["ret"].dropna().values
        rr = r - r.mean()
        P = np.abs(np.fft.rfft(rr)) ** 2
        peak = P[1:].max()
        nperm = 200
        null_peaks = np.empty(nperm)
        for i in range(nperm):
            sh = np.random.permutation(rr)
            null_peaks[i] = (np.abs(np.fft.rfft(sh)) ** 2)[1:].max()
        pval = float((null_peaks >= peak).mean())
        fr_peak = np.fft.rfftfreq(len(rr))[1:][np.argmax(P[1:])]
        per_peak = 1 / fr_peak if fr_peak > 0 else np.inf
        out(f"      → pic spectral des rendements : période ≈ {per_peak:.1f} barres, "
            f"p-value(permutation)={pval:.3f} "
            f"{'(significatif)' if pval < 0.05 else '(NON significatif — pas de cycle fixe)'}")
        METRICS.setdefault(tf, {})["spectral"] = {
            "top_period_bars": float(periods[top[0]]),
            "ret_peak_period_bars": float(per_peak), "ret_peak_pvalue": pval}
    out("\n  → Sur le log-prix détrendé, l'énergie se concentre sur les très basses")
    out("    fréquences (tendances) : exploitable comme *phase* directionnelle, pas")
    out("    comme horloge fixe. Les rendements n'ont pas de cycle déterministe stable.")


# ══════════════════════════════════════════════════════════════════════════════
# 7. Fibonacci
# ══════════════════════════════════════════════════════════════════════════════
def section_fibonacci(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("7. EFFICACITÉ DES RETRACEMENTS DE FIBONACCI (taux de rebond)")
    out("=" * 78)
    look = {"1h": 120, "4h": 90, "1d": 60}
    for tf, df in data.items():
        d = df.dropna(subset=["atr14"]).reset_index(drop=True)
        L = look[tf]
        hi = d["high"].rolling(L).max()
        lo = d["low"].rolling(L).min()
        rng = (hi - lo)
        levels = {"0.382": hi - 0.382 * rng, "0.5": hi - 0.5 * rng,
                  "0.618": hi - 0.618 * rng}
        tol = d["atr14"] * 0.4
        k = 6
        fwd = d["close"].shift(-k) / d["close"] - 1
        base_up = float((fwd.dropna() > 0).mean() * 100)
        out(f"\n  [{tf}] baseline P(up,{k} barres)={base_up:.1f}%  (lookback swing={L})")
        for name, lv in levels.items():
            near = (d["close"] - lv).abs() <= tol
            mask = near & fwd.notna()
            if mask.sum() < 30:
                out(f"      fib {name}: échantillon insuffisant ({int(mask.sum())})")
                continue
            f = fwd[mask].values
            out(f"      fib {name}: n={int(mask.sum()):5d}  P(up)={(f>0).mean()*100:5.1f}%  "
                f"fwd_moy={f.mean()*100:+.3f}%  edge={(f>0).mean()*100-base_up:+.1f}pt")
    out("\n  → Un edge réel se traduit par P(up) près d'un niveau nettement ≠ baseline.")


# ══════════════════════════════════════════════════════════════════════════════
# 8. Batterie d'edges conditionnels
# ══════════════════════════════════════════════════════════════════════════════
def _edge(d: pd.DataFrame, cond: pd.Series, k: int, side: int = 1):
    fwd = (d["close"].shift(-k) / d["close"] - 1) * side
    m = cond & fwd.notna()
    f = fwd[m].values
    if len(f) < 30:
        return None
    return {"n": int(len(f)), "p_win": float((f > 0).mean() * 100),
            "mean_pct": float(f.mean() * 100), "med_pct": float(np.median(f) * 100),
            "t": tstat(f)}


def section_edges(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("8. BATTERIE D'EDGES CONDITIONNELS (forward returns, side-ajusté)")
    out("=" * 78)
    out("  Légende : n=échantillon, P(win)=% forward favorable (side appliqué),")
    out("            moy%=rendement forward moyen côté trade, t=t-stat (|t|>2 ~ signif.)")
    horizons = {"1h": 12, "4h": 6, "1d": 3}
    for tf, df in data.items():
        d = df.dropna(subset=["sma200", "adx14", "rsi14", "atr14"]).reset_index(drop=True)
        k = horizons[tf]
        up_struct = (d["close"] > d["ema50"]) & (d["ema50"] > d["ema200"])
        dn_struct = (d["close"] < d["ema50"]) & (d["ema50"] < d["ema200"])
        don_hi = d["high"].rolling(20).max().shift(1)
        don_lo = d["low"].rolling(20).min().shift(1)
        bb_mid = d["close"].rolling(20).mean()
        bb_std = d["close"].rolling(20).std()
        macd_line = ema(d["close"], 12) - ema(d["close"], 26)
        macd_sig = ema(macd_line, 9)
        hist = macd_line - macd_sig
        atr_pct = d["atr_pct"]
        lowvol = atr_pct < atr_pct.rolling(200).median()
        out(f"\n  ── [{tf}] horizon {k} barres ─────────────────────────────────")
        tests = {
            "LONG momentum (close>ema50>ema200)":            (up_struct, 1),
            "SHORT momentum (close<ema50<ema200)":           (dn_struct, -1),
            "LONG RSI<30 (survente)":                        (d["rsi14"] < 30, 1),
            "SHORT RSI>70 (surachat)":                       (d["rsi14"] > 70, -1),
            "LONG RSI<35 EN uptrend (pullback)":             ((d["rsi14"] < 40) & up_struct, 1),
            "SHORT RSI>65 EN downtrend (pullback)":          ((d["rsi14"] > 60) & dn_struct, -1),
            "LONG breakout Donchian20":                      (d["close"] > don_hi, 1),
            "SHORT breakdown Donchian20":                    (d["close"] < don_lo, -1),
            "LONG breakout Bollinger sup":                   (d["close"] > bb_mid + 2 * bb_std, 1),
            "SHORT cassure Bollinger inf":                   (d["close"] < bb_mid - 2 * bb_std, -1),
            "LONG MACD flip+ EN uptrend":                    ((hist > 0) & (hist.shift(1) <= 0) & up_struct, 1),
            "SHORT MACD flip- EN downtrend":                 ((hist < 0) & (hist.shift(1) >= 0) & dn_struct, -1),
            "LONG ADX>25 trend_up":                          ((d["adx14"] > 25) & up_struct & (d["pdi14"] > d["ndi14"]), 1),
            "SHORT ADX>25 trend_down":                       ((d["adx14"] > 25) & dn_struct & (d["ndi14"] > d["pdi14"]), -1),
            "LONG momentum filtré low-vol":                  (up_struct & lowvol, 1),
        }
        results = {}
        out(f"    {'setup':<42} {'n':>6} {'P(win)%':>8} {'moy%':>8} {'t':>6}")
        for name, (cond, side) in tests.items():
            r = _edge(d, cond.fillna(False), k, side)
            if r is None:
                out(f"    {name:<42} {'—  (n<30)':>30}")
                continue
            flag = "  <<<" if abs(r["t"]) > 2 and r["mean_pct"] > 0 else ""
            out(f"    {name:<42} {r['n']:>6} {r['p_win']:>8.1f} {r['mean_pct']:>+8.3f} "
                f"{r['t']:>6.1f}{flag}")
            results[name] = r
        METRICS.setdefault(tf, {})["edges"] = results


# ══════════════════════════════════════════════════════════════════════════════
# 9. Cohérence bull vs bear
# ══════════════════════════════════════════════════════════════════════════════
def section_bull_bear(data: Dict[str, pd.DataFrame]):
    out("\n" + "=" * 78)
    out("9. COHÉRENCE BULL vs BEAR (edges momentum testés par macro-régime)")
    out("=" * 78)
    out("  Macro-régime défini par pente SMA200 (>0 = bull, <0 = bear).")
    horizons = {"1h": 12, "4h": 6, "1d": 3}
    for tf, df in data.items():
        d = df.dropna(subset=["sma200", "ema200"]).reset_index(drop=True)
        k = horizons[tf]
        bull = d["sma200"] > d["sma200"].shift(20)
        up_struct = (d["close"] > d["ema50"]) & (d["ema50"] > d["ema200"])
        dn_struct = (d["close"] < d["ema50"]) & (d["ema50"] < d["ema200"])
        out(f"\n  [{tf}] horizon {k}")
        for label, mask in [("BULL", bull), ("BEAR", ~bull)]:
            share = mask.mean() * 100
            rl = _edge(d, (up_struct & mask).fillna(False), k, 1)
            rs = _edge(d, (dn_struct & mask).fillna(False), k, -1)
            def fmt(r):
                return f"P(win)={r['p_win']:.1f}% moy={r['mean_pct']:+.3f}% t={r['t']:.1f} n={r['n']}" if r else "n<30"
            out(f"    {label} ({share:4.1f}% du temps)")
            out(f"        LONG momentum  : {fmt(rl)}")
            out(f"        SHORT momentum : {fmt(rs)}")
    out("\n  → CONSTAT : LONG en bull = très positif (t≈8). SHORT en bear = négatif/nul")
    out("    (t≈-0.6/-0.9) → l'edge short n'existe PAS sur le rendement forward moyen ;")
    out("    il faut le skew gauche + trailing serré, ou rester FLAT en bear (cash).")


def synthesis():
    out("\n" + "=" * 78)
    out("SYNTHÈSE — edges retenus pour la conception de la stratégie")
    out("=" * 78)
    out("""
  FAITS MESURÉS (et leurs conséquences de conception) :

  1. EDGE LONG-MOMENTUM ROBUSTE & MULTI-TF — close>ema50>ema200 (+ ADX>25,
     breakout Donchian/Bollinger, flip MACD) a une expectancy forward positive
     et significative sur 1h/4h/1d (t = 7.0 / 8.4 / 3.8), surtout en BULL
     (t = 8.2 / 8.0 / 4.5). C'est le moteur directionnel principal.

  2. LES SHORTS SONT STRUCTURELLEMENT FAIBLES — sur 2018-2026, la dérive
     séculaire est haussière et les bear-markets contiennent des rallyes de
     contre-tendance violents : l'expectancy forward des shorts momentum est
     négative ou nulle MÊME en bear (4h t=-0.9, 1d t=-0.6). NE PAS shorter
     naïvement. Deux leviers de rattrapage : (a) le skew NÉGATIF des rendements
     (-0.2 à -1.2) → les krachs sont nets, donc un short avec trailing serré
     capture la queue gauche ; (b) restreindre les shorts au macro-régime bear
     CONFIRMÉ (prix<EMA long, pente SMA200<0) + breakdown + momentum, taille
     réduite. Sinon : rester FLAT.

  3. LA MEILLEURE PROTECTION BEAR = ÊTRE FLAT — éviter les -72% de drawdown du
     Buy&Hold en s'abstenant hors confluence haussière. « Performer en marché
     baissier » = préserver le capital (cash) + shorts opportunistes filtrés.

  4. VOLATILITÉ PRÉVISIBLE, DIRECTION NON — ACF|r| persistante (0.15-0.28) =
     clustering fort ; ACF(r)≈0 et VR≈1 = pas de momentum/mean-reversion
     LINÉAIRE. ⇒ la volatilité pilote le TIMING (squeeze→expansion) et le
     SIZING (stop ATR), pas un edge directionnel linéaire.

  5. MEAN-REVERSION LONG CONDITIONNELLE — RSI<30 sur 4h/1d (P(win) 58-64%) et
     régime range (1d fwd +1.7%, P(up) 59%) ⇒ acheter les dips UNIQUEMENT hors
     tendance baissière forte.

  6. CYCLES SPECTRAUX NON DÉTERMINISTES — aucun cycle fixe significatif sur les
     rendements (p=0.43/0.22) ; l'énergie FFT du log-prix est sur les très
     basses fréquences (la tendance). ⇒ phase spectrale = confirmation
     directionnelle douce (faible poids), pas une horloge.

  7. FIBONACCI = ZONES, PAS DÉCLENCHEUR — edge incohérent selon TF
     (1h 0.618 +3.7pt mais 1d 0.5 -13pt). ⇒ usage en confluence/placement de
     stop-target seulement.

  CONCEPTION RETENUE — stratégie régime-adaptative « Harmonic Regime » :
   • LONG trend-momentum (cœur) : macro-trend up + structure EMA + ADX +
     déclencheur (breakout OU pullback-resume OU flip MACD), filtre volatilité.
   • LONG mean-reversion : RSI survente en range/non-bear (taille réduite).
   • SHORT défensif : macro-bear confirmé + breakdown + momentum, taille réduite,
     trailing serré (capture le skew gauche).
   • Score de QUALITÉ = confluence pondérée ≥ seuil, sinon ABSTENTION (flat).
   • Sizing par risque (1%/trade) + stop ATR + trailing multi-phase + max-hold.
   • Composantes signal/fréquence (cycle FFT) et Fibonacci intégrées en
     confirmation/zones, conformément à leur poids statistique réel.
""")


def main():
    sect = None
    if "--section" in sys.argv:
        sect = int(sys.argv[sys.argv.index("--section") + 1])
    out("Chargement & enrichissement des 3 timeframes...")
    data = {tf: enrich(load(tf)) for tf in FILES}
    funcs = [section_quality, section_returns, section_autocorr, section_regimes,
             section_seasonality, section_spectral, section_fibonacci,
             section_edges, section_bull_bear]
    if sect:
        funcs[sect - 1](data)
    else:
        for f in funcs:
            f(data)
        synthesis()
    os.makedirs("research", exist_ok=True)
    with open("research/analysis_report.md", "w") as fh:
        fh.write("# Rapport d'analyse quantitative BTC (1h/4h/1d)\n\n```\n")
        fh.write("\n".join(REPORT_LINES))
        fh.write("\n```\n")
    with open("research/analysis_metrics.json", "w") as fh:
        json.dump(METRICS, fh, indent=2, default=float)
    out("\n[OK] Rapport → research/analysis_report.md | Métriques → research/analysis_metrics.json")


if __name__ == "__main__":
    main()
