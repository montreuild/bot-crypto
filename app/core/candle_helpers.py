"""Primitives du CandleStore : provider, bornes, verrous (DETTE-04).

Extraites de `candle_store.py` (1 213 lignes). Aucune ne touche à l'état du
store — elles ne dépendent que de l'exchange et du timeframe, donc se testent
sans monter de store.
"""

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import polars as pl

from app.core.config import DATA_ROOT
from app.core.timeframes import TF_MS

OHLCV_DIR = os.path.join(DATA_ROOT, "ohlcv")

logger = logging.getLogger(__name__)

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

# Recousage : UNE pagination de la première à la dernière discontinuité
# (pas 8 trous × 6 pages, qui rejouaient la même plage pendant des heures
# et fragmentaient les gros trous). 200 × 1000 = 200 k barres — au-delà
# d'un 15m sur plusieurs années. Le reliquat (maintenance exchange) est
# mémoïsé 6 h.
_MAX_GAP_SPAN_PAGES = 200
# Au-delà, un trou n'est plus un créneau non tradé mais de l'historique
# manquant : on continue de le redemander au lieu de le déclarer inexistant.
_MAX_ABSENT_GAP_BARS = 12
_GAP_FILL_PAGE = 1000


def _fmt_ms(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return str(ms)


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


