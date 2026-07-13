"""
CandleStore — stockage Parquet persistant des bougies OHLCV par (symbol, timeframe).
Fetch incrémental, thread-safe, retourne un pl.DataFrame identique à MarketScanner.
"""

import os
import logging
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

from app.core.config import DATA_ROOT
from app.core.singleton import lazy_singleton

OHLCV_DIR = os.path.join(DATA_ROOT, "ohlcv")

logger = logging.getLogger(__name__)

# Plancher de `since` pour les fetch paginés : ~fondation d'OKX (2017-01-01).
# Évite de calculer un `since` absurde (ex. 50000 × 4h ≈ année 2003) que l'exchange
# REJETTE en renvoyant une liste vide → « premier fetch : 0 bougie » sur les gros
# timeframes. OKX renvoie les plus anciennes bougies DISPONIBLES pour un `since`
# antérieur au listing, mais pas pour un `since` d'avant sa création.
_MIN_SINCE_MS = 1_483_228_800_000  # 2017-01-01 UTC

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

    def __init__(self, base_dir: str = OHLCV_DIR):
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

    def load_cached(self, symbol: str, tf: str) -> pl.DataFrame:
        """DataFrame OHLCV en cache (sans aucun appel exchange). Vide si absent."""
        return self._load(self._path(symbol, tf))

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
        # On clampe à _MIN_SINCE_MS (2017-01-01) : un `since` trop ancien (ex. 1d × 20000
        # ⇒ avant le listing) fait rejeter la requête OHLCV par l'exchange (liste vide).
        if before_ms is not None:
            since = max(_MIN_SINCE_MS, before_ms - needed * tf_ms)
        else:
            since = max(_MIN_SINCE_MS, int(exchange.milliseconds()) - needed * tf_ms)

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

        since   = max(_MIN_SINCE_MS, exchange.milliseconds() - total * tf_ms)
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
                cols = list(_OHLCV_SCHEMA.keys())
                # Robustesse : force l'ORDRE canonique des colonnes. Un Parquet
                # écrit avec un ordre différent (time en dernier) casse sinon le
                # pl.concat/vstack « unable to vstack, column names don't match:
                # open/time ».
                if set(cols).issubset(df.columns):
                    df = df.select(cols)
                if df.height and df["time"].dtype != pl.Datetime("ms"):
                    df = df.with_columns(pl.col("time").cast(pl.Datetime("ms")))
                return df
            except Exception as e:
                logger.warning(f"[CandleStore] Fichier corrompu {path} — re-fetch : {e}")
        return pl.DataFrame(schema=_OHLCV_SCHEMA)

    def _save(self, path: Path, df: pl.DataFrame) -> None:
        """Écriture ATOMIQUE : Parquet écrit dans un .tmp puis os.replace
        (rename atomique sur le même filesystem). Un lecteur concurrent — y
        compris un second process (cli.py --backtest/--optimize pendant que le
        live tourne, cf. OPS-07 : le verrou _get_file_lock est intra-process
        seulement) — ne voit jamais de fichier tronqué."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            df.write_parquet(tmp, compression="zstd")
            os.replace(tmp, path)
        except Exception as e:
            logger.error(f"[CandleStore] Erreur écriture {path} : {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

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
get_store = lazy_singleton(
    CandleStore,
    doc="Retourne le singleton CandleStore (lazy init, thread-safe).",
)
