"""Stratégie Volatility Squeeze — l'antithèse (rule-based) de la lignée Omnibus.

Conçue en remettant en question les hypothèses de opus_omnibus V7/V8/V10/V11
(cf. research/CRITIQUE_omnibus_v7-v11.md). Constat central : la lignée Omnibus
prédit la DIRECTION par ML alors qu'elle admet elle-même un AUC_dir ≈ 0.53
(quasi-aléatoire), tout en sous-exploitant le seul edge robuste — l'AMPLITUDE /
volatilité (AUC ≈ 0.7, clustering ACF|r| ≈ 0.15-0.28). Elle trade en outre des
TF sous le mur des frais (15m/30m/1h) avec 20+ paramètres et du ML inline fragile.

Thèse de remplacement : **trader la volatilité (prévisible), pas la direction
(aléatoire).**
  • TIMING par la volatilité : on attend une **compression** (squeeze — largeur de
    Bollinger dans son percentile bas), puis sa **détente** (expansion).
  • DIRECTION = tendance établie, JAMAIS prédite : on ne prend que la cassure
    d'expansion **alignée sur la tendance** (EMA50/200 + pente SMA200). Hors
    tendance / en chop → **abstention** (pas de mean-reversion sur du bruit).
  • RÈGLE, pas ML : ~8 paramètres, déterministe, reproductible, zéro modèle,
    zéro réentraînement.
  • TF = 4h / 1d (au-dessus du mur des frais).
  • Sortie : stop ATR + trailing (laisse courir l'expansion) + max-hold.

« Coiled spring » : ressort comprimé qui se détend dans le sens de la tendance.
On garde l'unique edge réel de la lignée (volatilité) et on élimine ses erreurs
(direction-ML, chop-fade, bas TF, complexité).
"""

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.indicators import pre_val, precompute_df
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


def _safe(v, fb=0.0) -> float:
    try:
        f = float(v)
        return fb if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return fb


def _bb_width_series(close: np.ndarray, period: int) -> np.ndarray:
    """Largeur de Bollinger relative = 4·σ/µ (rolling), causale."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    w = sliding_window_view(close.astype(np.float64), period)  # (n-period+1, period)
    mean = w.mean(axis=1)
    std = w.std(axis=1)
    out[period - 1:] = 4.0 * std / np.maximum(mean, 1e-9)
    return out


class Strategy(BaseStrategy):
    name = "volatility_squeeze"

    # 4h = TF au-dessus du mur des frais (cf. critique). 1d possible (plus lent).
    timeframes: List[str] = ["4h", "1d"]

    warmup_bars = 260

    param_space: Dict[str, List] = {
        "squeeze_lookback": [80, 100, 150],
        "squeeze_pctl":     [0.15, 0.20, 0.25, 0.30],
        "release_window":   [3, 5, 8],
        "donchian":         [15, 20, 30],
        "adx_min":          [15, 18, 22],
        "sl_atr":           [1.3, 1.5, 2.0],
        "max_hold":         [20, 30, 40],
        "min_quality":      [0.56, 0.60, 0.64],
        "enable_short":     [True, False],
        "require_trend":    [True, False],
        "cooldown":         [0, 1, 2],
        "score_threshold":  [0.55, 0.60],
    }

    fixed_params: Dict[str, Any] = {}

    _D = {
        "bb_period":        20,
        "bb_std":           2.0,
        "squeeze_lookback": 100,
        "squeeze_pctl":     0.20,    # seules les compressions les plus serrées
        "release_window":   5,
        "donchian":         20,
        "adx_min":          22.0,    # tendance vraiment établie
        "slope_lookback":   20,
        "atr_expansion_lb": 3,
        "sl_atr":           1.5,
        "max_hold":         30,
        "min_quality":      0.62,
        "short_quality":    0.66,
        "enable_short":     True,
        "require_trend":    True,
        "cooldown":         1,
        # trailing : laisse courir l'expansion
        "trail_wide":       2.5,
        "breakeven_r":      0.7,
        "lock_r":           1.5,
        "tight_r":          4.0,
        "trail_tight":      1.0,
        "grace_bars":       3,
    }

    def __init__(self):
        self._call: Dict[str, int] = {}
        self._last: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return int(p.get("squeeze_lookback", self._D["squeeze_lookback"])) + 60

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
        if cnt - self._last.get(sym, -10**9) < int(p["cooldown"]):
            return self._none("Cooldown")

        close = df["close"]
        high = df["high"]
        low = df["low"]
        c = _safe(close[-1])
        atr = _safe(pre_val(df, "_pre_atr14"))
        if c <= 0 or atr <= 0:
            return self._none("Prix/ATR invalide")

        adx = _safe(pre_val(df, "_pre_adx14"))
        ema50 = _safe(pre_val(df, "_pre_ema50"), c)
        ema200 = _safe(pre_val(df, "_pre_ema200"), c)
        volr = _safe(pre_val(df, "_pre_volratio20"), 1.0)
        slb = int(p["slope_lookback"])
        sma200_s = df["_pre_sma200"]
        sma_now = _safe(sma200_s[-1], c)
        sma_prev = _safe(sma200_s[-1 - slb] if len(sma200_s) > slb else sma200_s[0], sma_now)

        # ── Squeeze : largeur de Bollinger dans son percentile bas ────────────
        look = int(p["squeeze_lookback"])
        rel = int(p["release_window"])
        bbp = int(p["bb_period"])
        tail_n = look + rel + bbp + 5
        close_np = close[-tail_n:].to_numpy().astype(float)
        bbw = _bb_width_series(close_np, bbp)
        bbw_valid = bbw[~np.isnan(bbw)]
        if len(bbw_valid) < look // 2:
            return self._none("Historique BB insuffisant")
        bbw_now = bbw_valid[-1]
        ref = bbw_valid[-1 - look:-1] if len(bbw_valid) > look else bbw_valid[:-1]
        thr = float(np.quantile(ref, p["squeeze_pctl"]))
        # « ressort comprimé récemment » : la largeur BB a plongé sous le seuil
        # dans la fenêtre de détente (release_window) précédant la barre courante.
        recent = bbw_valid[-rel - 1:]
        was_squeezed = bool(np.nanmin(recent) <= thr)
        squeeze_now = bbw_now <= thr
        # tension du ressort : 0 (large) → 1 (très comprimé)
        coil = float(max(0.0, min(1.0, 1.0 - (bbw_now / max(thr, 1e-9)) * 0.5))) if was_squeezed else 0.0

        # ── Bandes & breakout ─────────────────────────────────────────────────
        bb_win = close[-bbp:].to_numpy().astype(float)
        bb_mid = float(bb_win.mean())
        bb_sd = float(bb_win.std())
        bb_up = bb_mid + p["bb_std"] * bb_sd
        bb_lo = bb_mid - p["bb_std"] * bb_sd
        N = int(p["donchian"])
        donch_hi = _safe(high[-1 - N:-1].max(), c)
        donch_lo = _safe(low[-1 - N:-1].min(), c)
        lb = int(p["atr_expansion_lb"])
        atr_prev = _safe(df["_pre_atr14"][-1 - lb] if len(df) > lb else atr, atr)
        atr_expanding = atr > atr_prev

        # ── Tendance établie (jamais prédite) ─────────────────────────────────
        slope = sma_now - sma_prev
        trend_up = (c > ema200 and ema50 > ema200 and slope > 0) or not p["require_trend"]
        trend_dn = (c < ema200 and ema50 < ema200 and slope < 0)
        strong = adx >= p["adx_min"]
        adx_str = max(0.0, min((adx - p["adx_min"]) / 25.0, 1.0))

        breakout_up = c > donch_hi or c > bb_up
        breakdown = c < donch_lo or c < bb_lo

        ctx = {
            "bbw_now": round(bbw_now, 4), "bbw_thr": round(thr, 4),
            "squeezed": squeeze_now, "was_squeezed": was_squeezed, "coil": round(coil, 2),
            "regime": ("up" if (c > ema200 and ema50 > ema200) else
                       "down" if (c < ema200 and ema50 < ema200) else "neutral"),
            "adx": round(adx, 1), "atr_expanding": atr_expanding,
            "volratio": round(volr, 2), "slope": round(slope, 2),
            "donchian_hi": round(donch_hi, 2), "donchian_lo": round(donch_lo, 2),
        }

        # ── Décision : détente du squeeze DANS le sens de la tendance ──────────
        candidates = []
        if was_squeezed and strong:
            # LONG : ressort + cassure haussière + tendance up + expansion
            if trend_up and breakout_up and atr_expanding:
                q = 0.44 + 0.16 * adx_str + 0.16 * coil
                q += 0.06 if volr >= 1.3 else 0.0
                q += 0.05 if (c > ema200 and ema50 > ema200 and slope > 0) else 0.0
                candidates.append(self._mk("long", q, "SQUEEZE_BREAKOUT", p, atr,
                                           coil, adx_str, p["min_quality"]))
            # SHORT (conservateur) : ressort + cassure baissière + tendance down
            if p["enable_short"] and trend_dn and breakdown and atr_expanding:
                q = 0.44 + 0.16 * adx_str + 0.16 * coil
                q += 0.06 if volr >= 1.3 else 0.0
                candidates.append(self._mk("short", q, "SQUEEZE_BREAKDOWN", p, atr,
                                           coil, adx_str, p["short_quality"],
                                           size_scale=0.6))

        valid = [x for x in candidates if x is not None]
        if not valid:
            return self._none(
                f"Pas de détente alignée ({ctx['regime']}, squeeze={was_squeezed})", ctx=ctx)
        best = max(valid, key=lambda x: x["score"])
        best["indicators"] = ctx
        self._last[sym] = cnt
        return best

    def _mk(self, side, q, setup, p, atr, coil, adx_str, min_q, size_scale=1.0) -> Optional[dict]:
        score = round(min(max(q, 0.0), 0.95), 3)
        if score < min_q:
            return None
        # Sizing modéré (stratégie robuste, pas la version agressive) : conviction
        # = tension du ressort × force de tendance.
        size_factor = round(max(0.3, min(size_scale * (0.7 + 0.5 * coil + 0.3 * adx_str), 1.5)), 3)
        trail = {"trail_wide": p["trail_wide"], "grace_bars": p["grace_bars"],
                 "breakeven_r": p["breakeven_r"], "lock_r": p["lock_r"],
                 "tight_r": p["tight_r"], "trail_tight": p["trail_tight"]}
        return {
            "score": score, "side": side, "name": self.name, "atr": atr,
            "sl_atr_mult": float(p["sl_atr"]), "exit_after_bars": int(p["max_hold"]),
            "trail_override": trail, "size_factor": size_factor, "setup": setup,
            "reason": (f"VolSqueeze {side.upper()} | {setup} | coil={coil:.2f} "
                       f"q={score:.2f} taille×{size_factor:.2f} SL={p['sl_atr']:.1f}ATR"),
            "conditions": [
                f"Ressort comprimé puis détente ({setup})",
                "Direction = tendance établie (jamais prédite)",
                f"Score {score:.2f} ≥ seuil {min_q:.2f} · coil {coil:.2f} · ADX-force {adx_str:.2f}",
                f"Stop {p['sl_atr']:.1f}×ATR + trailing (laisse courir l'expansion)",
            ],
        }

    def _none(self, reason: str = "", ctx: dict = None) -> dict:
        d = {"score": 0, "side": "none", "name": self.name, "reason": reason}
        if ctx is not None:
            d["indicators"] = ctx
        return d
