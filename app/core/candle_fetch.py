"""Stratégies de récupération du CandleStore (DETTE-04).

Cinq chemins distincts vers l'exchange — incrémental, historique, complet,
plage de trou, tranche bornée — plus le recousage des trous détectés. Extraits
de `candle_store.py` : c'est la moitié du fichier, et la partie qui parle à
l'extérieur.
"""

import logging
import time
from pathlib import Path
from typing import Any, List, Optional

import polars as pl

from app.core.candle_helpers import (
    _GAP_FILL_PAGE,
    _MAX_ABSENT_GAP_BARS,
    _MAX_GAP_SPAN_PAGES,
    _NO_HISTORY_RETRY_S,
    _bars_span_ms,
    _fmt_ms,
    _min_since,
    _provider_for,
    _valid_bars,
    drop_forming_candle,
    epoch_ms,
)
from app.core.timeframes import TF_MS

logger = logging.getLogger(__name__)


class CandleFetchHost:
    """Contrat attendu de `CandleStore` (annotations, cf. `CandleMemosHost`)."""

    _deep_fetches: int
    _raw_to_df: Any
    _save: Any
    _gaps_on_cooldown: Any
    _mark_gaps_unfillable: Any
    _forget_gaps_unfillable: Any


class CandleFetchMixin(CandleFetchHost):
    def _fill_detected_gaps(self, exchange, symbol: str, tf: str,
                            df: pl.DataFrame, path: Path) -> pl.DataFrame:
        """Recoud les trous : un rattrapage de plage, puis mémo 6 h.

        Pagination unique de la première à la dernière discontinuité — pas
        8 trous × 6 pages, qui rejouaient la même fenêtre, fragmentaient
        les gros trous et spammaient le journal. Reliquat = barres que
        l'exchange ne publie pas (maintenance) : un essai, puis 6 h.
        """
        from app.core import ohlcv_absents as _abs
        from app.core.ohlcv_gaps import (
            calendar_for_symbol,
            completeness_from_gaps,
            creneaux_manquants,
            detect_ohlcv_gaps,
        )
        tf_ms = TF_MS.get(tf)
        if not tf_ms:
            return df
        _cal = calendar_for_symbol(symbol)
        absents = _abs.charger(path, symbol, tf)
        try:
            gaps = detect_ohlcv_gaps(df, tf, calendar=calendar_for_symbol(symbol),
                                     absents=absents)
        except Exception as e:
            logger.debug("[CandleStore] detect_ohlcv_gaps %s/%s KO : %s",
                         symbol, tf, e)
            return df
        if not gaps:
            self._forget_gaps_unfillable(symbol, tf)
            return df
        missing = sum(int(g.get("gap_bars") or 0) for g in gaps)
        if self._gaps_on_cooldown(symbol, tf, missing):
            logger.debug(
                "[CandleStore] %s/%s — recousage des trous ignoré "
                "(%d barre(s) manquante(s), mémo 6 h)",
                symbol, tf, missing,
            )
            return df

        indices = [int(g.get("index") or 0) for g in gaps
                   if 1 <= int(g.get("index") or 0) < len(df)]
        if not indices:
            self._mark_gaps_unfillable(symbol, tf, missing)
            return df
        start_ms = (epoch_ms(df["time"][min(indices) - 1]) or 0) + tf_ms
        end_ms = epoch_ms(df["time"][max(indices)]) or 0
        if start_ms <= 0 or end_ms <= start_ms:
            self._mark_gaps_unfillable(symbol, tf, missing)
            return df

        logger.info(
            "[CandleStore] %s/%s — rattrapage exchange %s → %s "
            "(%d trou(s), %d barre(s) manquante(s))",
            symbol, tf, _fmt_ms(start_ms), _fmt_ms(end_ms),
            len(gaps), missing,
        )
        known = set(df["time"].dt.epoch("ms").to_list()) if len(df) else set()
        n_before = len(df)
        # On ne confirme l'absence que des MICRO-trous. Mesuré sur le parc :
        # côté actions 15 m aucun trou ne dépasse 4 barres (329 trous), et le
        # seul gros trou crypto fait 3 939 barres — de l'historique manquant,
        # que l'exchange détient peut-être. Entre 4 et 3 939, rien : la borne
        # sépare deux populations réelles, elle n'arbitre pas un continuum.
        vises = []
        for g in gaps:
            j = int(g.get("index") or 0)
            if int(g.get("gap_bars") or 0) > _MAX_ABSENT_GAP_BARS:
                continue
            if 1 <= j < len(df):
                vises += creneaux_manquants(df["time"][j - 1], df["time"][j],
                                            tf_ms / 1000.0, _cal)
        raw, pages, reached, couvert = self._fetch_span(
            exchange, symbol, tf, start_ms, end_ms, known)

        if raw:
            df = _valid_bars(
                pl.concat([df, self._raw_to_df(raw)])
                .unique("time", keep="last").sort("time"),
                exchange, symbol,
            )
            df = drop_forming_candle(df, tf)
            if len(df) > n_before:
                self._save(path, df, log_gaps=False)

        filled = len(df) - n_before
        # La source a répondu sur `couvert` : tout créneau visé qui s'y trouve
        # et manque encore n'existe pas. C'est sa réponse, pas une supposition.
        n_confirmes = 0
        if couvert:
            deb, fin = couvert
            presents = set(df["time"].dt.epoch("ms").to_list()) if len(df) else set()
            # La dernière barre du span peut être en formation : on ne conclut pas.
            confirmes = [t for t in vises
                         if deb <= t < fin and t not in presents]
            n_confirmes = _abs.ajouter(path, symbol, tf, confirmes)
            if n_confirmes:
                absents = _abs.charger(path, symbol, tf)
                logger.info(
                    "[CandleStore] %s/%s — %d créneau(x) confirmé(s) absent(s) "
                    "à la source (marché ouvert, aucune transaction) : ils ne "
                    "seront plus redemandés ni comptés comme manquants",
                    symbol, tf, n_confirmes,
                )
        left, left_missing, left_comp = [], 0, 100.0
        try:
            left = detect_ohlcv_gaps(
                df, tf, calendar=calendar_for_symbol(symbol), absents=absents)
            left_missing = sum(int(g.get("gap_bars") or 0) for g in left)
            left_comp = completeness_from_gaps(len(df), left) * 100
        except Exception:
            pass

        if not left:
            self._forget_gaps_unfillable(symbol, tf)
            logger.info(
                "[CandleStore] %s/%s — série continue (complétude 100%%, "
                "+%d bougie(s), %d page(s))",
                symbol, tf, filled, pages,
            )
            return df

        if reached or filled <= 0:
            self._mark_gaps_unfillable(symbol, tf, left_missing)
            logger.info(
                "[CandleStore] %s/%s — rattrapage terminé : +%d bougie(s) "
                "(%d page(s)) · reste %d trou(s) / %d barre(s) "
                "(complétude=%.1f%%) que l'exchange ne publie pas — "
                "nouvel essai dans %.0f h",
                symbol, tf, filled, pages, len(left), left_missing,
                left_comp, _NO_HISTORY_RETRY_S / 3600.0,
            )
        else:
            logger.info(
                "[CandleStore] %s/%s — rattrapage partiel : +%d bougie(s) "
                "(%d page(s)) · reste %d trou(s) / %d barre(s) "
                "(complétude=%.1f%%) — reprise au prochain cycle",
                symbol, tf, filled, pages, len(left), left_missing, left_comp,
            )
        return df

    def _fetch_span(self, exchange, symbol: str, tf: str,
                    start_ms: int, end_ms: int, known_ts: set) -> tuple:
        """Dump exchange sur ``[start_ms, end_ms)``.

        Prefère ``fetch_ohlcv_max`` (une requête, réponse définitive). Sinon
        pagination ccxt jusqu'à ``end_ms``. Retourne
        ``(rows, pages, reached_end, couvert)`` où ``couvert`` est l'intervalle
        ``(min_ms, max_ms)`` que la réponse couvre RÉELLEMENT — ``None`` si
        rien. Hors de cet intervalle la source ne dit pas « ça n'existe pas »,
        elle ne dit rien : on n'y confirme aucune absence.
        """
        couvert: tuple[int, int] | None
        deep = getattr(_provider_for(exchange, symbol), "fetch_ohlcv_max", None)
        if callable(deep):
            try:
                rows = deep(symbol, tf) or []
            except Exception as e:
                logger.warning("[CandleStore] fetch_max %s/%s : %s",
                               symbol, tf, e)
                rows = []
            if rows:
                tous = [r[0] for r in rows]
                couvert = (min(tous), max(tous))
                fresh = []
                for r in rows:
                    ts = r[0]
                    if ts < start_ms or ts >= end_ms or ts in known_ts:
                        continue
                    known_ts.add(ts)
                    fresh.append(r)
                return fresh, 1, True, couvert
        rows, pages, reached = self._fetch_gap_range(
            exchange, symbol, tf, start_ms, end_ms, known_ts,
            pages_left=_MAX_GAP_SPAN_PAGES)
        # Pagination : la source a été interrogée sur tout [start, end) si elle
        # est allée au bout. Sinon on ne couvre que jusqu'à la dernière barre vue.
        if reached:
            couvert = (start_ms, end_ms)
        elif rows:
            couvert = (start_ms, max(r[0] for r in rows))
        else:
            couvert = None
        return rows, pages, reached, couvert

    def _fetch_gap_range(self, exchange, symbol: str, tf: str,
                         start_ms: int, end_ms: int, known_ts: set,
                         pages_left: int) -> tuple:
        """Fetch paginé d'une plage ``[start_ms, end_ms)``.

        Retourne ``(rows, pages, reached_end)``. ``reached_end`` est True si
        la source est épuisée ou si on a dépassé ``end_ms`` — pas si on a
        seulement atteint le plafond de pages.
        """
        if pages_left <= 0:
            return [], 0, False
        rate_sleep = getattr(exchange, "rateLimit", 1200) / 1000
        rows: list = []
        since = start_ms
        pages = 0
        reached = False
        while pages < pages_left and since < end_ms:
            try:
                batch = exchange.fetch_ohlcv(
                    symbol, tf, since=since, limit=_GAP_FILL_PAGE)
            except Exception as e:
                logger.warning(
                    "[CandleStore] fetch_span %s/%s since=%s : %s",
                    symbol, tf, since, e,
                )
                break
            pages += 1
            if not batch:
                reached = True
                break
            last_ts = since
            for c in batch:
                ts = c[0]
                last_ts = ts
                if ts < start_ms or ts >= end_ms:
                    continue
                if ts in known_ts:
                    continue
                known_ts.add(ts)
                rows.append(c)
            nxt = last_ts + 1
            if nxt <= since:
                reached = True
                break
            since = nxt
            if last_ts >= end_ms - 1:
                reached = True
                break
            if len(batch) < _GAP_FILL_PAGE:
                reached = True
                break
            if rate_sleep:
                time.sleep(rate_sleep)
        if since >= end_ms:
            reached = True
        return rows, pages, reached

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

