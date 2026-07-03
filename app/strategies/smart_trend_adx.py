"""BTC Smart Trend V6 — ADX Filter.

Portage fidèle du PineScript `BTC Smart Trend V6 - ADX Filter` :

  - Filtre de tendance : EMA(200).
  - Entrée LONG  : close > EMA200 ET ADX > seuil ET low  ≤ BB_lower[1] (bande
                   basse de la barre PRÉCÉDENTE) ET RSI < 40 ET bougie haussière.
  - Entrée SHORT : close < EMA200 ET ADX > seuil ET high ≥ BB_upper[1]
                   ET RSI > 60 ET bougie baissière.
  - Sortie : bracket FIXE (pas de trailing) — SL = close ∓ sl_mult×ATR,
             TP = close ± tp_mult×ATR (ancré sur le close du signal, comme Pine).

Écarts connus vs TradingView (documentés) :
  * ATR/ADX du projet sont lissés en EMA(span=n) et non en RMA(Wilder) comme
    ta.atr / ta.dmi → distances SL/TP et déclenchement ADX légèrement décalés.
  * Bollinger : écart-type de POPULATION (ddof=0), identique à ta.stdev/ta.bb.
  * Exécution : entrée au `open[i+1]` (le backtester exécute à l'ouverture de la
    barre suivante), SL/TP vérifiés intrabar avec priorité au stop en cas
    d'ambiguïté high/low — cohérent avec l'hypothèse conservatrice de TradingView.
"""
import logging
from typing import Dict, Any, List, Tuple

import numpy as np
import polars as pl

from app.engine.engine import BaseStrategy
from app.core.indicators import pre_val
from app.core.indicators_core import ema as _ema, rsi as _rsi, _true_range

logger = logging.getLogger(__name__)


def _rma(s: pl.Series, n: int) -> pl.Series:
    """Moyenne mobile de Wilder (RMA) = EMA d'alpha 1/n — exactement ``ta.rma`` /
    le lissage sous-jacent de ``ta.atr`` et ``ta.dmi`` en Pine (≠ EMA span=n du
    reste du projet)."""
    return s.ewm_mean(alpha=1.0 / n, adjust=False)


def _wilder_atr_adx(df: pl.DataFrame, n: int) -> Tuple[np.ndarray, np.ndarray]:
    """(ATR Wilder, ADX Wilder) en séries complètes, réplique de ``ta.atr`` /
    ``ta.dmi(n, n)`` du PineScript."""
    tr = _true_range(df)
    atr = _rma(tr, n)
    h, l = df["high"], df["low"]
    up = h.diff(1)
    dn = -l.diff(1)
    up_pos = up.clip(lower_bound=0.0)
    dn_pos = dn.clip(lower_bound=0.0)
    plus_dm  = up_pos * (up > dn).cast(pl.Float64)     # up>dn ET up>0
    minus_dm = dn_pos * (dn > up).cast(pl.Float64)     # dn>up ET dn>0
    atr_safe = atr.clip(lower_bound=1e-10)
    plus_di  = 100.0 * _rma(plus_dm, n)  / atr_safe
    minus_di = 100.0 * _rma(minus_dm, n) / atr_safe
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower_bound=1e-10)
    adx = _rma(dx, n).fill_null(0.0)
    return atr.fill_null(0.0).to_numpy(), adx.to_numpy()


class Strategy(BaseStrategy):
    name = "smart_trend_adx"

    # TF conseillés (le backtest confirmera le plus fidèle aux chiffres TradingView).
    timeframes: List[str] = ["1h", "2h", "4h", "1d"]

    # Warmup : EMA200 + marge (le backtester impose déjà un plancher de 210).
    warmup_bars: int = 220

    param_space: Dict[str, List] = {
        "ema_len":    [150, 200, 250],
        "adx_thresh": [15, 20, 25],
        "rsi_long":   [35, 40, 45],
        "rsi_short":  [55, 60, 65],
        "sl_mult":    [1.0, 1.2, 1.5],
        "tp_mult":    [2.0, 2.5, 3.0],
    }

    # Défauts = valeurs exactes du PineScript.
    fixed_params: Dict[str, Any] = {
        "ema_len":    200,
        "bb_len":     20,
        "bb_mult":    2.0,
        "rsi_len":    14,
        "rsi_long":   40,
        "rsi_short":  60,
        "adx_len":    14,
        "adx_thresh": 20,
        "atr_len":    14,
        "sl_mult":    1.2,
        "tp_mult":    2.5,
    }

    def __init__(self):
        # Cache Wilder ATR/ADX pré-calculé sur le df complet (backtest) : évite
        # O(n²) sur la boucle. Rempli par prepare_for_backtest, lu en O(1).
        self._w_atr: np.ndarray = None
        self._w_adx: np.ndarray = None
        self._w_close_ref: np.ndarray = None
        self._w_len_key: int = None

    def prepare_for_backtest(self, df: pl.DataFrame) -> None:
        """Pré-calcule ATR/ADX Wilder sur toute la fenêtre (une passe O(n))."""
        try:
            atr, adx = _wilder_atr_adx(df, int(self.fixed_params["atr_len"]))
            self._w_atr = atr
            self._w_adx = adx
            self._w_close_ref = df["close"].to_numpy().astype(float)
            self._w_len_key = int(self.fixed_params["atr_len"])
        except Exception as e:
            logger.warning(f"[smart_trend_adx] prepare_for_backtest KO : {e}")
            self._w_atr = self._w_adx = self._w_close_ref = None

    def _atr_adx(self, df: pl.DataFrame, atr_len: int, adx_len: int) -> Tuple[float, float]:
        """(ATR Wilder, ADX Wilder) de la dernière barre. Utilise le cache backtest
        si ``df`` en est un préfixe causal (même longueur/close), sinon calcule sur
        la fenêtre (live — RMA convergée sur ~500 barres)."""
        if (self._w_atr is not None and self._w_len_key == atr_len == adx_len):
            idx = df.height - 1
            ref = self._w_close_ref
            if 0 <= idx < len(ref) and abs(float(df["close"][-1]) - ref[idx]) < 1e-6:
                return float(self._w_atr[idx]), float(self._w_adx[idx])
        atr, adx = _wilder_atr_adx(df, atr_len)
        return float(atr[-1]), float(adx[-1])

    def min_bars_required(self, params: dict = None) -> int:
        p = (params or {}).get(self.name, {})
        ema_len = int(p.get("ema_len", 200))
        bb_len  = int(p.get("bb_len", 20))
        adx_len = int(p.get("adx_len", 14))
        return max(ema_len, bb_len, adx_len * 2) + 5

    # ── Helpers indicateurs (réutilise les colonnes pré-calculées si défauts) ──
    @staticmethod
    def _ema_last(df: pl.DataFrame, length: int) -> float:
        if length == 200:
            v = pre_val(df, "_pre_ema200")
            if v:
                return float(v)
        if length == 50:
            v = pre_val(df, "_pre_ema50")
            if v:
                return float(v)
        return float(_ema(df["close"], length)[-1])

    @staticmethod
    def _rsi_last(df: pl.DataFrame, length: int) -> float:
        # Le RSI du projet est déjà lissé en RMA (alpha=1/n) = ta.rsi Pine.
        if length == 14:
            v = pre_val(df, "_pre_rsi14")
            if v is not None:
                return float(v)
        return float(_rsi(df["close"], length)[-1])

    @staticmethod
    def _bb_prev(close: pl.Series, bb_len: int, bb_mult: float):
        """Bandes de Bollinger de la barre PRÉCÉDENTE (BB[1] du Pine).

        Basis = SMA(close, bb_len) ; sigma = écart-type de POPULATION (ddof=0,
        comme ta.stdev/ta.bb). Calculé sur la seule fenêtre utile (les bb_len
        closes se terminant à l'avant-dernière barre) → O(bb_len) par appel.
        Retourne (lower_prev, upper_prev) ou (None, None) si insuffisant.
        """
        if len(close) < bb_len + 1:
            return None, None
        window = close[-(bb_len + 1):-1]        # bb_len valeurs, finissant à t-1
        mid   = float(window.mean())
        sigma = float(window.std(ddof=0))
        return mid - bb_mult * sigma, mid + bb_mult * sigma

    def score(self, df: pl.DataFrame, params: dict = None,
              df_htf=None, symbol: str = "") -> Dict[str, Any]:
        p = (params or {}).get(self.name, {})
        ema_len    = int(p.get("ema_len",    self.fixed_params["ema_len"]))
        bb_len     = int(p.get("bb_len",     self.fixed_params["bb_len"]))
        bb_mult    = float(p.get("bb_mult",  self.fixed_params["bb_mult"]))
        rsi_len    = int(p.get("rsi_len",    self.fixed_params["rsi_len"]))
        rsi_long   = float(p.get("rsi_long", self.fixed_params["rsi_long"]))
        rsi_short  = float(p.get("rsi_short",self.fixed_params["rsi_short"]))
        adx_len    = int(p.get("adx_len",    self.fixed_params["adx_len"]))
        adx_thresh = float(p.get("adx_thresh",self.fixed_params["adx_thresh"]))
        atr_len    = int(p.get("atr_len",    self.fixed_params["atr_len"]))
        sl_mult    = float(p.get("sl_mult",  self.fixed_params["sl_mult"]))
        tp_mult    = float(p.get("tp_mult",  self.fixed_params["tp_mult"]))

        if len(df) < self.min_bars_required(params):
            return self._none("historique insuffisant")

        close = df["close"]; high = df["high"]; low = df["low"]; open_ = df["open"]
        c = float(close[-1]); o = float(open_[-1])
        h = float(high[-1]);  lo = float(low[-1])

        atr, adx = self._atr_adx(df, atr_len, adx_len)   # Wilder (ta.atr / ta.dmi)
        if atr <= 0:
            return self._none("ATR nul")

        ema200 = self._ema_last(df, ema_len)
        rsi    = self._rsi_last(df, rsi_len)
        bb_low_prev, bb_up_prev = self._bb_prev(close, bb_len, bb_mult)
        if bb_low_prev is None:
            return self._none("BB indisponible")

        strong = adx > adx_thresh
        indicators = {
            "ema200": round(ema200, 2), "rsi": round(rsi, 1), "adx": round(adx, 1),
            "bb_lower_prev": round(bb_low_prev, 2), "bb_upper_prev": round(bb_up_prev, 2),
            "atr": round(atr, 2),
        }

        # ── LONG : pullback dans la bande basse en tendance haussière ─────────
        long_cond = (c > ema200) and strong and (lo <= bb_low_prev) \
            and (rsi < rsi_long) and (c > o)
        if long_cond:
            return {
                "score": 1.0, "side": "long", "name": self.name, "atr": atr,
                "stop_hint": round(c - sl_mult * atr, 2),
                "tp_hint":   round(c + tp_mult * atr, 2),
                "disable_trailing": True,
                "indicators": indicators,
                "reason": (f"LONG dip BB: close>{ema200:.0f} EMA200, ADX {adx:.0f}>"
                           f"{adx_thresh:.0f}, low≤BBlow {bb_low_prev:.0f}, RSI {rsi:.0f}<{rsi_long:.0f}"),
            }

        # ── SHORT : rejet de la bande haute en tendance baissière ─────────────
        short_cond = (c < ema200) and strong and (h >= bb_up_prev) \
            and (rsi > rsi_short) and (c < o)
        if short_cond:
            return {
                "score": 1.0, "side": "short", "name": self.name, "atr": atr,
                "stop_hint": round(c + sl_mult * atr, 2),
                "tp_hint":   round(c - tp_mult * atr, 2),
                "disable_trailing": True,
                "indicators": indicators,
                "reason": (f"SHORT rejet BB: close<{ema200:.0f} EMA200, ADX {adx:.0f}>"
                           f"{adx_thresh:.0f}, high≥BBup {bb_up_prev:.0f}, RSI {rsi:.0f}>{rsi_short:.0f}"),
            }

        return self._none(
            f"pas de setup (close={c:.0f} ema200={ema200:.0f} adx={adx:.0f} rsi={rsi:.0f})"
        )

    def _none(self, reason: str = "") -> dict:
        return {"score": 0, "side": "none", "name": self.name, "reason": reason}
