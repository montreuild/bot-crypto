"""Stratégie Smart Money Concepts (SMC) — moteur ``app/core/smc.py``.

Deux familles de setups, prises uniquement dans le sens de la structure ou sur
rejet net d'une poche de liquidité :

  1. SWEEP_REVERSAL — une mèche prend la liquidité (equal lows / swing low pour
     un long, miroir pour un short) puis la bougie clôture de retour du bon
     côté (rejet). Les stops des retail viennent d'être consommés : entrée
     dans le sens opposé au sweep, stop sous l'extrême de la mèche.

  2. OB_RETEST — après une cassure de structure (BOS/CHoCH), le prix revient
     pour la première fois dans l'order block à l'origine de l'impulsion
     (zone de demande pour un long, d'offre pour un short) : entrée sur la
     zone, stop de l'autre côté de l'order block.

  3. BREAKER_RETEST — un order block invalidé inverse sa polarité (breaker
     block) : premier retest de la zone dans le nouveau sens (les stops
     piégés dans l'ancienne zone alimentent le mouvement).

Filtres DURS (validés sur BTC/USDC 30m→1d, 2019-2026) :
  - structure alignée obligatoire (long uniquement en trend haussier…) ;
  - côté momentum du range : pas de long en zone discount, pas de short en
    zone premium — sur crypto la force appelle la force, le « deep discount »
    d'une tendance haussière est le plus souvent une structure qui casse ;
  - biais EMA(``ema_filter_len``) : long au-dessus, short en dessous.

Confluences additionnées au score de base 0.50 (cap 1.0) :
  +0.10 structure alignée (toujours vrai pour OB_RETEST / BREAKER_RETEST)
  +0.10 pool « véritable » (equal highs/lows) plutôt que swing isolé (sweep)
  +0.10 order block « strength 2 » (son impulsion a cassé la structure)
  +0.10 prix du côté momentum fort (premium pour un long, discount pour un short)
  +0.05 chevauchement avec un FVG ouvert de même direction
  +0.05 volume > ``vol_confluence`` × SMA20(volume)
  +0.05 bougie de rejet colorée dans le sens du trade
  +0.05 tap de la trendline automatique (support pour un long, résistance
        pour un short, à ``tl_tol_atr``×ATR près) — le « buy orders » des
        traders de canaux

Cibles de TP : poches de liquidité opposées ET bords opposés des liquidity
voids non comblés (zones fines = aimants), première cible satisfaisant
``min_gain_pct`` et ``min_rr``.

Sorties : bracket FIXE (pas de trailing). SL sous/за l'extrême de la zone
(+ ``sl_buffer_atr``×ATR), TP posé juste devant la prochaine poche de
liquidité opposée (front-run de ``tp_front_run_atr``×ATR), sinon fallback
``tp_rr_fallback``×R.

⚠ FILTRE DE GAIN : une position n'est retenue que si le gain potentiel
(distance entrée → TP) dépasse ``min_gain_pct`` (défaut 0.4 %) ET si le
ratio gain/risque atteint ``min_rr``. Les setups dont la cible de liquidité
la plus proche est trop courte sont rejetés (ou re-ciblés sur la poche
suivante si elle respecte les deux contraintes).
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core import smc

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "smart_money"

    # Edge validé sur BTC/USDC 2019→2026 avec les défauts : 4h (PF 1.20,
    # DD −12 %). Les TF < 2h testent négatif (bruit + frais) — laissés à
    # l'optimiseur via param_space plutôt que recommandés ici.
    timeframes: List[str] = ["2h", "4h", "1d"]
    warmup_bars: int = 260

    param_space: Dict[str, List] = {
        "swing_len":      [2, 3, 5],
        "eq_tol_atr":     [0.15, 0.25, 0.4],
        "disp_body_atr":  [1.0, 1.3, 1.6],
        "min_rr":         [1.0, 1.2, 1.5],
        "min_gain_pct":   [0.4, 0.8, 1.2],
        "sl_buffer_atr":  [0.15, 0.25, 0.5],
        "ema_filter_len": [0, 100, 200],
        "choch_exit":     [True, False],
        "use_breakers":   [True, False],
        "use_void_targets": [True, False],
    }

    fixed_params: Dict[str, Any] = {
        "swing_len":        3,
        "eq_tol_atr":       0.25,
        "disp_body_atr":    1.3,
        "ob_lookback":      5,
        "fvg_min_atr":      0.2,
        "min_gain_pct":     0.4,    # ⚠ gain potentiel minimal (%) entrée → TP
        "min_rr":           1.2,    # ratio gain/risque minimal
        "sl_buffer_atr":    0.25,
        "tp_rr_fallback":   2.0,    # TP fallback (×R) si aucune poche exploitable
        "tp_front_run_atr": 0.1,    # TP posé juste avant la poche (front-run)
        "vol_confluence":   1.2,
        "allow_counter_trend": False,  # sweeps contre-tendance interdits
        "use_breakers":     False,  # setup BREAKER_RETEST : négatif sur BTC 4h
                                    # (−163 USDC / 220 trades, validation 2026-07)
                                    # → off par défaut, exploré par l'optimiseur
        "use_void_targets": True,   # bords des liquidity voids comme cibles TP
        "tl_tol_atr":       0.3,    # tolérance du tap de trendline (×ATR)
        "ema_filter_len":   200,    # biais EMA : long si close>EMA, short si close<EMA (0 = off)
        "choch_exit":       False,  # sortie anticipée sur CHoCH contre la position
                                    # (False par défaut : coupe systématiquement en perte
                                    # sur BTC 30m→1d, cf. validation 2026-07)
        "max_window":       3000,   # fenêtre d'analyse max (bornage O(n))
        "ob_max_age":       250,    # âge max (barres) d'un OB jouable
        "pool_max_age":     500,    # âge max d'une poche utilisable comme cible
        "choch_guard_bars": 5,      # pas d'entrée contre un CHoCH < N barres
    }

    def __init__(self):
        # Cache backtest : {index_barre: signal_dict} construit en une passe.
        self._bt_signals: Optional[Dict[int, dict]] = None
        self._bt_events_opposite: Optional[Dict[int, str]] = None
        self._bt_close_ref: Optional[np.ndarray] = None

    # ── Paramètres ───────────────────────────────────────────────────────────
    def _p(self, params: dict = None) -> Dict[str, Any]:
        p = dict(self.fixed_params)
        for k, v in ((params or {}).get(self.name, {}) or {}).items():
            if k in p and v is not None:
                p[k] = v
        return p

    @staticmethod
    def _smc_params(p: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "swing_left":    int(p["swing_len"]),
            "swing_right":   int(p["swing_len"]),
            "eq_tol_atr":    float(p["eq_tol_atr"]),
            "disp_body_atr": float(p["disp_body_atr"]),
            "ob_lookback":   int(p["ob_lookback"]),
            "fvg_min_atr":   float(p["fvg_min_atr"]),
        }

    def min_bars_required(self, params: dict = None) -> int:
        return 220

    # ── Pré-calcul backtest : une seule passe sur toute la fenêtre ───────────
    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            params = getattr(self, "_bt_params", None)
            p = self._p(params)
            res = smc.analyze(df, self._smc_params(p))
            close = df["close"].to_numpy().astype(float)
            open_ = df["open"].to_numpy().astype(float)
            low   = df["low"].to_numpy().astype(float)
            high  = df["high"].to_numpy().astype(float)
            volr  = self._vol_ratio_arr(df)
            ema   = self._ema_arr(df, int(p["ema_filter_len"]))

            signals: Dict[int, dict] = {}
            event_bars = sorted(
                {ev["index"] for ev in res["_all_sweeps"] if ev["rejected"]} |
                {ob["touched_at"] for ob in res["_all_obs"]
                 if ob["touched_at"] is not None} |
                {brk["touched_at"] for brk in res["_all_breakers"]
                 if brk["touched_at"] is not None}
            )
            for i in event_bars:
                sig = self._signal_at(res, i, open_, high, low, close, volr,
                                      ema, p)
                if sig is not None:
                    signals[i] = sig

            # Événements de structure opposés (early exit CHoCH)
            opposite: Dict[int, str] = {}
            for ev in res["_all_struct_events"]:
                if ev["kind"] == "CHoCH":
                    opposite[ev["index"]] = ev["direction"]

            self._bt_signals = signals
            self._bt_events_opposite = opposite
            self._bt_close_ref = close
        except Exception as e:
            logger.warning(f"[smart_money] prepare_for_backtest KO : {e}")
            self._bt_signals = None
            self._bt_events_opposite = None
            self._bt_close_ref = None

    def _cache_valid(self, df: pl.DataFrame) -> bool:
        if self._bt_signals is None or self._bt_close_ref is None:
            return False
        idx = df.height - 1
        ref = self._bt_close_ref
        return 0 <= idx < len(ref) and abs(float(df["close"][-1]) - ref[idx]) < 1e-9

    # ── Scoring ──────────────────────────────────────────────────────────────
    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = self._p(params)
        if len(df) < self.min_bars_required(params):
            return self._none("historique insuffisant")

        # Chemin backtest : lookup O(1) dans le cache pré-calculé.
        if self._cache_valid(df):
            sig = self._bt_signals.get(df.height - 1)
            return dict(sig) if sig else self._none("aucun setup SMC")

        # Chemin live/scanner : analyse de la fenêtre (bornée à max_window).
        win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
        res = smc.analyze(win, self._smc_params(p))
        i = len(win) - 1
        close = win["close"].to_numpy().astype(float)
        open_ = win["open"].to_numpy().astype(float)
        low   = win["low"].to_numpy().astype(float)
        high  = win["high"].to_numpy().astype(float)
        volr  = self._vol_ratio_arr(win)
        ema   = self._ema_arr(win, int(p["ema_filter_len"]))
        sig = self._signal_at(res, i, open_, high, low, close, volr, ema, p)
        return sig if sig else self._none(
            f"aucun setup SMC (bias {res['bias']['label']})"
        )

    # ── Sortie anticipée : CHoCH contre la position ──────────────────────────
    def check_early_exit(self, df: pl.DataFrame, position: dict,
                         params: dict = None) -> Optional[str]:
        if not bool(self._p(params).get("choch_exit", True)):
            return None
        idx = df.height - 1
        direction = None
        if (self._bt_events_opposite is not None and self._bt_close_ref is not None
                and idx < len(self._bt_close_ref)
                and abs(float(df["close"][-1]) - self._bt_close_ref[idx]) < 1e-9):
            direction = self._bt_events_opposite.get(idx)
        else:
            # Live : analyse d'une queue courte, suffisante pour la structure.
            p = self._p(params)
            win = df[-400:] if len(df) > 400 else df
            res = smc.analyze(win, self._smc_params(p))
            for ev in reversed(res["structure_events"]):
                if ev["index"] < len(win) - 1:
                    break
                if ev["kind"] == "CHoCH":
                    direction = ev["direction"]
                    break
        if direction is None:
            return None
        side = position.get("side")
        if side == "long" and direction == "down":
            return "smc_choch_down"
        if side == "short" and direction == "up":
            return "smc_choch_up"
        return None

    # ── Cœur : dérivation du signal à la barre i ─────────────────────────────
    def _signal_at(self, res: dict, i: int, open_: np.ndarray, high: np.ndarray,
                   low: np.ndarray, close: np.ndarray, volr: np.ndarray,
                   ema: Optional[np.ndarray], p: Dict[str, Any]) -> Optional[dict]:
        """Construit le meilleur signal SMC à la barre ``i`` (causale : seules
        les entités formées avant ``i`` sont utilisées). Retourne None si aucun
        setup ne passe les filtres (dont le gain minimal ``min_gain_pct``)."""
        trend_arr = res["_trend_arr"]
        atr = float(res["_atr_arr"][i])
        if atr <= 0 or i < 1:
            return None
        trend = int(trend_arr[i])
        c = float(close[i])
        candidates: List[dict] = []

        # Garde CHoCH : pas d'entrée contre un changement de caractère récent.
        guard = int(p["choch_guard_bars"])
        recent_choch_down = any(
            ev["kind"] == "CHoCH" and ev["direction"] == "down"
            and 0 <= i - ev["index"] <= guard
            for ev in res["_all_struct_events"]
        )
        recent_choch_up = any(
            ev["kind"] == "CHoCH" and ev["direction"] == "up"
            and 0 <= i - ev["index"] <= guard
            for ev in res["_all_struct_events"]
        )

        # Premium/discount CAUSAL à la barre i (pas l'état final de l'analyse).
        pd_zone = smc.premium_discount_at(res, high, low, close, i) or {}
        zone = pd_zone.get("zone", "")
        vol_ok = bool(volr[i] > float(p["vol_confluence"])) if i < len(volr) else False

        # Biais EMA (filtre institutionnel simple) : long au-dessus, short en
        # dessous. ema is None quand ema_filter_len=0 → filtre désactivé.
        long_ema_ok  = ema is None or c > float(ema[i])
        short_ema_ok = ema is None or c < float(ema[i])

        # Tap de trendline automatique (causal) : la mèche touche la ligne à
        # tl_tol_atr×ATR près et la clôture tient du bon côté.
        tl_tol = float(p["tl_tol_atr"]) * atr
        tl_sup = smc.trendline_value_at(res, i, "support")
        tl_res = smc.trendline_value_at(res, i, "resistance")
        tl_tap_long = (tl_sup is not None
                       and low[i] <= tl_sup + tl_tol and c > tl_sup)
        tl_tap_short = (tl_res is not None
                        and high[i] >= tl_res - tl_tol and c < tl_res)

        # ── A. Sweep reversal ────────────────────────────────────────────────
        # Filtres durs (validés sur BTC/USDC 1h→4h 2019-2026) :
        #   - AVEC la tendance uniquement : un sweep sell-side ne s'achète que
        #     dans une structure haussière (pullback qui prend la liquidité),
        #     jamais en contre-tendance (les deux pires buckets historiques).
        #   - Côté momentum du range : pas de long en zone discount ni de short
        #     en zone premium — sur crypto, la force appelle la force ; le
        #     « deep discount » d'une tendance haussière est le plus souvent
        #     une structure en train de casser.
        allow_ct = bool(p.get("allow_counter_trend", False))
        for ev in res["_all_sweeps"]:
            if ev["index"] != i or not ev["rejected"]:
                continue
            if ev["kind"] == "sell_side" and not recent_choch_down:
                if (trend != 1 and not allow_ct) or zone == "discount" \
                        or not long_ema_ok:
                    continue
                sc = 0.50
                sc += 0.10 if trend == 1 else 0.0
                sc += 0.10 if ev["source"] == "pool" else 0.0
                sc += 0.10 if zone == "premium" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] > open_[i] else 0.0
                sc += 0.05 if tl_tap_long else 0.0
                sl = min(float(low[i]), float(ev["level"])) - \
                    float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                         setup="SWEEP_REVERSAL", score=sc,
                                         detail=f"sweep {ev['source']} "
                                                f"{ev['level']:.6g}",
                                         trend=trend, zone=zone)
                if cand:
                    candidates.append(cand)
            elif ev["kind"] == "buy_side" and not recent_choch_up:
                if (trend != -1 and not allow_ct) or zone == "premium" \
                        or not short_ema_ok:
                    continue
                sc = 0.50
                sc += 0.10 if trend == -1 else 0.0
                sc += 0.10 if ev["source"] == "pool" else 0.0
                sc += 0.10 if zone == "discount" else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] < open_[i] else 0.0
                sc += 0.05 if tl_tap_short else 0.0
                sl = max(float(high[i]), float(ev["level"])) + \
                    float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                         setup="SWEEP_REVERSAL", score=sc,
                                         detail=f"sweep {ev['source']} "
                                                f"{ev['level']:.6g}",
                                         trend=trend, zone=zone)
                if cand:
                    candidates.append(cand)

        # ── B. Retest d'order block ──────────────────────────────────────────
        max_ob_age = int(p["ob_max_age"])
        for ob in res["_all_obs"]:
            if ob["touched_at"] != i or i - ob["created_at"] > max_ob_age:
                continue
            if ob["invalidated_at"] is not None and ob["invalidated_at"] <= i:
                continue
            if ob["kind"] == "bullish" and trend == 1 and not recent_choch_down:
                if c < ob["bottom"]:
                    continue          # zone déjà transpercée sur clôture
                if zone == "discount" or not long_ema_ok:
                    continue          # côté momentum uniquement (cf. sweeps)
                sc = 0.50 + 0.10      # structure alignée par construction
                sc += 0.10 if ob["strength"] >= 2 else 0.0
                sc += 0.10 if zone == "premium" else 0.0
                sc += 0.05 if self._fvg_overlap(res, i, "bullish",
                                                ob["bottom"], ob["top"]) else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] > open_[i] else 0.0
                sc += 0.05 if tl_tap_long else 0.0
                sl = float(ob["bottom"]) - float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                         setup="OB_RETEST", score=sc,
                                         detail=f"demande [{ob['bottom']:.6g}"
                                                f"–{ob['top']:.6g}]",
                                         trend=trend, zone=zone)
                if cand:
                    candidates.append(cand)
            elif ob["kind"] == "bearish" and trend == -1 and not recent_choch_up:
                if c > ob["top"]:
                    continue
                if zone == "premium" or not short_ema_ok:
                    continue          # côté momentum uniquement (cf. sweeps)
                sc = 0.50 + 0.10
                sc += 0.10 if ob["strength"] >= 2 else 0.0
                sc += 0.10 if zone == "discount" else 0.0
                sc += 0.05 if self._fvg_overlap(res, i, "bearish",
                                                ob["bottom"], ob["top"]) else 0.0
                sc += 0.05 if vol_ok else 0.0
                sc += 0.05 if close[i] < open_[i] else 0.0
                sc += 0.05 if tl_tap_short else 0.0
                sl = float(ob["top"]) + float(p["sl_buffer_atr"]) * atr
                cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                         setup="OB_RETEST", score=sc,
                                         detail=f"offre [{ob['bottom']:.6g}"
                                                f"–{ob['top']:.6g}]",
                                         trend=trend, zone=zone)
                if cand:
                    candidates.append(cand)

        # ── C. Retest de breaker block (OB invalidé → polarité inversée) ────
        # Le transpercement d'un OB accompagne le plus souvent un CHoCH : le
        # premier retest de la zone dans le NOUVEAU sens attrape les stops
        # piégés. Mêmes filtres durs que les autres setups.
        if bool(p.get("use_breakers", True)):
            for brk in res["_all_breakers"]:
                if brk["touched_at"] != i or i - brk["created_at"] > max_ob_age:
                    continue
                if brk["invalidated_at"] is not None and brk["invalidated_at"] <= i:
                    continue
                if brk["kind"] == "bullish" and trend == 1 \
                        and not recent_choch_down:
                    if c < brk["bottom"] or zone == "discount" or not long_ema_ok:
                        continue
                    sc = 0.50 + 0.10
                    sc += 0.10 if zone == "premium" else 0.0
                    sc += 0.05 if vol_ok else 0.0
                    sc += 0.05 if close[i] > open_[i] else 0.0
                    sc += 0.05 if tl_tap_long else 0.0
                    sl = float(brk["bottom"]) - float(p["sl_buffer_atr"]) * atr
                    cand = self._build_trade(res, i, "long", c, sl, atr, p,
                                             setup="BREAKER_RETEST", score=sc,
                                             detail=f"breaker [{brk['bottom']:.6g}"
                                                    f"–{brk['top']:.6g}]",
                                             trend=trend, zone=zone)
                    if cand:
                        candidates.append(cand)
                elif brk["kind"] == "bearish" and trend == -1 \
                        and not recent_choch_up:
                    if c > brk["top"] or zone == "premium" or not short_ema_ok:
                        continue
                    sc = 0.50 + 0.10
                    sc += 0.10 if zone == "discount" else 0.0
                    sc += 0.05 if vol_ok else 0.0
                    sc += 0.05 if close[i] < open_[i] else 0.0
                    sc += 0.05 if tl_tap_short else 0.0
                    sl = float(brk["top"]) + float(p["sl_buffer_atr"]) * atr
                    cand = self._build_trade(res, i, "short", c, sl, atr, p,
                                             setup="BREAKER_RETEST", score=sc,
                                             detail=f"breaker [{brk['bottom']:.6g}"
                                                    f"–{brk['top']:.6g}]",
                                             trend=trend, zone=zone)
                    if cand:
                        candidates.append(cand)

        if not candidates:
            return None
        best = max(candidates, key=lambda x: x["score"])
        best["score"] = round(min(best["score"], 1.0), 3)
        return best

    # ── Construction du trade : ciblage liquidité + filtre 0.4 % ────────────
    def _build_trade(self, res: dict, i: int, side: str, entry: float,
                     sl: float, atr: float, p: Dict[str, Any],
                     setup: str, score: float, detail: str,
                     trend: int = 0, zone: str = "") -> Optional[dict]:
        risk = (entry - sl) if side == "long" else (sl - entry)
        if risk <= 0 or entry <= 0:
            return None
        min_gain = float(p["min_gain_pct"])
        min_rr   = float(p["min_rr"])
        front    = float(p["tp_front_run_atr"]) * atr
        max_age  = int(p["pool_max_age"])

        # Cibles opposées : poches de liquidité + bords des liquidity voids non
        # comblés (zones fines = aimants). Première cible satisfaisant à la
        # fois le gain minimal (0.4 % par défaut) ET le RR minimal.
        use_voids = bool(p.get("use_void_targets", True))
        tp = None
        tp_src = ""
        if side == "long":
            liq = smc.liquidity_targets_above(res, i, entry, max_age=max_age)
            vds = smc.void_targets_above(res, i, entry, max_age=max_age) \
                if use_voids else []
            targets = sorted({(lv, "liquidité") for lv in liq} |
                             {(lv, "void") for lv in vds})
            for level, src in targets:
                cand = level - front
                gain_pct = (cand - entry) / entry * 100.0
                if gain_pct <= 0:
                    continue
                if gain_pct > min_gain and (cand - entry) / risk >= min_rr:
                    tp, tp_src = cand, f"{src} {level:.6g}"
                    break
        else:
            liq = smc.liquidity_targets_below(res, i, entry, max_age=max_age)
            vds = smc.void_targets_below(res, i, entry, max_age=max_age) \
                if use_voids else []
            targets = sorted({(lv, "liquidité") for lv in liq} |
                             {(lv, "void") for lv in vds}, reverse=True)
            for level, src in targets:
                cand = level + front
                gain_pct = (entry - cand) / entry * 100.0
                if gain_pct <= 0:
                    continue
                if gain_pct > min_gain and (entry - cand) / risk >= min_rr:
                    tp, tp_src = cand, f"{src} {level:.6g}"
                    break

        # Fallback : cible en multiple de R si aucune poche exploitable.
        if tp is None:
            rr = float(p["tp_rr_fallback"])
            tp = entry + rr * risk if side == "long" else entry - rr * risk
            tp_src = f"{rr:g}R"

        gain_pct = abs(tp - entry) / entry * 100.0
        rr_final = abs(tp - entry) / risk

        # ⚠ FILTRE : gain potentiel > min_gain_pct sinon position rejetée.
        if gain_pct <= min_gain or rr_final < min_rr:
            return None

        bias_label = {1: "haussier", -1: "baissier", 0: "neutre"}[int(trend)]
        arrow = "LONG" if side == "long" else "SHORT"
        return {
            "score": score, "side": side, "name": self.name, "atr": atr,
            "setup": setup,
            "stop_hint": round(sl, 8),
            "tp_hint":   round(tp, 8),
            "disable_trailing": True,
            "indicators": {
                "bias":     bias_label,
                "pd_zone":  zone or None,
                "gain_pct": round(gain_pct, 3),
                "rr":       round(rr_final, 2),
                "tp_source": tp_src,
            },
            "reason": (f"{arrow} {setup} : {detail} — TP {tp_src} "
                       f"(gain {gain_pct:.2f}% > {min_gain:g}%, RR {rr_final:.2f}), "
                       f"bias {bias_label}"),
        }

    @staticmethod
    def _fvg_overlap(res: dict, i: int, kind: str,
                     zone_lo: float, zone_hi: float) -> bool:
        """True si un FVG ouvert de même direction chevauche la zone [lo, hi]."""
        for fv in res["_all_fvgs"]:
            if fv["kind"] != kind or fv["index"] >= i:
                continue
            if fv["filled_at"] is not None and fv["filled_at"] <= i:
                continue
            if fv["bottom"] <= zone_hi and fv["top"] >= zone_lo:
                return True
        return False

    @staticmethod
    def _vol_ratio_arr(df: pl.DataFrame) -> np.ndarray:
        avg = df["volume"].rolling_mean(20).fill_null(0.0)
        avg = avg.clip(lower_bound=1e-9)
        return (df["volume"] / avg).to_numpy().astype(float)

    @staticmethod
    def _ema_arr(df: pl.DataFrame, length: int) -> Optional[np.ndarray]:
        if length <= 0:
            return None
        return (df["close"].ewm_mean(span=length, adjust=False)
                .to_numpy().astype(float))

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
