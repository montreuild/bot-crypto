"""BTC Liquidity Sweep V7 — Volume Footprint.

Portage fidèle du PineScript `BTC Liquidity Sweep V7 - Volume Footprint` :

  - Filtre de tendance : EMA(200).
  - Entrée LONG  : close > EMA200 ET low ≤ BB_lower (la mèche PERCE la bande basse)
                   ET close > BB_lower (clôture DANS la bande → sweep rejeté)
                   ET bougie verte ET volume > vol_sma × vol_mult.
  - Entrée SHORT : close < EMA200 ET high ≥ BB_upper ET close < BB_upper
                   ET bougie rouge ET volume > vol_sma × vol_mult.
  - Sortie : bracket FIXE (pas de trailing) — SL = close ∓ 1.5×ATR,
             TP = close ± 3.0×ATR (ancré sur le close du signal, comme Pine).

Différence clé avec `smart_trend_adx` : les bandes de Bollinger sont celles de la
BARRE COURANTE (ta.bb sans décalage), et le déclencheur est un balayage de
liquidité (mèche hors bande + clôture dedans) confirmé par le VOLUME, sans ADX
ni RSI.

Écarts de modélisation assumés (idem smart_trend_adx) :
  * ATR en RMA de Wilder (ta.atr), BB en écart-type de population (ta.bb).
  * Exécution au open[i+1] ; SL/TP intrabar avec priorité au stop.
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import polars as pl

from app.core.indicators import num as _num
from app.core.indicators import pre_val
from app.core.indicators_core import _true_range
from app.engine.engine import BaseStrategy


def _rma(s: pl.Series, n: int) -> pl.Series:
    """RMA de Wilder (= EMA d'alpha 1/n) — lissage de ta.atr en Pine."""
    return s.ewm_mean(alpha=1.0 / n, adjust=False)


def _wilder_atr(df: pl.DataFrame, n: int) -> np.ndarray:
    return _rma(_true_range(df), n).fill_null(0.0).to_numpy()


class Strategy(BaseStrategy):
    name = "liquidity_sweep_vol"

    timeframes: List[str] = ["1h", "2h", "4h", "1d"]
    warmup_bars: int = 220

    param_space: Dict[str, List] = {
        "ema_len":  [150, 200, 250],
        "bb_mult":  [1.8, 2.0, 2.2],
        "vol_mult": [1.2, 1.5, 2.0],
        "sl_mult":  [1.2, 1.5, 2.0],
        "tp_mult":  [2.5, 3.0, 3.5],
    }

    # Défauts = valeurs exactes du PineScript.
    fixed_params: Dict[str, Any] = {
        "ema_len":  200,
        "bb_len":   20,
        "bb_mult":  2.0,
        "vol_len":  20,
        "vol_mult": 1.5,
        "atr_len":  14,
        "sl_mult":  1.5,
        "tp_mult":  3.0,
    }

    def __init__(self):
        self._w_atr: Optional[np.ndarray] = None
        self._w_close_ref: Optional[np.ndarray] = None
        self._w_len_key: Optional[int] = None

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule l'ATR Wilder sur toute la fenêtre (une passe O(n))."""
        try:
            self._w_atr = _wilder_atr(df, int(self.fixed_params["atr_len"]))
            self._w_close_ref = df["close"].to_numpy().astype(float)
            self._w_len_key = int(self.fixed_params["atr_len"])
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"[liquidity_sweep_vol] prepare_for_backtest KO : {e}")
            self._w_atr = self._w_close_ref = None

    def _atr_last(self, df: pl.DataFrame, atr_len: int) -> float:
        atr_c, ref = self._w_atr, self._w_close_ref
        if atr_c is not None and ref is not None and self._w_len_key == atr_len:
            idx = df.height - 1
            if 0 <= idx < len(ref) and abs(float(df["close"][-1]) - ref[idx]) < 1e-6:
                return float(atr_c[idx])
        return float(_wilder_atr(df, atr_len)[-1])

    def min_bars_required(self, params: dict | None = None) -> int:
        p = (params or {}).get(self.name, {})
        ema_len = int(p.get("ema_len", 200))
        bb_len  = int(p.get("bb_len", 20))
        vol_len = int(p.get("vol_len", 20))
        return max(ema_len, bb_len, vol_len) + 5

    @staticmethod
    def _ema_last(df: pl.DataFrame, length: int) -> float:
        if length == 200:
            v = pre_val(df, "_pre_ema200")
            if v:
                return float(v)
        from app.core.indicators_core import ema as _ema
        return float(_ema(df["close"], length)[-1])

    @staticmethod
    def _bb_now(close: pl.Series, bb_len: int, bb_mult: float):
        """Bandes de Bollinger de la BARRE COURANTE (ta.bb), écart-type population."""
        if len(close) < bb_len:
            return None, None
        window = close[-bb_len:]
        mid   = _num(window.mean())
        sigma = _num(window.std(ddof=0))
        return mid - bb_mult * sigma, mid + bb_mult * sigma

    def score(self, df: pl.DataFrame, params: dict | None = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})
        ema_len  = int(p.get("ema_len",   self.fixed_params["ema_len"]))
        bb_len   = int(p.get("bb_len",    self.fixed_params["bb_len"]))
        bb_mult  = float(p.get("bb_mult", self.fixed_params["bb_mult"]))
        vol_len  = int(p.get("vol_len",   self.fixed_params["vol_len"]))
        vol_mult = float(p.get("vol_mult",self.fixed_params["vol_mult"]))
        atr_len  = int(p.get("atr_len",   self.fixed_params["atr_len"]))
        sl_mult  = float(p.get("sl_mult", self.fixed_params["sl_mult"]))
        tp_mult  = float(p.get("tp_mult", self.fixed_params["tp_mult"]))

        if len(df) < self.min_bars_required(params):
            return self._none("historique insuffisant")

        close = df["close"]
        high = df["high"]
        low = df["low"]
        open_ = df["open"]
        vol  = df["volume"]
        c = float(close[-1])
        o = float(open_[-1])
        h = float(high[-1])
        lo = float(low[-1])

        atr = self._atr_last(df, atr_len)
        if atr <= 0:
            return self._none("ATR nul")

        ema200 = self._ema_last(df, ema_len)
        bb_low, bb_up = self._bb_now(close, bb_len, bb_mult)
        if bb_low is None:
            return self._none("BB indisponible")

        vol_now = float(vol[-1])
        vol_sma = _num(vol[-vol_len:].mean())
        vol_ok  = vol_now > vol_sma * vol_mult

        indicators = {
            "ema200": round(ema200, 2), "bb_lower": round(bb_low, 2),
            "bb_upper": round(bb_up, 2), "atr": round(atr, 2),
            "vol_x": round(vol_now / vol_sma, 2) if vol_sma > 0 else 0.0,
        }

        # ── LONG : sweep de la bande basse rejeté, volume smart-money ─────────
        long_cond = (c > ema200) and (lo <= bb_low) and (c > bb_low) \
            and (c > o) and vol_ok
        if long_cond:
            return {
                "score": 1.0, "side": "long", "name": self.name, "atr": atr,
                "stop_hint": round(c - sl_mult * atr, 2),
                "tp_hint":   round(c + tp_mult * atr, 2),
                "disable_trailing": True,
                "indicators": indicators,
                "reason": (f"LONG sweep BB: low≤{bb_low:.0f}≤close, vol×"
                           f"{indicators['vol_x']:.1f}>{vol_mult}, close>{ema200:.0f} EMA200"),
            }

        # ── SHORT : sweep de la bande haute rejeté, volume smart-money ────────
        short_cond = (c < ema200) and (h >= bb_up) and (c < bb_up) \
            and (c < o) and vol_ok
        if short_cond:
            return {
                "score": 1.0, "side": "short", "name": self.name, "atr": atr,
                "stop_hint": round(c + sl_mult * atr, 2),
                "tp_hint":   round(c - tp_mult * atr, 2),
                "disable_trailing": True,
                "indicators": indicators,
                "reason": (f"SHORT sweep BB: high≥{bb_up:.0f}≥close, vol×"
                           f"{indicators['vol_x']:.1f}>{vol_mult}, close<{ema200:.0f} EMA200"),
            }

        return self._none(
            f"pas de sweep (close={c:.0f} ema200={ema200:.0f} vol×{indicators['vol_x']:.1f})"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
