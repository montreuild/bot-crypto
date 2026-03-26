"""
OHLCVCache — cache multi-timeframe in-memory avec precompute d'indicateurs.

Responsabilités :
  - Cache TTL par timeframe des DataFrames OHLCV (Polars)
  - Filtre "nouvelle bougie" (évite les recalculs sur la même bougie)
  - Precompute vectorisé des indicateurs partagés (RSI, ATR, ADX, MACD, vol_ratio)
  - Cache ATR par symbole pour le position management
  - Compteur d'erreurs exchange par symbole
  - Volatility brake (ATR BTC/USDC → RiskManager)
  - Purge périodique des entrées expirées

Usage dans LiveTrader :
    self.ohlcv_cache = OHLCVCache(scanner=self.scanner, cfg=cfg,
                                   notif=self.notif, risk=self.risk)
    df  = self.ohlcv_cache.get("BTC/USDC", "1h", open_positions)
    atr = self.ohlcv_cache.get_cached_atr("BTC/USDC")
    self.ohlcv_cache.update_volatility_brake(self.risk)
    self.ohlcv_cache.purge(active_symbols)
"""
import logging
import time
from typing import Dict, Optional, Tuple

import polars as pl

from app.core.indicators import precompute_df, atr_val as _compute_atr
from app.engine.optimizer import RECOMMENDED_LIMIT

logger = logging.getLogger(__name__)

# TTL du cache OHLCV par timeframe (secondes)
_OHLCV_TTL: Dict[str, int] = {
    "1m": 30, "5m": 60, "15m": 180, "30m": 360,
    "1h": 600, "4h": 2400, "1d": 14400,
}


class OHLCVCache:
    """
    Cache OHLCV multi-TF in-memory avec gestion du cycle de vie complet.

    Paramètres
    ----------
    scanner  : MarketScanner — utilisé pour fetch_ohlcv
    cfg      : dict config globale
    notif    : Notifier — pour notify_exchange_error
    risk     : RiskManager — pour update_volatility dans update_volatility_brake
    """

    def __init__(self, scanner, cfg: dict, notif, risk):
        self._scanner = scanner
        self._cfg     = cfg
        self._notif   = notif
        self._risk    = risk

        # (symbol, tf) → (timestamp_fetch, DataFrame)
        self._ohlcv_cache: Dict[Tuple[str, str], Tuple[float, pl.DataFrame]] = {}
        # symbol → (timestamp_fetch, atr_value)
        self._atr_cache: Dict[str, Tuple[float, float]] = {}
        # (symbol, tf) → dernier timestamp de bougie (ms)
        self._last_candle_ts: Dict[Tuple[str, str], int] = {}
        # symbol → compteur d'erreurs consécutives
        self._exchange_errors: Dict[str, int] = {}

        scan_interval = cfg["trading"].get("scan_interval", 60)
        self._atr_cache_ttl: int = max(scan_interval // 2, 30)

    # ── Accès principal ──────────────────────────────────────────────────

    def get(self, symbol: str, tf: str,
            open_positions: Optional[dict] = None) -> Optional[pl.DataFrame]:
        """
        Retourne le DataFrame OHLCV pour (symbol, tf), avec cache TTL.

        Le filtre "nouvelle bougie" est appliqué quand open_positions est vide :
        si le dernier timestamp de bougie est identique au précédent et qu'aucune
        position n'est ouverte, retourne None pour éviter un recalcul inutile.

        Parameters
        ----------
        open_positions : dict ou None
            Positions actuellement ouvertes. Si None, le filtre est désactivé
            (comportement conservatif : on ne saute pas la bougie).
        """
        key = (symbol, tf)
        ttl = _OHLCV_TTL.get(tf, 120)
        cached = self._ohlcv_cache.get(key)
        if cached and (time.time() - cached[0]) < ttl:
            return cached[1]

        limit = RECOMMENDED_LIMIT.get(tf, 500)
        fetch_limit = min(limit, 500)
        df = self._scanner.fetch_ohlcv(symbol, tf, limit=fetch_limit)
        if df is None or len(df) < 220:
            self._exchange_errors[symbol] = self._exchange_errors.get(symbol, 0) + 1
            self._notif.notify_exchange_error(
                symbol, f"fetch_ohlcv {tf} retourné vide ou trop court",
                self._exchange_errors[symbol]
            )
            return None
        self._exchange_errors[symbol] = 0

        # Filtre "nouvelle bougie" — skip si même ts et pas de position ouverte
        try:
            last_ts_raw = df["time"][-1]
            last_ts = (
                int(last_ts_raw.timestamp() * 1000) if hasattr(last_ts_raw, "timestamp")
                else int(last_ts_raw) if isinstance(last_ts_raw, (int, float))
                else 0
            )
            prev_ts  = self._last_candle_ts.get(key, 0)
            has_open = open_positions is None or bool(open_positions)
            if last_ts == prev_ts and not has_open:
                logger.debug(f"[OHLCVCache] {symbol}/{tf} : même bougie — skip")
                return None
            self._last_candle_ts[key] = last_ts
        except Exception:
            pass

        # Mise à jour cache ATR (TF primaire, utilisé par _manage_position)
        atr = _compute_atr(df)
        if atr > 0:
            self._atr_cache[symbol] = (time.time(), float(atr))

        # Pré-calcul vectorisé des indicateurs partagés (RSI, ATR, ADX, MACD, vol_ratio).
        # Appelé une seule fois par fetch — toutes les stratégies lisent ensuite via
        # pre_val(df, "_pre_rsi14") en O(1) au lieu de recalculer en O(n) chacune.
        try:
            df = precompute_df(df)
        except Exception as _pc_err:
            logger.debug(f"[OHLCVCache] precompute {symbol}/{tf} KO : {_pc_err}")

        self._ohlcv_cache[key] = (time.time(), df)
        return df

    # ── Cache ATR ────────────────────────────────────────────────────────

    def get_cached_atr(self, symbol: str) -> Optional[float]:
        """Retourne l'ATR mis en cache si non expiré, sinon None."""
        cached = self._atr_cache.get(symbol)
        if cached and (time.time() - cached[0]) < self._atr_cache_ttl:
            return cached[1]
        return None

    def set_atr(self, symbol: str, atr: float) -> None:
        """Met à jour manuellement le cache ATR (ex. après fetch fallback dans _manage_position)."""
        self._atr_cache[symbol] = (time.time(), float(atr))

    # ── Volatility brake ─────────────────────────────────────────────────

    def update_volatility_brake(self) -> None:
        """
        Calcule l'ATR BTC/USDC 1h et met à jour le volatility brake du RiskManager.
        Appel idempotent — utilise le cache interne si disponible.
        """
        try:
            df_btc = self.get("BTC/USDC", "1h")
            if df_btc is not None and len(df_btc) > 10:
                atr   = _compute_atr(df_btc)
                price = float(df_btc["close"][-1])
                if price > 0 and atr > 0:
                    self._risk.update_volatility(atr / price)
        except Exception as e:
            logger.debug(f"[OHLCVCache] volatility brake KO : {e}")

    # ── Purge mémoire ────────────────────────────────────────────────────

    def clear(self) -> None:
        """Vide les caches OHLCV et ATR (ex. après coupure réseau)."""
        self._ohlcv_cache.clear()
        self._atr_cache.clear()
        logger.debug("[OHLCVCache] Caches OHLCV et ATR vidés.")

    def purge(self, active_symbols) -> None:
        """
        Nettoie les entrées expirées ou orphelines du cache.
        Appelé périodiquement depuis LiveTrader._purge_memory().

        Parameters
        ----------
        active_symbols : iterable — symboles actuellement actifs (scanner.get_symbols())
        """
        now = time.time()

        # Cache OHLCV : entrées dont TTL × 10 est dépassé
        stale = [k for k, v in self._ohlcv_cache.items()
                 if (now - v[0]) > _OHLCV_TTL.get(k[1], 120) * 10]
        for k in stale:
            del self._ohlcv_cache[k]

        # Timestamps de bougies pour les symboles inactifs
        active_set = set(active_symbols)
        for key in list(self._last_candle_ts.keys()):
            if key[0] not in active_set:
                del self._last_candle_ts[key]

        # Cache ATR expiré
        cutoff_atr = now - self._atr_cache_ttl * 10
        self._atr_cache = {s: v for s, v in self._atr_cache.items() if v[0] > cutoff_atr}

        # Erreurs exchange pour les symboles inactifs
        self._exchange_errors = {
            s: v for s, v in self._exchange_errors.items() if s in active_set
        }

        logger.debug(
            f"[OHLCVCache] Purge : {len(self._ohlcv_cache)} entrées OHLCV, "
            f"{len(self._atr_cache)} ATR."
        )
