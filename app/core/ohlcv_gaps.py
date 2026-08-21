"""Détection de trous OHLCV, calendrier-aware (D-03).

``detect_ohlcv_gaps`` comparait uniquement à ``1,5 × Δ`` : un week-end XPAR
était un « trou ». Puis une pile d'heuristiques a tenté d'élargir ce seuil —
marge de fraîcheur, prochaine ouverture, échantillonnage du span — sans jamais
décrire la réalité.

La question posée est désormais la bonne : ``calendar.expected_bars_between``
répond « combien de barres DEVRAIENT exister entre ces deux horodatages ». Une
fermeture en produit zéro, une séance manquante en produit autant qu'il en
manque.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_TF_MINS = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "1d": 1440,
}


def calendar_for_symbol(symbol: str, cfg: Optional[dict] = None):
    """Heuristique venue : suffixe action → XPAR, sinon 24/7."""
    from app.core.market_calendar import ALWAYS_OPEN, get_calendar
    sym = (symbol or "").upper()
    if any(sym.endswith(sfx) for sfx in (".PA", ".AS", ".F", ".DE", ".L")):
        return get_calendar("XPAR", cfg)
    return ALWAYS_OPEN


def _as_dt(ts) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def detect_ohlcv_gaps(df, timeframe: str, calendar=None, symbol: str = "") -> list:
    """Trous successifs au-delà du seuil attendu (calendaire si fourni)."""
    if df is None or len(df) < 2 or "time" not in df.columns:
        return []
    expected_mins = _TF_MINS.get(timeframe, 60)
    expected_secs = expected_mins * 60
    cal = calendar
    if cal is None and symbol:
        cal = calendar_for_symbol(symbol)

    times = df["time"]
    n = len(times)
    delta_secs_arr = _delta_seconds(df)
    gaps = []
    # Un écart inférieur ou égal à 1,5×tf ne peut pas cacher de barre : c'est
    # le seul cas où l'on peut trancher sans interroger le calendrier, et il
    # couvre la quasi-totalité des barres.
    simple_allowed = expected_secs * 1.5
    for i in range(1, n):
        # DAT-04 : un horodatage nul dans la colonne `time` produisait un nul
        # dans `diff()`, donc `float(None)` et une TypeError. Elle était avalée
        # par `_backfill_gaps` — rattrapage abandonné en silence — et remontait
        # jusqu'à la route par `stats()`. Un écart n'est pas mesurable entre
        # deux bornes dont une manque : on saute la paire en le signalant,
        # plutôt que d'interrompre le scan de tout un fichier.
        if times[i - 1] is None or times[i] is None:
            logger.warning(
                "[OHLCV] horodatage nul à l'index %d — paire ignorée, "
                "la détection de trous ne peut pas la mesurer", i,
            )
            continue
        _brut = delta_secs_arr[i] if delta_secs_arr is not None else None
        delta_secs = (float(_brut) if _brut is not None
                      else _one_delta_secs(times[i - 1], times[i]))
        if delta_secs <= simple_allowed:
            continue
        # Au-delà, on ne devine pas un « écart tolérable » : on demande au
        # calendrier combien de barres DEVRAIENT exister entre les deux.
        # Une fermeture (nuit, week-end, férié) en produit zéro et n'est donc
        # pas un trou ; une séance manquante en produit autant qu'il en manque.
        try:
            manquantes = int(cal.expected_bars_between(
                _as_dt(times[i - 1]), _as_dt(times[i]), int(expected_secs)))
        except Exception:
            # Calendrier incomplet ou horodatage inattendu : repli arithmétique
            # plutôt que silence — un trou non signalé ne se rattrape jamais.
            manquantes = max(0, int(round(delta_secs / expected_secs)) - 1)
        if manquantes > 0:
            gaps.append({
                "index":        int(i),
                "time_before":  str(times[i - 1])[:16],
                "time_after":   str(times[i])[:16],
                "gap_bars":     int(manquantes),
                "gap_duration": str(times[i] - times[i - 1]),
            })
    return gaps





def _one_delta_secs(a, b) -> float:
    delta = b - a
    try:
        return float(delta.total_seconds())
    except AttributeError:
        return float(delta)


def _delta_seconds(df):
    """Écarts successifs en secondes (colonne vectorisée si possible)."""
    try:
        import polars as pl
        if not isinstance(df, pl.DataFrame):
            return None
        dtype = df.schema.get("time")
        if dtype in (pl.Datetime, pl.Date):
            return df.select(pl.col("time").diff().dt.total_seconds())["time"].to_list()
    except Exception:
        return None
    return None


def completeness_from_gaps(n_bars: int, gaps: list) -> float:
    missing = sum(int(g.get("gap_bars") or 0) for g in gaps)
    denom = n_bars + missing
    if denom <= 0:
        return 1.0
    return round(n_bars / denom, 4)
