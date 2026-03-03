"""Scanner multi-actifs USDC — détecte les opportunités sur plusieurs paires."""
import logging
from typing import List, Dict, Any
import pandas as pd
from app.core.indicators import detect_regime, adx_val, volume_ratio
from app.core.exchange import RobustExchange
from app.engine.engine import Engine

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self, exchange: RobustExchange, engine: Engine, cfg: dict):
        self.exchange = exchange
        self.engine   = engine
        self.cfg      = cfg
        self.pairs    = cfg["scanner"]["pairs"]
        self.tf       = cfg["trading"]["timeframe"]
        self.min_adx  = cfg["scanner"].get("min_adx", 20)
        self.min_vol  = cfg["scanner"].get("min_volume_ratio", 0.8)
        self.thresh   = cfg["trading"].get("score_threshold", 0.55)
        self.params   = cfg.get("strategy_params", {})

    def scan(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Scanne toutes les paires et retourne les opportunités filtrées."""
        opportunities = []
        for pair in self.pairs:
            try:
                result = self._scan_pair(pair, limit)
                if result:
                    opportunities.append(result)
            except Exception as e:
                logger.warning(f"[Scanner] Erreur {pair}: {e}")
        opportunities.sort(key=lambda x: x["score"], reverse=True)
        logger.info(f"[Scanner] {len(opportunities)}/{len(self.pairs)} paires avec signal")
        return opportunities

    def _scan_pair(self, symbol: str, limit: int) -> Dict[str, Any]:
        ohlcv = self.exchange.fetch_ohlcv(symbol, self.tf, limit=limit)
        df    = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        if len(df) < 60:
            return None

        regime  = detect_regime(df.tail(50))
        adx_v   = adx_val(df.tail(30))
        vol_r   = float(volume_ratio(df.tail(20), 20).iloc[-1] if len(df)>=20 else 0)

        # Filtre ADX et volume
        if adx_v < self.min_adx and regime["regime"] == "trending":
            return None
        if vol_r < self.min_vol:
            return None

        signal = self.engine.best_signal(df, regime=regime, params=self.params)
        if signal.get("side","none") == "none" or signal.get("score",0) < self.thresh:
            return None

        return {
            "symbol":   symbol,
            "side":     signal["side"],
            "score":    signal["score"],
            "strategy": signal.get("name",""),
            "regime":   regime["regime"],
            "adx":      round(adx_v, 2),
            "vol_ratio": round(vol_r, 3),
            "price":    float(df["close"].iloc[-1]),
            "reason":   signal.get("reason",""),
        }

    def get_ohlcv_df(self, symbol: str, limit: int = 500) -> pd.DataFrame:
        """Retourne un DataFrame OHLCV propre pour une paire."""
        ohlcv = self.exchange.fetch_ohlcv(symbol, self.tf, limit=limit)
        df    = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df
