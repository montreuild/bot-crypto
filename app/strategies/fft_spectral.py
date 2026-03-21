"""
Stratégie FFT Spectrale — Analyse spectrale pure

Logique :
  1. Détrending logarithmique des prix (retrait de la tendance long terme)
  2. Fenêtre de Hanning (anti spectral leakage — évite les artefacts de bord)
  3. FFT via l'algorithme de Cooley-Tukey (numpy.fft.rfft)
  4. Extraction des cycles dominants dans [fft_min_period, fft_max_period] barres
  5. Phase de chaque cycle → direction anticipée (+1 montée, −1 descente)
  6. Signal directionnel pondéré par l'énergie spectrale de chaque cycle

Interprétation des cycles :
  - Période : durée du cycle en barres (jours si TF=1d, heures si TF=1h…)
  - Poids spectral (%) : part de la variance totale expliquée par ce cycle
  - Phase / jours avant retournement : estimation du prochain point de retournement

Score bot 0-1 :
  score = 0.55 + confidence × 0.39   (max 0.94)
  confidence = |avg_signal_pondéré| ∈ [0, 1]

Signal généré seulement si :
  - direction ≠ 0  (signal pondéré suffisamment directionnel)
  - confidence ≥ min_confidence
  - R:R ≥ rr_min

Paramètres optimisables :
  fft_min_period   : période minimale retenue (barres, défaut 5)
  fft_max_period   : période maximale retenue (barres, défaut 150)
  fft_top_n        : nombre de cycles extraits (défaut 10)
  min_confidence   : seuil de confiance pour émettre un signal (défaut 0.20)
  min_bars         : nombre minimum de bougies requises (défaut 180)
  cooldown         : barres d'attente entre deux signaux (défaut 30)
  rr_min           : ratio risque/récompense minimum (défaut 1.5)
"""

import logging
from typing import Dict, Any, List

import polars as pl

from app.engine.engine import BaseStrategy
from app.strategies.utils import fft_direction as _fft_direction
from app.core.indicators import (
    atr_val as calc_atr,
    pre_val,
    support_resistance_levels,
    nearest_support,
    nearest_resistance,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  Stratégie
# ══════════════════════════════════════════════════════════════════════════════

class Strategy(BaseStrategy):
    name = "fft_spectral"

    timeframes: List[str] = ["4h", "1d"]

    param_space: Dict[str, List] = {
        "fft_min_period":  [5, 7, 10],
        "fft_max_period":  [100, 150, 200],
        "fft_top_n":       [7, 10, 15],
        "min_confidence":  [0.15, 0.20, 0.25, 0.30],
        "min_bars":        [150, 180, 250],
        "cooldown":        [20, 30, 40],
        "rr_min":          [1.3, 1.5, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get("fft_spectral", {})
        return max(int(p.get("min_bars", 180)), 60)

    def score(
        self,
        df: pl.DataFrame,
        params: dict = None,
        df_htf=None,
        symbol: str = "",
    ) -> Dict[str, Any]:
        p = (params or {}).get("fft_spectral", {})

        # ── Paramètres ────────────────────────────────────────────────────────
        fft_min_period = int(p.get("fft_min_period",    5))
        fft_max_period = int(p.get("fft_max_period",  150))
        fft_top_n      = int(p.get("fft_top_n",         10))
        min_confidence = float(p.get("min_confidence", 0.20))
        min_bars       = max(int(p.get("min_bars",     180)), 60)
        cooldown       = int(p.get("cooldown",          30))
        rr_min         = float(p.get("rr_min",          1.5))

        sym = symbol or "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        if len(df) < min_bars:
            return self._none(f"Données insuffisantes : {len(df)}/{min_bars} barres")

        if cnt - self._last_signal.get(sym, -999) < cooldown:
            return self._none("Cooldown")

        close = df["close"]
        c_now = float(close[-1])

        # ATR pour sizing et placement du stop
        atr_val = pre_val(df, "_pre_atr14") or calc_atr(df, 14)
        if atr_val <= 0:
            return self._none("ATR invalide")

        # ── Analyse FFT ───────────────────────────────────────────────────────
        prices_np = close.to_numpy().astype(float)
        fft       = _fft_direction(prices_np, fft_min_period, fft_max_period, fft_top_n)

        direction  = fft["direction"]
        confidence = fft["confidence"]

        if direction == 0 or confidence < min_confidence:
            return self._none(
                f"Signal FFT neutre "
                f"(dir={direction}, conf={confidence:.3f} < {min_confidence})"
            )

        # ── S/R pour stop et target ───────────────────────────────────────────
        sr = support_resistance_levels(
            df,
            window      = 5,
            cluster_pct = 0.018,
            min_touches = 1,
            max_levels  = 4,
            lookback    = min(180, len(df) - 1),
        )
        nearest_sup = nearest_support(c_now, sr["supports"])
        nearest_res = nearest_resistance(c_now, sr["resistances"])

        # ── Normalisation du score 0-1 ────────────────────────────────────────
        bot_score = min(round(0.55 + confidence * 0.39, 3), 0.94)

        # Résumé des cycles pour l'affichage
        cycle_lines = [
            f"  Cycle {c['period']:.0f} barres "
            f"({c['weight_pct']:.1f}%) "
            f"→ {'↑' if c['direction'] > 0 else '↓'} "
            f"retournement dans ~{c['days_to_reversal']:.0f} barres"
            for c in fft["cycles"][:3]
        ]

        indicators = {
            "fft_direction":  direction,
            "fft_confidence": round(confidence, 4),
            "fft_avg_signal": fft["avg_signal"],
            "next_reversal":  fft["next_reversal"],
            "n_cycles":       len(fft["cycles"]),
            "top_cycles":     fft["cycles"][:5],
        }

        # ── LONG ─────────────────────────────────────────────────────────────
        if direction > 0:
            stop_l = (
                nearest_sup - atr_val * 0.5
                if nearest_sup is not None
                else c_now - atr_val * 2.0
            )
            risk_l = c_now - stop_l
            if risk_l <= 0:
                return self._none("Stop long invalide")

            target_l = nearest_res if nearest_res else c_now + risk_l * rr_min
            reward_l = max(target_l - c_now, 0.0)
            rr_l     = reward_l / risk_l if risk_l > 0 else 0.0
            if rr_l < rr_min:
                return self._none(f"R:R FFT long {rr_l:.2f} < {rr_min}")

            self._last_signal[sym] = cnt
            return {
                "score":      bot_score,
                "side":       "long",
                "name":       self.name,
                "atr":        atr_val,
                "stop_hint":  round(stop_l, 4),
                "indicators": indicators,
                "conditions": [
                    f"FFT signal haussier : avg={fft['avg_signal']:.3f} ✓",
                    f"Confiance spectrale : {confidence:.3f} ≥ {min_confidence} ✓",
                    f"Cycles analysés : {len(fft['cycles'])}",
                    *cycle_lines,
                    f"Retournement estimé dans ~{fft['next_reversal']:.0f} barres",
                    f"R:R {rr_l:.2f} ✓",
                ],
                "reason": (
                    f"FFT LONG avg={fft['avg_signal']:.3f} "
                    f"conf={confidence:.3f} "
                    f"({len(fft['cycles'])} cycles) "
                    f"retournement ~{fft['next_reversal']:.0f} barres "
                    f"R:R={rr_l:.2f}"
                ),
            }

        # ── SHORT ─────────────────────────────────────────────────────────────
        stop_s = (
            nearest_res + atr_val * 0.5
            if nearest_res is not None
            else c_now + atr_val * 2.0
        )
        risk_s = stop_s - c_now
        if risk_s <= 0:
            return self._none("Stop short invalide")

        target_s = nearest_sup if nearest_sup else c_now - risk_s * rr_min
        reward_s = max(c_now - target_s, 0.0)
        rr_s     = reward_s / risk_s if risk_s > 0 else 0.0
        if rr_s < rr_min:
            return self._none(f"R:R FFT short {rr_s:.2f} < {rr_min}")

        self._last_signal[sym] = cnt
        return {
            "score":      bot_score,
            "side":       "short",
            "name":       self.name,
            "atr":        atr_val,
            "stop_hint":  round(stop_s, 4),
            "indicators": indicators,
            "conditions": [
                f"FFT signal baissier : avg={fft['avg_signal']:.3f} ✓",
                f"Confiance spectrale : {confidence:.3f} ≥ {min_confidence} ✓",
                f"Cycles analysés : {len(fft['cycles'])}",
                *cycle_lines,
                f"Retournement estimé dans ~{fft['next_reversal']:.0f} barres",
                f"R:R {rr_s:.2f} ✓",
            ],
            "reason": (
                f"FFT SHORT avg={fft['avg_signal']:.3f} "
                f"conf={confidence:.3f} "
                f"({len(fft['cycles'])} cycles) "
                f"retournement ~{fft['next_reversal']:.0f} barres "
                f"R:R={rr_s:.2f}"
            ),
        }
