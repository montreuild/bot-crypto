"""
CandleStore — stockage Parquet persistant des bougies OHLCV par (symbol, timeframe).
Fetch incrémental, thread-safe, retourne un pl.DataFrame identique à MarketScanner.
"""

import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

from app.core.candle_fetch import CandleFetchMixin

# Ré-exports : `epoch_ms`, `drop_forming_candle`, `_get_file_lock` et `OHLCV_DIR`
# sont importés depuis ce module par le reste du dépôt.
from app.core.candle_helpers import (  # noqa: F401
    _GAP_FILL_PAGE,
    _MAX_ABSENT_GAP_BARS,
    _MAX_GAP_SPAN_PAGES,
    _MIN_SINCE_MS,
    _OHLCV_SCHEMA,
    _SYMBOL_RE,
    OHLCV_DIR,
    _bars_span_ms,
    _fmt_ms,
    _get_file_lock,
    _min_since,
    _provider_for,
    _valid_bars,
    drop_forming_candle,
    epoch_ms,
)
from app.core.candle_memos import _NO_HISTORY_RETRY_S, CandleMemosMixin  # noqa: F401
from app.core.singleton import lazy_singleton
from app.core.timeframes import TF_SECONDS

logger = logging.getLogger(__name__)

# SEC-002 — mêmes contraintes que app/api/schemas.py (symbole + timeframe whitelist).
# ── CandleStore ────────────────────────────────────────────────────────────────

class CandleStore(CandleFetchMixin, CandleMemosMixin):
    """
    Stockage Parquet persistant des bougies OHLCV.
    Stratégie : cache local → fetch incrémental → merge+déduplique → persistance.
    """

    def __init__(self, base_dir: str = OHLCV_DIR):
        self._base = Path(base_dir)
        #: `(symbol, tf) → (plus vieille bougie connue, instant de réessai)`.
        #: Mémorise qu'un backfill historique n'a RIEN ramené, pour ne pas le
        #: rejouer à chaque cycle. Invalidé dès que la borne basse du cache
        #: bouge — si de l'historique arrive par une autre voie, on retente.
        self._no_history: Dict[tuple, tuple] = {}
        self._no_history_lock = threading.Lock()
        #: Nombre d'amorçages profonds réussis — sert à savoir si le
        #: premier fetch a déjà ramené toute la profondeur disponible.
        self._deep_fetches = 0
        #: `(symbol, tf) → (missing_bars, retry_at)` — trous intérieurs que
        #: l'exchange n'a pas pu combler. Même délai que l'historique épuisé.
        self._unfillable_gaps: Dict[tuple, tuple] = {}
        #: `path → (n_barres, dernier_ts, trous)` — dernier scan de trous, pour
        #: ne rescanner que la queue à la sauvegarde suivante.
        self._gaps_cache: Dict[str, tuple] = {}
        self._gaps_cache_lock = threading.Lock()
        logger.info(f"[CandleStore] Initialisation — répertoire : {self._base.resolve()}")

    # ── API publique ──────────────────────────────────────────────────────────

    def fetch(self, exchange, symbol: str, tf: str,
              total: int, prefer_cache: bool = False) -> Optional[pl.DataFrame]:
        """Retourne `total` bougies pour (symbol, tf) depuis cache local + exchange.

        prefer_cache=True : si le Parquet local contient déjà au moins ``total``
        bougies, on les retourne directement SANS aucun appel à l'exchange
        (ni fetch incrémental, ni backfill historique, ni recousage de
        trous). Utilisé par les backtests et l'optimiseur, qui travaillent
        sur une plage historique fixe. Le live et le scanner (défaut
        ``prefer_cache=False``) restent à jour et tentent de recoudre les
        trous intérieurs non calendaires, même si le cache dépasse ``total``.
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
                return drop_forming_candle(df_cached.tail(total), tf)

            # Fetch incrémental ou complet
            bootstrapped_deep = False
            if len(df_cached) > 0:
                last_ms  = epoch_ms(df_cached["time"].max()) or 0
                new_raw  = self._fetch_incremental(exchange, symbol, tf, last_ms + 1)
            else:
                logger.info(f"[CandleStore] {symbol}/{tf} — premier fetch ({total} bougies)")
                before = self._deep_fetches
                new_raw = self._fetch_full(exchange, symbol, tf, total)
                bootstrapped_deep = self._deep_fetches > before and bool(new_raw)

            # Merge + persistance si nouvelles données
            if new_raw:
                df_new    = self._raw_to_df(new_raw)
                df_merged = _valid_bars(
                    pl.concat([df_cached, df_new]).unique("time", keep="last").sort("time"),
                    exchange, symbol,
                )
                df_merged = drop_forming_candle(df_merged, tf)
                self._save(path, df_merged)
                df_cached = df_merged
                logger.debug(
                    f"[CandleStore] {symbol}/{tf} : +{len(new_raw)} bougies "
                    f"→ {len(df_cached)} stockées"
                )

            # Si le cache est insuffisant, tenter de récupérer des bougies historiques plus anciennes
            if len(df_cached) < total:
                missing   = total - len(df_cached)
                first_ms  = epoch_ms(df_cached["time"].min()) if len(df_cached) > 0 else None
                if bootstrapped_deep:
                    # L'amorçage vient de ramener TOUT ce que la source
                    # possède : chercher plus ancien derrière est une requête
                    # dont on connaît déjà la réponse.
                    self._mark_exhausted(symbol, tf, first_ms)
                    logger.info(
                        f"[CandleStore] {symbol}/{tf} — {len(df_cached)}/{total} "
                        f"bougies : c'est tout l'historique publié par la source "
                        f"pour cette granularité"
                    )
                    old_raw: list = []
                elif self._history_exhausted(symbol, tf, first_ms):
                    logger.debug(
                        f"[CandleStore] {symbol}/{tf} — backfill historique ignoré "
                        f"({len(df_cached)}/{total}) : le provider l'a déjà déclaré "
                        f"épuisé et la borne basse du cache n'a pas bougé"
                    )
                    old_raw = []
                else:
                    logger.info(
                        f"[CandleStore] {symbol}/{tf} — cache insuffisant "
                        f"({len(df_cached)}/{total} bougies) — recherche de "
                        f"bougies manquantes (il en faudrait {missing} de plus)"
                    )
                    # Les horodatages connus permettent au chemin profond de
                    # distinguer un trou intérieur d'une barre déjà en cache.
                    known_ts = (set(df_cached["time"].dt.epoch("ms").to_list())
                                if len(df_cached) else set())
                    old_raw = self._fetch_historical(exchange, symbol, tf, first_ms,
                                                     missing, known_ts=known_ts)
                if old_raw:
                    n_before  = len(df_cached)
                    df_old    = self._raw_to_df(old_raw)
                    df_merged = _valid_bars(
                        pl.concat([df_cached, df_old]).unique("time", keep="first").sort("time"),
                        exchange, symbol,
                    )
                    df_merged = drop_forming_candle(df_merged, tf)
                    self._save(path, df_merged)
                    df_cached = df_merged
                    self._forget_exhausted(symbol, tf)
                    # Le DELTA RÉEL, pas la taille du lot reçu. L'ancien message
                    # annonçait « tentative de récupération de 126 bougies » puis
                    # « +1054 » : le chemin profond ignore la borne `missing`, si
                    # bien que l'intention affichée et l'action ne parlaient pas
                    # de la même chose. Après déduplication, seul ce delta décrit
                    # ce qui a réellement changé sur disque.
                    logger.info(
                        f"[CandleStore] {symbol}/{tf} : +{len(df_cached) - n_before} "
                        f"bougies ({len(old_raw)} reçues, le reste déjà en cache) "
                        f"→ {len(df_cached)} stockées au total"
                    )
                elif not self._history_exhausted(symbol, tf, first_ms):
                    self._mark_exhausted(symbol, tf, first_ms)
                    logger.info(
                        f"[CandleStore] {symbol}/{tf} — aucune bougie historique supplémentaire "
                        f"disponible sur l'exchange (cache : {len(df_cached)} bougies) — "
                        f"la demande ne sera pas rejouée avant "
                        f"{_NO_HISTORY_RETRY_S / 3600:.0f} h"
                    )

            # Trous INTÉRIEURS : le fetch incrémental ne regarde qu'après la
            # dernière barre, et le backfill historique ci-dessus ne tourne
            # que si le cache est plus court que `total`. Un BTC 15m à 58 k
            # barres avec 7 trous au milieu n'était donc jamais recousu
            # (l'UI refetch ne permet pas non plus de forcer `bars`).
            if len(df_cached) >= 2:
                df_cached = self._fill_detected_gaps(
                    exchange, symbol, tf, df_cached, path)

        if len(df_cached) == 0:
            return None

        result = drop_forming_candle(df_cached.tail(total), tf)
        return result if len(result) >= 1 else None

    def load_cached(self, symbol: str, tf: str) -> pl.DataFrame:
        """DataFrame OHLCV en cache (sans aucun appel exchange). Vide si absent."""
        return self._load(self._path(symbol, tf))

    def fetch_range(self, exchange, symbol: str, tf: str,
                    start=None, end=None, *,
                    total: int, prefer_cache: bool = False) -> Optional[pl.DataFrame]:
        """A-03 : backfill jusqu'à ``total`` (persisté une fois), puis lit [start, end].

        Le coût exchange n'est payé que si le Parquet est plus mince que
        ``total``. La profondeur reste dans le store pour les prochains runs.
        Seule la fenêtre demandée est renvoyée au backtest.
        """
        filled = self.fetch(exchange, symbol, tf, total=total, prefer_cache=prefer_cache)
        if start is None and end is None:
            return filled
        out = self.load_range(symbol, tf, start=start, end=end)
        return out if len(out) else None

    def load_range(self, symbol: str, tf: str,
                   start=None, end=None) -> pl.DataFrame:
        """Lit le Parquet filtré par plage (predicate pushdown, A-03).

        Ne touche pas l'exchange. DataFrame vide si le fichier n'existe pas
        ou si le scan échoue. ``start`` / ``end`` sont des ``datetime`` naïfs
        UTC, comme la colonne ``time`` du store.
        """
        path = self._path(symbol, tf)
        if not path.exists():
            return pl.DataFrame(schema=_OHLCV_SCHEMA)  # type: ignore[arg-type]
        try:
            lf = pl.scan_parquet(path)
            if start is not None:
                lf = lf.filter(pl.col("time") >= start)
            if end is not None:
                lf = lf.filter(pl.col("time") <= end)
            df = lf.collect()
            cols = list(_OHLCV_SCHEMA.keys())
            if set(cols).issubset(df.columns):
                df = df.select(cols)
            if df.height and df["time"].dtype != pl.Datetime("ms"):
                df = df.with_columns(pl.col("time").cast(pl.Datetime("ms")))
            return df
        except Exception as e:
            logger.warning(f"[CandleStore] load_range {symbol}/{tf} KO : {e}")
            return pl.DataFrame(schema=_OHLCV_SCHEMA)  # type: ignore[arg-type]

    def count_bars(self, symbol: str, tf: str) -> int:
        """Nombre de bougies en cache sans charger le DataFrame complet.

        Utilise les métadonnées Parquet (row-group) — O(1) par fichier, ce qui
        permet d'enrichir un univers de 100+ symboles sans bloquer l'UI.
        """
        path = self._path(symbol, tf)
        if not path.exists():
            return 0
        try:
            import pyarrow.parquet as pq
            meta = pq.read_metadata(path)
            return int(meta.num_rows or 0)
        except Exception:
            try:
                # Repli : une seule colonne (beaucoup plus léger que stats()).
                return int(pl.read_parquet(path, columns=["time"]).height)
            except Exception:
                return 0

    def stats(self, symbol: str, tf: str) -> dict:
        """Stats du cache Parquet pour (symbol, tf)."""
        path = self._path(symbol, tf)
        df   = self._load(path)
        if len(df) == 0:
            return {"symbol": symbol, "tf": tf, "bars": 0,
                    "from": None, "to": None, "size_kb": 0}
        from app.core import ohlcv_absents as _abs
        from app.core.ohlcv_gaps import (
            calendar_for_symbol,
            completeness_from_gaps,
            detect_ohlcv_gaps,
        )
        gaps = detect_ohlcv_gaps(df, tf, calendar=calendar_for_symbol(symbol),
                                 absents=_abs.charger(path, symbol, tf))
        return {
            "symbol":  symbol,
            "tf":      tf,
            "bars":    len(df),
            "from":    str(df["time"].min()),
            "to":      str(df["time"].max()),
            "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
            "gaps":    len(gaps),
            "completeness": completeness_from_gaps(len(df), gaps),
        }

    def all_stats(self) -> list:
        """Stats de tous les fichiers Parquet OHLCV dans base_dir.

        Ignore les artefacts non-timeframe (ex. ``BTCUSDT__funding.parquet``
        des dérivés) pour ne pas lever ``ValueError`` sur un TF hors whitelist.

        Compte les bougies en parallèle + cache mtime (sinon /data/status
        dépasse 30 s sous Docker/Windows → UI « cache vide » par timeout).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not hasattr(self, "_all_stats_cache"):
            self._all_stats_cache: Dict[str, tuple] = {}  # path -> (mtime, row)

        # Un niveau seulement : ``<base>/<SYMBOL>/<tf>.parquet``. Un rglob
        # ramassait aussi les copies imbriquées (ex. ``ohlcv/data/BTC_USDC/``)
        # → doublons (symbole, tf) → clés React identiques sur /data.
        paths: list = []
        if self._base.is_dir():
            for symbol_dir in sorted(p for p in self._base.iterdir() if p.is_dir()):
                for parquet in sorted(symbol_dir.glob("*.parquet")):
                    tf = parquet.stem
                    if tf not in TF_SECONDS:
                        continue
                    symbol = symbol_dir.name.replace("_", "/", 1)
                    paths.append((parquet, symbol, tf))

        def _one(item):
            parquet, symbol, tf = item
            try:
                st = parquet.stat()
                mtime = st.st_mtime
                key = str(parquet)
                hit = self._all_stats_cache.get(key)
                if hit and hit[0] == mtime:
                    return hit[1]
                bars = self.count_bars(symbol, tf)
                row = {
                    "symbol": symbol,
                    "tf": tf,
                    "bars": bars,
                    "from": None,
                    "to": None,
                    "size_kb": round(st.st_size / 1024, 1),
                    "completeness": None,
                    "gaps": 0,
                }
                sidecar = parquet.with_suffix(".gaps.json")
                if sidecar.exists():
                    try:
                        import json as _json
                        info = _json.loads(sidecar.read_text(encoding="utf-8"))
                        row["gaps"] = int(info.get("gaps") or 0)
                        row["completeness"] = info.get("completeness")
                    except Exception:
                        pass
                self._all_stats_cache[key] = (mtime, row)
                return row
            except Exception:
                return None

        results = []
        # I/O bound (bind-mount Windows) : le parallélisme gagne ~3-5×.
        workers = min(16, max(4, (len(paths) // 32) or 4))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_one, p) for p in paths]
            for fut in as_completed(futs):
                row = fut.result()
                if row:
                    results.append(row)
        results.sort(key=lambda r: (r["symbol"], r["tf"]))
        return results

    # ── Mémo « plus d'historique disponible » ─────────────────────────────────

    def _path(self, symbol: str, tf: str) -> Path:
        """Chemin Parquet (symbol, tf) — refuse path-traversal et TF hors whitelist (SEC-002)."""
        if tf not in TF_SECONDS:
            raise ValueError(
                f"Timeframe invalide : {tf!r}. "
                f"Autorisés : {', '.join(sorted(TF_SECONDS))}"
            )
        if not symbol or not _SYMBOL_RE.fullmatch(symbol) or ".." in symbol:
            raise ValueError(f"Symbole invalide : {symbol!r}")
        safe = symbol.replace("/", "_").replace(":", "_")
        base = self._base.resolve()
        path = (base / safe / f"{tf}.parquet").resolve()
        if not path.is_relative_to(base):
            raise ValueError(f"Symbole invalide : {symbol!r}")
        return path

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
        return pl.DataFrame(schema=_OHLCV_SCHEMA)  # type: ignore[arg-type]

    def _save(self, path: Path, df: pl.DataFrame, *, log_gaps: bool = True) -> None:
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
            self._warn_write_gaps(path, df, log=log_gaps)
        except Exception as e:
            logger.error(f"[CandleStore] Erreur écriture {path} : {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _gaps_incrementaux(self, path: Path, df, tf: str, cal, absents) -> list:
        """Trous du fichier, en ne rescannant que ce qui a été ajouté.

        Le rapport était recalculé sur TOUT l'historique à chaque sauvegarde,
        alors qu'une sauvegarde ajoute des barres à la fin. Mesuré : 90 % du
        coût de `_save` (509 ms de détection contre 59 ms d'écriture sur sept
        fichiers), et le pire cas est l'actions en journalier — 21 µs/barre
        contre 1 µs en crypto, la marche de séances coûtant vingt fois plus.

        Un ajout ne peut créer de trou qu'à la jonction : on rescanne à partir
        de l'avant-dernière barre connue. Le préfixe est vérifié inchangé —
        `_backfill_gaps` insère au milieu, et retombe alors sur un scan complet.
        """
        from app.core.ohlcv_gaps import detect_ohlcv_gaps
        n = len(df)
        cle = str(path)
        with self._gaps_cache_lock:
            memo = self._gaps_cache.get(cle)
        if memo is not None:
            n_prec, ts_prec, gaps_prec = memo
            if 2 <= n_prec <= n:
                try:
                    inchange = int(df["time"].dt.epoch("ms")[n_prec - 1]) == ts_prec
                except Exception:
                    inchange = False
                if inchange:
                    if n == n_prec:
                        return list(gaps_prec)
                    queue = detect_ohlcv_gaps(df[n_prec - 1:], tf, calendar=cal,
                                              absents=absents)
                    gaps = list(gaps_prec) + [
                        {**g, "index": int(g["index"]) + n_prec - 1} for g in queue
                    ]
                    self._memoriser_gaps(cle, n, df, gaps)
                    return gaps
        gaps = detect_ohlcv_gaps(df, tf, calendar=cal, absents=absents)
        self._memoriser_gaps(cle, n, df, gaps)
        return gaps

    def _memoriser_gaps(self, cle: str, n: int, df, gaps: list) -> None:
        try:
            dernier = int(df["time"].dt.epoch("ms")[n - 1]) if n else 0
        except Exception:
            return
        with self._gaps_cache_lock:
            self._gaps_cache[cle] = (n, dernier, list(gaps))

    def _warn_write_gaps(self, path: Path, df: pl.DataFrame, *,
                         log: bool = True) -> None:
        """D-03 : sidecar + WARNING des trous non calendaires à l'écriture.

        Après un rattrapage, le reliquat est mémoïsé 6 h : on passe en
        DEBUG pour ne pas répéter le même WARNING à chaque barre incrémentale.
        """
        try:
            from app.core import ohlcv_absents as _abs
            from app.core.ohlcv_gaps import (
                calendar_for_symbol,
                completeness_from_gaps,
            )
            tf = path.stem
            symbol = path.parent.name.replace("_", "/", 1)
            gaps = self._gaps_incrementaux(
                path, df, tf, calendar_for_symbol(symbol),
                _abs.charger(path, symbol, tf))
            missing = sum(int(g.get("gap_bars") or 0) for g in gaps)
            comp = completeness_from_gaps(len(df), gaps)
            try:
                import json as _json
                path.with_suffix(".gaps.json").write_text(
                    _json.dumps({"gaps": len(gaps), "missing_bars": missing,
                                 "completeness": comp}),
                    encoding="utf-8",
                )
            except OSError:
                pass
            if not gaps:
                return
            if (not log) or self._gaps_on_cooldown(symbol, tf, missing):
                logger.debug(
                    "[CandleStore] %s/%s : %d trou(s) non calendaire(s), "
                    "%d barre(s) manquante(s), complétude=%.1f%%",
                    symbol, tf, len(gaps), missing, comp * 100,
                )
                return
            logger.warning(
                "[CandleStore] %s/%s : %d trou(s) non calendaire(s), "
                "%d barre(s) manquante(s), complétude=%.1f%%",
                symbol, tf, len(gaps), missing, comp * 100,
            )
        except Exception as e:
            logger.debug("[CandleStore] detect_ohlcv_gaps KO : %s", e)

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
