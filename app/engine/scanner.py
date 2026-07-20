"""Scanner de marché — screen des paires, fetch OHLCV via CandleStore (Parquet persistant)."""
import logging
import time
from typing import Dict, List, Optional, Tuple

import polars as pl

from app.core.bot_identity import resolve_venue
from app.core.candle_store import get_store
from app.core.indicators import detect_regime, precompute_df
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL

logger = logging.getLogger(__name__)


class MarketScanner:
    def __init__(self, exchange, cfg: dict):
        self.exchange = exchange
        self.cfg      = cfg
        self.scfg     = cfg.get("scanner", {})
        # S2-03 : min_volume_quote_24h (générique) prime sur l'ancienne clé
        # min_volume_usdc_24h — même défaut, alias propagé dans load_config.
        self.min_vol  = cfg["trading"].get(
            "min_volume_quote_24h", cfg["trading"].get("min_volume_usdc_24h", 5_000_000)
        )
        self._symbols_cache: Optional[List[str]] = None
        self._symbols_cache_ts: float = 0.0

    def get_symbols(self, ttl: float = 0.0) -> List[str]:
        """
        Retourne la liste des symboles à trader.

        Parameters
        ----------
        ttl : float
            Si > 0, met en cache le résultat pendant ``ttl`` secondes.
            Évite un appel exchange par cycle dans la boucle live.
        """
        if ttl > 0 and self._symbols_cache is not None:
            if (time.time() - self._symbols_cache_ts) < ttl:
                return self._symbols_cache

        symbols = self.scfg.get("symbols", [DEFAULT_CONFIG_SYMBOL, "ETH/USDC"])
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
        if ttl > 0:
            self._symbols_cache    = symbols
            self._symbols_cache_ts = time.time()
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
        # S2-03 : devise de cotation résolue via la venue par défaut (USDC en
        # crypto historique) au lieu du littéral "/USDC" — le scan dynamique
        # reste un concept crypto (les actions utilisent un univers statique,
        # cf. chantier G2), mais la devise n'est plus codée en dur ici.
        quote = resolve_venue(self.cfg).quote_currency
        tickers = self.exchange.fetch_tickers()
        matching = {s: t for s, t in tickers.items()
                   if s.endswith(f"/{quote}") and (t.get("quoteVolume") or 0) >= self.min_vol}
        ranked = sorted(matching.items(), key=lambda x: x[1].get("quoteVolume", 0), reverse=True)
        return [s for s, _ in ranked[:top_n]]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> Optional[pl.DataFrame]:
        """
        Retourne `limit` bougies pour (symbol, timeframe), avec indicateurs pré-calculés.
        Utilise CandleStore : fetch incrémental + persistence Parquet.
        Les colonnes _pre_* (RSI, ATR, ADX, MACD, vol_ratio) sont ajoutées via
        precompute_df() pour que compute_indicators() lise directement ces colonnes
        sans recalculer.
        """
        try:
            df = get_store().fetch(self.exchange, symbol, timeframe, total=limit)
            if df is None or len(df) < 50:
                return None
            return precompute_df(df)
        except Exception as e:
            logger.error(f"[Scanner] fetch_ohlcv {symbol}/{timeframe} : {e}")
            return None

    def detect_regime(self, df: pl.DataFrame) -> str:
        """Délègue à indicators.detect_regime — source unique de vérité."""
        return detect_regime(df)

    def compute_indicators(self, df: pl.DataFrame) -> Dict:
        """
        Calcule les indicateurs pour l'affichage UI/scanner.

        Lit les colonnes _pre_* ajoutées par fetch_ohlcv() (via precompute_df)
        pour RSI, ATR, ADX, MACD et vol_ratio — évite tout recalcul redondant.
        Seules les EMAs 20/50/200 et les Bandes de Bollinger sont calculées ici
        car elles ne font pas partie du pré-calcul partagé.
        """
        close  = df["close"]
        n      = len(df)
        if n < 60:
            return {}

        # EMAs — colonnes pré-calculées par precompute_df
        ema20  = float(df["_pre_ema20"][-1])
        ema50  = float(df["_pre_ema50"][-1])
        ema200 = float(df["_pre_ema200"][-1]) if n >= 200 else None

        last_close = float(close[-1])

        # Indicateurs pré-calculés par fetch_ohlcv()
        rsi_v     = float(df["_pre_rsi14"][-1])
        atr       = float(df["_pre_atr14"][-1])
        adx       = float(df["_pre_adx14"][-1])
        vol_ratio = float(df["_pre_volratio20"][-1])
        macd_line = float(df["_pre_macd_line"][-1])
        macd_sig  = float(df["_pre_macd_sig"][-1])
        macd_hist = float(df["_pre_macd_hist"][-1])

        atr_pct = round(atr / last_close * 100, 3) if last_close > 0 else 0.0

        # Bandes de Bollinger — non incluses dans precompute_df
        sma20  = close.rolling_mean(20)
        std20  = close.rolling_std(20)
        bb_up  = float((sma20 + 2 * std20)[-1])
        bb_dn  = float((sma20 - 2 * std20)[-1])
        bb_mid = float(sma20[-1])

        return {
            "ema20":       round(ema20, 6),
            "ema50":       round(ema50, 6),
            "ema200":      round(ema200, 6) if ema200 is not None else None,
            "rsi":         round(rsi_v, 2),
            "atr":         round(atr, 6),
            "atr_pct":     atr_pct,
            "vol_ratio":   round(vol_ratio, 3),
            "macd":        round(macd_line, 6),
            "macd_signal": round(macd_sig, 6),
            "macd_hist":   round(macd_hist, 6),
            "bb_upper":    round(bb_up, 6),
            "bb_lower":    round(bb_dn, 6),
            "bb_mid":      round(bb_mid, 6),
            "adx":         round(adx, 2),
            "regime":      "trend" if adx >= 25 else "range",
            "close":       round(last_close, 6),
        }

    def screen(self, timeframe: str, limit: int = 500) -> List[Dict]:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        symbols = self.get_symbols()
        max_workers = min(len(symbols), int(self.scfg.get("scan_workers", 4)))

        def _process(symbol: str) -> Optional[Dict]:
            df = self.fetch_ohlcv(symbol, timeframe, limit)
            if df is None:
                return None
            try:
                indicators = self.compute_indicators(df)
                volume_24h = _estimate_volume_24h(df, timeframe)
                if volume_24h < self.min_vol:
                    return None
                adx     = indicators.get("adx", 0)
                atr_pct = indicators.get("atr_pct", 0)
                regime_label, strategies = recommend_strategy(adx, atr_pct)
                return {
                    "symbol":       symbol,
                    "indicators":   indicators,
                    "volume_24h":   volume_24h,
                    "regime":       indicators.get("regime", "unknown"),
                    "regime_label": regime_label,
                    "strategies":   strategies,
                    "bars":         len(df),
                }
            except Exception as e:
                logger.error(f"[Scanner] screen {symbol} : {e}")
                return None

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_process, s): s for s in symbols}
            for f in as_completed(futures):
                r = f.result()
                if r is not None:
                    results.append(r)
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


# ── Fonctions utilitaires ──────────────────────────────────────────────────

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


def _estimate_volume_24h(df: pl.DataFrame, timeframe: str) -> float:
    tf_mins = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,
               "1h":60,"2h":120,"4h":240,"1d":1440}
    mins    = tf_mins.get(timeframe, 15)
    bars_24 = int(1440 / mins)
    slice_  = df.tail(min(bars_24, len(df)))
    return float((slice_["close"] * slice_["volume"]).sum())
