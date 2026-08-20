"""Détection de trous OHLCV, calendrier-aware (D-03).

``detect_ohlcv_gaps`` comparait uniquement à ``1,5 × Δ`` : un week-end XPAR
était un « trou ». Ici, un calendrier de séance élargit le seuil à
``calendar.max_gap_seconds`` ; un marché 24/7 garde le seuil historique.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

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
    simple_allowed = expected_secs * 1.5
    for i in range(1, n):
        delta_secs = float(delta_secs_arr[i]) if delta_secs_arr is not None else _one_delta_secs(times[i - 1], times[i])
        if delta_secs <= simple_allowed and cal is None:
            continue
        allowed = simple_allowed
        if cal is not None:
            try:
                ts = _as_dt(times[i - 1])
                after = _as_dt(times[i])
                # Week-end / férié : si l'intérieur du span est fermé, ce n'est
                # pas un trou de données. 1d : aucun jour de séance entre les
                # deux barres (ven. minuit → lun. minuit).
                if _calendar_closed_span(cal, ts, after, expected_secs):
                    continue
                end = cal.session_end(ts)
                nxt = cal.next_open(end or ts)
                if nxt is not None:
                    allowed = max(
                        allowed,
                        (nxt - ts).total_seconds() + expected_secs * 1.5,
                    )
                try:
                    allowed = max(allowed, float(cal.max_gap_seconds(ts, expected_secs)))
                except Exception:
                    pass
            except Exception:
                pass
        if delta_secs > allowed:
            gap_bars = max(0, round(delta_secs / expected_secs) - 1)
            gaps.append({
                "index":        int(i),
                "time_before":  str(times[i - 1])[:16],
                "time_after":   str(times[i])[:16],
                "gap_bars":     int(gap_bars),
                "gap_duration": str(times[i] - times[i - 1]),
            })
    return gaps


def _calendar_closed_span(cal, ts: datetime, after: datetime, expected_secs: float) -> bool:
    """True si le span est une fermeture calendaire, pas un trou de données."""
    if _interior_all_closed(cal, ts, after):
        return True
    if expected_secs < 86400 * 0.9:
        return False
    d0 = ts.date()
    d1 = after.date()
    d = d0 + timedelta(days=1)
    while d < d1:
        noon = datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)
        try:
            if cal.is_open(noon):
                return False
            morning = datetime(d.year, d.month, d.day, 8, 0, tzinfo=timezone.utc)
            nxt = cal.next_open(morning)
            if nxt is not None and nxt.date() == d:
                return False
        except Exception:
            return False
        d += timedelta(days=1)
    return True


def _interior_all_closed(cal, ts: datetime, after: datetime, samples: int = 7) -> bool:
    total = (after - ts).total_seconds()
    if total <= 0:
        return False
    for k in range(1, samples + 1):
        mid = ts + timedelta(seconds=total * k / (samples + 1))
        try:
            if cal.is_open(mid):
                return False
        except Exception:
            return False
    return True


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
