"""Stratégie Fear & Momentum — rebond sur dips en tendance haussière, short sur exhaustion."""
import logging
from typing import Any, Dict, List

import polars as pl

from app.core.indicators import (
    adx_val as calc_adx,
)
from app.core.indicators import (
    atr_series as calc_atr_series,
)
from app.core.indicators import (
    atr_val as calc_atr,
)
from app.core.indicators import (
    ema_window,
    htf_trend,
    macd_hist_last3,
    market_structure,
    pre_val,
)
from app.core.indicators import (
    rsi as calc_rsi,
)
from app.core.indicators import (
    vol_ratio as calc_vol,
)
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "fear_momentum"

    timeframes: List[str] = ["1h", "1d"]

    param_space: Dict[str, List] = {
        "ema_fast":        [13, 21, 34],
        "ema_mid":         [50],
        "rsi_fear":        [35, 38, 42, 45],
        "rsi_greed":       [58, 62, 65],
        "vol_cap_min":     [1.5, 1.8, 2.2, 2.5],
        "atr_max_dip":     [3.0, 4.0, 5.0],
        "ema200_max_dist": [0.05, 0.08, 0.12],
        "cooldown":        [15, 18, 22, 25],
        "rr_min":          [1.4, 1.6, 2.0],
    }

    fixed_params: Dict[str, Any] = {}

    def __init__(self):
        self._last_signal: Dict[str, int] = {}
        self._call_count:  Dict[str, int] = {}
        # Réutilisation causale (histogramme MACD / EMA custom) si params non défaut.
        self._bt_full_df = None
        self._macd_cache: Dict[tuple, Any] = {}
        self._ema_cache:  Dict[tuple, Any] = {}

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Mémorise le df complet pour réutiliser l'histogramme MACD (causal)."""
        self._bt_full_df = df

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get("fear_momentum", {})
        ema_trend = int(p.get("ema_trend", 200))
        return max(ema_trend + 10, 240)

    # ─────────────────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get("fear_momentum", {})

        ema_fast    = int(p.get("ema_fast",        21))
        ema_trend   = int(p.get("ema_trend",       200))
        ema_mid     = int(p.get("ema_mid",          50))
        rsi_fear    = float(p.get("rsi_fear",       42))   # RSI < X → zone de peur
        rsi_greed   = float(p.get("rsi_greed",      62))   # RSI > X → zone de greed
        vol_cap_min = float(p.get("vol_cap_min",   1.8))   # volume bougie capitulation
        atr_max_dip = float(p.get("atr_max_dip",   4.0))   # dip max en × ATR (filtre crashes)
        cooldown    = int(p.get("cooldown",          18))
        rr_min      = float(p.get("rr_min",         1.6))
        ema200_max_dist = float(p.get("ema200_max_dist", 0.08))  # correction max sous EMA200

        sym = symbol or str(df["time"][-1]) if "time" in df.columns else "default"
        cnt = self._call_count.get(sym, 0) + 1
        self._call_count[sym] = cnt

        min_bars = max(ema_trend + 10, 240)
        if len(df) < min_bars:
            return self._none(f"EMA{ema_trend} requiert {min_bars} bougies min, {len(df)} disponibles")

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        open_  = df["open"]
        volume = df["volume"]

        # ── Calculs de base ──────────────────────────────────────────────────
        _ema_map = {20: "_pre_ema20", 50: "_pre_ema50", 200: "_pre_ema200"}
        lf   = pre_val(df, _ema_map.get(ema_fast, ""))  or float(
            ema_window(df, ema_fast,  full_df=self._bt_full_df, cache=self._ema_cache)[-1])
        lm   = pre_val(df, _ema_map.get(ema_mid, ""))   or float(
            ema_window(df, ema_mid,   full_df=self._bt_full_df, cache=self._ema_cache)[-1])
        lt   = pre_val(df, _ema_map.get(ema_trend, "")) or float(
            ema_window(df, ema_trend, full_df=self._bt_full_df, cache=self._ema_cache)[-1])

        c0   = float(close[-1])
        h0   = float(high[-1])
        l0   = float(low[-1])
        o0   = float(open_[-1])

        atr_val   = pre_val(df, "_pre_atr14") or calc_atr(df, 14)
        if atr_val <= 0:
            return self._none()

        # ATR series : colonne pré-calculée si dispo, sinon recalcul
        atr_s     = df["_pre_atr14"] if "_pre_atr14" in df.columns else calc_atr_series(df, 14)
        _rsi_s    = df["_pre_rsi14"] if "_pre_rsi14" in df.columns else calc_rsi(close, 14)
        rsi0      = float(_rsi_s[-1])
        rsi1      = float(_rsi_s[-2])
        rsi2      = float(_rsi_s[-3])
        rsi3      = float(_rsi_s[-4])

        vr        = pre_val(df, "_pre_volratio20") or calc_vol(df)
        adx_val   = pre_val(df, "_pre_adx14")      or calc_adx(df, 14)
        htf       = htf_trend(df_htf, df_ltf=df)
        ms_window = int(p.get("market_struct_window", 5))
        struct    = market_structure(high, low, window=ms_window)

        # MACD(12,26,9) : colonne pré-calculée O(1), sinon série causale réutilisée.
        mh0 = pre_val(df, "_pre_macd_hist")
        if mh0 is None:
            mh0, mh1, mh2 = macd_hist_last3(
                df, 12, 26, 9, full_df=self._bt_full_df, cache=self._macd_cache)
        else:
            _mhist = df["_pre_macd_hist"]
            mh1 = float(_mhist[-2])
            mh2 = float(_mhist[-3])

        # Borne la fenêtre : rolling_mean(20) sur toute la colonne croissante ne
        # sert qu'à lire la dernière valeur → O(n)/barre (O(n²) en backtest).
        vol_avg  = float(volume.tail(20).rolling_mean(20)[-1])

        # ── Cooldown ─────────────────────────────────────────────────────────
        if cnt - self._last_signal.get(sym, -999) < cooldown:
            return self._none(f"Cooldown {cnt - self._last_signal.get(sym,0)}/{cooldown}")

        # ═══════════════════════════════════════════════════════════════════════
        #  LONG — Fear in Uptrend
        # ═══════════════════════════════════════════════════════════════════════

        # ── Condition 0 : Tendance haussière structurelle ────────────────────
        # EMA21 > EMA50 (tendance intermédiaire OK)
        # Prix dans les 8% autour de EMA200 (en correction, pas en crash)
        trend_bull  = lf > lm and c0 >= lt * (1 - ema200_max_dist)
        # HTF doit être au moins neutre (on ne combat pas la tendance supérieure)
        htf_ok      = htf >= 0

        if not (trend_bull and htf_ok):
            # Tentative short side (cherche greed exhaustion)
            pass  # handled below
        else:
            # ── Identifier le dip récent (5 dernières barres) ─────────────────
            # Le prix doit avoir baissé d'au moins 1× ATR depuis un récent haut
            recent_high = float(high[-6:-1].max())
            dip_depth   = (recent_high - c0) / atr_val

            # Trop petit : pas un vrai dip (juste du bruit)
            # Trop grand  : potentiel renversement, pas un dip sain
            dip_valid = 0.8 <= dip_depth <= atr_max_dip

            if dip_valid:

                # ── Signal 1 : RSI Fear Zone + Recovery ─────────────────────
                # RSI est tombé en zone de peur (<rsi_fear) ET commence à remonter
                rsi_was_in_fear = min(rsi1, rsi2, rsi3) < rsi_fear
                rsi_recovering  = rsi0 > rsi1 and rsi0 > rsi_fear * 0.85
                sig1 = rsi_was_in_fear and rsi_recovering

                # ── Signal 2 : Volume Capitulation ───────────────────────────
                # Une des 3 dernières bougies a eu un volume de capitulation
                # (volume > vol_cap_min× avec corps baissier = vendeurs épuisés)
                cap_found = False
                for k in range(1, 4):
                    v_k  = float(volume[-1 - k])
                    o_k  = float(open_[-1 - k])
                    c_k  = float(close[-1 - k])
                    bearish_body = c_k < o_k
                    if (v_k / max(vol_avg, 1e-9) >= vol_cap_min) and bearish_body:
                        cap_found = True
                        break
                sig2 = cap_found

                # ── Signal 3 : Marteau / Pinbar sur support ──────────────────
                # Mèche basse longue : le bas descend mais la clôture remonte
                # = rejet du bas = acheteurs ont absorbé la vente
                body      = abs(c0 - o0)
                lower_wick = min(o0, c0) - l0
                upper_wick = h0 - max(o0, c0)
                hammer = (lower_wick >= body * 1.5       # mèche basse > 1.5× le corps
                          and lower_wick >= upper_wick * 2  # mèche basse dominante
                          and c0 >= o0)                      # bougie verte (force)
                # Alternative : doji sur support (incertitude après une baisse)
                doji = body < atr_val * 0.25 and lower_wick >= atr_val * 0.4
                sig3 = hammer or doji

                # ── Signal 4 : Divergence RSI Haussière ──────────────────────
                # Prix fait un bas plus bas, mais RSI fait un bas moins bas
                # = les vendeurs perdent de la force
                prev_low = float(low[-6:-2].min())
                if l0 < prev_low:
                    prev_rsi_low = float(_rsi_s[-6:-2].min())
                    rsi_div_bull = rsi0 > prev_rsi_low  # RSI ne fait pas un nouveau bas
                else:
                    rsi_div_bull = False
                sig4 = rsi_div_bull

                # ── Signal 5 : MACD Divergence / Recovery ────────────────────
                # Histogramme MACD commence à remonter depuis un creux
                # (même si encore négatif : le momentum baissier ralentit)
                macd_floor  = mh0 > mh1 and mh0 > mh2    # remonte depuis le bas
                macd_x_bull = mh1 < 0 and mh0 > 0        # cross zéro haussier (fort)
                sig5 = macd_floor or macd_x_bull

                # ── Signal 6 : ATR Contraction avant le dip ──────────────────
                # Le dip survient après une période de faible volatilité
                # = le marché s'est compressé avant → le dip est sain, pas panique
                atr_before_dip  = float(atr_s[-8:-3].mean())
                atr_recent      = float(atr_s[-3:-1].mean())
                # ATR récent n'est pas explosif (< 2× l'ATR pré-dip)
                atr_not_exploding = atr_recent < atr_before_dip * 2.2
                sig6 = atr_not_exploding

                # ── Signal 7 : Structure de marché intacte ───────────────────
                # Le marché fait toujours des HH/HL (trend intact)
                sig7 = struct > 0

                # ── Signal 8 : Prix revient au-dessus du support EMA ─────────
                # La bougie courante clôture AU-DESSUS de l'EMA fast
                # = le niveau a tenu, les acheteurs reprennent le contrôle
                price_reclaim = c0 >= lf * 0.998  # dans les 0.2% de l'EMA fast
                sig8 = price_reclaim

                # ── Comptage des signaux et score ─────────────────────────────
                signals_long = [sig1, sig2, sig3, sig4, sig5, sig6, sig7, sig8]
                n_sig = sum(signals_long)

                # Minimum 3 signaux sur 8 pour un setup de base
                # (les stratégies concurrentes n'en demandent qu'un seul)
                if n_sig < 3:
                    reasons = []
                    if not sig1:
                        reasons.append(f"RSI pas en zone peur ({rsi0:.0f})")
                    if not sig2:
                        reasons.append("Pas de capitulation")
                    if not sig3:
                        reasons.append("Pas de marteau/doji")
                    if not sig4:
                        reasons.append("Pas de divergence RSI")
                    if not sig5:
                        reasons.append(f"MACD pas en recovery ({mh0:+.5f})")
                    return self._none(f"Seulement {n_sig}/8 signaux: " + " | ".join(reasons[:2]))

                # Signaux premium : forte probabilité supplémentaire
                premium = (sig1 and sig2)    # peur + capitulation = combo classique
                or_premium2 = (sig4 and sig5) # divergence double = signal fort

                # ── R:R check ────────────────────────────────────────────────
                # Stop sous le plus bas du dip (invalide le setup)
                dip_low   = float(low[-6:].min())
                stop_long = dip_low - atr_val * 0.25
                risk      = c0 - stop_long
                if risk <= 0:
                    return self._none("Stop invalide")

                # Target = 1× le mouvement qui précédait la correction
                # (retour vers le récent haut = objectif naturel)
                target   = recent_high
                reward   = target - c0
                rr       = reward / risk if risk > 0 else 0

                if rr < rr_min:
                    return self._none(f"R:R {rr:.2f} < {rr_min} (target={target:.0f} stop={stop_long:.0f})")

                # ── Score ─────────────────────────────────────────────────────
                base       = 0.65

                # Bonus par signal présent (pondérés par importance)
                sig_scores = {
                    sig1: 0.08,   # RSI fear + recovery — critique
                    sig2: 0.07,   # Volume capitulation — critique
                    sig3: 0.06,   # Marteau/doji — important
                    sig4: 0.07,   # Divergence RSI — important
                    sig5: 0.06,   # MACD recovery — important
                    sig6: 0.03,   # ATR contraction — contextuel
                    sig7: 0.04,   # Structure marché — contextuel
                    sig8: 0.04,   # Prix reclaim EMA — contextuel
                }
                sig_bonus = sum(v for s, v in sig_scores.items() if s)

                # Bonus R:R (meilleur R:R = signal plus fort)
                rr_bonus   = min((rr - rr_min) * 0.03, 0.08)

                # Bonus ADX : tendance confirmée
                adx_bonus  = 0.04 if adx_val >= 22 else 0.0

                # Bonus HTF : tendance supérieure confirme
                htf_bonus  = 0.04 if htf > 0 else 0.0

                # Bonus premium : combos de signaux forts
                premium_bonus  = 0.05 if premium else 0.0      # peur + capitulation
                premium2_bonus = 0.04 if or_premium2 else 0.0  # divergence double

                # Malus si très proche de l'EMA200 (zone de danger)
                ema200_dist = (c0 - lt) / lt
                dist_malus  = -0.05 if ema200_dist < -0.03 else 0.0

                score = min(
                    base + sig_bonus + rr_bonus + adx_bonus + htf_bonus
                    + premium_bonus + premium2_bonus + dist_malus,
                    0.95
                )

                self._last_signal[sym] = cnt
                return {
                    "score":      round(score, 3),
                    "side":       "long",
                    "name":       self.name,
                    "atr":        atr_val,
                    "stop_hint":  round(stop_long, 2),
                    "indicators": {
                        "rsi":          round(rsi0, 1),
                        "rsi_prev":     round(rsi1, 1),
                        "ema_fast":     round(lf, 2),
                        "ema_mid":      round(lm, 2),
                        "ema200":       round(lt, 2),
                        "adx":          round(adx_val, 1),
                        "vol_ratio":    round(vr, 2),
                        "macd_hist":    round(mh0, 6),
                        "dip_depth_atr":round(dip_depth, 2),
                        "n_signals":    n_sig,
                        "rr":           round(rr, 2),
                        "htf_trend":    htf,
                        "struct":       struct,
                        "hammer":       hammer,
                        "cap_found":    cap_found,
                        "rsi_div":      sig4,
                    },
                    "conditions": [
                        f"Tendance haussière : EMA{ema_fast}({lf:.0f}) > EMA50({lm:.0f}) ✓",
                        f"Dip {dip_depth:.1f}× ATR depuis {recent_high:.0f} ✓",
                        f"Signaux actifs {n_sig}/8 : "
                        + ", ".join(filter(None, [
                            "RSI fear↑" if sig1 else "",
                            "Cap." if sig2 else "",
                            "Marteau" if hammer else ("Doji" if doji else ""),
                            "RSI div" if sig4 else "",
                            "MACD↑" if sig5 else "",
                            "ATR OK" if sig6 else "",
                            "HH/HL" if sig7 else "",
                            "Reclaim" if sig8 else "",
                        ])),
                        f"R:R {rr:.2f} ({c0:.0f} → {target:.0f}, stop {stop_long:.0f}) ✓",
                        f"HTF: {'haussier' if htf>0 else 'neutre'} | ADX {adx_val:.0f}",
                    ],
                    "reason": (
                        f"Fear&Mom LONG {n_sig}/8 sigs — dip {dip_depth:.1f}×ATR "
                        f"RSI={rsi0:.0f}↑ R:R={rr:.2f} "
                        f"{'cap+' if sig2 else ''}{'div+' if sig4 else ''}{'MACD+' if sig5 else ''}"
                    ),
                }

        # ═══════════════════════════════════════════════════════════════════════
        #  SHORT — Greed Exhaustion (Dead-cat bounce)
        # ═══════════════════════════════════════════════════════════════════════

        # Tendance baissière structurelle
        trend_bear  = lf < lm and c0 <= lt * 1.08
        htf_ok_s    = htf <= 0

        if trend_bear and htf_ok_s:
            # Rally récent dans une tendance baissière
            recent_low   = float(low[-6:-1].min())
            rally_height = (c0 - recent_low) / atr_val
            rally_valid  = 0.8 <= rally_height <= 4.0

            if rally_valid:
                # Inverse des signaux longs
                rsi_was_greedy = max(rsi1, rsi2, rsi3) > (100 - rsi_fear)
                rsi_topping    = rsi0 < rsi1 and rsi0 < rsi_greed * 1.1
                sig1_s = rsi_was_greedy and rsi_topping

                # Volume faible sur le rally (no conviction = dead-cat)
                low_vol_rally = vr < 1.0
                sig2_s = low_vol_rally

                # Shooting star / Doji haut
                body_s       = abs(c0 - o0)
                upper_wick_s = h0 - max(o0, c0)
                lower_wick_s = min(o0, c0) - l0
                shoot_star   = (upper_wick_s >= body_s * 1.5
                                and upper_wick_s >= lower_wick_s * 2
                                and c0 <= o0)
                sig3_s = shoot_star

                # RSI divergence baissière
                prev_high_price = float(high[-6:-2].max())
                if h0 > prev_high_price:
                    prev_rsi_high = float(_rsi_s[-6:-2].max())
                    sig4_s = rsi0 < prev_rsi_high
                else:
                    sig4_s = False

                # MACD commence à baisser
                sig5_s = (mh0 < mh1 and mh0 < mh2) or (mh1 > 0 and mh0 < 0)

                sig6_s = struct < 0  # LL/LH structure

                n_sig_s = sum([sig1_s, sig2_s, sig3_s, sig4_s, sig5_s, sig6_s])

                if n_sig_s >= 2:
                    # ── Filtre anti-short en zone de survente ──────────────────
                    # Ne pas shorter si RSI déjà en zone de peur (< rsi_fear) :
                    # on est trop tard dans la jambe baissière, risque de squeeze élevé.
                    if rsi0 < rsi_fear:
                        return self._none(
                            f"RSI oversold ({rsi0:.0f} < {rsi_fear}) — short interdit "
                            f"(risque squeeze, entrée trop tardive dans la jambe)"
                        )
                    # Ne pas shorter si MACD histogramme remonte depuis un bas (divergence) :
                    # le momentum baissier s'essouffle, le rebond est probable.
                    if mh0 > mh1 and mh0 > -5:
                        return self._none(
                            f"MACD divergence haussière ({mh0:+.5f} > {mh1:+.5f}) — short interdit"
                        )
                    rally_high_s = float(high[-6:].max())
                    stop_short   = rally_high_s + atr_val * 0.25
                    risk_s       = stop_short - c0
                    if risk_s <= 0:
                        return self._none("Stop invalide (short)")

                    target_s = recent_low
                    reward_s = c0 - target_s
                    rr_s     = reward_s / risk_s if risk_s > 0 else 0
                    if rr_s < rr_min:
                        return self._none(f"R:R short {rr_s:.2f} < {rr_min}")

                    base_s    = 0.63
                    sig_b_s   = (0.08*sig1_s + 0.06*sig2_s + 0.07*sig3_s +
                                 0.07*sig4_s + 0.06*sig5_s + 0.04*sig6_s)
                    rr_b_s    = min((rr_s - rr_min) * 0.03, 0.06)
                    htf_b_s   = 0.04 if htf < 0 else 0.0

                    score_s = min(base_s + sig_b_s + rr_b_s + htf_b_s, 0.93)

                    self._last_signal[sym] = cnt
                    return {
                        "score":      round(score_s, 3),
                        "side":       "short",
                        "name":       self.name,
                        "atr":        atr_val,
                        "stop_hint":  round(stop_short, 2),
                        "indicators": {
                            "rsi":       round(rsi0, 1),
                            "ema_fast":  round(lf, 2),
                            "ema200":    round(lt, 2),
                            "adx":       round(adx_val, 1),
                            "vol_ratio": round(vr, 2),
                            "n_signals": n_sig_s,
                            "rr":        round(rr_s, 2),
                        },
                        "conditions": [
                            f"Tendance baissière : EMA{ema_fast}({lf:.0f}) < EMA50({lm:.0f}) ✓",
                            f"Rally {rally_height:.1f}× ATR — {n_sig_s}/6 signaux d'exhaustion ✓",
                            f"R:R {rr_s:.2f} ✓",
                        ],
                        "reason": (
                            f"Fear&Mom SHORT {n_sig_s}/6 sigs — rally {rally_height:.1f}×ATR "
                            f"RSI={rsi0:.0f}↓ R:R={rr_s:.2f}"
                        ),
                    }

        return self._none(
            f"trend={'bull' if trend_bull else 'bear' if trend_bear else 'neutre'} "
            f"RSI={rsi0:.0f} htf={htf} struct={struct}"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
