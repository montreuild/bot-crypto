"""Stratégie Scoring Statistique Opus V2 — identique à V1 + trades en Trend Up.

Différence clé par rapport à V1 :
- V1 : Trend Up → pas de trade (AUC direction ≈ 0.50 avec la formule baissière)
- V2 : Trend Up → formule miroir avec biais haussier +2.0 (au lieu de -2.0)

Logique "inverse de la baisse" (hypothèse utilisateur validée) :
  Trend Down : z = -2.0 (biais baissier) + features(RSI_oversold, lower_wick, ...)
               → détecte les rebonds oversold dans une baisse
  Trend Up   : z = +2.0 (biais haussier) + mêmes features
               → par symétrie, le même RSI_oversold devient "achat du dip dans la hausse"
               → et RSI_overbought > 70 (rsi_n négatif) atténue le biais → signal de prudence
               → upper_wick (shooting star) réduit wick_n → signal de prudence

Comportement attendu en Trend Up (formule miroir) :
  RSI 40 (dip à acheter)  : z ≈ +2.0 + 1.0 = +3.0 → P(hausse) ≈ 0.95 ✓
  RSI 60 (neutre)          : z ≈ +2.0 - 1.0 = +1.0 → P(hausse) ≈ 0.73
  RSI 80 (overbought)      : z ≈ +2.0 - 3.0 = -1.0 → P(hausse) ≈ 0.27 → prudence ✓
  Shooting star (upper wick): z fortement réduit → signal d'alerte ✓

Seuils Trend Up (conservateurs car AUC non validé sur historique) :
  amp_thresh_tu  = 0.45  (même que Trend Down)
  dir_dist_tu    = 0.20  (plus strict que TD=0.08 → filtre les zones neutres)
  size_factor_tu = 0.75  (taille prudente, entre Choppy=0.5 et Trend Down=1.0)
"""

import logging
import math
from typing import Dict, Any, List

import polars as pl
from app.engine.engine import BaseStrategy
from app.core.indicators import pre_val

logger = logging.getLogger(__name__)

# ── Constantes régime ────────────────────────────────────────────────────────
REGIME_RANGE    = 0
REGIME_TREND_UP = 1
REGIME_TREND_DN = 2
REGIME_CHOPPY   = 3

REGIME_LABELS = {
    REGIME_RANGE:    "Range",
    REGIME_TREND_UP: "Trend Up",
    REGIME_TREND_DN: "Trend Down",
    REGIME_CHOPPY:   "Choppy",
}

REGIME_AMP_MEDIAN = {
    REGIME_RANGE:    0.66,
    REGIME_TREND_UP: 0.69,
    REGIME_TREND_DN: 0.78,
    REGIME_CHOPPY:   0.72,
}


# ── Fonctions de scoring scalaires ──────────────────────────────────────────
def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, z))))


def _detect_regime(adx: float, sma20: float, sma50: float,
                   sma100: float, sma200: float,
                   adx_threshold: float = 20.0) -> int:
    if adx < adx_threshold:
        return REGIME_RANGE
    if sma20 > sma50 > sma100 > sma200:
        return REGIME_TREND_UP
    if sma20 < sma50 < sma100 < sma200:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _proba_amplitude(atr_ratio: float, vs_ratio: float, rs_ratio: float,
                     bb_ratio: float) -> float:
    z = (-0.5
         + 1.5 * (atr_ratio - 1.0)
         + 1.0 * (rs_ratio  - 1.0)
         + 0.8 * (vs_ratio  - 1.0)
         + 0.5 * (bb_ratio  - 1.0))
    return _sig(z)


def _proba_direction(rsi: float, lower_wick_r: float, upper_wick_r: float,
                     close_pos: float, macd_hist_norm: float,
                     vol_ratio: float, regime: int) -> float:
    """P(hausse) — formule miroir pour Trend Up (biais +2.0).

    Trend Up   : z = +2.0 + rsi_n*2.5 + wick_n*2.0 + ...
                 → achat du dip (RSI oversold) et prudence si overbought/shooting star
    Trend Down : z = -2.0 + rsi_n*2.5 + wick_n*2.0 + ...
                 → rebond oversold dans la baisse
    """
    rsi_n  = (50.0 - rsi) / 25.0
    wick_n = lower_wick_r * 3.0 - upper_wick_r * 2.0
    pos_n  = (0.5 - close_pos) * 2.0
    macd_n = math.copysign(min(abs(macd_hist_norm) * 500.0, 1.5), macd_hist_norm)
    vol_n  = min(max(vol_ratio - 1.0, 0.0), 1.0) * 0.3

    if regime == REGIME_TREND_UP:
        # Formule miroir : biais haussier +2.0
        # rsi_n positif (RSI < 50 = dip) → renforce le signal haussier
        # rsi_n négatif (RSI > 50 = overbought) → atténue → prudence
        z = (+2.0
             + rsi_n  * 2.5
             + wick_n * 2.0
             + pos_n  * 1.0
             + macd_n * 0.6
             + vol_n)
    elif regime == REGIME_TREND_DN:
        z = (-2.0
             + rsi_n  * 2.5
             + wick_n * 2.0
             + pos_n  * 1.0
             + macd_n * 0.6
             + vol_n)
    elif regime == REGIME_RANGE:
        z = (rsi_n * 0.8 + wick_n * 1.0
             + pos_n * 1.5 + macd_n * 1.0 + vol_n)
    elif regime == REGIME_CHOPPY:
        z = (rsi_n * 1.0 + wick_n * 1.5
             + pos_n * 0.8 + macd_n * 1.2 + vol_n)
    else:
        z = (rsi_n * 0.4 + wick_n * 0.4
             + pos_n * 0.4 + macd_n * 0.4 + vol_n)

    return _sig(z)


def _hour_multiplier(df: pl.DataFrame) -> float:
    try:
        ts = df["time"][-1]
        if hasattr(ts, 'hour'):
            h = ts.hour
        else:
            import datetime
            h = datetime.datetime.utcfromtimestamp(int(ts) / 1_000_000).hour
        if 13 <= h <= 17:
            return 1.0
        if 8 <= h <= 12:
            return 0.7
        if 0 <= h <= 5:
            return 0.3
        return 0.5
    except Exception:
        return 0.5


# ── Classe Strategy ──────────────────────────────────────────────────────────
class Strategy(BaseStrategy):
    name = "scoring_statistique_opus_v2"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":     [15, 20, 25],
        "amp_thresh_tu":     [0.35, 0.40, 0.45, 0.50],
        "amp_thresh_td":     [0.35, 0.40, 0.45, 0.50],
        "amp_thresh_other":  [0.45, 0.50, 0.55, 0.60],
        "dir_dist_tu":       [0.15, 0.20, 0.25, 0.30],
        "dir_dist_td":       [0.05, 0.08, 0.10, 0.12],
        "dir_dist_other":    [0.08, 0.10, 0.12, 0.15],
        "rr_min":            [0.5, 0.6, 0.8, 1.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._call_count:  Dict[str, int] = {}
        self._last_signal: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        return 215

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})

        adx_threshold  = float(p.get("adx_threshold",    20.0))
        amp_thresh_tu  = float(p.get("amp_thresh_tu",    0.45))
        amp_thresh_td  = float(p.get("amp_thresh_td",    0.45))
        amp_thresh_oth = float(p.get("amp_thresh_other", 0.55))
        dir_dist_tu    = float(p.get("dir_dist_tu",      0.20))
        dir_dist_td    = float(p.get("dir_dist_td",      0.08))
        dir_dist_oth   = float(p.get("dir_dist_other",   0.12))
        rr_min         = float(p.get("rr_min",           0.6))

        sym = symbol or "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)})")

        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        open_ = df["open"]

        c_now = float(close[-1])
        if c_now <= 0:
            return self._none("Prix invalide")

        # ── Indicateurs précompilés ──────────────────────────────────────
        atr_now  = pre_val(df, "_pre_atr14") or 0.0
        rsi_now  = pre_val(df, "_pre_rsi14") or 50.0
        adx_now  = pre_val(df, "_pre_adx14") or 0.0
        macd_h   = pre_val(df, "_pre_macd_hist") or 0.0
        vr       = pre_val(df, "_pre_volratio20") or 1.0

        if atr_now <= 0:
            return self._none("ATR invalide")

        # ── Features (ratios par moyenne mobile 100 → TF-indépendant) ────
        if "_pre_atr14" not in df.columns:
            return self._none("Pre-compute manquant")

        atr_pct_s = df["_pre_atr14"] / close.clip(lower_bound=1e-9)

        log_rets  = (close / close.shift(1).clip(lower_bound=1e-9)).log(2.718281828)
        vol_std_s = log_rets.rolling_std(20).fill_null(0)
        range_s   = (high - low) / close.clip(lower_bound=1e-9)
        std20     = close.rolling_std(20)
        sma20_s   = close.rolling_mean(20)
        bb_width_s = (4.0 * std20) / sma20_s.clip(lower_bound=1e-9)

        def _ratio(series: pl.Series) -> float:
            now_v = series[-1]
            if now_v is None:
                return 1.0
            now  = float(now_v)
            tail = series.tail(100).drop_nulls()
            if len(tail) == 0:
                return 1.0
            mean = float(tail.mean() or 0.0)
            return now / max(mean, 1e-9)

        atr_ratio = _ratio(atr_pct_s)
        vs_ratio  = _ratio(vol_std_s)
        rs_ratio  = _ratio(range_s)
        bb_ratio  = _ratio(bb_width_s)

        # ── SMAs pour régime ──────────────────────────────────────────────
        sma20_v  = float(sma20_s[-1]                 or c_now)
        sma50_v  = float(close.rolling_mean(50)[-1]  or c_now)
        sma100_v = float(close.rolling_mean(100)[-1] or c_now)
        sma200_v = float(close.rolling_mean(200)[-1] or c_now)

        # ── Structure de bougie ──────────────────────────────────────────
        c_v = float(close[-1]); o_v = float(open_[-1])
        h_v = float(high[-1]);  l_v = float(low[-1])
        t_rng = max(h_v - l_v, 1e-9)
        bt = max(c_v, o_v); bb = min(c_v, o_v)
        upper_wick_r  = (h_v - bt) / t_rng
        lower_wick_r  = (bb - l_v) / t_rng
        close_pos     = (c_v - l_v) / t_rng
        macd_hist_norm = macd_h / c_now

        # ── Régime + scores ───────────────────────────────────────────────
        regime = _detect_regime(adx_now, sma20_v, sma50_v, sma100_v, sma200_v,
                                adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        proba_amp = _proba_amplitude(atr_ratio, vs_ratio, rs_ratio, bb_ratio)
        proba_dir = _proba_direction(rsi_now, lower_wick_r, upper_wick_r,
                                     close_pos, macd_hist_norm, vr, regime)
        dir_dist  = abs(proba_dir - 0.5)

        hour_mult = _hour_multiplier(df)

        # ── Règle de décision par régime ─────────────────────────────────
        if regime == REGIME_TREND_UP:
            amp_thresh = amp_thresh_tu
            dir_thresh = dir_dist_tu
            size_fac   = 0.75
        elif regime == REGIME_TREND_DN:
            amp_thresh = amp_thresh_td
            dir_thresh = dir_dist_td
            size_fac   = 1.0
        else:  # Range / Choppy
            amp_thresh = amp_thresh_oth
            dir_thresh = dir_dist_oth
            size_fac   = 0.5

        if proba_amp < amp_thresh:
            return self._none(
                f"P(événement)={proba_amp:.2f} < {amp_thresh:.2f} | {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )
        if dir_dist < dir_thresh:
            return self._none(
                f"|P(dir)-0.5|={dir_dist:.2f} < {dir_thresh:.2f} | {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        side = "long" if proba_dir > 0.5 else "short"

        # ── Stop / Take Profit ────────────────────────────────────────────
        if side == "long":
            stop   = c_now - 1.5 * atr_now
            target = c_now + 1.0 * atr_now
            risk   = max(c_now - stop, 1e-9)
            rr     = (target - c_now) / risk
        else:
            stop   = c_now + 1.5 * atr_now
            target = c_now - 1.0 * atr_now
            risk   = max(stop - c_now, 1e-9)
            rr     = (c_now - target) / risk

        if rr < rr_min:
            return self._none(
                f"R:R {rr:.2f} < {rr_min} | {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        # ── Score composite ───────────────────────────────────────────────
        amp_med   = REGIME_AMP_MEDIAN[regime] / 100.0
        score_raw = proba_amp * dir_dist * 2.0 * amp_med * 100.0
        score     = 0.50 + min(score_raw * 0.45, 0.44)
        score    *= (size_fac * 0.5 + 0.5)
        score    *= (hour_mult * 0.3 + 0.7)
        score     = round(min(score, 0.94), 3)

        self._last_signal[sym] = cnt

        conditions = [
            f"Régime : {regime_lbl} (ADX={adx_now:.0f})",
            f"P(événement) = {proba_amp:.2f} ≥ {amp_thresh:.2f} ✓",
            f"P(direction) = {proba_dir:.2f} → |dist|={dir_dist:.2f} ≥ {dir_thresh:.2f} ✓",
            f"{'Pleine taille' if size_fac==1.0 else ('3/4' if size_fac==0.75 else 'Demi-taille')} | Heure ×{hour_mult:.1f}",
            f"R:R = {rr:.2f}",
        ]

        return {
            "score":      score,
            "side":       side,
            "name":       self.name,
            "atr":        atr_now,
            "stop_hint":  round(stop, 2),
            "proba_amp":  round(proba_amp, 3),
            "proba_dir":  round(proba_dir, 3),
            "regime":     regime,
            "regime_lbl": regime_lbl,
            "indicators": {
                "adx":          round(adx_now, 1),
                "rsi":          round(rsi_now, 1),
                "atr_ratio":    round(atr_ratio, 3),
                "rs_ratio":     round(rs_ratio,  3),
                "vs_ratio":     round(vs_ratio,  3),
                "bb_ratio":     round(bb_ratio,  3),
                "upper_wick_r": round(upper_wick_r, 3),
                "lower_wick_r": round(lower_wick_r, 3),
                "close_pos":    round(close_pos, 3),
                "hour_mult":    hour_mult,
                "size_factor":  size_fac,
                "sma20":        round(sma20_v, 2),
                "sma200":       round(sma200_v, 2),
            },
            "conditions": conditions,
            "reason": (
                f"OpusV2 {side.upper()} | {regime_lbl} | "
                f"amp={proba_amp:.2f} dir={proba_dir:.2f} "
                f"ADX={adx_now:.0f} RSI={rsi_now:.0f}"
            ),
        }

    def _none(self, reason: str = "", proba_amp: float = 0.0,
              proba_dir: float = 0.5, regime: int = -1) -> dict:
        return {
            "score":      0,
            "side":       "none",
            "name":       self.name,
            "reason":     reason,
            "proba_amp":  round(proba_amp, 3),
            "proba_dir":  round(proba_dir, 3),
            "regime":     regime,
            "regime_lbl": REGIME_LABELS.get(regime, "?"),
        }
