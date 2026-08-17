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

Indépendant de MarketScanner : appelle CandleStore directement via l'exchange,
comme le fait scanner.fetch_ohlcv() — sans passer par la couche engine.

Usage dans LiveTrader :
    self.ohlcv_cache = OHLCVCache(exchange=self.exchange, cfg=cfg,
                                   notif=self.notif, risk=self.risk)
    df  = self.ohlcv_cache.get("BTC/USDC", "1h", open_positions)
    atr = self.ohlcv_cache.get_cached_atr("BTC/USDC")
    self.ohlcv_cache.update_volatility_brake()
    self.ohlcv_cache.purge(active_symbols)
"""
import logging
import threading
import time
from typing import Dict, Optional, Tuple

import polars as pl

from app.core.candle_store import drop_forming_candle, epoch_ms, get_store
from app.core.indicators import atr_val as _compute_atr
from app.core.indicators import precompute_df
from app.core.param_resolution import DEFAULT_CONFIG_SYMBOL
from app.engine.optimizer_search import RECOMMENDED_LIMIT

logger = logging.getLogger(__name__)

# TTL du cache OHLCV par timeframe (secondes)
_OHLCV_TTL: Dict[str, int] = {
    "1m": 30, "5m": 60, "15m": 180, "30m": 360,
    "1h": 600, "4h": 2400, "1d": 14400,
}

# Durée d'un timeframe en millisecondes — source unique (V4-A).
# (_OHLCV_TTL ci-dessus reste local : c'est un intervalle de POLLING, pas
# une durée de timeframe.)
from app.core.timeframes import TF_MS as _TF_MS  # noqa: E402


class OHLCVCache:
    """
    Cache OHLCV multi-TF in-memory avec gestion du cycle de vie complet.

    Dépend directement de l'exchange (via CandleStore) — indépendant de MarketScanner.

    Paramètres
    ----------
    exchange : RobustExchange — passé à CandleStore pour les fetches
    cfg      : dict config globale
    notif    : Notifier — pour notify_exchange_error
    risk     : RiskManager — pour update_volatility dans update_volatility_brake
    """

    def __init__(self, exchange, cfg: dict, notif, risk):
        self._exchange = exchange
        self._cfg      = cfg
        self._notif    = notif
        self._risk     = risk

        # S1-02 : protège les dicts ci-dessous — update_volatility_brake()/
        # _enrich_derivatives() tournent depuis le cycle principal, mais
        # get()/purge() sont aussi appelés depuis les threads d'auto-opt et
        # de forward-test (cf. app/live/auto_opt_mixin.py). RLock : get()
        # réacquiert le verrou en deux temps (lecture cache, puis écriture
        # après le fetch réseau) sans jamais s'auto-bloquer.
        self._lock = threading.RLock()

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

        # ── Dérivés (funding/OI/long-short/taker) au fil de l'eau ──────────────
        # Opt-in via config (derivatives.enabled). Accumulés dans data/derivatives/
        # comme l'OHLCV via CandleStore, et mergés en colonnes (funding_z, etc.)
        # dans le df de scoring. Dégradation gracieuse : tout échec est avalé.
        dcfg = cfg.get("derivatives", {}) or {}
        self._deriv_enabled: bool = bool(dcfg.get("enabled", False))
        self._deriv_period: str   = str(dcfg.get("period", "1h"))
        self._deriv_interval: float = float(dcfg.get("refresh_interval", 300))
        self._deriv_zwin: int     = int(dcfg.get("z_window", 90))
        self._deriv_store = None
        if self._deriv_enabled:
            try:
                from app.core.derivatives import DerivativesStore
                self._deriv_store = DerivativesStore()
                logger.info("[OHLCVCache] Dérivés activés — accumulation au fil de l'eau "
                            f"(period={self._deriv_period}, refresh={self._deriv_interval:.0f}s)")
            except Exception as e:
                logger.warning(f"[OHLCVCache] Init dérivés KO (désactivés) : {e}")
                self._deriv_enabled = False

    def _enrich_derivatives(self, symbol: str, df: pl.DataFrame) -> pl.DataFrame:
        """Accumule (fetch incrémental throttlé) puis merge les colonnes dérivées.
        Gracieux : en cas d'échec, retourne le df OHLCV inchangé."""
        if not self._deriv_enabled or self._deriv_store is None:
            return df
        try:
            self._deriv_store.refresh(self._exchange, symbol, self._deriv_period,
                                      min_interval=self._deriv_interval)
            return self._deriv_store.align_to_ohlcv(
                df, symbol, exchange=None, period=self._deriv_period,
                refresh=False, z_window=self._deriv_zwin)
        except Exception as e:
            logger.debug(f"[OHLCVCache] enrich dérivés {symbol} KO : {e}")
            return df

    # ── Bougie en cours de formation ───────────────────────────────────────

    def _drop_forming_candle(self, df: pl.DataFrame, tf: str) -> pl.DataFrame:
        """D-01 : même élagage que ``CandleStore.drop_forming_candle``."""
        return drop_forming_candle(df, tf)

    def get_forming_range(self, symbol: str, tf: str):
        """``(low, high)`` de la bougie en formation, ou ``None``.

        L-01 : le stop live doit se juger sur le plus-bas/plus-haut de
        l'intervalle, pas sur ``ticker.last``. La bougie en formation est
        retirée du cache de scoring ; on la relit ici, brute.
        """
        try:
            df = get_store().fetch(self._exchange, symbol, tf, total=2)
            if df is None or df.height == 0:
                return None
            return float(df["low"][-1]), float(df["high"][-1])
        except Exception as e:
            logger.debug(f"[OHLCVCache] forming range {symbol}/{tf} KO : {e}")
            return None

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
        with self._lock:
            cached = self._ohlcv_cache.get(key)
            if cached and (time.time() - cached[0]) < ttl:
                return cached[1]

        # Fetch réseau HORS verrou : un lock tenu ici sérialiserait tous les
        # (symbol, tf) entre eux à chaque cycle (get() est appelé pour
        # chaque symbole actif), alors que seuls les dicts partagés
        # ci-dessous ont besoin d'exclusion mutuelle.
        fetch_limit = min(RECOMMENDED_LIMIT.get(tf, 500), 500)
        try:
            df = get_store().fetch(self._exchange, symbol, tf, total=fetch_limit)
        except Exception as _fe:
            logger.error(f"[OHLCVCache] fetch {symbol}/{tf} : {_fe}")
            df = None
        # Parité de timing avec le backtest : on ne score que sur des bougies
        # clôturées. La dernière bougie renvoyée par l'exchange est souvent
        # encore en formation (close non définitif) → repaint et exécution une
        # barre trop tôt vs backtest. On l'élague avant tout calcul.
        if df is not None:
            df = self._drop_forming_candle(df, tf)
        if df is None or len(df) < 220:
            with self._lock:
                self._exchange_errors[symbol] = self._exchange_errors.get(symbol, 0) + 1
                n_errors = self._exchange_errors[symbol]
            self._notif.notify_exchange_error(
                symbol, f"fetch_ohlcv {tf} retourné vide ou trop court", n_errors
            )
            return None
        with self._lock:
            self._exchange_errors[symbol] = 0

            # Filtre "nouvelle bougie" — skip si même ts et pas de position ouverte
            try:
                last_ts  = epoch_ms(df["time"][-1]) or 0
                prev_ts  = self._last_candle_ts.get(key, 0)
                has_open = open_positions is None or bool(open_positions)
                if last_ts == prev_ts and not has_open:
                    logger.debug(f"[OHLCVCache] {symbol}/{tf} : même bougie — skip")
                    return None
                self._last_candle_ts[key] = last_ts
            except Exception as e:
                logger.debug(f"[OHLCVCache] vérif bougie {symbol}/{tf} : {e}")

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

        # Enrichissement dérivés (opt-in) — accumulation + colonnes funding_z, etc.
        df = self._enrich_derivatives(symbol, df)

        with self._lock:
            self._ohlcv_cache[key] = (time.time(), df)
        return df

    # ── Cache ATR ────────────────────────────────────────────────────────

    def get_cached_atr(self, symbol: str) -> Optional[float]:
        """Retourne l'ATR mis en cache si non expiré, sinon None."""
        with self._lock:
            cached = self._atr_cache.get(symbol)
            if cached and (time.time() - cached[0]) < self._atr_cache_ttl:
                return cached[1]
            return None

    def set_atr(self, symbol: str, atr: float) -> None:
        """Met à jour manuellement le cache ATR (ex. après fetch fallback dans _manage_position)."""
        with self._lock:
            self._atr_cache[symbol] = (time.time(), float(atr))

    # ── Volume quote moyen (FIN-07) ─────────────────────────────────────────

    def get_avg_quote_volume(self, symbol: str, tf: str, lookback: int = 20) -> Optional[float]:
        """Moyenne glissante du volume en devise de cotation (``volume × close``)
        sur les ``lookback`` dernières bougies mises en cache — même fenêtre que
        ``ctx.qvol_arr`` côté backtest (modèle ``slippage_model: size``, BT-10).

        Lit uniquement le cache déjà rempli par ``get()`` (pas de fetch réseau
        dédié) : retourne ``None`` si (symbol, tf) n'a pas encore été chargé.
        """
        with self._lock:
            cached = self._ohlcv_cache.get((symbol, tf))
        if not cached:
            return None
        df = cached[1]
        if df is None or len(df) < lookback or "volume" not in df.columns:
            return None
        try:
            tail = df.tail(lookback)
            avg = float((tail["volume"] * tail["close"]).mean())
            return avg if avg > 0 else None
        except Exception as e:
            logger.debug(f"[OHLCVCache] get_avg_quote_volume {symbol}/{tf} KO : {e}")
            return None

    # ── Volatility brake ─────────────────────────────────────────────────

    def update_volatility_brake(self) -> None:
        """
        Met à jour le volatility brake du RiskManager depuis l'ATR BTC/USDC 1h.

        `get()` ayant déjà mis l'ATR en cache lors du fetch, on le réutilise
        directement plutôt que de le recalculer.
        """
        try:
            df_btc = self.get(DEFAULT_CONFIG_SYMBOL, "1h")
            if df_btc is None or len(df_btc) <= 10:
                return
            price = float(df_btc["close"][-1])
            atr   = self.get_cached_atr(DEFAULT_CONFIG_SYMBOL)  # déjà calculé dans get()
            if price > 0 and atr and atr > 0:
                self._risk.update_volatility(atr / price)
        except Exception as e:
            logger.debug(f"[OHLCVCache] volatility brake KO : {e}")

    # ── Purge mémoire ────────────────────────────────────────────────────

    def clear(self) -> None:
        """Vide les caches OHLCV et ATR (ex. après coupure réseau)."""
        with self._lock:
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
        active_set = set(active_symbols)

        with self._lock:
            # Cache OHLCV : entrées dont TTL × 10 est dépassé
            stale = [k for k, v in self._ohlcv_cache.items()
                     if (now - v[0]) > _OHLCV_TTL.get(k[1], 120) * 10]
            for k in stale:
                del self._ohlcv_cache[k]

            # Timestamps de bougies pour les symboles inactifs
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

            n_ohlcv, n_atr = len(self._ohlcv_cache), len(self._atr_cache)

        logger.debug(f"[OHLCVCache] Purge : {n_ohlcv} entrées OHLCV, {n_atr} ATR.")
