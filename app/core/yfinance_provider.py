"""Provider de données actions (G2) — Yahoo Finance, **data-only**.

Implémente le protocole ``MarketDataProvider`` (``app/core/providers.py``) et
la poignée d'attributs que ``CandleStore`` attend d'un exchange ccxt
(``parse_timeframe``, ``milliseconds``, ``rateLimit``) : c'est un branchement,
pas une réécriture — le store Parquet, le cache OHLCV, le scanner et les
stratégies fonctionnent sans le savoir.

Deux chemins d'accès, dans cet ordre :

1. le paquet ``yfinance`` s'il est installé (gère cookie/crumb, ce que
   l'endpoint brut exige de plus en plus souvent) ;
2. sinon l'API chart publique via ``requests`` (déjà une dépendance) — le bot
   n'ajoute donc **aucune dépendance obligatoire**. ``yfinance`` réinstallerait
   pandas, supprimé du projet en phase 6 : c'est un choix laissé à
   l'utilisateur, et la frontière pandas ne dépasse pas ``_fetch_via_yfinance``.

Limitations de l'API Yahoo, prises en charge explicitement
----------------------------------------------------------
* **Profondeur bornée par granularité** : 1 m → 7 jours, 2 m/5 m/15 m/30 m/90 m
  → 60 jours, 1 h → 730 jours, journalier et au-delà → illimité. Une demande
  plus profonde est **tronquée** (avec un avertissement émis une seule fois par
  couple symbole/TF) au lieu de revenir vide — c'est ce que fait Yahoo, autant
  le rendre lisible. Conséquence directe : un backtest 15 m sur actions ne peut
  pas dépasser ~60 jours d'historique.
* **Intervalles inexistants** : 3 m, 2 h et 4 h ne sont pas cotés par Yahoo. Ils
  sont ré-agrégés côté client depuis l'intervalle de base (1 m ou 1 h), ancrés
  sur l'epoch pour rester alignés entre deux appels.
* **Quotas** : pas de quota documenté, mais un throttling agressif (HTTP 429).
  Un intervalle minimum entre requêtes est imposé **au niveau du processus**
  (les threads du scanner ne peuvent donc pas rafaler), avec retry à backoff
  exponentiel et cache TTL court pour absorber les appels répétés d'un cycle.
* **Barres à volume nul** : légitimes sur actions peu liquides (contrairement à
  la crypto, où c'est un signe de données cassées). Le provider le signale au
  ``CandleStore`` via ``drop_zero_volume = False``.
* **Bougie en formation** : renvoyée par Yahoo comme en crypto — c'est
  ``OHLCVCache._drop_forming_candle`` qui l'élague, rien à faire ici.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:                        # pragma: no cover
    _requests = None
    _HAS_REQUESTS = False

try:
    import yfinance as _yf  # type: ignore[import-not-found]
    _HAS_YFINANCE = True
except Exception:                          # pragma: no cover — dépendance optionnelle
    _yf = None
    _HAS_YFINANCE = False

#: Disjoncteur de quota, PARTAGÉ PAR PROCESSUS — les threads du scanner
#: interrogent Yahoo sous le même quota, donc doivent s'arrêter ensemble.
_RATE_LOCK = threading.Lock()
_RATE_STATE: Dict[str, float] = {"consecutive": 0.0, "until": 0.0}
_RATE_TRIP_AFTER = 5        # 429 consécutifs avant ouverture
_RATE_COOLDOWN_S = 900.0    # 15 min — Yahoo ne desserre pas plus vite

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Durée d'un timeframe en secondes — source unique du projet.
from app.core.timeframes import TF_MS as _TF_MS  # noqa: E402


class ExecutionNotSupported(RuntimeError):
    """Levée si l'on tente de passer un ordre sur un provider data-only."""


# ── Table des intervalles Yahoo ─────────────────────────────────────────────
#
# tf projet → (intervalle Yahoo, profondeur maximale en jours, facteur
# d'agrégation client). ``max_days = 0`` signifie « pas de limite ».
_YahooSpec = Tuple[str, int, int]

_INTERVALS: Dict[str, _YahooSpec] = {
    "1m":  ("1m",  7,   1),
    "2m":  ("2m",  60,  1),
    "3m":  ("1m",  7,   3),    # inexistant chez Yahoo → agrégé depuis 1 m
    "5m":  ("5m",  60,  1),
    "15m": ("15m", 60,  1),
    "30m": ("30m", 60,  1),
    "1h":  ("1h",  730, 1),
    "2h":  ("1h",  730, 2),    # inexistant chez Yahoo → agrégé depuis 1 h
    "4h":  ("1h",  730, 4),    # idem
    "6h":  ("1h",  730, 6),    # idem
    "8h":  ("1h",  730, 8),    # idem
    "12h": ("1h",  730, 12),   # idem
    "1d":  ("1d",  0,   1),
}

_DAY_MS = 86_400_000


class YFinanceProvider:
    """Données de marché actions via Yahoo Finance. Aucune exécution d'ordre.

    Paramètres (``config.yaml › providers.yfinance``) :
      ``suffix``               — suffixe de place ajouté aux symboles sans point
                                 (``".PA"`` pour Euronext Paris).
      ``symbol_map``           — surcharges explicites ``{symbole: ticker}``.
      ``min_request_interval`` — secondes entre deux requêtes (défaut 1.0).
      ``cache_ttl``            — TTL du cache de réponses, secondes (défaut 60).
      ``max_retries``          — tentatives sur erreur réseau/429 (défaut 3).
      ``timeout``              — timeout HTTP, secondes (défaut 20).
    """

    #: Le CandleStore ne doit PAS filtrer les barres à volume nul (cf. module).
    drop_zero_volume = False
    #: Pas de plancher historique arbitraire : Yahoo remonte avant 2017.
    min_since_ms = 0

    def __init__(self, cfg: Optional[dict] = None, name: str = "yfinance"):
        pcfg = ((cfg or {}).get("providers") or {}).get(name) or {}
        self.id = name
        self.name = name
        self._suffix = str(pcfg.get("suffix", "") or "")
        self._symbol_map: Dict[str, str] = dict(pcfg.get("symbol_map") or {})
        self._min_interval = float(pcfg.get("min_request_interval", 1.0))
        self._cache_ttl = float(pcfg.get("cache_ttl", 60.0))
        self._max_retries = int(pcfg.get("max_retries", 3))
        self._timeout = float(pcfg.get("timeout", 20.0))
        self._prefer_yfinance = bool(pcfg.get("prefer_yfinance", True))

        # ``rateLimit`` en ms — lu par CandleStore pour espacer ses pages.
        self.rateLimit = int(self._min_interval * 1000)

        self._throttle_lock = threading.Lock()
        self._last_request_at = 0.0
        self._cache: Dict[Tuple[str, str], Tuple[float, List[list]]] = {}
        self._cache_lock = threading.Lock()
        self._truncation_warned: set = set()
        self._session = None

        backend = "yfinance" if (self._prefer_yfinance and _HAS_YFINANCE) else "API chart"
        logger.info(
            f"[{self.id}] Provider actions data-only — backend={backend}, "
            f"throttle={self._min_interval:.1f}s, cache={self._cache_ttl:.0f}s"
        )

    # ── Compatibilité ccxt attendue par CandleStore ────────────────────────

    @staticmethod
    def parse_timeframe(timeframe: str) -> int:
        """Durée d'un timeframe en **secondes** (même contrat que ccxt)."""
        ms = _TF_MS.get(timeframe)
        if not ms:
            raise ValueError(f"Timeframe non supporté : {timeframe}")
        return int(ms // 1000)

    @staticmethod
    def milliseconds() -> int:
        return int(time.time() * 1000)

    # ── Symboles ───────────────────────────────────────────────────────────

    def to_provider_symbol(self, symbol: str) -> str:
        """``BTC/USDC``-style ou ticker nu → ticker Yahoo.

        Une surcharge explicite prime ; sinon la base d'un symbole ``A/B`` est
        suffixée par la place (``AIR/EUR`` → ``AIR.PA``) ; un ticker contenant
        déjà un point est laissé intact (``AIR.PA``).
        """
        raw = (symbol or "").strip()
        if raw in self._symbol_map:
            return str(self._symbol_map[raw])
        base = raw.split("/")[0] if "/" in raw else raw
        if "." in base or not self._suffix:
            return base
        return f"{base}{self._suffix}"

    def load_markets(self) -> dict:
        """Marchés connus — dérivés des symboles déjà résolus (pas d'appel réseau).

        Yahoo n'expose pas de catalogue : l'univers d'un marché actions est un
        fichier ``data/universe/*.yaml``, pas une découverte (cf.
        ``app/core/universe.py``).
        """
        return {sym: {"symbol": sym, "id": self.to_provider_symbol(sym), "active": True}
                for sym in self._symbol_map}

    # ── Throttling & retry ─────────────────────────────────────────────────

    def _throttle(self) -> None:
        """Espace les requêtes au niveau du **processus** (threads du scanner)."""
        with self._throttle_lock:
            wait = self._min_interval - (time.time() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.time()

    # ── Fenêtre de requête ─────────────────────────────────────────────────

    def _resolve_window(self, symbol: str, timeframe: str, limit: int,
                        since: Optional[int]) -> Optional[Tuple[str, int, int, int]]:
        """(intervalle Yahoo, facteur d'agrégation, period1_s, period2_s).

        Applique la profondeur maximale autorisée pour la granularité — c'est
        ici que la limitation Yahoo devient visible et traçable plutôt que de
        se manifester par une réponse vide inexpliquée.
        """
        spec = _INTERVALS.get(timeframe)
        if spec is None:
            logger.warning(f"[{self.id}] Timeframe '{timeframe}' non supporté par Yahoo.")
            return None
        interval, max_days, factor = spec

        tf_ms = _TF_MS.get(timeframe) or 3_600_000
        now_ms = self.milliseconds()
        span_ms = max(int(limit), 1) * tf_ms
        start_ms = int(since) if since else now_ms - span_ms
        # Marge d'une bougie : Yahoo exclut parfois la borne basse.
        start_ms -= tf_ms

        if max_days > 0:
            floor_ms = now_ms - max_days * _DAY_MS
            if start_ms < floor_ms:
                key = (symbol, timeframe)
                if key not in self._truncation_warned:
                    self._truncation_warned.add(key)
                    logger.warning(
                        f"[{self.id}] {symbol}/{timeframe} — Yahoo plafonne cette "
                        f"granularité à {max_days} jours : la fenêtre demandée "
                        f"({(now_ms - start_ms) / _DAY_MS:.0f} j) est tronquée. "
                        f"Utilisez un timeframe plus large pour un historique profond."
                    )
                start_ms = floor_ms

        if start_ms >= now_ms:
            return None
        return interval, factor, int(start_ms // 1000), int(now_ms // 1000) + 60

    # ── Récupération OHLCV ─────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100,
                    since: Optional[int] = None) -> List[list]:
        """Bougies au format ccxt ``[ts_ms, open, high, low, close, volume]``.

        Toujours croissant, jamais ``None`` : un échec renvoie une liste vide,
        que ``CandleStore`` interprète comme « rien de neuf » (dégradation
        gracieuse, aucun plantage de cycle).
        """
        window = self._resolve_window(symbol, timeframe, limit, since)
        if window is None:
            return []
        interval, factor, period1, period2 = window
        ticker = self.to_provider_symbol(symbol)

        cache_key = (ticker, interval)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and (time.time() - cached[0]) < self._cache_ttl:
                rows = list(cached[1])
                return self._finalize(rows, timeframe, factor, since, limit)

        rows = self._fetch_raw(ticker, interval, period1, period2)
        if rows:
            with self._cache_lock:
                self._cache[cache_key] = (time.time(), list(rows))
        return self._finalize(rows, timeframe, factor, since, limit)

    def _finalize(self, rows: List[list], timeframe: str, factor: int,
                  since: Optional[int], limit: int) -> List[list]:
        if not rows:
            return []
        if factor > 1:
            rows = _aggregate(rows, _TF_MS.get(timeframe) or 3_600_000)
        if since:
            rows = [r for r in rows if r[0] >= int(since)]
        return rows[-int(limit):] if limit and len(rows) > int(limit) else rows

    def _cooldown_remaining(self) -> float:
        """Secondes restantes avant de réessayer après une salve de 429."""
        with _RATE_LOCK:
            return max(0.0, _RATE_STATE["until"] - time.time())

    def _note_rate_limit(self) -> None:
        """Compte les 429 consécutifs et ouvre le disjoncteur au-delà du seuil.

        Sans lui, un quota Yahoo atteint coûte ``max_retries`` requêtes PAR
        symbole et PAR timeframe : sur un univers de 98 titres et 5 TF, un cycle
        de scan passe de quelques secondes à une demi-heure, à ne rien
        rapporter. Le compteur est au niveau du PROCESSUS — les threads du
        scanner partagent le même quota, donc doivent partager le disjoncteur.
        """
        with _RATE_LOCK:
            _RATE_STATE["consecutive"] += 1
            if _RATE_STATE["consecutive"] >= _RATE_TRIP_AFTER:
                _RATE_STATE["until"] = time.time() + _RATE_COOLDOWN_S
                _RATE_STATE["consecutive"] = 0
                logger.warning(
                    f"[{self.id}] quota Yahoo atteint {_RATE_TRIP_AFTER} fois de "
                    f"suite — pause de {_RATE_COOLDOWN_S:.0f}s pour tout le "
                    f"processus. Les symboles concernés sont ignorés jusque-là ; "
                    f"pour peupler le cache hors ligne : "
                    f"python scripts/backfill_equities.py"
                )

    def _note_success(self) -> None:
        with _RATE_LOCK:
            _RATE_STATE["consecutive"] = 0

    def _fetch_raw(self, ticker: str, interval: str,
                   period1: int, period2: int) -> List[list]:
        """Une requête (avec retry) vers le backend disponible."""
        remaining = self._cooldown_remaining()
        if remaining > 0:
            logger.debug(f"[{self.id}] {ticker}/{interval} ignoré — disjoncteur "
                         f"de quota actif encore {remaining:.0f}s")
            return []
        delay = 2.0
        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                if self._prefer_yfinance and _HAS_YFINANCE:
                    rows = self._fetch_via_yfinance(ticker, interval, period1, period2)
                else:
                    rows = self._fetch_via_chart_api(ticker, interval, period1, period2)
                if rows:
                    self._note_success()
                    return rows
                logger.debug(
                    f"[{self.id}] {ticker}/{interval} — réponse vide "
                    f"(tentative {attempt}/{self._max_retries})"
                )
            except Exception as e:
                rate_limited = "429" in str(e)
                if rate_limited:
                    self._note_rate_limit()
                    if self._cooldown_remaining() > 0:
                        # Disjoncteur ouvert pendant nos propres retries :
                        # insister ne fera qu'aggraver le quota.
                        return []
                logger.warning(
                    f"[{self.id}] {ticker}/{interval} KO ({attempt}/{self._max_retries}) : {e}"
                )
            if attempt < self._max_retries:
                time.sleep(delay)
                delay *= 2      # Yahoo répond 429 en rafale : backoff exponentiel
        return []

    def _fetch_via_yfinance(self, ticker: str, interval: str,
                            period1: int, period2: int) -> List[list]:
        """Chemin ``yfinance``. **Seul endroit** où pandas peut apparaître."""
        hist = _yf.Ticker(ticker).history(
            start=period1, end=period2, interval=interval,
            auto_adjust=False, actions=False, raise_errors=False,
        )
        if hist is None or len(hist) == 0:
            return []
        opens = [float(v) for v in hist["Open"].tolist()]
        highs = [float(v) for v in hist["High"].tolist()]
        lows = [float(v) for v in hist["Low"].tolist()]
        closes = [float(v) for v in hist["Close"].tolist()]
        volumes = [float(v or 0.0) for v in hist["Volume"].tolist()]
        stamps = [int(ts.timestamp() * 1000) for ts in hist.index]
        rows = [[stamps[i], opens[i], highs[i], lows[i], closes[i], volumes[i]]
                for i in range(len(stamps))]
        return _clean(rows)

    def _fetch_via_chart_api(self, ticker: str, interval: str,
                             period1: int, period2: int) -> List[list]:
        """Chemin sans dépendance : API chart publique via ``requests``."""
        if not _HAS_REQUESTS:
            raise RuntimeError("'requests' est requis pour le provider actions")
        if self._session is None:
            self._session = _requests.Session()
            self._session.headers.update({"User-Agent": _USER_AGENT,
                                          "Accept": "application/json"})
        resp = self._session.get(
            _CHART_URL.format(symbol=ticker),
            params={"period1": period1, "period2": period2, "interval": interval,
                    "includePrePost": "false"},
            timeout=self._timeout,
        )
        if resp.status_code == 429:
            raise RuntimeError("HTTP 429 — quota Yahoo atteint, backoff")
        resp.raise_for_status()
        payload = resp.json() or {}
        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise RuntimeError(f"Yahoo : {chart['error']}")
        results = chart.get("result") or []
        if not results:
            return []
        result = results[0]
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs = quote.get("open") or [], quote.get("high") or []
        lows, closes = quote.get("low") or [], quote.get("close") or []
        volumes = quote.get("volume") or []
        rows = []
        for i, ts in enumerate(stamps):
            try:
                o, h, lo, c = opens[i], highs[i], lows[i], closes[i]
            except IndexError:
                break
            if None in (o, h, lo, c):
                continue                    # barre creuse (suspension, illiquidité)
            vol = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
            rows.append([int(ts) * 1000, float(o), float(h), float(lo),
                         float(c), float(vol)])
        return _clean(rows)

    # ── Tickers ────────────────────────────────────────────────────────────

    def fetch_ticker(self, symbol: str) -> dict:
        """Dernier prix connu, dérivé de la dernière bougie disponible.

        Volontairement basé sur l'OHLCV (et donc sur le cache et le throttling
        communs) plutôt que sur ``Ticker.info``, lourd, instable et non
        cacheable — un cycle live appelle ``fetch_ticker`` par symbole.
        """
        for tf in ("1m", "1h", "1d"):
            rows = self.fetch_ohlcv(symbol, tf, limit=2)
            if rows:
                last = rows[-1]
                return {
                    "symbol": symbol, "last": last[4], "close": last[4],
                    "open": last[1], "high": last[2], "low": last[3],
                    "baseVolume": last[5], "quoteVolume": last[5] * last[4],
                    "timestamp": last[0],
                }
        logger.warning(f"[{self.id}] fetch_ticker {symbol} — aucune donnée.")
        return {"symbol": symbol, "last": 0.0}

    def fetch_tickers(self, symbols: Optional[list] = None) -> dict:
        """Tickers pour une liste de symboles (Yahoo n'a pas de « tout le marché »).

        Sans ``symbols``, retourne ``{}`` plutôt que d'essayer d'énumérer une
        place : le scan dynamique par volume est un concept crypto, l'univers
        actions est statique (cf. ``app/core/universe.py``).
        """
        if not symbols:
            logger.debug(f"[{self.id}] fetch_tickers sans liste — univers statique attendu.")
            return {}
        out = {}
        for sym in symbols:
            try:
                out[sym] = self.fetch_ticker(sym)
            except Exception as e:
                logger.warning(f"[{self.id}] fetch_ticker {sym} : {e}")
        return out

    # ── Exécution : non supportée ──────────────────────────────────────────
    #
    # Ces méthodes existent pour **échouer explicitement**. Ne pas les définir
    # du tout serait plus « pur » (le provider ne satisferait alors pas le
    # protocole ``ExecutionProvider``), mais produirait un ``AttributeError``
    # opaque au milieu d'une ouverture de position. Un message nommant la venue
    # mal configurée est un bien meilleur diagnostic.

    def create_order(self, *_a, **_kw):
        raise ExecutionNotSupported(
            f"{self.id} est un provider de données : aucun ordre ne peut être "
            f"envoyé. Assignez la venue à un ExecutionProvider (G3) ou laissez "
            f"can_execute=false pour recevoir une notification de trade."
        )

    def cancel_order(self, *_a, **_kw):
        raise ExecutionNotSupported(f"{self.id} : annulation d'ordre non supportée")

    def fetch_order(self, *_a, **_kw):
        raise ExecutionNotSupported(f"{self.id} : consultation d'ordre non supportée")

    def fetch_balance_detail(self) -> dict:
        """Pas de compte chez un fournisseur de données : solde neutre."""
        return {"free": 0.0, "used": 0.0, "total": 0.0, "borrowed": 0.0}


# ── Helpers ────────────────────────────────────────────────────────────────

def _clean(rows: List[list]) -> List[list]:
    """Trie, déduplique et écarte les barres non cotées (prix nul/négatif)."""
    seen: set = set()
    out: List[list] = []
    for r in sorted(rows, key=lambda x: x[0]):
        if r[0] in seen or r[4] <= 0:
            continue
        seen.add(r[0])
        out.append(r)
    return out


def _aggregate(rows: List[list], target_ms: int) -> List[list]:
    """Ré-agrège des bougies vers un timeframe plus large, ancré sur l'epoch.

    Nécessaire pour 3 m / 2 h / 4 h, que Yahoo ne cote pas. L'ancrage epoch
    (``ts − ts % target``) garantit que deux appels successifs produisent les
    **mêmes** bornes de bougie, condition pour que le cache Parquet incrémental
    du ``CandleStore`` déduplique correctement.
    """
    if target_ms <= 0 or not rows:
        return rows
    buckets: Dict[int, list] = {}
    order: List[int] = []
    for ts, o, h, lo, c, v in rows:
        key = int(ts) - (int(ts) % target_ms)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = [key, o, h, lo, c, v]
            order.append(key)
        else:
            bucket[2] = max(bucket[2], h)
            bucket[3] = min(bucket[3], lo)
            bucket[4] = c
            bucket[5] += v
    return [buckets[k] for k in sorted(order)]
