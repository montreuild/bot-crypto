"""Stratégie V4 Regime Scoring — scoring heuristique amplitude+direction par régime.

Basée sur les conclusions du rapport V4 (50 000 bougies BTC/USDC) :
- Amplitude (AUC OOS 0.75 sur 15m) : prédire si un mouvement significatif aura lieu
  → features clés : ATR_pct, vol_std_20, range_size, BB_width (effet GARCH)
- Direction (AUC OOS 0.87 en Trend Down) : prédire le sens du mouvement
  → conditionné par régime : efficace surtout en Trend Down
- Régime 4 classes (ADX + alignement SMA20/50/100/200)
- Filtre horaire : 13h-17h UTC = session US, taux d'événements ×2

Règle de décision (rapport §6.4) :
- Trend Down : amp ≥ 0.50 ET |dir - 0.5| ≥ 0.10 → taille pleine
- Range/Choppy : amp ≥ 0.60 ET |dir - 0.5| ≥ 0.15 → demi-taille
- Trend Up : pas de trade (AUC direction ≈ 0.50)
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

# Amplitude médiane observée par régime (rapport V4 §3.5, en %)
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
    """Détection du régime V4 (4 classes) : ADX + alignement SMA."""
    if adx < adx_threshold:
        return REGIME_RANGE
    if sma20 > sma50 > sma100 > sma200:
        return REGIME_TREND_UP
    if sma20 < sma50 < sma100 < sma200:
        return REGIME_TREND_DN
    return REGIME_CHOPPY


def _proba_amplitude(atr_pct: float, vol_std: float, range_size: float,
                     bb_width: float = 0.0,
                     atr_lag3: float = 0.0, vs_lag3: float = 0.0) -> float:
    """Probabilité heuristique d'occurrence d'un événement (rapport V4 §6.1).
    Formule calibrée sur AUC 0.75 (15m OOS) — effet GARCH non-linéaire.
    """
    z = (-3.5
         + 200.0 * atr_pct   + 80.0 * atr_lag3
         + 60.0  * vol_std   + 30.0 * vs_lag3
         + 90.0  * range_size
         + 35.0  * bb_width)
    return _sig(z)


def _proba_direction(rsi: float, lower_wick_r: float, upper_wick_r: float,
                     close_pos: float, macd_hist_norm: float,
                     vol_ratio: float, regime: int) -> float:
    """Probabilité directionnelle P(hausse | événement) — rapport V4 §6.2-6.3.

    AUC attendu par régime :
    - Trend Down : ~0.87 (formule avec biais baissier + rebond oversold)
    - Range : 0.50-0.70
    - Choppy : 0.58-0.60
    - Trend Up : ~0.50 (pas de signal fiable → ne pas trader)
    """
    rsi_n  = (50.0 - rsi) / 25.0                          # [-2,+2]
    wick_n = lower_wick_r * 3.0 - upper_wick_r * 2.0      # structure bougie
    pos_n  = (0.5 - close_pos) * 2.0                       # [-1,+1]
    macd_n = math.copysign(min(abs(macd_hist_norm) * 500.0, 1.5), macd_hist_norm)
    vol_n  = min(max(vol_ratio - 1.0, 0.0), 1.0) * 0.3

    if regime == REGIME_TREND_DN:
        # Biais baissier de base : en downtrend la plupart des bougies sont rouges
        # Sauf : RSI oversold + mèche basse → rebond technique identifiable
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
    else:  # Trend Up — signal faible
        z = (rsi_n * 0.4 + wick_n * 0.4
             + pos_n * 0.4 + macd_n * 0.4 + vol_n)

    return _sig(z)


def _hour_multiplier(df: pl.DataFrame) -> float:
    """Multiplicateur de taille selon l'heure UTC (rapport V4 §6.5)."""
    try:
        ts = df["time"][-1]
        if hasattr(ts, 'hour'):
            h = ts.hour
        else:
            import datetime
            h = datetime.datetime.utcfromtimestamp(int(ts) / 1_000_000).hour
        if 13 <= h <= 17:
            return 1.0    # Session US : lift ×2 sur les événements
        if 8 <= h <= 12:
            return 0.7    # Session EU
        if 0 <= h <= 5:
            return 0.3    # Nuit : calme, slippage élevé
        return 0.5
    except Exception:
        return 0.5


# ── Classe Strategy ──────────────────────────────────────────────────────────
class Strategy(BaseStrategy):
    """Stratégie V4 Regime Scoring.

    Score composite = P(événement) × |P(direction) - 0.5| × amplitude_médiane_régime
    Seuils et taille de position adaptés par régime.
    """

    name = "v4_regime_scoring"

    timeframes: List[str] = ["15m", "30m", "1h"]

    param_space: Dict[str, Any] = {
        "adx_threshold":     [15, 20, 25],
        "amp_thresh_td":     [0.40, 0.45, 0.50, 0.55],
        "amp_thresh_other":  [0.50, 0.55, 0.60, 0.65],
        "dir_dist_td":       [0.08, 0.10, 0.12, 0.15],
        "dir_dist_other":    [0.10, 0.12, 0.15, 0.18],
        "rr_min":            [1.0, 1.2, 1.5],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._call_count:  Dict[str, int] = {}
        self._last_signal: Dict[str, int] = {}

    def min_bars_required(self, params: dict = None) -> int:
        return 215  # SMA200 + marge

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})

        adx_threshold  = float(p.get("adx_threshold",    20.0))
        amp_thresh_td  = float(p.get("amp_thresh_td",    0.50))
        amp_thresh_oth = float(p.get("amp_thresh_other", 0.60))
        dir_dist_td    = float(p.get("dir_dist_td",      0.10))
        dir_dist_oth   = float(p.get("dir_dist_other",   0.15))
        rr_min         = float(p.get("rr_min",           1.2))

        sym = symbol or "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        if len(df) < self.min_bars_required(params):
            return self._none(f"Données insuffisantes ({len(df)}<{self.min_bars_required()})")

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

        atr_pct  = atr_now / c_now

        # ── Calcul des features on-demand ────────────────────────────────
        # Volatilité réalisée (log-returns std 20)
        log_rets = (close / close.shift(1).clip(lower_bound=1e-9)).log(2.718281828)
        vol_std20 = float(log_rets.rolling_std(20)[-1] or 0.0)

        range_size = float((high[-1] - low[-1]) / c_now)

        sma20_arr  = close.rolling_mean(20)
        std20_arr  = close.rolling_std(20)
        bb_width   = float((4.0 * std20_arr[-1]) / (sma20_arr[-1] or 1e-9)) if sma20_arr[-1] else 0.0

        sma20_v  = float(sma20_arr[-1]          or c_now)
        sma50_v  = float(close.rolling_mean(50)[-1]  or c_now)
        sma100_v = float(close.rolling_mean(100)[-1] or c_now)
        sma200_v = float(close.rolling_mean(200)[-1] or c_now)

        # Lags (t-3) pour l'amplitude
        atr_lag3 = float(df["_pre_atr14"][-3] / float(close[-3])) if "_pre_atr14" in df.columns and len(df) > 3 else atr_pct
        vs_lag3  = float(log_rets.rolling_std(20)[-3] or 0.0) if len(df) > 3 else vol_std20

        # Structure de bougie
        c_v  = float(close[-1]); o_v = float(open_[-1])
        h_v  = float(high[-1]);  l_v = float(low[-1])
        t_rng = max(h_v - l_v, 1e-9)
        bt    = max(c_v, o_v); bb = min(c_v, o_v)
        upper_wick_r = (h_v - bt) / t_rng
        lower_wick_r = (bb - l_v)  / t_rng
        close_pos    = (c_v - l_v) / t_rng
        macd_hist_norm = macd_h / c_now

        # ── Régime V4 ─────────────────────────────────────────────────────
        regime = _detect_regime(adx_now, sma20_v, sma50_v, sma100_v, sma200_v,
                                adx_threshold)
        regime_lbl = REGIME_LABELS[regime]

        # ── Score amplitude (P qu'un événement se produise) ───────────────
        proba_amp = _proba_amplitude(atr_pct, vol_std20, range_size, bb_width,
                                     atr_lag3, vs_lag3)

        # ── Score direction P(hausse) ─────────────────────────────────────
        proba_dir = _proba_direction(rsi_now, lower_wick_r, upper_wick_r,
                                     close_pos, macd_hist_norm, vr, regime)

        dir_dist = abs(proba_dir - 0.5)

        # ── Filtre horaire ────────────────────────────────────────────────
        hour_mult = _hour_multiplier(df)

        # ── Règle de décision par régime (rapport §6.4) ───────────────────
        if regime == REGIME_TREND_UP:
            return self._none(
                f"Trend Up — pas d'edge directionnel (AUC≈0.50) | "
                f"amp={proba_amp:.2f} dir={proba_dir:.2f}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        if regime == REGIME_TREND_DN:
            amp_ok   = proba_amp >= amp_thresh_td
            dir_ok   = dir_dist  >= dir_dist_td
            size_fac = 1.0
        else:  # Range ou Choppy
            amp_ok   = proba_amp >= amp_thresh_oth
            dir_ok   = dir_dist  >= dir_dist_oth
            size_fac = 0.5

        if not amp_ok:
            return self._none(
                f"Amplitude insuffisante : P(event)={proba_amp:.2f} "
                f"(seuil {'%.2f'%amp_thresh_td if regime==REGIME_TREND_DN else '%.2f'%amp_thresh_oth}) "
                f"| {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )
        if not dir_ok:
            return self._none(
                f"Direction incertaine : |P(dir)-0.5|={dir_dist:.2f} "
                f"(seuil {'%.2f'%dir_dist_td if regime==REGIME_TREND_DN else '%.2f'%dir_dist_oth}) "
                f"| {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        side = "long" if proba_dir > 0.5 else "short"

        # ── Stop / Take Profit (rapport §6.6) ─────────────────────────────
        atr14_stop = atr_now
        if side == "long":
            stop   = c_now - 1.5 * atr14_stop
            target = c_now + 1.0 * atr14_stop
            risk   = max(c_now - stop, 1e-9)
            rr     = (target - c_now) / risk
        else:
            stop   = c_now + 1.5 * atr14_stop
            target = c_now - 1.0 * atr14_stop
            risk   = max(stop - c_now, 1e-9)
            rr     = (c_now - target) / risk

        if rr < rr_min:
            return self._none(
                f"R:R {rr:.2f} < {rr_min} | {regime_lbl}",
                proba_amp=proba_amp, proba_dir=proba_dir, regime=regime,
            )

        # ── Score composite ────────────────────────────────────────────────
        amp_med = REGIME_AMP_MEDIAN[regime] / 100.0    # en décimal
        score_raw = proba_amp * dir_dist * 2.0 * amp_med * 100.0
        # Normaliser en [0.50, 0.95]
        score = 0.50 + min(score_raw * 0.45, 0.44)
        score *= (size_fac * 0.5 + 0.5)              # Demi-taille → score légèrement réduit
        score *= (hour_mult * 0.3 + 0.7)             # Heure → légère modulation
        score = round(min(score, 0.94), 3)

        self._last_signal[sym] = cnt

        conditions = [
            f"Régime : {regime_lbl} (ADX={adx_now:.0f})",
            f"P(événement) = {proba_amp:.2f} ≥ {amp_thresh_td if regime==REGIME_TREND_DN else amp_thresh_oth:.2f} ✓",
            f"P(direction) = {proba_dir:.2f} → |dist|={dir_dist:.2f} ≥ {dir_dist_td if regime==REGIME_TREND_DN else dir_dist_oth:.2f} ✓",
            f"{'Pleine taille' if size_fac==1.0 else 'Demi-taille'} | Heure ×{hour_mult:.1f}",
            f"R:R = {rr:.2f} | SMA20={sma20_v:.0f} SMA200={sma200_v:.0f}",
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
                "atr_pct":      round(atr_pct * 100, 3),
                "vol_std20":    round(vol_std20 * 100, 3),
                "range_size":   round(range_size * 100, 3),
                "bb_width":     round(bb_width * 100, 3),
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
                f"V4 {side.upper()} | {regime_lbl} | "
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
