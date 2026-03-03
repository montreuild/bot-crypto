"""
Stratégie Pullback Trend — La plus performante en trend following 1h.

Principe : ne jamais chasser le marché.
  1. Identifier une tendance claire (EMA structure + ADX)
  2. Attendre un pullback sain vers l'EMA fast (repli de 0.2-1.5%)
  3. Confirmer le rebond sur support (bougie de signal + RSI remonte)
  4. Entrer avec un R:R favorable (stop sous le pullback bas)

Avantages vs entrée au cross :
  - Prix d'entrée meilleur (après repli)
  - Stop plus naturel (sous le swing low du pullback)
  - R:R structurellement supérieur
  - Moins de faux signaux

Anti-overtrading :
  - Cooldown de 25 barres (25h minimum entre signaux)
  - Exige 3 conditions simultanées minimales
"""
import logging
from typing import Dict, Any
import numpy as np
import pandas as pd
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "pullback_trend"

    def __init__(self):
        self._last_signal_bar: int = -999
        self._call_count: int = 0

    def score(self, df: pd.DataFrame, params: dict = None) -> Dict[str, Any]:
        self._call_count += 1
        p = (params or {}).get("pullback_trend", {})

        ema_fast   = int(p.get("ema_fast",    21))
        ema_mid    = int(p.get("ema_mid",     50))
        ema_slow   = int(p.get("ema_slow",   100))
        ema_trend  = int(p.get("ema_trend",  200))
        adx_min    = float(p.get("adx_min",   18))    # Seuil bas pour 1h
        # Zone de pullback : le prix doit être entre X% sous l'EMA fast et au-dessus
        pb_zone    = float(p.get("pb_zone_pct", 0.005))  # ±0.5% de l'EMA fast
        # RSI pendant le pullback : doit être revenu en zone neutre
        rsi_pb_low  = float(p.get("rsi_pb_low",  35))   # Survendu temporaire OK
        rsi_pb_high = float(p.get("rsi_pb_high", 55))   # Pas encore suracheté
        vol_min     = float(p.get("vol_min",    0.9))    # Volume peut être faible au pullback
        cooldown    = int(p.get("cooldown",      25))

        min_bars = max(ema_trend + 10, 220)
        if len(df) < min_bars:
            return self._none()

        close  = df["close"].astype(float)
        high   = df["high"].astype(float)
        low    = df["low"].astype(float)

        # ── EMAs ────────────────────────────────────────────────────────────────
        ef   = close.ewm(span=ema_fast,  adjust=False).mean()
        em   = close.ewm(span=ema_mid,   adjust=False).mean()
        es   = close.ewm(span=ema_slow,  adjust=False).mean()
        et   = close.ewm(span=ema_trend, adjust=False).mean()

        lf   = float(ef.iloc[-1])
        lm   = float(em.iloc[-1])
        ls   = float(es.iloc[-1])
        lt   = float(et.iloc[-1])
        c0   = float(close.iloc[-1])
        c1   = float(close.iloc[-2])
        c2   = float(close.iloc[-3])

        # ── Indicateurs ─────────────────────────────────────────────────────────
        rsi_series = self._rsi(close, 14)
        rsi_now    = float(rsi_series.iloc[-1])
        rsi_prev   = float(rsi_series.iloc[-2])
        rsi_prev2  = float(rsi_series.iloc[-3])

        adx = self._adx(df, 14)
        atr = self._atr(df, 14)
        if atr <= 0:
            return self._none()

        vol_avg   = df["volume"].rolling(20).mean().iloc[-1]
        vol_now   = float(df["volume"].iloc[-1])
        vol_ratio = float(vol_now / max(vol_avg, 1e-9))

        # ── Cooldown ─────────────────────────────────────────────────────────
        if self._call_count - self._last_signal_bar < cooldown:
            return self._none(f"Cooldown ({self._call_count - self._last_signal_bar}/{cooldown})")

        # ══════════════════════════════════════════════════════════════════════
        # LONG : Tendance haussière + pullback + rebond
        # ══════════════════════════════════════════════════════════════════════
        #
        # Condition de tendance :
        #   - EMA structure : ef > em > es (alignées)
        #   - Prix au-dessus d'au moins l'EMA mid
        #   - ADX confirme la directionnalité
        #
        # Condition de pullback (sur les 5 dernières barres) :
        #   - Le prix est passé temporairement sous l'EMA fast OU très proche
        #   - RSI est tombé en dessous de 55 (pas de suracheté pendant le pullback)
        #   - Le prix n'a pas cassé l'EMA mid (pullback sain)
        #
        # Condition de rebond (barre courante) :
        #   - Prix revient au-dessus de l'EMA fast
        #   - RSI repart à la hausse depuis une zone basse
        #   - Volume OK
        #
        # ─────────────────────────────────────────────────────────────────────

        ema_structure_bull = lf > lm and lm > ls  # EMA bien alignées en hausse
        price_above_mid    = c0 > lm               # Prix au-dessus de l'EMA mid
        trend_intact       = c0 > lt * 0.97        # Tolérance 3% sous EMA200 (correction ok)

        if ema_structure_bull and price_above_mid and adx >= adx_min and trend_intact:

            # ── Cherche un pullback récent (5 dernières barres) ──────────────
            # Le prix doit avoir touché ou approché l'EMA fast
            pb_found = False
            pb_low   = float('inf')  # Le plus bas du pullback
            for k in range(1, 6):
                c_k   = float(close.iloc[-1 - k])
                ef_k  = float(ef.iloc[-1 - k])
                low_k = float(low.iloc[-1 - k])
                # Prix proche de l'EMA fast (±pb_zone)
                in_zone = abs(c_k - ef_k) / ef_k <= pb_zone * 2.0
                # Ou prix légèrement sous l'EMA fast (pullback sain)
                slight_under = ef_k * (1 - pb_zone * 1.5) <= c_k <= ef_k * (1 + pb_zone)
                if in_zone or slight_under:
                    pb_found = True
                    pb_low   = min(pb_low, low_k)

            # ── Barre de signal : rebond confirmé ────────────────────────────
            # Prix au-dessus ou très près de l'EMA fast
            price_at_ema   = abs(c0 - lf) / lf <= pb_zone * 1.5
            price_above_ema = c0 >= lf * (1 - pb_zone * 0.5)

            # Bougie haussière (clôture > ouverture)
            bullish_bar = c0 > c1

            # RSI reprend sa montée depuis une zone basse
            rsi_recovery = (rsi_now >= rsi_pb_low and                # RSI sortit du survendu
                            rsi_now <= rsi_pb_high + 15 and          # Pas encore suracheté
                            rsi_now >= rsi_prev - 3)                 # RSI ne chute plus

            # EMA fast elle-même monte (tendance locale intacte)
            ef_slope = (lf - float(ef.iloc[-4])) / lf * 100
            ema_rising = ef_slope > 0

            # Volume : acceptable (pas besoin d'être fort, c'est un repli)
            vol_ok = vol_ratio >= vol_min

            if (pb_found and (price_at_ema or price_above_ema) and
                    bullish_bar and rsi_recovery and ema_rising and vol_ok):

                # ── Score ─────────────────────────────────────────────────────
                # Base haute car entrée sur pullback = setup de qualité
                ema_stack    = (lf - lm) / lm * 100     # % d'écart EMA fast/mid
                adx_bonus    = min((adx - adx_min) / 20, 1.0) * 0.10
                rsi_quality  = 1.0 - abs(rsi_now - 48) / 25   # Optimal autour de 48
                rsi_bonus    = max(rsi_quality * 0.06, 0)
                vol_bonus    = min((vol_ratio - vol_min) * 0.05, 0.08)
                # Bonus si pullback profond mais pas trop (EMA ± 0.2-0.5%)
                pb_depth     = (lf - pb_low) / lf * 100
                pb_bonus     = 0.06 if 0.1 <= pb_depth <= 1.5 else 0.02
                ema_bonus    = min(ema_stack * 3, 0.10)

                score = min(
                    0.63
                    + adx_bonus
                    + rsi_bonus
                    + vol_bonus
                    + pb_bonus
                    + ema_bonus,
                    0.93
                )

                # Stop : sous le plus bas du pullback (naturel)
                # Si le pb_low est trop proche, utiliser 1.5×ATR
                natural_stop = pb_low - atr * 0.3
                atr_stop     = c0 - atr * 2.0
                stop_ref     = max(natural_stop, atr_stop)  # Le moins restrictif

                self._last_signal_bar = self._call_count
                return {
                    "score":      round(score, 3),
                    "side":       "long",
                    "name":       self.name,
                    "atr":        atr,
                    "stop_hint":  round(stop_ref, 2),
                    "indicators": {
                        "ema_fast":    round(lf, 2),
                        "ema_mid":     round(lm, 2),
                        "ema_slow":    round(ls, 2),
                        "ema200":      round(lt, 2),
                        "rsi":         round(rsi_now, 1),
                        "adx":         round(adx, 1),
                        "vol_ratio":   round(vol_ratio, 2),
                        "pb_low":      round(pb_low, 2),
                        "pb_depth_pct":round(pb_depth, 3),
                        "ef_slope":    round(ef_slope, 4),
                    },
                    "conditions": [
                        f"EMA structure: {lf:.0f} > {lm:.0f} > {ls:.0f} ✓",
                        f"Prix ({c0:.0f}) > EMA mid ({lm:.0f}) ✓",
                        f"Pullback trouvé (profondeur {pb_depth:.2f}%) ✓",
                        f"Rebond confirmé (bougie haussière) ✓",
                        f"RSI {rsi_now:.0f} en recovery depuis zone basse ✓",
                        f"ADX {adx:.1f} ≥ {adx_min} ✓",
                        f"Volume {vol_ratio:.2f}x ✓",
                    ],
                    "reason": (
                        f"Pullback LONG EMA{ema_fast} — PB depth={pb_depth:.2f}% "
                        f"RSI={rsi_now:.0f} ADX={adx:.0f} vol={vol_ratio:.1f}x"
                    ),
                }

        # ══════════════════════════════════════════════════════════════════════
        # SHORT : Tendance baissière + rally + rejet
        # ══════════════════════════════════════════════════════════════════════

        ema_structure_bear = lf < lm and lm < ls
        price_below_mid    = c0 < lm
        trend_down_intact  = c0 < lt * 1.03

        if ema_structure_bear and price_below_mid and adx >= adx_min and trend_down_intact:

            # Rally récent vers l'EMA fast
            rally_found = False
            rally_high  = 0.0
            for k in range(1, 6):
                c_k   = float(close.iloc[-1 - k])
                ef_k  = float(ef.iloc[-1 - k])
                h_k   = float(high.iloc[-1 - k])
                in_zone = abs(c_k - ef_k) / ef_k <= pb_zone * 2.0
                slight_above = ef_k * (1 - pb_zone) <= c_k <= ef_k * (1 + pb_zone * 1.5)
                if in_zone or slight_above:
                    rally_found = True
                    rally_high  = max(rally_high, h_k)

            price_at_ema_bear = abs(c0 - lf) / lf <= pb_zone * 1.5
            price_below_ema   = c0 <= lf * (1 + pb_zone * 0.5)
            bearish_bar       = c0 < c1
            rsi_rejection     = (rsi_now <= (100 - rsi_pb_low) and
                                 rsi_now >= (100 - rsi_pb_high - 15) and
                                 rsi_now <= rsi_prev + 3)
            ef_descending     = (lf - float(ef.iloc[-4])) / lf * 100 < 0
            vol_ok_s          = vol_ratio >= vol_min

            if (rally_found and (price_at_ema_bear or price_below_ema) and
                    bearish_bar and rsi_rejection and ef_descending and vol_ok_s):

                ema_stack_b   = (lm - lf) / lm * 100
                adx_bonus     = min((adx - adx_min) / 20, 1.0) * 0.10
                rsi_quality_b = 1.0 - abs(rsi_now - 52) / 25
                rsi_bonus_b   = max(rsi_quality_b * 0.06, 0)
                vol_bonus_b   = min((vol_ratio - vol_min) * 0.05, 0.08)
                rally_depth   = (rally_high - lf) / lf * 100
                pb_bonus_b    = 0.06 if 0.1 <= rally_depth <= 1.5 else 0.02
                ema_bonus_b   = min(ema_stack_b * 3, 0.10)

                score = min(
                    0.62
                    + adx_bonus
                    + rsi_bonus_b
                    + vol_bonus_b
                    + pb_bonus_b
                    + ema_bonus_b,
                    0.92
                )

                self._last_signal_bar = self._call_count
                return {
                    "score":      round(score, 3),
                    "side":       "short",
                    "name":       self.name,
                    "atr":        atr,
                    "indicators": {
                        "ema_fast":  round(lf, 2), "ema_mid":  round(lm, 2),
                        "ema_slow":  round(ls, 2), "ema200":   round(lt, 2),
                        "rsi":       round(rsi_now, 1), "adx": round(adx, 1),
                        "vol_ratio": round(vol_ratio, 2), "rally_high": round(rally_high, 2),
                    },
                    "conditions": [
                        f"EMA structure: {lf:.0f} < {lm:.0f} < {ls:.0f} ✓",
                        f"Prix ({c0:.0f}) < EMA mid ({lm:.0f}) ✓",
                        f"Rally rejeté vers EMA{ema_fast} ✓",
                        f"Bougie baissière ✓ | RSI {rsi_now:.0f} | ADX {adx:.0f}",
                    ],
                    "reason": (
                        f"Pullback SHORT EMA{ema_fast} — rally rejeté "
                        f"RSI={rsi_now:.0f} ADX={adx:.0f} vol={vol_ratio:.1f}x"
                    ),
                }

        return self._none(
            f"EMA: {'bull' if ema_structure_bull else 'bear' if ema_structure_bear else 'neutre'} | "
            f"ADX={adx:.0f} | RSI={rsi_now:.0f} | vol={vol_ratio:.1f}x"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}

    def _rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        d = close.diff()
        g = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        l = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        return 100 - 100 / (1 + g / l.replace(0, np.nan))

    def _adx(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period * 2: return 0.0
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        up = h.diff().clip(lower=0); down = (-l.diff()).clip(lower=0)
        pdm = up.where(up > down, 0.0); mdm = down.where(down > up, 0.0)
        tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        atr14 = tr.ewm(span=period, adjust=False).mean().replace(0, np.nan)
        dip   = 100 * pdm.ewm(span=period, adjust=False).mean() / atr14
        dim   = 100 * mdm.ewm(span=period, adjust=False).mean() / atr14
        dx    = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
        v     = float(dx.ewm(span=period, adjust=False).mean().iloc[-1])
        return v if not np.isnan(v) else 0.0

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        v  = float(tr.rolling(period).mean().iloc[-1])
        return v if v > 0 else 0.0
