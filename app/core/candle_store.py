"""
CandleStore — stockage Parquet persistant des bougies OHLCV par (symbol, timeframe).
Fetch incrémental, thread-safe, retourne un pl.DataFrame identique à MarketScanner.
"""

import logging
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

logger = logging.getLogger(__name__)

# Schéma Parquet — time stocké en ms pour cohérence avec ccxt
_OHLCV_SCHEMA = {
    "time":   pl.Datetime("ms"),
    "open":   pl.Float64,
    "high":   pl.Float64,
    "low":    pl.Float64,
    "close":  pl.Float64,
    "volume": pl.Float64,
}

# ── Verrous par fichier (thread-safety) ────────────────────────────────────────
_locks_registry: Dict[str, threading.Lock] = {}
_registry_lock  = threading.Lock()


def _get_file_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _registry_lock:
        if key not in _locks_registry:
            _locks_registry[key] = threading.Lock()
        return _locks_registry[key]


# ── CandleStore ────────────────────────────────────────────────────────────────

class CandleStore:
    """
    Stockage Parquet persistant des bougies OHLCV.
    Stratégie : cache local → fetch incrémental → merge+déduplique → persistance.
    """

    def __init__(self, base_dir: str = "data/ohlcv"):
        self._base = Path(base_dir)
        logger.info(f"[CandleStore] Initialisation — répertoire : {self._base.resolve()}")

    # ── API publique ──────────────────────────────────────────────────────────

    def fetch(self, exchange, symbol: str, tf: str,
              total: int, prefer_cache: bool = False) -> Optional[pl.DataFrame]:
        """Retourne `total` bougies pour (symbol, tf) depuis cache local + exchange.

        prefer_cache=True : si le Parquet local contient déjà au moins ``total``
        bougies, on les retourne directement SANS aucun appel à l'exchange
        (ni fetch incrémental, ni backfill historique). Utilisé par les backtests
        et l'optimiseur, qui travaillent sur une plage historique fixe et n'ont
        pas besoin de la dernière bougie en cours de formation. Le live et le
        scanner gardent le défaut (prefer_cache=False) pour rester à jour.
        """
        path = self._path(symbol, tf)
        lock = _get_file_lock(path)

        with lock:
            df_cached = self._load(path)

            # Court-circuit cache : assez de données en stock → pas d'appel exchange.
            if prefer_cache and len(df_cached) >= total:
                logger.info(
                    f"[CandleStore] {symbol}/{tf} — cache suffisant "
                    f"({len(df_cached)} ≥ {total} bougies), aucun appel à l'exchange"
                )
                return df_cached.tail(total)

            # Fetch incrémental ou complet
            if len(df_cached) > 0:
                last_ms  = int(df_cached["time"].max().timestamp() * 1000)
                new_raw  = self._fetch_incremental(exchange, symbol, tf, last_ms + 1)
            else:
                logger.info(f"[CandleStore] {symbol}/{tf} — premier fetch ({total} bougies)")
                new_raw  = self._fetch_full(exchange, symbol, tf, total)

            # Merge + persistance si nouvelles données
            if new_raw:
                df_new    = self._raw_to_df(new_raw)
                df_merged = (
                    pl.concat([df_cached, df_new])
                    .unique("time")
                    .sort("time")
                    .filter((pl.col("volume") > 0) & (pl.col("close") > 0))
                    .drop_nulls()
                )
                self._save(path, df_merged)
                df_cached = df_merged
                logger.debug(
                    f"[CandleStore] {symbol}/{tf} : +{len(new_raw)} bougies "
                    f"→ {len(df_cached)} stockées"
                )

            # Si le cache est insuffisant, tenter de récupérer des bougies historiques plus anciennes
            if len(df_cached) < total:
                missing   = total - len(df_cached)
                first_ms  = int(df_cached["time"].min().timestamp() * 1000) if len(df_cached) > 0 else None
                logger.info(
                    f"[CandleStore] {symbol}/{tf} — cache insuffisant "
                    f"({len(df_cached)}/{total} bougies) — "
                    f"tentative de récupération de {missing} bougies historiques"
                )
                old_raw = self._fetch_historical(exchange, symbol, tf, first_ms, missing)
                if old_raw:
                    df_old    = self._raw_to_df(old_raw)
                    df_merged = (
                        pl.concat([df_cached, df_old])
                        .unique("time")
                        .sort("time")
                        .filter((pl.col("volume") > 0) & (pl.col("close") > 0))
                        .drop_nulls()
                    )
                    self._save(path, df_merged)
                    df_cached = df_merged
                    logger.info(
                        f"[CandleStore] {symbol}/{tf} : +{len(old_raw)} bougies historiques "
                        f"→ {len(df_cached)} stockées au total"
                    )
                else:
                    logger.info(
                        f"[CandleStore] {symbol}/{tf} — aucune bougie historique supplémentaire "
                        f"disponible sur l'exchange (cache : {len(df_cached)} bougies)"
                    )

        if len(df_cached) == 0:
            return None

        result = df_cached.tail(total)
        return result if len(result) >= 1 else None

    def stats(self, symbol: str, tf: str) -> dict:
        """Stats du cache Parquet pour (symbol, tf)."""
        path = self._path(symbol, tf)
        df   = self._load(path)
        if len(df) == 0:
            return {"symbol": symbol, "tf": tf, "bars": 0,
                    "from": None, "to": None, "size_kb": 0}
        return {
            "symbol":  symbol,
            "tf":      tf,
            "bars":    len(df),
            "from":    str(df["time"].min()),
            "to":      str(df["time"].max()),
            "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
        }

    def all_stats(self) -> list:
        """Stats de tous les fichiers Parquet dans base_dir."""
        results = []
        for parquet in sorted(self._base.rglob("*.parquet")):
            # Structure: base/{symbol}/{tf}.parquet
            tf     = parquet.stem
            symbol = parquet.parent.name.replace("_", "/", 1)
            results.append(self.stats(symbol, tf))
        return results

    # ── Fetch interne ─────────────────────────────────────────────────────────

    def _fetch_incremental(self, exchange, symbol: str, tf: str,
                           since_ms: int) -> List[list]:
        """Fetch uniquement les bougies depuis since_ms, pagine jusqu'à 10 pages."""
        LIMIT      = 1000
        all_raw    = []
        seen_ts    = set()
        since      = since_ms
        rate_sleep = getattr(exchange, "rateLimit", 1200) / 1000

        for _ in range(10):
            try:
                batch = exchange.fetch_ohlcv(symbol, tf, since=since, limit=LIMIT)
            except Exception as e:
                logger.warning(f"[CandleStore] fetch_incr {symbol}/{tf} : {e}")
                break

            if not batch:
                break

            added = 0
            for c in batch:
                if c[0] not in seen_ts:
                    seen_ts.add(c[0])
                    all_raw.append(c)
                    added += 1

            if added == 0:
                break
            since = batch[-1][0] + 1
            if len(batch) < LIMIT:
                break
            time.sleep(rate_sleep)

        all_raw.sort(key=lambda x: x[0])
        return all_raw

    def _fetch_historical(self, exchange, symbol: str, tf: str,
                          before_ms: Optional[int], needed: int) -> List[list]:
        """Fetch des bougies antérieures à before_ms pour compléter le cache."""
        LIMIT      = 1000
        rate_sleep = getattr(exchange, "rateLimit", 1200) / 1000

        try:
            tf_ms = exchange.parse_timeframe(tf) * 1000
        except Exception as e:
            logger.warning(f"[CandleStore] parse_timeframe '{tf}' KO : {e} — fallback 1h")
            tf_ms = 3_600_000  # fallback 1h en ms

        # Point de départ : assez loin dans le passé pour couvrir les bougies manquantes.
        # On clampe à 0 pour éviter un timestamp négatif (ex. 1d × 20000 > cache début)
        # qui ferait rejeter la requête OHLCV par l'exchange (paramètre since invalide).
        if before_ms is not None:
            since = max(0, before_ms - needed * tf_ms)
        else:
            since = max(0, int(exchange.milliseconds()) - needed * tf_ms)

        all_raw = []
        seen_ts = set()

        while len(all_raw) < needed:
            try:
                batch = exchange.fetch_ohlcv(symbol, tf, since=since, limit=LIMIT)
            except Exception as e:
                logger.warning(f"[CandleStore] fetch_historical {symbol}/{tf} : {e}")
                break

            if not batch:
                break

            added = 0
            for c in batch:
                # Garder uniquement les bougies antérieures au cache existant
                if before_ms is None or c[0] < before_ms:
                    if c[0] not in seen_ts:
                        seen_ts.add(c[0])
                        all_raw.append(c)
                        added += 1

            if added == 0:
                break

            since = batch[-1][0] + 1
            if len(batch) < LIMIT:
                break
            time.sleep(rate_sleep)

        all_raw.sort(key=lambda x: x[0])
        return all_raw

    def _fetch_full(self, exchange, symbol: str, tf: str,
                    total: int) -> List[list]:
        """Fetch complet paginé — premier chargement uniquement."""
        LIMIT      = 1000
        rate_sleep = getattr(exchange, "rateLimit", 1200) / 1000

        if total <= LIMIT:
            try:
                return exchange.fetch_ohlcv(symbol, tf, limit=total) or []
            except Exception as e:
                logger.warning(f"[CandleStore] fetch_full {symbol}/{tf} : {e}")
                return []

        try:
            tf_ms = exchange.parse_timeframe(tf) * 1000
        except Exception as e:
            logger.warning(f"[CandleStore] parse_timeframe '{tf}' KO : {e} — fallback 1h")
            tf_ms = 3_600_000  # fallback 1h en ms

        since   = exchange.milliseconds() - total * tf_ms
        all_raw = []
        seen_ts = set()

        while len(all_raw) < total:
            try:
                batch = exchange.fetch_ohlcv(symbol, tf, since=since, limit=LIMIT)
            except Exception as e:
                logger.warning(f"[CandleStore] fetch_full page {symbol}/{tf} : {e}")
                break

            if not batch:
                break

            added = 0
            for c in batch:
                if c[0] not in seen_ts:
                    seen_ts.add(c[0])
                    all_raw.append(c)
                    added += 1

            if added == 0:
                break
            since = batch[-1][0] + 1
            time.sleep(rate_sleep)

        all_raw.sort(key=lambda x: x[0])
        return all_raw[-total:]

    # ── Helpers Parquet ───────────────────────────────────────────────────────

    def _path(self, symbol: str, tf: str) -> Path:
        safe = symbol.replace("/", "_").replace(":", "_")
        return self._base / safe / f"{tf}.parquet"

    def _load(self, path: Path) -> pl.DataFrame:
        if path.exists():
            try:
                df = pl.read_parquet(path)
                if df["time"].dtype != pl.Datetime("ms"):
                    df = df.with_columns(pl.col("time").cast(pl.Datetime("ms")))
                return df
            except Exception as e:
                logger.warning(f"[CandleStore] Fichier corrompu {path} — re-fetch : {e}")
        return pl.DataFrame(schema=_OHLCV_SCHEMA)

    def _save(self, path: Path, df: pl.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.write_parquet(path, compression="zstd")
        except Exception as e:
            logger.error(f"[CandleStore] Erreur écriture {path} : {e}")

    @staticmethod
    def _raw_to_df(raw: List[list]) -> pl.DataFrame:
        return (
            pl.DataFrame(
                raw,
                schema=["time", "open", "high", "low", "close", "volume"],
                orient="row",
            )
            .with_columns(pl.from_epoch("time", time_unit="ms").cast(pl.Datetime("ms")))
        )


# ── Singleton global ───────────────────────────────────────────────────────────

_default_store: Optional[CandleStore] = None
_store_lock    = threading.Lock()


def get_store(base_dir: str = "data/ohlcv") -> CandleStore:
    """Retourne le singleton CandleStore (lazy init, thread-safe)."""
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                _default_store = CandleStore(base_dir)
    return _default_store
