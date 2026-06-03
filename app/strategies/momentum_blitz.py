"""Stratégie Momentum Blitz — agressive, dynamique, plein capital (data-driven).

Pendant AGRESSIF de ``harmonic_regime`` : vise le rendement absolu maximal le
plus vite possible, en assumant un drawdown élevé. Conçue à partir de
``research/analysis_aggressive.py`` (edges de gros mouvement nets de frais).

Faits mesurés exploités :
  • L'edge breakout/momentum ne bat les frais (RC≈0.30%) qu'à partir de 1h
    (net +0.12%/16 barres) et surtout 4h (net +0.49%/12 barres) ; sur 15m/30m
    les frais dominent → TF d'exécution = 1h (rapide) et 4h.
  • Le filtre d'alignement HTF augmente nettement l'edge net (1h −0.03→+0.12).
  • Asymétrie forte : MFE/MAE≈1.24, queue droite p90 +3-6% vs MAE ~−1.7/−3.5%
    → COUPER VITE (stop serré) et LAISSER COURIR (trailing large) = capter la
    queue droite. C'est là qu'est le rendement.
  • Shorts négatifs même filtrés → long-biais (short désactivé par défaut).

Mécanique d'agression (dans le cadre du moteur) :
  • Déploiement PLEIN CAPITAL : ``size_factor`` 1.0→2.0 selon la conviction
    (surge de volume × expansion d'ATR × force ADX × alignement HTF). Combiné à
    un stop serré, le notional sature le cap (≈50%) puis ×size_factor → ~100%.
  • Entrées « ignition » : cassure Donchian + surge volume + expansion ATR +
    HTF aligné, OU continuation de tendance forte (EMA empilées + MACD + ADX).
  • Exits asymétriques : stop initial serré (``sl_atr_long``≈1.3×ATR), breakeven
    rapide puis trailing LARGE (``trail_wide``≈3×ATR, ``tight_r`` haut) + max-hold
    long → on laisse courir les gagnants.
  • Seuil de qualité plus bas que harmonic (plus de trades) mais gate
    vol-surge+HTF pour rester net-positif après frais.

⚠️  Risque assumé : drawdowns élevés. Le rendement absolu se règle via
    ``risk_per_trade`` et ``backtest.max_notional_pct`` (knobs de portefeuille) ;
    voir research/ pour les courbes rendement/DD/Monte-Carlo.

Implémentée en plain ``BaseStrategy`` : lecture O(1) des colonnes ``_pre_*``
(via ``precompute_df`` idempotent) + features légères de queue (Donchian,
expansion d'ATR). Pas de FFT dans la boucle chaude → backtest 50k bougies rapide.
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
    name = "momentum_blitz"

    # TF d'exécution = 4h (seul TF où l'edge breakout/momentum bat les frais +
    # le bruit, mesuré). 15m/30m/1h backtestés NÉGATIFS (frais > edge) → la
    # stratégie reste importable mais n'y est pas viable. « Rapide » sur BTC =
    # swing 4h (trades bouclés en ~1-4 jours), pas du scalping intraday.
    timeframes: List[str] = ["4h"]

    warmup_bars = 230

    param_space: Dict[str, List] = {
        "donchian":         [15, 20, 30],
        "vol_surge_min":    [1.3, 1.5, 1.8],
        "adx_min":          [15, 18, 22],
        "sl_atr_long":      [1.1, 1.3, 1.6],
        "sl_atr_short":     [1.0, 1.2],
        "trail_wide":       [2.5, 3.0, 4.0],
        "tight_r":          [4.0, 5.0, 6.0],
        "max_hold":         [16, 24, 36, 48],
        "min_quality":      [0.54, 0.57, 0.60],
        "max_size_factor":  [1.5, 1.8, 2.0],
        "enable_short":     [True, False],
        "enable_continuation": [True, False],
        "require_htf":      [True, False],
        "cooldown":         [0, 1, 2],
        "score_threshold":  [0.52, 0.55, 0.58],
    }

    fixed_params: Dict[str, Any] = {}

    _D = {
        "donchian":         20,
        "bb_period":        20,
        "vol_surge_min":    1.6,
        "adx_min":          22.0,
        "atr_expansion_lb": 3,
        "sl_atr_long":      1.3,
        "sl_atr_short":     1.2,
        "trail_wide":       3.0,     # trailing LARGE → laisse courir
        "breakeven_r":      0.5,     # protège vite
        "lock_r":           1.5,
        "tight_r":          5.0,     # reste large longtemps
        "trail_tight":      1.2,
        "grace_bars":       3,
        "max_hold":         30,
        "min_quality":      0.60,
        "short_quality":    0.62,
        "max_size_factor":  2.0,     # PLEIN CAPITAL sur forte conviction
        "base_size_factor": 1.0,
        "slope_lookback":   20,
        "enable_short":     False,   # shorts négatifs nets → off par défaut
        "enable_continuation": False, # continuation = piège à frais → off par défaut
        "cont_vol_min":     1.2,     # si activée, exiger un minimum de volume
        "require_htf":      True,
        "cooldown":         1,
    }

    def __init__(self):
        self._call: Dict[str, int] = {}
        self._last_entry: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        return 220

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
        macd_prev = _safe(df["_pre_macd_hist"][-2] if len(df) > 1 else 0.0)
        volr = _safe(pre_val(df, "_pre_volratio20"), 1.0)

        slb = int(p["slope_lookback"])
        sma200_s = df["_pre_sma200"]
        sma_now = _safe(sma200_s[-1], c)
        sma_prev = _safe(sma200_s[-1 - slb] if len(sma200_s) > slb else sma200_s[0], sma_now)

        # ── Features de queue ─────────────────────────────────────────────────
        N = int(p["donchian"])
        donch_hi = _safe(high[-1 - N:-1].max(), c)
        donch_lo = _safe(low[-1 - N:-1].min(), c)
        bb = int(p["bb_period"])
        bb_win = close[-bb:].to_numpy().astype(float)
        bb_up = float(bb_win.mean()) + 2.0 * float(bb_win.std())
        bb_lo = float(bb_win.mean()) - 2.0 * float(bb_win.std())
        lb = int(p["atr_expansion_lb"])
        atr_prev = _safe(df["_pre_atr14"][-1 - lb] if len(df) > lb else atr, atr)
        atr_expanding = atr > atr_prev
        atr_pct = atr / c * 100.0

        # ── États ─────────────────────────────────────────────────────────────
        slope = sma_now - sma_prev
        htf_up = c > ema200 and slope > 0
        htf_dn = c < ema200 and slope < 0
        strong = adx >= p["adx_min"]
        adx_str = max(0.0, min((adx - p["adx_min"]) / 25.0, 1.0))
        vol_surge = volr >= p["vol_surge_min"]
        vol_str = max(0.0, min((volr - 1.0) / 2.0, 1.0))   # 0..1 (volr 1→3)
        breakout = c > donch_hi or c > bb_up
        breakdown = c < donch_lo or c < bb_lo
        stacked_up = c > ema20 > ema50 > ema200
        stacked_dn = c < ema20 < ema50 < ema200
        macd_up = macd_h > 0 and macd_h > macd_prev
        macd_dn = macd_h < 0 and macd_h < macd_prev

        ctx = {
            "regime": ("up" if htf_up else "down" if htf_dn else "neutral"),
            "adx": round(adx, 1), "rsi": round(rsi, 1), "volratio": round(volr, 2),
            "atr_pct": round(atr_pct, 3), "atr_expanding": atr_expanding,
            "breakout": breakout, "stacked_up": stacked_up,
            "donchian_hi": round(donch_hi, 2), "donchian_lo": round(donch_lo, 2),
            "slope": round(slope, 2),
        }

        candidates = []

        # ── LONG IGNITION ─────────────────────────────────────────────────────
        htf_ok = htf_up or not p["require_htf"]
        if htf_ok and c > ema50 and strong:
            ignition = breakout and vol_surge and atr_expanding
            # Continuation = piège à frais si non gatée : exige volume + ADX réel.
            continuation = (p["enable_continuation"] and stacked_up and macd_up
                            and volr >= p["cont_vol_min"] and adx_str >= 0.3)
            if ignition or continuation:
                q = 0.42
                q += 0.14 * adx_str
                q += 0.12 if ignition else 0.0
                q += 0.08 if continuation else 0.0
                q += 0.10 * vol_str
                q += 0.06 if atr_expanding else 0.0
                q += 0.05 if htf_up else 0.0
                q += 0.04 if c > ema20 else 0.0
                # Conviction → taille (PLEIN CAPITAL si tous les feux verts)
                conv = (0.45 * adx_str + 0.35 * vol_str +
                        0.10 * (1.0 if atr_expanding else 0.0) +
                        0.10 * (1.0 if (breakout and htf_up) else 0.0))
                size = p["base_size_factor"] + conv * (p["max_size_factor"] - p["base_size_factor"])
                candidates.append(self._mk(
                    "long", q, "LONG_IGNITION" if ignition else "LONG_CONT",
                    ("breakout" if ignition else "trend"),
                    p, atr, size, p["sl_atr_long"], p["max_hold"], p["min_quality"]))

        # ── SHORT BLITZ (optionnel, négatif net → off par défaut) ─────────────
        if p["enable_short"] and (htf_dn or not p["require_htf"]) and c < ema50 and strong:
            ign_s = breakdown and vol_surge and atr_expanding
            cont_s = stacked_dn and macd_dn
            if ign_s or cont_s:
                q = 0.42 + 0.14 * adx_str + (0.12 if ign_s else 0.0) + 0.10 * vol_str
                q += 0.05 if htf_dn else 0.0
                conv = 0.5 * adx_str + 0.4 * vol_str
                size = 0.6 * (p["base_size_factor"] + conv *
                              (p["max_size_factor"] - p["base_size_factor"]))
                candidates.append(self._mk(
                    "short", q, "SHORT_BLITZ", ("breakdown" if ign_s else "trend"),
                    p, atr, size, p["sl_atr_short"], p["max_hold"], p["short_quality"]))

        valid = [x for x in candidates if x is not None]
        if not valid:
            return self._none(f"Pas d'ignition ({ctx['regime']})", ctx=ctx)
        best = max(valid, key=lambda x: x["score"])
        best["indicators"] = ctx
        self._last_entry[sym] = cnt
        return best

    def _mk(self, side, q, setup, trig, p, atr, size, sl_mult, max_hold, min_q) -> Optional[dict]:
        score = round(min(max(q, 0.0), 0.97), 3)
        if score < min_q:
            return None
        size_factor = round(max(0.3, min(size, 2.0)), 3)
        trail = {"trail_wide": p["trail_wide"], "grace_bars": p["grace_bars"],
                 "breakeven_r": p["breakeven_r"], "lock_r": p["lock_r"],
                 "tight_r": p["tight_r"], "trail_tight": p["trail_tight"]}
        return {
            "score": score, "side": side, "name": self.name, "atr": atr,
            "sl_atr_mult": float(sl_mult), "exit_after_bars": int(max_hold),
            "trail_override": trail, "size_factor": size_factor, "setup": setup,
            "reason": (f"MomentumBlitz {side.upper()} | {setup} ({trig}) | "
                       f"q={score:.2f} TAILLE×{size_factor:.2f} (plein capital) "
                       f"SL={sl_mult:.1f}ATR trail_large hold≤{max_hold}b"),
            "conditions": [
                f"Ignition {setup} ({trig})",
                f"Score {score:.2f} ≥ seuil {min_q:.2f} ✓",
                f"Taille agressive ×{size_factor:.2f} (déploiement plein capital)",
                f"Stop serré {sl_mult:.1f}×ATR + trailing LARGE (laisse courir)",
            ],
        }

    def _none(self, reason: str = "", ctx: dict = None) -> dict:
        d = {"score": 0, "side": "none", "name": self.name, "reason": reason}
        if ctx is not None:
            d["indicators"] = ctx
        return d
