"""
Module 1 — Scanner multi-actifs :
  - Multi-paires USDC
  - Filtrage par volume, spread, liquidité
  - Détection régime de marché (Trend vs Range)
  - Indicateurs techniques communs
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
        """Retourne la liste des symboles à scanner."""
        symbols = self.scfg.get("symbols", ["BTC/USDC", "ETH/USDC"])
        if self.scfg.get("dynamic_scan"):
            try:
                return self._dynamic_symbols(self.scfg.get("top_n", 20))
            except Exception as e:
                logger.warning(f"[Scanner] Scan dynamique KO : {e}, utilise liste statique")
        return symbols

    def _dynamic_symbols(self, top_n: int) -> List[str]:
        """Récupère les top N paires USDC par volume."""
        tickers = self.exchange.fetch_tickers()
        usdc = {s: t for s, t in tickers.items()
                if s.endswith("/USDC") and (t.get("quoteVolume") or 0) >= self.min_vol}
        ranked = sorted(usdc.items(), key=lambda x: x[1].get("quoteVolume", 0), reverse=True)
        return [s for s, _ in ranked[:top_n]]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> Optional[pd.DataFrame]:
        """Récupère OHLCV et transforme en DataFrame fiabilisé."""
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not raw or len(raw) < 50:
                return None
            df = pd.DataFrame(raw, columns=["time","open","high","low","close","volume"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            # Validation : supprime les bougies avec volumes nuls ou prix aberrants
            df = df[df["volume"] > 0].copy()
            df = df[df["close"] > 0].copy()
            df.dropna(inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df
        except Exception as e:
            logger.error(f"[Scanner] fetch_ohlcv {symbol}/{timeframe} : {e}")
            return None

    def detect_regime(self, df: pd.DataFrame) -> str:
        """
        Détecte le régime de marché : 'trend' ou 'range'.
        Méthode : ADX > 25 = trend, sinon range.
        """
        if len(df) < 30:
            return "unknown"
        adx = _compute_adx(df, 14)
        return "trend" if adx >= 25 else "range"

    def compute_indicators(self, df: pd.DataFrame) -> Dict:
        """Calcul centralisé de tous les indicateurs communs."""
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]
        n      = len(df)
        if n < 60:
            return {}

        # Moyennes mobiles
        ema20  = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50  = close.ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1] if n >= 200 else None

        # RSI
        rsi = _compute_rsi(close, 14)

        # ATR
        atr = _compute_atr(df, 14)

        # Volume ratio vs moyenne 20
        vol_ma20  = volume.rolling(20).mean().iloc[-1]
        vol_ratio = float(volume.iloc[-1] / vol_ma20) if vol_ma20 > 0 else 1.0

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        signal= macd.ewm(span=9, adjust=False).mean()
        hist  = macd - signal

        # Bollinger Bands
        sma20  = close.rolling(20).mean()
        std20  = close.rolling(20).std()
        bb_up  = (sma20 + 2 * std20).iloc[-1]
        bb_dn  = (sma20 - 2 * std20).iloc[-1]
        bb_mid = sma20.iloc[-1]

        # ADX
        adx = _compute_adx(df, 14)

        return {
            "ema20": round(float(ema20), 6),
            "ema50": round(float(ema50), 6),
            "ema200": round(float(ema200), 6) if ema200 is not None else None,
            "rsi": round(rsi, 2),
            "atr": round(atr, 6),
            "vol_ratio": round(vol_ratio, 3),
            "macd": round(float(macd.iloc[-1]), 6),
            "macd_signal": round(float(signal.iloc[-1]), 6),
            "macd_hist": round(float(hist.iloc[-1]), 6),
            "bb_upper": round(bb_up, 6),
            "bb_lower": round(bb_dn, 6),
            "bb_mid": round(bb_mid, 6),
            "adx": round(adx, 2),
            "regime": "trend" if adx >= 25 else "range",
            "close": round(float(close.iloc[-1]), 6),
        }

    def screen(self, timeframe: str, limit: int = 500) -> List[Dict]:
        """Scanne tous les symboles et retourne les résultats d'analyse."""
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
                results.append({
                    "symbol":     symbol,
                    "indicators": indicators,
                    "volume_24h": volume_24h,
                    "regime":     indicators.get("regime", "unknown"),
                    "bars":       len(df),
                })
            except Exception as e:
                logger.error(f"[Scanner] screen {symbol} : {e}")
        return results


# ── Fonctions indicateurs ──────────────────────────────────────────────────
def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0


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
    plus[plus < minus]  = 0
    minus[minus <= plus] = 0
    atr14   = tr.rolling(period).mean()
    di_plus = 100 * plus.rolling(period).mean() / atr14.replace(0, np.nan)
    di_minus= 100 * minus.rolling(period).mean() / atr14.replace(0, np.nan)
    dx      = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx     = dx.rolling(period).mean()
    return float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 0.0


def _estimate_volume_24h(df: pd.DataFrame, timeframe: str) -> float:
    """Estime le volume 24h en USDC à partir du DataFrame."""
    tf_mins = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"1d":1440}
    mins    = tf_mins.get(timeframe, 15)
    bars_24 = int(1440 / mins)
    slice_  = df.tail(min(bars_24, len(df)))
    return float((slice_["close"] * slice_["volume"]).sum())
