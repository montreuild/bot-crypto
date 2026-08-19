"""
CandleStore — stockage Parquet persistant des bougies OHLCV par (symbol, timeframe).
Fetch incrémental, thread-safe, retourne un pl.DataFrame identique à MarketScanner.
"""

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

from app.core.config import DATA_ROOT
from app.core.singleton import lazy_singleton
from app.core.timeframes import TF_MS, TF_SECONDS

OHLCV_DIR = os.path.join(DATA_ROOT, "ohlcv")

logger = logging.getLogger(__name__)

# SEC-002 — mêmes contraintes que app/api/schemas.py (symbole + timeframe whitelist).
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:\-]{0,31}$")

# Plancher de `since` pour les fetch paginés : ~fondation d'OKX (2017-01-01).
# Évite de calculer un `since` absurde (ex. 50000 × 4h ≈ année 2003) que l'exchange
# REJETTE en renvoyant une liste vide → « premier fetch : 0 bougie » sur les gros
# timeframes. OKX renvoie les plus anciennes bougies DISPONIBLES pour un `since`
# antérieur au listing, mais pas pour un `since` d'avant sa création.
#
# G2 : c'est une contrainte d'exchange crypto, pas une vérité de marché — une
# action cote souvent depuis les années 1990. Un provider peut donc l'abaisser
# via l'attribut `min_since_ms` (cf. YFinanceProvider).
_MIN_SINCE_MS = 1_483_228_800_000  # 2017-01-01 UTC


def epoch_ms(value) -> Optional[int]:
    """Datetime **naïf** du schéma OHLCV → epoch ms, lu en UTC.

    `datetime.timestamp()` interprète un datetime naïf en heure LOCALE. Or la
    colonne `time` est un `Datetime("ms")` naïf qui porte de l'UTC (cf.
    `_raw_to_df`, `pl.from_epoch`). Sur une machine à UTC+1, la conversion
    retirait donc une heure aux bornes du cache, et ce décalage n'était pas
    inoffensif : `before_ms` (borne basse) partait une heure trop tôt, si bien
    que le backfill historique s'arrêtait AVANT les bougies qui touchent le
    cache. Il restait un TROU permanent d'une heure à la jonction, que rien ne
    venait jamais combler — invisible en UTC, systématique à Paris.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return None


def _provider_for(exchange, symbol: str):
    """Provider RÉELLEMENT interrogé pour ce symbole.

    `exchange` peut être un `ProviderRouter` (cf. app/core/provider_router.py) :
    il route `fetch_ohlcv` par symbole, mais son `__getattr__` renvoie tout le
    reste à l'exchange par défaut — l'exchange crypto. Interroger le routeur
    pour `min_since_ms`, `bars_span_ms`, `drop_zero_volume` ou
    `fetch_ohlcv_max` répondait donc systématiquement « ccxt » : le contrat que
    YFinanceProvider expose au store était invisible, et les actions étaient
    servies avec les hypothèses de la crypto (plancher 2017, temps calendaire
    continu, barres à volume nul rejetées, aucun amorçage profond).
    """
    resolve = getattr(exchange, "provider_for", None)
    if callable(resolve):
        try:
            return resolve(symbol)
        except Exception as e:
            logger.debug(f"[CandleStore] provider_for('{symbol}') KO : {e}")
    return exchange


def _min_since(exchange, symbol: str) -> int:
    """Plancher de `since` applicable au provider (défaut : fondation d'OKX)."""
    return int(getattr(_provider_for(exchange, symbol),
                       "min_since_ms", _MIN_SINCE_MS))


def _bars_span_ms(exchange, symbol: str, tf: str, count: int, tf_ms: int) -> int:
    """Temps calendaire à remonter pour espérer `count` bougies de `tf`.

    En crypto c'est `count × durée` : le marché ne ferme jamais. Sur une place
    à séances, non — une bougie de 15 m consomme ~1 h de calendrier une fois
    les nuits, week-ends et fériés déduits, et reculer de `count × 15 m` ne
    ramène qu'un quart des bougies demandées. Le cache reste alors sous le
    compte visé et le store redemande à chaque cycle, indéfiniment.

    Le provider tranche via `bars_span_ms` (cf. YFinanceProvider) ; sans elle,
    le comportement crypto historique est conservé à l'identique.
    """
    fn = getattr(_provider_for(exchange, symbol), "bars_span_ms", None)
    if callable(fn):
        try:
            return int(fn(tf, count))
        except Exception as e:
            logger.debug(f"[CandleStore] bars_span_ms {tf} KO : {e} — défaut calendaire")
    return count * tf_ms


#: Délai avant de retenter un historique que le provider a déclaré épuisé.
#: Sans mémo, les 98 titres × 5 TF du SBF 120 rejouaient la même requête
#: perdante à chaque cycle de scan — la boucle visible dans les logs.
_NO_HISTORY_RETRY_S = 6 * 3600.0


def _valid_bars(df: pl.DataFrame, exchange, symbol: str) -> pl.DataFrame:
    """Écarte les barres inexploitables, selon ce que le provider garantit.

    En crypto, une bougie à volume nul signale des données cassées. Sur
    actions, elle est parfaitement normale (valeur peu liquide, séance sans
    échange) : la rejeter trouerait l'historique. Le provider tranche via
    `drop_zero_volume` (défaut True = comportement crypto historique).
    """
    valid = pl.col("close") > 0
    if bool(getattr(_provider_for(exchange, symbol), "drop_zero_volume", True)):
        valid = valid & (pl.col("volume") > 0)
    return df.filter(valid).drop_nulls()


def drop_forming_candle(df: pl.DataFrame, tf: str,
                        now_ms: Optional[int] = None) -> pl.DataFrame:
    """D-01 : retire la dernière barre si elle n'est pas encore close.

    Une ouverture ``t`` couvre ``[t, t+Δ)``. Persistée, son close provisoire
    empoisonne les backtests suivants. No-op si TF inconnu ou s'il ne resterait
    plus aucune barre.
    """
    tf_ms = TF_MS.get(tf)
    if not tf_ms or df is None or df.height <= 1:
        return df
    try:
        last_ms = epoch_ms(df["time"][-1])
        if last_ms is None:
            return df
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        # Forming = now est DANS [t, t+Δ). Une barre future (fixtures de
        # test, horloge en retard) ou déjà close n'est pas élaguée.
        if last_ms <= now < last_ms + tf_ms:
            return df.head(df.height - 1)
    except Exception as e:
        logger.debug(f"[CandleStore] élagage bougie en formation {tf} KO : {e}")
    return df

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
        #: `(symbol, tf) → (plus vieille bougie connue, instant de réessai)`.
        #: Mémorise qu'un backfill historique n'a RIEN ramené, pour ne pas le
        #: rejouer à chaque cycle. Invalidé dès que la borne basse du cache
        #: bouge — si de l'historique arrive par une autre voie, on retente.
        self._no_history: Dict[tuple, tuple] = {}
        self._no_history_lock = threading.Lock()
        #: Nombre d'amorçages profonds réussis — sert à savoir si le
        #: premier fetch a déjà ramené toute la profondeur disponible.
        self._deep_fetches = 0
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
        from app.core.ohlcv_gaps import (
            calendar_for_symbol,
            completeness_from_gaps,
            detect_ohlcv_gaps,
        )
        gaps = detect_ohlcv_gaps(df, tf, calendar=calendar_for_symbol(symbol))
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

        paths: list = []
        for parquet in sorted(self._base.rglob("*.parquet")):
            tf = parquet.stem
            if tf not in TF_SECONDS:
                continue
            symbol = parquet.parent.name.replace("_", "/", 1)
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

    def _history_exhausted(self, symbol: str, tf: str,
                           first_ms: Optional[int]) -> bool:
        with self._no_history_lock:
            entry = self._no_history.get((symbol, tf))
        if entry is None:
            return False
        marked_first, retry_at = entry
        if time.time() >= retry_at:
            return False        # le provider a peut-être publié de l'historique
        # La borne basse a bougé : de l'historique est arrivé par une autre
        # voie (backfill hors ligne, autre timeframe). Le mémo ne vaut plus.
        return marked_first == first_ms

    def _mark_exhausted(self, symbol: str, tf: str,
                        first_ms: Optional[int]) -> None:
        with self._no_history_lock:
            self._no_history[(symbol, tf)] = (first_ms,
                                              time.time() + _NO_HISTORY_RETRY_S)

    def _forget_exhausted(self, symbol: str, tf: str) -> None:
        with self._no_history_lock:
            self._no_history.pop((symbol, tf), None)

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
                # Une TypeError/AttributeError ici n'est PAS un incident réseau :
                # c'est un défaut de code dans la pile de fetch, et le message
                # seul ("unsupported operand type(s) for +") ne dit pas où. On
                # journalise la trace pour ces cas, sans noyer les logs sous les
                # timeouts et rate-limits, qui sont attendus et fréquents.
                logger.warning(f"[CandleStore] fetch_incr {symbol}/{tf} : {e}",
                               exc_info=isinstance(e, (TypeError, AttributeError,
                                                       KeyError, ValueError)))
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
                          before_ms: Optional[int], needed: int,
                          known_ts: Optional[set] = None) -> List[list]:
        """Fetch des bougies manquantes pour compléter le cache.

        ``known_ts`` : horodatages DÉJÀ en cache. Sans eux, seul le critère
        « antérieur à ``before_ms`` » distingue le neuf du connu — et il laisse
        échapper les **trous intérieurs**, ces barres absentes du cache alors
        qu'elles tombent dans sa plage. Le chemin profond ci-dessous les avait
        pourtant sous la main et les jetait : sur `AC.PA/4h`, la source publiait
        1522 barres, le cache en stockait 1442, et l'écart de 80 ne se comblait
        jamais — aucun autre chemin ne le pouvait, le fetch incrémental ne
        regardant qu'après la dernière barre connue.
        """
        LIMIT      = 1000
        rate_sleep = getattr(exchange, "rateLimit", 1200) / 1000

        # Même raisonnement qu'à l'amorçage : quand le provider sait rendre
        # toute sa profondeur en une requête, la question « as-tu quelque chose
        # avant telle date ? » reçoit une réponse DÉFINITIVE, au lieu d'une
        # réponse relative à la fenêtre qu'on a devinée. Un cache déjà amorcé —
        # par le script de backfill, par une version antérieure du bot — passe
        # aussi par ici, et c'est le seul moyen qu'il rattrape sa profondeur.
        deep = getattr(_provider_for(exchange, symbol), "fetch_ohlcv_max", None)
        if callable(deep):
            try:
                rows = deep(symbol, tf) or []
            except Exception as e:
                logger.warning(f"[CandleStore] fetch_max {symbol}/{tf} : {e}")
                rows = []
            known = known_ts or set()
            older = [r for r in rows
                     if before_ms is not None and r[0] < before_ms]
            gaps = [r for r in rows
                    if (before_ms is None or r[0] >= before_ms)
                    and r[0] not in known]
            fresh = older + gaps
            if rows:
                logger.info(
                    f"[CandleStore] {symbol}/{tf} — profondeur maximale de la "
                    f"source : {len(rows)} bougies ; {len(older)} antérieures au "
                    f"cache, {len(gaps)} comblant des trous intérieurs"
                )
            if fresh:
                fresh.sort(key=lambda x: x[0])
                return fresh
            if rows:
                # Le provider a répondu, et il n'a rien que le cache ignore.
                # C'est une réponse, pas un échec : inutile de retenter en borné.
                return []

        try:
            tf_ms = exchange.parse_timeframe(tf) * 1000
        except Exception as e:
            logger.warning(f"[CandleStore] parse_timeframe '{tf}' KO : {e} — fallback 1h")
            tf_ms = 3_600_000  # fallback 1h en ms

        # Point de départ : assez loin dans le passé pour couvrir les bougies manquantes.
        # On clampe à _MIN_SINCE_MS (2017-01-01) : un `since` trop ancien (ex. 1d × 20000
        # ⇒ avant le listing) fait rejeter la requête OHLCV par l'exchange (liste vide).
        floor_ms = _min_since(exchange, symbol)
        span_ms  = _bars_span_ms(exchange, symbol, tf, needed, tf_ms)
        if before_ms is not None:
            since = max(floor_ms, before_ms - span_ms)
        else:
            since = max(floor_ms, int(exchange.milliseconds()) - span_ms)

        all_raw: list = []
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

        # Amorçage : si le provider sait rendre TOUT son historique en une
        # requête, c'est la bonne réponse. Deviner une fenêtre à partir d'un
        # nombre de bougies reste une estimation (heures de séance, fériés,
        # date d'introduction) ; là, c'est la source qui décide de sa
        # profondeur. Une seule requête, et le cache part complet — donc plus
        # de backfill perdant au cycle suivant. Absent en ccxt : la crypto
        # garde exactement le chemin paginé ci-dessous.
        deep = getattr(_provider_for(exchange, symbol), "fetch_ohlcv_max", None)
        if callable(deep):
            try:
                rows = deep(symbol, tf) or []
            except Exception as e:
                logger.warning(f"[CandleStore] fetch_max {symbol}/{tf} : {e}",
                               exc_info=isinstance(e, (TypeError, AttributeError,
                                                       KeyError, ValueError)))
                rows = []
            if rows:
                self._deep_fetches += 1
                logger.info(f"[CandleStore] {symbol}/{tf} — amorçage profond : "
                            f"{len(rows)} bougies (tout l'historique disponible)")
                return rows
            # Liste vide : quota, réseau ou timeframe non supporté. On retombe
            # sur le chemin borné plutôt que de renvoyer un cache vide.

        if total <= LIMIT:
            try:
                return exchange.fetch_ohlcv(symbol, tf, limit=total) or []
            except Exception as e:
                # Même parti pris qu'en incrémental : trace pour les défauts de
                # code, message seul pour les aléas réseau.
                logger.warning(f"[CandleStore] fetch_full {symbol}/{tf} : {e}",
                               exc_info=isinstance(e, (TypeError, AttributeError,
                                                       KeyError, ValueError)))
                return []

        try:
            tf_ms = exchange.parse_timeframe(tf) * 1000
        except Exception as e:
            logger.warning(f"[CandleStore] parse_timeframe '{tf}' KO : {e} — fallback 1h")
            tf_ms = 3_600_000  # fallback 1h en ms

        since   = max(_min_since(exchange, symbol),
                      exchange.milliseconds()
                      - _bars_span_ms(exchange, symbol, tf, total, tf_ms))
        all_raw: list = []
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
            self._warn_write_gaps(path, df)
        except Exception as e:
            logger.error(f"[CandleStore] Erreur écriture {path} : {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _warn_write_gaps(self, path: Path, df: pl.DataFrame) -> None:
        """D-03 : journalise les trous non calendaires à l'écriture."""
        try:
            from app.core.ohlcv_gaps import (
                calendar_for_symbol,
                completeness_from_gaps,
                detect_ohlcv_gaps,
            )
            tf = path.stem
            symbol = path.parent.name.replace("_", "/", 1)
            gaps = detect_ohlcv_gaps(df, tf, calendar=calendar_for_symbol(symbol))
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
