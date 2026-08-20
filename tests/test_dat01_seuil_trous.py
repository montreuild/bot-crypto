"""DAT-01 — le seuil de détection d'un trou reste à 1,5×tf sur un marché 24/7.

`max_gap_seconds` mesure la fraîcheur tolérée d'une donnée LIVE, pas la taille
acceptable d'un trou historique. L'appliquer à un calendrier 24/7 le portait à
3×tf (`_STALE_TF_MULTIPLIER`) et masquait tout trou de 1 à 2 barres — 15 trous
réels non détectés sur BTC_USDC 1h, 4 sur 4h.

Sur un marché à séances il garde son sens (temps jusqu'à la prochaine
ouverture) : c'est lui qui couvre le week-end pour les bougies journalières,
dont l'horodatage à 23:00 UTC met un jour ouvré à l'intérieur du span.
"""
import datetime as dt

import polars as pl

from app.core.ohlcv_gaps import calendar_for_symbol, detect_ohlcv_gaps


def _serie_1h(n: int = 200, retirer: list[int] | None = None) -> pl.DataFrame:
    start = dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc)
    idx = [i for i in range(n) if i not in set(retirer or [])]
    return pl.DataFrame({
        "time":   [start + dt.timedelta(hours=i) for i in idx],
        "open":   [100.0] * len(idx), "high": [101.0] * len(idx),
        "low":    [99.0] * len(idx),  "close": [100.0] * len(idx),
        "volume": [10.0] * len(idx),
    })


def test_une_barre_manquante_est_un_trou_en_24_7():
    """Écart de 2×tf : sous l'ancien seuil de 3×tf il passait inaperçu."""
    gaps = detect_ohlcv_gaps(_serie_1h(retirer=[50]), "1h", symbol="BTC/USDC")
    assert len(gaps) == 1, f"attendu 1 trou, obtenu {gaps}"
    assert gaps[0]["gap_bars"] == 1


def test_deux_barres_manquantes_sont_un_trou_en_24_7():
    """Écart de 3×tf : exactement à l'ancien seuil, donc masqué avant."""
    gaps = detect_ohlcv_gaps(_serie_1h(retirer=[50, 51]), "1h", symbol="BTC/USDC")
    assert len(gaps) == 1, f"attendu 1 trou, obtenu {gaps}"
    assert gaps[0]["gap_bars"] == 2


def test_une_serie_complete_ne_declare_aucun_trou():
    assert detect_ohlcv_gaps(_serie_1h(), "1h", symbol="BTC/USDC") == []


def test_le_week_end_d_une_action_n_est_pas_un_trou():
    """Garde-fou de non-régression : les bougies 1d sont horodatées à 23:00 UTC
    (minuit Paris), si bien que le span vendredi→lundi contient un jour ouvré.
    Le retrait de `max_gap_seconds` pour les actions ferait remonter ~1 400
    faux trous par symbole."""
    jours, t = [], dt.datetime(2021, 1, 3, 23, 0, tzinfo=dt.timezone.utc)
    while len(jours) < 40:
        if t.weekday() < 5:                     # séance du lendemain à Paris
            jours.append(t)
        t += dt.timedelta(days=1)
    df = pl.DataFrame({
        "time":   jours,
        "open":   [100.0] * len(jours), "high": [101.0] * len(jours),
        "low":    [99.0] * len(jours),  "close": [100.0] * len(jours),
        "volume": [10.0] * len(jours),
    })
    gaps = detect_ohlcv_gaps(df, "1d", calendar=calendar_for_symbol("AC.PA"))
    assert gaps == [], f"aucun trou attendu sur des séances consécutives : {gaps}"
