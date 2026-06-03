"""Stratégie Harmonic Regime — confluence régime-adaptative (data-driven).

Conçue à partir de l'analyse quantitative exhaustive de BTC 1h/4h/1d
(``research/analysis_btc.py``). Faits mesurés exploités :

  • LONG trend-momentum (close>EMA50>EMA200 + ADX + breakout) = edge forward
    positif robuste et multi-TF (t≈7-8), surtout en macro-bull.
  • Volatilité fortement clusterisée (ACF|r|≈0.15-0.28) mais direction quasi
    non-autocorrélée (VR≈1) → la volatilité pilote le TIMING (squeeze→expansion)
    et le SIZING (stop ATR), pas un edge directionnel linéaire.
  • Shorts structurellement faibles (dérive haussière + rebonds violents) :
    expectancy forward négative même en bear → shorts uniquement en macro-bear
    CONFIRMÉ, taille réduite, trailing serré (capture le skew gauche des krachs).
    Sinon : abstention (FLAT) = meilleure protection bear (évite le DD -72% B&H).
  • Mean-reversion long douce en range (RSI survente, hors tendance baissière).
  • Cycles FFT non déterministes (p>0.2) → phase spectrale = confirmation à
    faible poids, pas une horloge. Fibonacci = zones de confluence, pas un
    déclencheur isolé.

Décision = score de confluence pondéré, régime-adaptatif, ≥ seuil de qualité ;
abstention par défaut. Sizing par risque + stop ATR + trailing multi-phase
(infra ``TrailingStopManager`` réutilisée via ``trail_override``) + max-hold.

Trois setups, on garde le meilleur score :
  A. LONG_TREND     — momentum de tendance (cœur)
  B. LONG_MEANREV   — rebond de survente en range (secondaire, taille réduite)
  C. SHORT_DEF      — cassure en macro-bear confirmé (défensif, taille réduite)

Implémentée en plain ``BaseStrategy`` : lecture O(1) des colonnes ``_pre_*``
(via ``precompute_df`` idempotent) + features légères (Donchian/Bollinger/
médiane d'ATR%/cycle FFT/Fibonacci) calculées sur la queue de fenêtre. La FFT
est mémoïsée par pas (``cycle_stride``) pour garder le backtest 50k bougies
rapide sans recourir au hook ``prepare_for_backtest`` (réservé aux ML).
"""

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import precompute_df, pre_val

logger = logging.getLogger(__name__)


def _safe(v, fb=0.0) -> float:
    try:
        f = float(v)
        return fb if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return fb


class Strategy(BaseStrategy):
    name = "harmonic_regime"

    # TF recommandés = 4h / 1d (swing). L'analyse montre que sur 1h l'edge
    # directionnel (≈+0.1%/12 barres) est INFÉRIEUR au coût round-trip (~0.3%
    # taker+spread+borrow) → non rentable après frais. La stratégie reste
    # importable sur 1h mais n'y est pas recommandée (cf. research/).
    timeframes: List[str] = ["4h", "1d"]

    # Warmup : couvre SMA200 + pente + fenêtre de cycle.
    warmup_bars = 260

    param_space: Dict[str, List] = {
        "adx_min":          [18, 22, 25, 28],
        "slope_lookback":   [20, 40],
        "donchian":         [20, 30, 55],
        "rsi_pullback":     [38, 42, 46],
        "rsi_oversold":     [28, 30, 35],
        "vol_extreme_mult": [1.8, 2.2, 2.6],
        "sl_atr_long":      [1.3, 1.6, 2.0],
        "sl_atr_short":     [1.0, 1.2, 1.5],
        "max_hold":         [16, 24, 36, 48],
        "min_quality":      [0.56, 0.60, 0.64],
        "enable_short":     [True, False],
        "enable_meanrev":   [True, False],
        "use_cycle":        [True, False],
        "cooldown":         [0, 2, 4],
        "score_threshold":  [0.55, 0.60, 0.65],
    }

    fixed_params: Dict[str, Any] = {}

    # ── Defaults (issus de l'analyse + premier backtest) ──────────────────────
    _D = {
        "adx_min":          24.0,
        "slope_lookback":   20,
        "donchian":         20,
        "bb_period":        20,
        "bb_std":           2.0,
        "rsi_pullback":     42.0,
        "rsi_oversold":     32.0,
        "vol_extreme_mult": 2.2,
        "vol_med_window":   200,
        "fib_lookback":     120,
        "sl_atr_long":      1.8,
        "sl_atr_short":     1.2,
        "max_hold":         36,
        "max_hold_mr":      12,
        "min_quality":      0.62,
        "short_quality":    0.66,   # shorts plus sélectifs
        "enable_short":     True,
        "enable_meanrev":   True,
        "use_cycle":        True,
        "cooldown":         2,
        # cycle FFT
        "cycle_win":        180,
        "cycle_min":        10,
        "cycle_max":        90,
        "cycle_stride":     3,
        # trailing (bank fast mais laisse courir le clustering)
        "grace_bars":       3,
        "breakeven_r":      0.7,
        "lock_r":           1.5,
        "tight_r":          3.0,
        "trail_wide":       2.0,
        "trail_tight":      0.7,
    }

    def __init__(self):
        self._call: Dict[str, int] = {}
        self._last_entry: Dict[str, int] = {}
        # Cache cycle strié : symbol → (len_vu, dir, pos)
        self._cyc: Dict[str, tuple] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return max(int(self._D["vol_med_window"]),
                   int(p.get("cycle_win", self._D["cycle_win"]))) + 30

    # ── Cycle dominant (FFT) — confirmation douce, mémoïsée par pas ────────────
    def _cycle(self, close: np.ndarray, p: dict, sym: str):
        if not p["use_cycle"]:
            return 0, 0.0
        win = int(p["cycle_win"])
        n = len(close)
        if n < win:
            return 0, 0.0
        cached = self._cyc.get(sym)
        if cached is not None and (n - cached[0]) < int(p["cycle_stride"]):
            return cached[1], cached[2]
        x = close[-win:].astype(np.float64)
        t = np.arange(win)
        logp = np.log(np.maximum(x, 1e-9))
        detr = logp - np.polyval(np.polyfit(t, logp, 1), t)
        w = detr * np.hanning(win)
        fft = np.fft.rfft(w)
        freqs = np.fft.rfftfreq(win)
        amp = np.abs(fft)
        with np.errstate(divide="ignore", invalid="ignore"):
            periods = np.where(freqs > 0, 1.0 / freqs, np.inf)
        band = (periods >= p["cycle_min"]) & (periods <= p["cycle_max"])
        idx = np.where(band)[0]
        if len(idx) == 0:
            self._cyc[sym] = (n, 0, 0.0)
            return 0, 0.0
        k = idx[int(np.argmax(amp[idx]))]
        cur_phase = 2.0 * np.pi * float(freqs[k]) * (win - 1) + float(np.angle(fft[k]))
        cyc_dir = 1 if math.sin(cur_phase) < 0 else -1      # dérivée de cos : +1 montant
        cyc_pos = float(math.cos(cur_phase))                # +1 sommet, −1 creux
        self._cyc[sym] = (n, cyc_dir, cyc_pos)
        return cyc_dir, cyc_pos

    # ── Scoring ───────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = {**self._D, **((params or {}).get(self.name, {}))}
        sym = symbol or "default"

        if "_pre_atr14" not in df.columns:
            df = precompute_df(df)
        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        cnt = self._call.get(sym, 0) + 1
        self._call[sym] = cnt
        if cnt - self._last_entry.get(sym, -10**9) < int(p["cooldown"]):
            return self._none("Cooldown")

        close = df["close"]
        high = df["high"]
        low = df["low"]
        c = _safe(close[-1])
        atr = _safe(pre_val(df, "_pre_atr14"))
        if c <= 0 or atr <= 0:
            return self._none("Prix/ATR invalide")

        rsi = _safe(pre_val(df, "_pre_rsi14"), 50.0)
        adx = _safe(pre_val(df, "_pre_adx14"))
        pdi = _safe(pre_val(df, "_pre_pdi14"))
        ndi = _safe(pre_val(df, "_pre_ndi14"))
        ema20 = _safe(pre_val(df, "_pre_ema20"), c)
        ema50 = _safe(pre_val(df, "_pre_ema50"), c)
        ema200 = _safe(pre_val(df, "_pre_ema200"), c)
        macd_h = _safe(pre_val(df, "_pre_macd_hist"))
        volr = _safe(pre_val(df, "_pre_volratio20"), 1.0)

        sma200_s = df["_pre_sma200"]
        slb = int(p["slope_lookback"])
        sma_now = _safe(sma200_s[-1], c)
        sma_prev = _safe(sma200_s[-1 - slb] if len(sma200_s) > slb else sma200_s[0], sma_now)
        macd_prev = _safe(df["_pre_macd_hist"][-2] if len(df) > 1 else 0.0)

        # ── Features légères (queue de fenêtre) ───────────────────────────────
        N = int(p["donchian"])
        donch_hi = _safe(high[-1 - N:-1].max(), c)
        donch_lo = _safe(low[-1 - N:-1].min(), c)
        bb = int(p["bb_period"])
        bb_win = close[-bb:].to_numpy().astype(float)
        bb_mid = float(bb_win.mean())
        bb_sd = float(bb_win.std())
        bb_up = bb_mid + p["bb_std"] * bb_sd
        bb_lo = bb_mid - p["bb_std"] * bb_sd

        atr_s = df["_pre_atr14"].to_numpy().astype(float)
        c_s = close.to_numpy().astype(float)
        vw = int(p["vol_med_window"])
        atr_pct_tail = (atr_s[-vw:] / np.maximum(c_s[-vw:], 1e-9)) * 100.0
        vol_med = float(np.median(atr_pct_tail))
        atr_pct = atr / c * 100.0
        vol_low = atr_pct <= vol_med
        vol_extreme = atr_pct >= p["vol_extreme_mult"] * vol_med

        # Fibonacci (swing roulant) — zones de confluence
        flb = int(p["fib_lookback"])
        hh = _safe(high[-flb:].max(), c)
        ll = _safe(low[-flb:].min(), c)
        rng = max(hh - ll, 1e-9)
        fib = {r: hh - r * rng for r in (0.382, 0.5, 0.618)}
        fib_tol = 0.5 * atr
        near_fib_sup = any(abs(c - lv) <= fib_tol and c >= lv for lv in fib.values())
        near_fib_res = any(abs(c - lv) <= fib_tol and c <= lv for lv in fib.values())

        cyc_dir, cyc_pos = self._cycle(c_s, p, sym)

        # ── États de régime ───────────────────────────────────────────────────
        slope = sma_now - sma_prev
        macro_up = c > sma_now and slope > 0
        macro_dn = c < sma_now and slope < 0
        struct_up = c > ema50 > ema200
        struct_dn = c < ema50 < ema200
        strong = adx >= p["adx_min"]
        adx_str = max(0.0, min((adx - p["adx_min"]) / 25.0, 1.0))  # 0..1

        ctx = {
            "regime": ("trend_up" if (struct_up and strong) else
                       "trend_down" if (struct_dn and strong) else
                       "range" if adx < p["adx_min"] else "choppy"),
            "adx": round(adx, 1), "rsi": round(rsi, 1), "atr_pct": round(atr_pct, 3),
            "vol_med_pct": round(vol_med, 3), "macro": ("bull" if macro_up else
                                                        "bear" if macro_dn else "neutral"),
            "cycle_dir": cyc_dir, "cycle_pos": round(cyc_pos, 2),
            "slope": round(slope, 2), "volratio": round(volr, 2),
            "near_fib_sup": near_fib_sup, "near_fib_res": near_fib_res,
            "fib": {f"{k:.3f}": round(v, 2) for k, v in fib.items()},
            "donchian_hi": round(donch_hi, 2), "donchian_lo": round(donch_lo, 2),
            "bb_up": round(bb_up, 2), "bb_lo": round(bb_lo, 2),
        }

        candidates = []

        # ── A. LONG_TREND (cœur) ──────────────────────────────────────────────
        if struct_up and pdi >= ndi and strong:
            breakout = c > donch_hi or c > bb_up
            macd_up = macd_h > 0 and macd_h > macd_prev
            pullback = rsi <= p["rsi_pullback"] and macd_h >= macd_prev and c > ema50
            if breakout or macd_up or pullback:
                q = 0.40
                q += 0.10 if c > ema20 else 0.0
                q += 0.18 * adx_str
                q += 0.12 if breakout else 0.0
                q += 0.08 if macd_up else 0.0
                q += 0.06 if pullback else 0.0
                q += 0.06 if macro_up else 0.0
                q += 0.05 if volr >= 1.2 else 0.0
                if p["use_cycle"]:
                    q += 0.05 if cyc_dir > 0 else 0.0
                    q -= 0.05 if cyc_pos > 0.8 else 0.0   # proche d'un sommet de cycle
                q += 0.04 if (pullback and near_fib_sup) else 0.0
                q -= 0.08 if vol_extreme else 0.0          # ne pas chasser un parabolique
                trig = ("breakout" if breakout else "macd" if macd_up else "pullback")
                candidates.append(self._mk(
                    "long", q, "LONG_TREND", trig, p, atr, c,
                    sl_mult=p["sl_atr_long"], max_hold=p["max_hold"],
                    size=(1.0 if macro_up else 0.8) * (0.6 + 0.4 * adx_str),
                    min_q=p["min_quality"]))

        # ── C. SHORT_DEF (défensif, macro-bear confirmé) ──────────────────────
        if p["enable_short"] and struct_dn and ndi > pdi and strong and macro_dn:
            breakdown = c < donch_lo or c < bb_lo
            macd_dn = macd_h < 0 and macd_h < macd_prev
            if breakdown or macd_dn:
                q = 0.42
                q += 0.10 if c < ema20 else 0.0
                q += 0.18 * adx_str
                q += 0.12 if breakdown else 0.0
                q += 0.08 if macd_dn else 0.0
                q += 0.05 if volr >= 1.2 else 0.0
                if p["use_cycle"]:
                    q += 0.05 if cyc_dir < 0 else 0.0
                    q -= 0.05 if cyc_pos < -0.8 else 0.0  # proche d'un creux de cycle
                q += 0.04 if near_fib_res else 0.0
                candidates.append(self._mk(
                    "short", q, "SHORT_DEF",
                    ("breakdown" if breakdown else "macd"), p, atr, c,
                    sl_mult=p["sl_atr_short"], max_hold=p["max_hold"],
                    size=0.5 * (0.6 + 0.4 * adx_str),
                    min_q=p["short_quality"],
                    trail_tight=True))

        # ── B. LONG_MEANREV (rebond de survente en range, hors bear) ──────────
        if (p["enable_meanrev"] and not macro_dn and not struct_dn
                and adx < p["adx_min"]):
            if rsi <= p["rsi_oversold"] and (c <= bb_lo or near_fib_sup):
                q = 0.40
                q += min((p["rsi_oversold"] - rsi) / 20.0, 0.12)
                q += 0.08 if c <= bb_lo else 0.0
                q += 0.06 if near_fib_sup else 0.0
                q += 0.05 if macro_up else 0.0
                if p["use_cycle"]:
                    q += 0.05 if cyc_pos < -0.5 else 0.0  # bas de cycle
                q -= 0.06 if vol_extreme else 0.0
                candidates.append(self._mk(
                    "long", q, "LONG_MEANREV", "rsi_oversold", p, atr, c,
                    sl_mult=p["sl_atr_long"], max_hold=p["max_hold_mr"],
                    size=0.5, min_q=p["min_quality"]))

        valid = [x for x in candidates if x is not None]
        if not valid:
            return self._none(f"Pas de confluence ({ctx['regime']}/{ctx['macro']})",
                              ctx=ctx)
        best = max(valid, key=lambda x: x["score"])
        best["indicators"] = ctx
        self._last_entry[sym] = cnt
        return best

    # ── Construction du signal ────────────────────────────────────────────────
    def _mk(self, side, q, setup, trig, p, atr, c, sl_mult, max_hold, size,
            min_q, trail_tight=False) -> Optional[dict]:
        score = round(min(max(q, 0.0), 0.96), 3)
        if score < min_q:
            return None
        size_factor = round(max(0.2, min(size, 1.5)), 3)
        if trail_tight:
            trail = {"trail_wide": 1.5, "grace_bars": 2, "breakeven_r": 0.5,
                     "lock_r": 1.2, "tight_r": 2.2, "trail_tight": 0.5}
        else:
            trail = {"trail_wide": p["trail_wide"], "grace_bars": p["grace_bars"],
                     "breakeven_r": p["breakeven_r"], "lock_r": p["lock_r"],
                     "tight_r": p["tight_r"], "trail_tight": p["trail_tight"]}
        return {
            "score": score,
            "side": side,
            "name": self.name,
            "atr": atr,
            "sl_atr_mult": float(sl_mult),
            "exit_after_bars": int(max_hold),
            "trail_override": trail,
            "size_factor": size_factor,
            "setup": setup,
            "reason": (f"HarmonicRegime {side.upper()} | {setup} ({trig}) | "
                       f"q={score:.2f} size×{size_factor:.2f} "
                       f"SL={sl_mult:.1f}ATR hold≤{max_hold}b"),
            "conditions": [
                f"Setup {setup} déclencheur={trig}",
                f"Score qualité {score:.2f} ≥ seuil {min_q:.2f} ✓",
                f"SL={sl_mult:.1f}×ATR · trailing {'serré' if trail_tight else 'standard'}"
                f" · max-hold {max_hold} barres",
                f"Taille ×{size_factor:.2f} (risque 1%/trade)",
            ],
        }

    def _none(self, reason: str = "", ctx: dict = None) -> dict:
        d = {"score": 0, "side": "none", "name": self.name, "reason": reason}
        if ctx is not None:
            d["indicators"] = ctx
        return d
