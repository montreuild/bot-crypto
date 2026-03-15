"""
Scanner V8 — Multi-Timeframe

Nouveautés V8 :
  - recommend_strategy() : UI uniquement (information, pas de décision trading)
  - opportunity_scan() : score vol+ATR, filtres config
  - compute_indicators() enrichi avec atr_pct
  - screen() enrichi avec regime_label et strategies
  - _compute_adx() plus robuste
"""
import logging
from typing import List, Dict, Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MarketScanner:
    def __init__(self, exchange, cfg: dict):
        self.exchange = exchange
        self.cfg      = cfg
        self.scfg     = cfg.get("scanner", {})
        self.min_vol  = cfg["trading"].get("min_volume_usdc_24h", 5_000_000)

    def get_symbols(self) -> List[str]:
        symbols = self.scfg.get("symbols", ["BTC/USDC", "ETH/USDC"])
        if self.scfg.get("dynamic_scan"):
            try:
                return self._dynamic_symbols(self.scfg.get("top_n", 20))
            except Exception as e:
                logger.warning(f"[Scanner] Scan dynamique KO : {e}, liste statique utilisée")
        if self.scfg.get("volume_filter", False):
            try:
                symbols = self._filter_by_volume(symbols)
            except Exception as e:
                logger.warning(f"[Scanner] Filtre volume KO : {e}")
        return symbols

    def _filter_by_volume(self, symbols: List[str]) -> List[str]:
        tickers = self.exchange.fetch_tickers(symbols)
        ranked  = []
        for sym in symbols:
            t   = tickers.get(sym, {})
            vol = t.get("quoteVolume") or 0.0
            if vol >= self.min_vol:
                ranked.append((sym, vol))
        ranked.sort(key=lambda x: x[1], reverse=True)
        top_n = self.scfg.get("vol_rank_top_n", 0)
        if top_n and top_n > 0:
            ranked = ranked[:top_n]
        filtered = [s for s, _ in ranked]
        logger.info(f"[Scanner] Filtre volume : {len(filtered)}/{len(symbols)} symboles retenus")
        return filtered if filtered else symbols

    def _dynamic_symbols(self, top_n: int) -> List[str]:
        tickers = self.exchange.fetch_tickers()
        usdc = {s: t for s, t in tickers.items()
                if s.endswith("/USDC") and (t.get("quoteVolume") or 0) >= self.min_vol}
        ranked = sorted(usdc.items(), key=lambda x: x[1].get("quoteVolume", 0), reverse=True)
        return [s for s, _ in ranked[:top_n]]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> Optional[pd.DataFrame]:
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not raw or len(raw) < 50:
                return None
            df = pd.DataFrame(raw, columns=["time","open","high","low","close","volume"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            df = df[df["volume"] > 0].copy()
            df = df[df["close"] > 0].copy()
            df.dropna(inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df
        except Exception as e:
            logger.error(f"[Scanner] fetch_ohlcv {symbol}/{timeframe} : {e}")
            return None

    def detect_regime(self, df: pd.DataFrame) -> str:
        if len(df) < 30:
            return "unknown"
        adx = _compute_adx(df, 14)
        return "trend" if adx >= 25 else "range"

    def compute_indicators(self, df: pd.DataFrame) -> Dict:
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]
        n      = len(df)
        if n < 60:
            return {}

        ema20  = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50  = close.ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1] if n >= 200 else None
        rsi    = _compute_rsi(close, 14)
        atr    = _compute_atr(df, 14)
        last_close = float(close.iloc[-1])
        atr_pct = round(atr / last_close * 100, 3) if last_close > 0 else 0.0

        vol_ma20  = volume.rolling(20).mean().iloc[-1]
        vol_ratio = float(volume.iloc[-1] / vol_ma20) if vol_ma20 > 0 else 1.0

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        signal= macd.ewm(span=9, adjust=False).mean()
        hist  = macd - signal

        sma20  = close.rolling(20).mean()
        std20  = close.rolling(20).std()
        bb_up  = (sma20 + 2 * std20).iloc[-1]
        bb_dn  = (sma20 - 2 * std20).iloc[-1]
        bb_mid = sma20.iloc[-1]

        adx = _compute_adx(df, 14)

        return {
            "ema20":      round(float(ema20), 6),
            "ema50":      round(float(ema50), 6),
            "ema200":     round(float(ema200), 6) if ema200 is not None else None,
            "rsi":        round(rsi, 2),
            "atr":        round(atr, 6),
            "atr_pct":    atr_pct,
            "vol_ratio":  round(vol_ratio, 3),
            "macd":       round(float(macd.iloc[-1]), 6),
            "macd_signal":round(float(signal.iloc[-1]), 6),
            "macd_hist":  round(float(hist.iloc[-1]), 6),
            "bb_upper":   round(bb_up, 6),
            "bb_lower":   round(bb_dn, 6),
            "bb_mid":     round(bb_mid, 6),
            "adx":        round(adx, 2),
            "regime":     "trend" if adx >= 25 else "range",
            "close":      round(last_close, 6),
        }

    def screen(self, timeframe: str, limit: int = 500) -> List[Dict]:
        results = []
        for symbol in self.get_symbols():
            df = self.fetch_ohlcv(symbol, timeframe, limit)
            if df is None:
                continue
            try:
                indicators = self.compute_indicators(df)
                volume_24h = _estimate_volume_24h(df, timeframe)
                if volume_24h < self.min_vol:
                    continue
                adx    = indicators.get("adx", 0)
                atr_pct= indicators.get("atr_pct", 0)
                regime_label, strategies = recommend_strategy(adx, atr_pct)
                results.append({
                    "symbol":       symbol,
                    "indicators":   indicators,
                    "volume_24h":   volume_24h,
                    "regime":       indicators.get("regime", "unknown"),
                    "regime_label": regime_label,
                    "strategies":   strategies,
                    "bars":         len(df),
                })
            except Exception as e:
                logger.error(f"[Scanner] screen {symbol} : {e}")
        return results

    def opportunity_scan(self, timeframe: str = "1h") -> List[Dict]:
        """
        Scanne les opportunités selon score combiné vol + ATR%.
        Retourne les meilleures paires triées par score décroissant.
        """
        ocfg     = self.scfg.get("opportunity_scan", {})
        min_vol  = float(ocfg.get("min_vol_24h_m", 10)) * 1e6
        min_atr  = float(ocfg.get("min_atr_pct", 1.0))
        max_atr  = float(ocfg.get("max_atr_pct", 10.0))
        top_n    = int(ocfg.get("top_n", 15))

        raw = []
        for symbol in self.get_symbols():
            df = self.fetch_ohlcv(symbol, timeframe, limit=200)
            if df is None:
                continue
            try:
                indicators = self.compute_indicators(df)
                vol_24h    = _estimate_volume_24h(df, timeframe)
                atr_pct    = indicators.get("atr_pct", 0)
                if vol_24h < min_vol:
                    continue
                if not (min_atr <= atr_pct <= max_atr):
                    continue
                raw.append({"symbol": symbol, "indicators": indicators, "volume_24h": vol_24h})
            except Exception as e:
                logger.debug(f"[OpScan] {symbol} : {e}")

        if not raw:
            return []

        # Rang normalisé : 40% volume + 60% ATR%
        vols = [r["volume_24h"] for r in raw]
        atrs = [r["indicators"].get("atr_pct", 0) for r in raw]
        max_vol = max(vols) or 1
        max_atr_v = max(atrs) or 1

        for r in raw:
            vol_rank = r["volume_24h"] / max_vol
            atr_rank = r["indicators"].get("atr_pct", 0) / max_atr_v
            r["score"] = round(0.40 * vol_rank + 0.60 * atr_rank, 4)
            adx = r["indicators"].get("adx", 0)
            ap  = r["indicators"].get("atr_pct", 0)
            r["regime_label"], r["strategies"] = recommend_strategy(adx, ap)

        raw.sort(key=lambda x: x["score"], reverse=True)
        return raw[:top_n]


# ── Fonctions indicateurs ─────────────────────────────────────────────────

def recommend_strategy(adx: float, atr_pct: float) -> Tuple[str, List[str]]:
    """
    Retourne (label_regime, stratégies_recommandées) pour l'affichage UI.
    Usage informatif uniquement — les stratégies filtrent elles-mêmes leur régime.
    """
    if adx < 22 and atr_pct > 4.0:
        return "Panic/Spike", ["fear_momentum"]
    elif adx >= 25 and atr_pct >= 1.5:
        return "Trend fort", ["supertrend_macd", "trend"]
    elif 18 <= adx < 25 and atr_pct >= 0.8:
        return "Trend modéré", ["pullback_trend", "trend"]
    elif adx < 18:
        return "Range", ["breakout"]
    else:
        return "Neutre", ["supertrend_macd", "breakout"]


def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = float(rsi.iloc[-1])
    return val if not np.isnan(val) else 50.0


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    val = float(tr.rolling(period).mean().iloc[-1])
    return val if val > 0 else 0.0


def _compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    h, l, c = df["high"], df["low"], df["close"]
    tr    = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    plus  = (h - h.shift()).clip(lower=0)
    minus = (l.shift() - l).clip(lower=0)
    plus[plus < minus]   = 0
    minus[minus <= plus] = 0
    atr14   = tr.rolling(period).mean()
    di_plus = 100 * plus.rolling(period).mean() / atr14.replace(0, np.nan)
    di_minus= 100 * minus.rolling(period).mean() / atr14.replace(0, np.nan)
    dx      = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx     = dx.rolling(period).mean()
    val     = float(adx.iloc[-1])
    return val if not np.isnan(val) else 0.0


def _estimate_volume_24h(df: pd.DataFrame, timeframe: str) -> float:
    tf_mins = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,
               "1h":60,"2h":120,"4h":240,"1d":1440}
    mins    = tf_mins.get(timeframe, 15)
    bars_24 = int(1440 / mins)
    slice_  = df.tail(min(bars_24, len(df)))
    return float((slice_["close"] * slice_["volume"]).sum())
