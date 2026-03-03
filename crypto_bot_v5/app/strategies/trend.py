"""
Stratégie Trend Following — V4

Lacunes V3 corrigées :
  - cross_lookback réduit à 6 (évite d'entrer sur un cross vieux de 12h)
  - Filtre "no-entry zone" : ne pas entrer si prix > 2% au-dessus de l'EMA fast
    (overextension = mauvais R:R)
  - EMA200 assouplie : tolérance de 4% sous l'EMA200 (corrections normales BTC)
  - Condition de continuation durcie : slope_pct relatif à l'ATR (scale-agnostique)
  - Score base rehaussé : 0.62 (était 0.56), meilleure sélectivité
  - Short conditions symétriques et cohérentes
  - ADX computation via EWM (plus stable)
"""
import logging
from typing import Dict, Any
import numpy as np
import pandas as pd
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "trend"

    def __init__(self):
        self._last_signal_bar: int = -999
        self._call_count: int = 0

    def score(self, df: pd.DataFrame, params: dict = None) -> Dict[str, Any]:
        self._call_count += 1
        p = (params or {}).get("trend", {})

        ema_fast       = int(p.get("ema_fast",       21))
        ema_slow       = int(p.get("ema_slow",       50))
        ema_trend      = int(p.get("ema_trend",     200))
        rsi_low        = float(p.get("rsi_low",      28))   # slightly wider
        rsi_high       = float(p.get("rsi_high",     74))
        adx_min        = float(p.get("adx_min",      18))   # abaissé → plus de signaux
        vol_min        = float(p.get("vol_min",      0.9))  # abaissé
        cross_lookback = int(p.get("cross_lookback",  6))   # ↓ 12→6 — cross récents seulement
        cooldown       = int(p.get("cooldown",        20))
        # Filtre overextension : ne pas entrer si trop loin de l'EMA fast
        overext_pct    = float(p.get("overext_pct",  0.020)) # 2% max au-dessus EMA
        # Tolérance EMA200 : permettre corrections (-4%)
        ema200_tol     = float(p.get("ema200_tol",   0.04))

        min_bars = max(ema_trend + 10, 220)
        if len(df) < min_bars:
            return self._none()

        close = df["close"].astype(float)
        high  = df["high"].astype(float)
        low   = df["low"].astype(float)

        # ── EMAs ─────────────────────────────────────────────────────────────
        ema_f = close.ewm(span=ema_fast,  adjust=False).mean()
        ema_s = close.ewm(span=ema_slow,  adjust=False).mean()
        ema_t = close.ewm(span=ema_trend, adjust=False).mean()

        lf    = float(ema_f.iloc[-1])
        ls    = float(ema_s.iloc[-1])
        lt    = float(ema_t.iloc[-1])
        c_now = float(close.iloc[-1])

        # ── ATR ───────────────────────────────────────────────────────────────
        atr = self._atr(df, 14)
        if atr <= 0:
            return self._none()

        # ── RSI ───────────────────────────────────────────────────────────────
        rsi_now = float(self._rsi(close, 14).iloc[-1])

        # ── ADX ───────────────────────────────────────────────────────────────
        adx = self._adx(df, 14)

        # ── Volume ────────────────────────────────────────────────────────────
        vol_avg   = df["volume"].rolling(20).mean().iloc[-1]
        vol_now   = float(df["volume"].iloc[-1])
        vol_ratio = float(vol_now / max(vol_avg, 1e-9))

        # ── Pente EMA fast — normalisée par ATR (scale-agnostique) ──────────
        # Précédent : pente en % du prix → dépend du niveau (65k btc = toujours grand)
        # Nouveau   : pente en ATR → comparable quel que soit le niveau
        slope_atr = (lf - float(ema_f.iloc[-4])) / atr

        # ── Cross EMA récent (lookback réduit à 6) ──────────────────────────
        cross_bull = False
        cross_bear = False
        for k in range(1, min(cross_lookback + 1, len(df) - 2)):
            pf = float(ema_f.iloc[-1 - k])
            ps = float(ema_s.iloc[-1 - k])
            if pf <= ps and lf > ls: cross_bull = True; break
            if pf >= ps and lf < ls: cross_bear = True; break

        # ── Filtre overextension : ne pas chasser ──────────────────────────
        overextended = (
            (lf > ls and (c_now - lf) / lf > overext_pct) or
            (lf < ls and (lf - c_now) / lf > overext_pct)
        )

        # ── Structure de marché ─────────────────────────────────────────────
        hh_hl = self._check_structure(high, low, n_pivots=4)

        # ── Cooldown ─────────────────────────────────────────────────────────
        if self._call_count - self._last_signal_bar < cooldown:
            return self._none(f"Cooldown")

        indicators = {
            "ema_fast": round(lf, 2), "ema_slow": round(ls, 2), "ema200": round(lt, 2),
            "rsi": round(rsi_now, 1), "adx": round(adx, 1),
            "vol_ratio": round(vol_ratio, 2), "slope_atr": round(slope_atr, 4),
            "overextended": overextended, "hh_hl": hh_hl,
        }

        # ═══ LONG ════════════════════════════════════════════════════════════
        cond_ema   = lf > ls
        # EMA200 assouplie : prix peut être jusqu'à 4% sous l'EMA200 (corrections)
        cond_trend = c_now >= lt * (1 - ema200_tol)
        # Entry : cross récent OU continuation avec pente ATR significative
        cond_entry = cross_bull or (lf > ls and slope_atr > 0.03)
        cond_adx   = adx >= adx_min
        cond_rsi   = rsi_low <= rsi_now <= rsi_high
        cond_vol   = vol_ratio >= vol_min
        # Rejet overextension : pas d'entrée si déjà trop loin de l'EMA
        cond_noext = not overextended

        if cond_ema and cond_trend and cond_entry and cond_adx and cond_rsi and cond_vol and cond_noext:
            ema_spread  = (lf - ls) / ls * 100
            adx_norm    = min((adx - adx_min) / 25, 1.0)
            rsi_optimal = max(1.0 - abs(rsi_now - 52) / 22, 0)
            vol_bonus   = min((vol_ratio - vol_min) * 0.04, 0.09)
            cross_bonus = 0.07 if cross_bull else 0.0
            struct_bonus = 0.05 if hh_hl > 0 else 0.0
            slope_bonus  = min(slope_atr * 0.04, 0.07)

            score = min(
                0.62
                + min(ema_spread * 4, 0.12)
                + adx_norm * 0.12
                + rsi_optimal * 0.07
                + vol_bonus
                + cross_bonus
                + struct_bonus
                + slope_bonus,
                0.94
            )
            self._last_signal_bar = self._call_count
            return {
                "score": round(score, 3), "side": "long", "name": self.name,
                "atr": atr, "indicators": indicators,
                "conditions": [
                    f"EMA{ema_fast} ({lf:.0f}) > EMA{ema_slow} ({ls:.0f}) ✓",
                    f"Prix ({c_now:.0f}) ≥ EMA200 ({lt:.0f}) ×{1-ema200_tol:.2f} ✓",
                    f"ADX {adx:.1f} ≥ {adx_min} ✓",
                    f"RSI {rsi_now:.0f} ∈ [{rsi_low}-{rsi_high}] ✓",
                    f"Vol {vol_ratio:.2f}x | Pas overextended ✓",
                    f"Entry: {'Cross récent' if cross_bull else 'Continuation'} "
                    f"(slope {slope_atr:+.3f} ATR)",
                    f"Structure: {'HH/HL ✓' if hh_hl > 0 else 'neutre'}",
                ],
                "reason": (
                    f"Trend LONG {'cross' if cross_bull else 'cont'} | "
                    f"ADX={adx:.0f} RSI={rsi_now:.0f} vol={vol_ratio:.1f}x slope={slope_atr:.3f}"
                ),
            }

        # ═══ SHORT ═══════════════════════════════════════════════════════════
        cond_ema_s   = lf < ls
        cond_trend_s = c_now <= lt * (1 + ema200_tol)
        cond_entry_s = cross_bear or (lf < ls and slope_atr < -0.03)
        cond_rsi_s   = (100 - rsi_high) <= rsi_now <= (100 - rsi_low)
        cond_noext_s = not overextended

        if cond_ema_s and cond_trend_s and cond_entry_s and cond_adx and cond_rsi_s and cond_vol and cond_noext_s:
            ema_spread_s = (ls - lf) / ls * 100
            adx_norm_s   = min((adx - adx_min) / 25, 1.0)
            rsi_opt_s    = max(1.0 - abs(rsi_now - 48) / 22, 0)
            vol_bonus_s  = min((vol_ratio - vol_min) * 0.04, 0.09)
            cross_bonus_s = 0.07 if cross_bear else 0.0
            struct_bonus_s = 0.05 if hh_hl < 0 else 0.0

            score = min(
                0.62
                + min(ema_spread_s * 4, 0.12)
                + adx_norm_s * 0.12
                + rsi_opt_s * 0.07
                + vol_bonus_s
                + cross_bonus_s
                + struct_bonus_s,
                0.93
            )
            self._last_signal_bar = self._call_count
            return {
                "score": round(score, 3), "side": "short", "name": self.name,
                "atr": atr, "indicators": indicators,
                "conditions": [
                    f"EMA{ema_fast} ({lf:.0f}) < EMA{ema_slow} ({ls:.0f}) ✓",
                    f"Prix ({c_now:.0f}) ≤ EMA200 ({lt:.0f}) ×{1+ema200_tol:.2f} ✓",
                    f"ADX {adx:.1f} | RSI {rsi_now:.0f} | Vol {vol_ratio:.2f}x",
                ],
                "reason": (
                    f"Trend SHORT {'cross' if cross_bear else 'cont'} | "
                    f"ADX={adx:.0f} RSI={rsi_now:.0f} slope={slope_atr:.3f}"
                ),
            }

        missing = []
        if not cond_ema:   missing.append(f"EMA align ✗")
        if not cond_trend: missing.append(f"EMA200 ✗")
        if not cond_adx:   missing.append(f"ADX {adx:.0f}<{adx_min} ✗")
        if not cond_rsi:   missing.append(f"RSI {rsi_now:.0f} ✗")
        if overextended:   missing.append(f"Overextended ✗")
        return self._none(" | ".join(missing))

    def _check_structure(self, high: pd.Series, low: pd.Series, n_pivots: int = 4) -> int:
        if len(high) < n_pivots * 8:
            return 0
        window = 5
        highs = [float(high.iloc[-i*window-1:-i*window+window-1].max())
                 for i in range(1, n_pivots + 1)]
        lows  = [float(low.iloc[-i*window-1:-i*window+window-1].min())
                 for i in range(1, n_pivots + 1)]
        hh = sum(1 for i in range(len(highs)-1) if highs[i] > highs[i+1])
        hl = sum(1 for i in range(len(lows)-1)  if lows[i]  > lows[i+1])
        ll = sum(1 for i in range(len(highs)-1) if highs[i] < highs[i+1])
        lh = sum(1 for i in range(len(lows)-1)  if lows[i]  < lows[i+1])
        if (hh + hl) >= n_pivots - 1: return 1
        if (ll + lh) >= n_pivots - 1: return -1
        return 0

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}

    def _rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        d = close.diff()
        g = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
        l = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
        return 100 - (100 / (1 + g / l.replace(0, np.nan)))

    def _adx(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period * 2: return 0.0
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        up, down = h.diff().clip(lower=0), (-l.diff()).clip(lower=0)
        pdm = up.where(up > down, 0.0); mdm = down.where(down > up, 0.0)
        tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean().replace(0, np.nan)
        dip = 100 * pdm.ewm(span=period, adjust=False).mean() / atr
        dim = 100 * mdm.ewm(span=period, adjust=False).mean() / atr
        dx  = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
        v   = float(dx.ewm(span=period, adjust=False).mean().iloc[-1])
        return v if not np.isnan(v) else 0.0

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        v  = float(tr.rolling(period).mean().iloc[-1])
        return v if v > 0 else 0.0
