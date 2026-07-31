"""Stratégie ``trend_rider`` — trend-follower long-biaisé construit UNIQUEMENT à
partir d'indicateurs techniques classiques (aucun concept SMC).

Genèse (campagne de mesure 2026-07-08) :
  1. Screening INDIVIDUEL de ~22 indicateurs comme signal autonome sur BTC 4h :
     aucun n'a d'edge fort ; seul l'alignement de tendance EMA200 + EMA20/50 est
     positif sur toutes les périodes.
  2. Combinaisons : la confluence naïve et les triggers momentum sur-tradent et
     perdent ; le LONG-ONLY (biais haussier crypto — les shorts saignent) sur un
     « trend-hold » filtré régime devient positif sur les deux périodes.

Setup : à l'apparition (front montant) d'un RÉGIME de tendance
  [ close > EMA_trend  ET  EMA_fast > EMA_slow  ET  ADX > adx_min  ET  DI+ > DI−
    ET  choppiness < chop_max ]  → entrée LONG (miroir short si ``long_only``
  désactivé). On laisse ensuite COURIR via le trailing du Backtester.

Confluences (montent le score → sizing) : CVD haussier, volume, ADX fort,
confirmation bougie (pin/engulfing), RSI pas suracheté. Sortie : trailing
``trail_mult``×ATR (pas de TP fixe) ; SL initial ``stop_atr``×ATR.

Validation BTC 4h (vrai Backtester) : FULL +370 PF 1.18 Sharpe 2.4 ; OOS
2024-26 +49 PF 1.11 — oos_score positif, TRADABLE (edge modeste, complémentaire
du smart_money structurel).
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

import app.core.indicators as ind
from app.core.indicators_core import atr_wilder
from app.engine.engine import BaseStrategy

logger = logging.getLogger(__name__)


class Strategy(BaseStrategy):
    name = "trend_rider"
    timeframes: List[str] = ["4h"]     # seul TF avec oos_score positif

    param_space: Dict[str, List] = {
        "adx_min":        [18, 22, 25],
        "chop_max":       [50.0, 55.0, 61.8],
        "stop_atr":       [1.0, 1.5, 2.0],
        "trail_mult":     [2.5, 3.5],
        "min_score":      [0.5, 0.6, 0.7],
        "long_only":      [True, False],
        "size_by_confluence": [True, False],
    }

    fixed_params: Dict[str, Any] = {
        "ema_fast":     20,
        "ema_slow":     50,
        "ema_trend":    200,
        "adx_len":      14,
        "adx_min":      22,      # force de tendance minimale
        "adx_strong":   30,      # bonus de confluence
        "chop_len":     14,
        "chop_max":     55.0,    # ne trader qu'hors congestion
        "stop_atr":     1.5,     # SL initial = stop_atr × ATR
        "trail_mult":   3.5,     # trailing (trail_wide) — laisse courir
        "vol_min":      1.2,     # bonus si volume > vol_min × SMA20
        "rsi_max":      68,      # bonus si RSI < rsi_max (marge de hausse)
        "cvd_slope_len": 5,      # fenêtre de pente du CVD
        "long_only":    True,    # biais haussier crypto (les shorts saignent)
        "min_score":    0.5,     # seuil de score interne
        # Sizing pondéré par confluence (hook natif size_factor du Backtester)
        "size_by_confluence": True,
        "size_conf_slope":  3.0,
        "size_conf_center": 0.7,
        "max_window":   400,     # fenêtre live bornée
    }

    def __init__(self):
        self._sig: Optional[Dict[int, dict]] = None
        self._close_ref: Optional[np.ndarray] = None

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        return max(int(p.get("ema_trend", 200)) + 20, 220)

    def _p(self, params: dict = None) -> Dict[str, Any]:
        p = dict(self.fixed_params)
        for k, v in ((params or {}).get(self.name, {}) or {}).items():
            if k in p and v is not None:
                p[k] = v
        return p

    # ── Séries d'indicateurs (toutes causales) ───────────────────────────────
    def _series(self, df: pl.DataFrame, p: Dict[str, Any]) -> Dict[str, np.ndarray]:
        close = df["close"]
        adx_l, pdi, ndi = ind.adx(df, int(p["adx_len"]))
        cvd = ind.cvd(df).to_numpy()
        k = int(p["cvd_slope_len"])
        cvd_slope = np.zeros(len(cvd))
        if len(cvd) > k:
            cvd_slope[k:] = cvd[k:] - cvd[:-k]
        return {
            "c":     close.to_numpy(),
            "ema_f": ind.ema(close, int(p["ema_fast"])).to_numpy(),
            "ema_s": ind.ema(close, int(p["ema_slow"])).to_numpy(),
            "ema_t": ind.ema(close, int(p["ema_trend"])).to_numpy(),
            "adx":   adx_l.to_numpy(),
            "pdi":   pdi.to_numpy(),
            "ndi":   ndi.to_numpy(),
            "chop":  ind.choppiness(df, int(p["chop_len"])).fill_null(50.0).to_numpy(),
            "atr":   atr_wilder(df, 14).fill_null(0.0).to_numpy(),
            "rsi":   ind.rsi(close, 14).to_numpy(),
            "vr":    ind.volume_ratio(df, 20).fill_null(1.0).to_numpy(),
            "cvd_slope": cvd_slope,
            "pin":   ind.pin_bar(df).to_numpy(),
            "eng":   ind.engulfing(df).to_numpy(),
        }

    def _regime(self, s: Dict[str, np.ndarray], p: Dict[str, Any]):
        adx_ok = (s["adx"] > float(p["adx_min"]))
        up = (s["c"] > s["ema_t"]) & (s["ema_f"] > s["ema_s"]) & adx_ok \
            & (s["pdi"] > s["ndi"]) & (s["chop"] < float(p["chop_max"]))
        dn = (s["c"] < s["ema_t"]) & (s["ema_f"] < s["ema_s"]) & adx_ok \
            & (s["ndi"] > s["pdi"]) & (s["chop"] < float(p["chop_max"]))
        return up, dn

    def _score(self, s: Dict[str, np.ndarray], i: int, side: str,
               p: Dict[str, Any]) -> float:
        sc = 0.5
        if side == "long":
            sc += 0.1 if s["cvd_slope"][i] > 0 else 0.0
            sc += 0.1 if s["vr"][i] > float(p["vol_min"]) else 0.0
            sc += 0.1 if s["adx"][i] > float(p["adx_strong"]) else 0.0
            sc += 0.1 if (s["pin"][i] > 0 or s["eng"][i] > 0) else 0.0
            sc += 0.1 if s["rsi"][i] < float(p["rsi_max"]) else 0.0
        else:
            sc += 0.1 if s["cvd_slope"][i] < 0 else 0.0
            sc += 0.1 if s["vr"][i] > float(p["vol_min"]) else 0.0
            sc += 0.1 if s["adx"][i] > float(p["adx_strong"]) else 0.0
            sc += 0.1 if (s["pin"][i] < 0 or s["eng"][i] < 0) else 0.0
            sc += 0.1 if s["rsi"][i] > (100 - float(p["rsi_max"])) else 0.0
        return round(min(sc, 1.0), 3)

    def _build(self, s: Dict[str, np.ndarray], i: int, side: str,
               p: Dict[str, Any]) -> Optional[dict]:
        atr = float(s["atr"][i])
        c = float(s["c"][i])
        if atr <= 0 or c <= 0:
            return None
        sc = self._score(s, i, side, p)
        if sc < float(p["min_score"]):
            return None
        stop = c - float(p["stop_atr"]) * atr if side == "long" \
            else c + float(p["stop_atr"]) * atr
        size_factor = 1.0
        if bool(p.get("size_by_confluence", False)):
            slope = float(p["size_conf_slope"])
            center = float(p["size_conf_center"])
            size_factor = max(0.4, min(1.7, 1.0 + slope * (sc - center)))
        arrow = "LONG" if side == "long" else "SHORT"
        return {
            "score": sc, "side": side, "name": self.name, "atr": atr,
            "stop_hint": round(stop, 8),
            "tp_hint": None,
            "disable_trailing": False,
            "trail_override": {"trail_wide": float(p["trail_mult"]), "mode": "dynamic"},
            "size_factor": size_factor,
            "indicators": {
                "adx": round(float(s["adx"][i]), 1),
                "chop": round(float(s["chop"][i]), 1),
                "rsi": round(float(s["rsi"][i]), 1),
            },
            "reason": (f"{arrow} trend-rider : EMA align + ADX {s['adx'][i]:.0f} "
                       f"+ chop {s['chop'][i]:.0f} (score {sc:.2f})"),
        }

    def _signal_at(self, s: Dict[str, np.ndarray], up: np.ndarray,
                   dn: np.ndarray, i: int, p: Dict[str, Any]) -> Optional[dict]:
        """Entrée au FRONT MONTANT du régime (nouvelle tendance confirmée)."""
        if i < 1:
            return None
        if up[i] and not up[i - 1]:
            return self._build(s, i, "long", p)
        if not bool(p.get("long_only", True)) and dn[i] and not dn[i - 1]:
            return self._build(s, i, "short", p)
        return None

    # ── Pré-calcul backtest : une passe sur toute la fenêtre ─────────────────
    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        try:
            p = self._p(getattr(self, "_bt_params", None))
            s = self._series(df, p)
            up, dn = self._regime(s, p)
            sig: Dict[int, dict] = {}
            # Démarre au min_bars : score() supprime les signaux plus précoces
            # (garde-fou d'historique) → cache aligné sur le chemin live.
            start = max(1, self.min_bars_required(getattr(self, "_bt_params", None)) - 1)
            for i in range(start, len(s["c"])):
                out = self._signal_at(s, up, dn, i, p)
                if out is not None:
                    sig[i] = out
            self._sig = sig
            self._close_ref = s["c"]
        except Exception as e:            # pragma: no cover
            logger.warning(f"[trend_rider] prepare_for_backtest KO : {e}")
            self._sig = None
            self._close_ref = None

    def _cache_valid(self, df: pl.DataFrame) -> bool:
        if self._sig is None or self._close_ref is None:
            return False
        idx = df.height - 1
        ref = self._close_ref
        return 0 <= idx < len(ref) and abs(float(df["close"][-1]) - ref[idx]) < 1e-9

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = self._p(params)
        if len(df) < self.min_bars_required(params):
            return self._none("historique insuffisant")
        # Chemin backtest : lookup O(1) dans le cache pré-calculé.
        if self._cache_valid(df):
            sig = self._sig.get(df.height - 1)
            return dict(sig) if sig else self._none("pas de setup")
        # Chemin live/scanner : calcul sur la fenêtre bornée.
        win = df[-int(p["max_window"]):] if len(df) > int(p["max_window"]) else df
        s = self._series(win, p)
        up, dn = self._regime(s, p)
        out = self._signal_at(s, up, dn, len(s["c"]) - 1, p)
        return out if out is not None else self._none("pas de setup")

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
