"""DerivativesStore — données de dérivés GRATUITES pour signaux directionnels.

Agrège, sans clé API (endpoints publics), les métriques de dérivés qui portent
le vrai edge directionnel crypto (cf. research/DERIVATIVES_integration.md) :

  • funding_rate          — funding des perp (ccxt fetch_funding_rate_history)
  • open_interest         — OI (ccxt fetch_open_interest_history)
  • long_short_ratio      — ratio comptes long/short (Binance futures-data REST)
  • taker_buy_sell_ratio  — volume taker acheteur/vendeur = order-flow agressif

Pattern aligné sur CandleStore : cache Parquet par (symbol, métrique), polars,
thread-safe. Toutes les méthodes réseau sont GRACIEUSES : en cas d'échec (pas de
réseau, IP géo-bloquée, endpoint indisponible) elles renvoient un DataFrame vide
et ``align_to_ohlcv`` laisse simplement les colonnes absentes — les stratégies
consommatrices basculent alors sur leur logique OHLCV pure.

⚠️ Limites des sources gratuites :
  • funding_rate : historique long (plusieurs années) via ccxt.
  • open_interest / long_short / taker : Binance ne sert que ~30 DERNIERS JOURS
    sur les endpoints publics → exploitables en LIVE, pas pour un backtest pluri-
    annuel (pour cela : source payante type Coinglass/Coinalyze/Glassnode, ou
    accumulation locale au fil de l'eau dans le cache Parquet de ce module).
"""

import json
import logging
import threading
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import polars as pl

logger = logging.getLogger(__name__)

_BINANCE_FAPI = "https://fapi.binance.com"
_PERIOD_OK = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d")

# Compat polars : le kwarg ``min_periods`` (≤1.20, version épinglée 1.0.0) a été
# renommé ``min_samples`` ensuite. Détection une fois à l'import.
import inspect as _inspect
_MIN_SAMPLES_KW = (
    {"min_samples": 10}
    if "min_samples" in _inspect.signature(pl.Expr.rolling_mean).parameters
    else {"min_periods": 10}
)

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path), threading.Lock())


def to_perp_symbol(symbol: str) -> str:
    """'BTC/USDC' | 'BTC/USDT' → 'BTCUSDT' (perp USDT = référence funding/OI).

    Format compact utilisé pour les clés de cache Parquet et les endpoints REST
    Binance futures-data (long/short, taker)."""
    base = symbol.split("/")[0].split(":")[0].upper()
    return f"{base}USDT"


def _ccxt_swap_symbol(symbol: str, exchange=None) -> str:
    """Symbole du perp linéaire USDT au format ccxt, pour fetch_funding/OI.

    OKX exige le suffixe de règlement (``BTC/USDT:USDT``) ; Binance & co
    acceptent ``BTC/USDT``. On référence toujours le perp **USDT** (liquidité
    funding/OI), quelle que soit la quote de la paire spot tradée."""
    base = symbol.split("/")[0].split(":")[0].upper()
    name = (getattr(exchange, "id", "") or "").lower()
    if name == "okx":
        return f"{base}/USDT:USDT"
    return f"{base}/USDT"


def _http_get_json(url: str, timeout: float = 8.0) -> Optional[list]:
    """GET JSON gracieux : renvoie None en cas d'échec (réseau, géo-block, etc.)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "crypto-bot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001 — volontairement large : dégradation gracieuse
        logger.debug(f"[Derivatives] GET KO {url[:70]} : {type(e).__name__} {e}")
        return None


class DerivativesStore:
    """Cache Parquet + fetch des métriques de dérivés. Sans clé API."""

    def __init__(self, base_dir: str = "data/derivatives", fapi_base: str = _BINANCE_FAPI):
        self._base = Path(base_dir)
        self._fapi = fapi_base.rstrip("/")
        self._last_refresh: Dict[str, float] = {}   # (symbol|period) → ts dernier fetch réseau

    def _path(self, symbol: str, metric: str) -> Path:
        return self._base / f"{to_perp_symbol(symbol)}__{metric}.parquet"

    def _load(self, path: Path) -> pl.DataFrame:
        if path.exists():
            try:
                return pl.read_parquet(path)
            except Exception as e:
                logger.warning(f"[Derivatives] Lecture {path.name} KO : {e}")
        return pl.DataFrame({"time": [], "value": []},
                            schema={"time": pl.Datetime("ms"), "value": pl.Float64})

    def _save(self, path: Path, df: pl.DataFrame, extra_cols: List[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        df.sort("time").unique("time", keep="last").sort("time").write_parquet(path)

    def _merge_cache(self, path: Path, df_new: pl.DataFrame) -> pl.DataFrame:
        with _lock(path):
            df = pl.concat([self._load(path).select(df_new.columns), df_new], how="vertical") \
                if path.exists() else df_new
            df = df.sort("time").unique("time", keep="last").sort("time")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                df.write_parquet(path)
            except Exception as e:
                logger.warning(f"[Derivatives] Écriture {path.name} KO : {e}")
            return df

    # ── Fetchers (gracieux) ────────────────────────────────────────────────────

    def fetch_funding(self, exchange, symbol: str, limit: int = 1000) -> pl.DataFrame:
        """Historique du funding via ccxt (long historique). exchange = RobustExchange/ccxt."""
        sym = _ccxt_swap_symbol(symbol, exchange)
        try:
            raw = exchange.fetch_funding_rate_history(sym, limit=limit)
        except Exception as e:
            logger.debug(f"[Derivatives] funding KO : {e}")
            raw = None
        if not raw:
            return self._load(self._path(symbol, "funding"))
        df = pl.DataFrame({
            "time": [int(x["timestamp"]) for x in raw if x.get("timestamp")],
            "value": [float(x["fundingRate"]) for x in raw if x.get("timestamp")],
        }).with_columns(pl.col("time").cast(pl.Datetime("ms")))
        return self._merge_cache(self._path(symbol, "funding"), df)

    def fetch_open_interest(self, exchange, symbol: str, period: str = "1h",
                            limit: int = 500) -> pl.DataFrame:
        """OI via ccxt fetch_open_interest_history (Binance : ~30 derniers jours)."""
        sym = _ccxt_swap_symbol(symbol, exchange)
        try:
            raw = exchange.fetch_open_interest_history(sym, period, limit=limit)
        except Exception as e:
            logger.debug(f"[Derivatives] OI KO : {e}")
            raw = None
        if not raw:
            return self._load(self._path(symbol, "oi"))
        rows_t, rows_v = [], []
        for x in raw:
            ts = x.get("timestamp")
            val = (x.get("openInterestValue") or x.get("openInterestAmount")
                   or (x.get("info") or {}).get("sumOpenInterest"))
            if ts and val is not None:
                rows_t.append(int(ts)); rows_v.append(float(val))
        if not rows_t:
            return self._load(self._path(symbol, "oi"))
        df = pl.DataFrame({"time": rows_t, "value": rows_v}) \
            .with_columns(pl.col("time").cast(pl.Datetime("ms")))
        return self._merge_cache(self._path(symbol, "oi"), df)

    def _fetch_fapi_data(self, symbol: str, endpoint: str, metric: str,
                         value_key: str, period: str = "1h", limit: int = 500) -> pl.DataFrame:
        """Endpoints Binance futures-data publics (long/short, taker). ~30 j d'historique."""
        if period not in _PERIOD_OK:
            period = "1h"
        url = (f"{self._fapi}/futures/data/{endpoint}"
               f"?symbol={to_perp_symbol(symbol)}&period={period}&limit={limit}")
        raw = _http_get_json(url)
        if not raw or not isinstance(raw, list):
            return self._load(self._path(symbol, metric))
        rows_t, rows_v = [], []
        for x in raw:
            ts = x.get("timestamp")
            if ts is None or value_key not in x:
                continue
            try:
                rows_t.append(int(ts)); rows_v.append(float(x[value_key]))
            except (TypeError, ValueError):
                continue
        if not rows_t:
            return self._load(self._path(symbol, metric))
        df = pl.DataFrame({"time": rows_t, "value": rows_v}) \
            .with_columns(pl.col("time").cast(pl.Datetime("ms")))
        return self._merge_cache(self._path(symbol, metric), df)

    def fetch_long_short_ratio(self, symbol: str, period: str = "1h", limit: int = 500) -> pl.DataFrame:
        # globalLongShortAccountRatio → champ "longShortRatio"
        return self._fetch_fapi_data(symbol, "globalLongShortAccountRatio",
                                     "lsratio", "longShortRatio", period, limit)

    def fetch_taker_ratio(self, symbol: str, period: str = "1h", limit: int = 500) -> pl.DataFrame:
        # takerlongshortRatio → champ "buySellRatio"
        return self._fetch_fapi_data(symbol, "takerlongshortRatio",
                                     "taker", "buySellRatio", period, limit)

    # ── Alignement sur l'OHLCV (sans lookahead) ────────────────────────────────

    def align_to_ohlcv(self, df_ohlcv: pl.DataFrame, symbol: str, exchange=None,
                       period: str = "1h", refresh: bool = False,
                       z_window: int = 90) -> pl.DataFrame:
        """Ajoute au df OHLCV les colonnes dérivées via join_asof (backward = causal).

        Colonnes ajoutées si données disponibles (sinon laissées absentes) :
          funding_rate, funding_z, open_interest, oi_change_pct,
          long_short_ratio, lsr_z, taker_buy_sell_ratio, taker_z

        ``refresh=True`` (live) : tente un fetch réseau ; sinon lit le cache Parquet
        (permet le backtest si l'utilisateur a accumulé/fourni un historique).
        """
        if "time" not in df_ohlcv.columns or len(df_ohlcv) == 0:
            return df_ohlcv
        # Normalise l'unité de temps en ms (clé de join_asof) — cohérent avec
        # CandleStore ; évite un SchemaError si l'OHLCV source est en µs.
        out = df_ohlcv.with_columns(pl.col("time").cast(pl.Datetime("ms"))).sort("time")

        def _series(metric: str, fetch_fn) -> Optional[pl.DataFrame]:
            df = fetch_fn() if refresh else self._load(self._path(symbol, metric))
            if df is None or len(df) == 0:
                return None
            return df.sort("time").rename({"value": metric})

        funding = _series("funding", lambda: self.fetch_funding(exchange, symbol)) if (exchange or not refresh) else None
        oi      = _series("oi", lambda: self.fetch_open_interest(exchange, symbol, period)) if (exchange or not refresh) else None
        lsr     = _series("lsratio", lambda: self.fetch_long_short_ratio(symbol, period))
        taker   = _series("taker", lambda: self.fetch_taker_ratio(symbol, period))

        def _zcol(name: str) -> pl.Expr:
            mean = pl.col(name).rolling_mean(z_window, **_MIN_SAMPLES_KW)
            std = pl.col(name).rolling_std(z_window, **_MIN_SAMPLES_KW)
            return ((pl.col(name) - mean) / (std + 1e-12))

        if funding is not None:
            out = out.join_asof(funding.rename({"funding": "funding_rate"}),
                                on="time", strategy="backward")
            if "funding_rate" in out.columns:
                out = out.with_columns(_zcol("funding_rate").alias("funding_z"))
        if oi is not None:
            out = out.join_asof(oi.rename({"oi": "open_interest"}), on="time", strategy="backward")
            if "open_interest" in out.columns:
                out = out.with_columns(
                    (pl.col("open_interest").pct_change() * 100).alias("oi_change_pct"))
        if lsr is not None:
            out = out.join_asof(lsr.rename({"lsratio": "long_short_ratio"}), on="time", strategy="backward")
            if "long_short_ratio" in out.columns:
                out = out.with_columns(_zcol("long_short_ratio").alias("lsr_z"))
        if taker is not None:
            out = out.join_asof(taker.rename({"taker": "taker_buy_sell_ratio"}), on="time", strategy="backward")
            if "taker_buy_sell_ratio" in out.columns:
                out = out.with_columns(_zcol("taker_buy_sell_ratio").alias("taker_z"))
        return out

    def refresh(self, exchange, symbol: str, period: str = "1h",
                min_interval: float = 300.0) -> bool:
        """Accumulation « au fil de l'eau » : fetch incrémental des 4 métriques et
        merge dans le cache Parquet (comme CandleStore pour l'OHLCV). Throttlé par
        ``min_interval`` (s) et par (symbol, period). Gracieux (aucune exception
        propagée). Retourne True si un fetch réseau a été tenté."""
        import time
        key = f"{to_perp_symbol(symbol)}|{period}"
        now = time.time()
        if now - self._last_refresh.get(key, 0.0) < min_interval:
            return False
        self._last_refresh[key] = now
        try:
            if exchange is not None:
                self.fetch_funding(exchange, symbol)
                self.fetch_open_interest(exchange, symbol, period)
            self.fetch_long_short_ratio(symbol, period)
            self.fetch_taker_ratio(symbol, period)
        except Exception as e:  # noqa: BLE001 — accumulation best-effort
            logger.debug(f"[Derivatives] refresh {key} KO : {e}")
        return True

    def latest(self, symbol: str, exchange=None, period: str = "1h") -> Dict[str, float]:
        """Dernières valeurs (live) : funding/OI via ccxt, LS/taker via REST. Gracieux."""
        res: Dict[str, float] = {}
        try:
            if exchange is not None:
                f = self.fetch_funding(exchange, symbol, limit=100)
                if len(f):
                    res["funding_rate"] = float(f["value"][-1])
                oi = self.fetch_open_interest(exchange, symbol, period, limit=50)
                if len(oi):
                    res["open_interest"] = float(oi["value"][-1])
            ls = self.fetch_long_short_ratio(symbol, period, limit=50)
            if len(ls):
                res["long_short_ratio"] = float(ls["value"][-1])
            tk = self.fetch_taker_ratio(symbol, period, limit=50)
            if len(tk):
                res["taker_buy_sell_ratio"] = float(tk["value"][-1])
        except Exception as e:
            logger.debug(f"[Derivatives] latest KO : {e}")
        return res
