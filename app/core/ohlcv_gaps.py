"""Détection de trous OHLCV, calendrier-aware (D-03).

``detect_ohlcv_gaps`` comparait uniquement à ``1,5 × Δ`` : un week-end XPAR
était un « trou ». Ici, un calendrier de séance élargit le seuil à
``calendar.max_gap_seconds`` ; un marché 24/7 garde le seuil historique.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    gaps = []
    for i in range(1, len(times)):
        delta = times[i] - times[i - 1]
        try:
            delta_secs = delta.total_seconds()
        except AttributeError:
            delta_secs = float(delta)
        allowed = expected_secs * 1.5
        if cal is not None:
            try:
                ts = _as_dt(times[i - 1])
                # max_gap_seconds mesure le stale live, pas un trou historique :
                # en séance il ne vaut que 3×tf. Ici on autorise jusqu'à la
                # prochaine ouverture (nuit / week-end / férié).
                end = cal.session_end(ts)
                nxt = cal.next_open(end or ts)
                if nxt is not None:
                    allowed = max(
                        allowed,
                        (nxt - ts).total_seconds() + expected_secs * 1.5,
                    )
            except Exception:
                pass
        if delta_secs > allowed:
            gap_bars = max(0, round(delta_secs / expected_secs) - 1)
            gaps.append({
                "index":        int(i),
                "time_before":  str(times[i - 1])[:16],
                "time_after":   str(times[i])[:16],
                "gap_bars":     int(gap_bars),
                "gap_duration": str(delta),
            })
    return gaps


def completeness_from_gaps(n_bars: int, gaps: list) -> float:
    missing = sum(int(g.get("gap_bars") or 0) for g in gaps)
    denom = n_bars + missing
    if denom <= 0:
        return 1.0
    return round(n_bars / denom, 4)
