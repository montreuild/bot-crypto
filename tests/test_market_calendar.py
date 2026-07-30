"""G2 — calendrier de marché générique (app/core/market_calendar.py).

Deux exigences opposées à vérifier ensemble :
  1. le crypto ne bouge pas d'un iota (venue sans calendrier → 24/7) ;
  2. Euronext Paris est correctement fermé (nuit, week-end, fériés mobiles,
     demi-séances), sinon le bot scorerait dans le vide.
"""
from datetime import date, datetime, timezone

from app.core.market_calendar import (
    ALWAYS_OPEN,
    XPAR_CALENDAR,
    AlwaysOpenCalendar,
    HolidayRule,
    MarketCalendar,
    calendar_from_spec,
    clear_cache,
    easter_date,
    get_calendar,
)


def _utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# ── 24/7 : le défaut crypto ────────────────────────────────────────────────

class TestAlwaysOpen:
    def test_open_at_any_time(self):
        cal = AlwaysOpenCalendar()
        assert cal.is_open(_utc(2026, 1, 1, 3, 0))       # jour de l'an, 3h du matin
        assert cal.is_open(_utc(2026, 8, 16, 23, 59))    # dimanche soir

    def test_no_session_end_so_nothing_to_close(self):
        assert AlwaysOpenCalendar().session_end(_utc(2026, 7, 1, 12)) is None

    def test_empty_and_unknown_codes_fall_back_to_24x7(self):
        clear_cache()
        for code in ("", None, "24x7", "  "):
            assert get_calendar(code) is ALWAYS_OPEN
        # Code inconnu : on n'invente pas d'horaires, on reste ouvert.
        assert get_calendar("XZZZ").is_open(_utc(2026, 1, 1, 3))

    def test_satisfies_protocol(self):
        assert isinstance(ALWAYS_OPEN, MarketCalendar)
        assert isinstance(XPAR_CALENDAR, MarketCalendar)


# ── Pâques : base des fériés mobiles ───────────────────────────────────────

class TestEaster:
    def test_known_easter_sundays(self):
        # Références indépendantes du code (comput ecclésiastique grégorien).
        assert easter_date(2024) == date(2024, 3, 31)
        assert easter_date(2025) == date(2025, 4, 20)
        assert easter_date(2026) == date(2026, 4, 5)
        assert easter_date(2027) == date(2027, 3, 28)

    def test_rule_resolves_relative_to_easter(self):
        good_friday = HolidayRule("easter", offset=-2).resolve(2026)
        easter_monday = HolidayRule("easter", offset=1).resolve(2026)
        assert good_friday == date(2026, 4, 3)
        assert easter_monday == date(2026, 4, 6)


# ── Euronext Paris ─────────────────────────────────────────────────────────

class TestXPAR:
    def test_open_during_session_closed_outside(self):
        cal = XPAR_CALENDAR
        # 2026-07-01 est un mercredi. Paris est à UTC+2 en été.
        assert not cal.is_open(_utc(2026, 7, 1, 6, 0))    # 08:00 locale — pré-ouverture
        assert cal.is_open(_utc(2026, 7, 1, 7, 30))       # 09:30 locale
        assert cal.is_open(_utc(2026, 7, 1, 15, 0))       # 17:00 locale
        assert not cal.is_open(_utc(2026, 7, 1, 15, 40))  # 17:40 locale — clôturé

    def test_winter_time_shifts_the_session(self):
        # 2026-01-07, mercredi. Paris à UTC+1 : séance 08:00-16:30 UTC.
        cal = XPAR_CALENDAR
        assert not cal.is_open(_utc(2026, 1, 7, 7, 30))
        assert cal.is_open(_utc(2026, 1, 7, 8, 30))
        assert cal.is_open(_utc(2026, 1, 7, 16, 0))
        assert not cal.is_open(_utc(2026, 1, 7, 16, 40))

    def test_weekend_closed(self):
        cal = XPAR_CALENDAR
        assert not cal.is_open(_utc(2026, 7, 4, 10))   # samedi
        assert not cal.is_open(_utc(2026, 7, 5, 10))   # dimanche

    def test_fixed_and_movable_holidays_closed(self):
        cal = XPAR_CALENDAR
        assert not cal.is_open(_utc(2026, 1, 1, 10))    # jour de l'an
        assert not cal.is_open(_utc(2026, 5, 1, 10))    # fête du travail
        assert not cal.is_open(_utc(2026, 12, 25, 10))  # Noël
        assert not cal.is_open(_utc(2026, 4, 3, 10))    # Vendredi saint
        assert not cal.is_open(_utc(2026, 4, 6, 10))    # lundi de Pâques

    def test_french_public_holidays_that_euronext_trades(self):
        """Euronext cote le 14 juillet et le 15 août — ne pas les fermer."""
        cal = XPAR_CALENDAR
        assert cal.is_open(_utc(2026, 7, 14, 10))       # mardi 14 juillet
        # 2026-11-11 est un mercredi : séance normale sur Euronext.
        assert cal.is_open(_utc(2026, 11, 11, 10))

    def test_early_close_on_christmas_eve(self):
        cal = XPAR_CALENDAR
        # 2026-12-24 est un jeudi, clôture 14:05 locale = 13:05 UTC (hiver).
        assert cal.is_open(_utc(2026, 12, 24, 12, 0))
        assert not cal.is_open(_utc(2026, 12, 24, 13, 30))

    def test_session_end_returns_close_of_current_session(self):
        end = XPAR_CALENDAR.session_end(_utc(2026, 7, 1, 10))
        assert end == _utc(2026, 7, 1, 15, 30)          # 17:30 locale
        assert XPAR_CALENDAR.session_end(_utc(2026, 7, 1, 20)) is None

    def test_next_open_skips_weekend(self):
        # Vendredi 20:00 UTC → lundi 07:00 UTC (09:00 locale).
        nxt = XPAR_CALENDAR.next_open(_utc(2026, 7, 3, 20))
        assert nxt == _utc(2026, 7, 6, 7, 0)

    def test_next_open_returns_now_when_open(self):
        now = _utc(2026, 7, 1, 10)
        assert XPAR_CALENDAR.next_open(now) == now

    def test_max_gap_covers_the_whole_weekend(self):
        """Un cache à jour le vendredi soir ne doit pas être jugé périmé."""
        friday_evening = _utc(2026, 7, 3, 20)
        gap = XPAR_CALENDAR.max_gap_seconds(friday_evening, tf_seconds=3600)
        assert gap > 2.5 * 86400                       # couvre jusqu'à lundi
        # 24/7 : aucune tolérance étendue, la marge reste celle du timeframe.
        assert ALWAYS_OPEN.max_gap_seconds(friday_evening, 3600) == 3 * 3600


# ── Déclaration par configuration ──────────────────────────────────────────

class TestDeclarativeCalendar:
    def test_calendar_from_spec_with_lunch_break(self):
        cal = calendar_from_spec("XTEST", {
            "timezone": "UTC",
            "days": [0, 1, 2, 3, 4],
            "sessions": ["09:00-11:30", "13:00-17:00"],
            "holidays": ["01-01", {"easter": 1}],
        })
        assert cal.is_open(_utc(2026, 7, 1, 10))
        assert not cal.is_open(_utc(2026, 7, 1, 12))    # pause déjeuner
        assert cal.is_open(_utc(2026, 7, 1, 14))
        assert not cal.is_open(_utc(2026, 4, 6, 10))    # lundi de Pâques

    def test_config_declaration_wins_over_builtin(self):
        clear_cache()
        cfg = {"calendars": {"XPAR": {"timezone": "UTC", "days": [0, 1, 2, 3, 4, 5, 6],
                                      "sessions": ["00:00-23:59"]}}}
        cal = get_calendar("XPAR", cfg)
        assert cal.is_open(_utc(2026, 7, 4, 10))        # samedi, ouvert par config
        clear_cache()

    def test_malformed_entries_are_ignored_not_fatal(self):
        cal = calendar_from_spec("XBAD", {
            "sessions": ["09:00-17:30", "pas-une-plage"],
            "holidays": ["01-01", "zz-zz", {"inconnu": 1}],
            "early_closes": {"12-24": "14:05", "oops": "nope"},
        })
        assert cal.is_open(_utc(2026, 7, 1, 10))
