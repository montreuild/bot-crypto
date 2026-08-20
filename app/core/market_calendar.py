"""Calendrier de marché générique (G2 — actions SBF120 en paper).

La boucle live a longtemps supposé un marché **24/7** : c'est vrai en crypto,
faux dès qu'on branche une place organisée (Euronext Paris ouvre 09:00-17:30,
du lundi au vendredi, hors jours fériés). Ce module fournit l'abstraction qui
manquait, sans rien coder en dur côté moteur :

  - ``MarketCalendar`` — protocole structurel (PEP 544) : ``is_open``,
    ``session_end``, ``next_open``, ``max_gap_seconds``.
  - ``AlwaysOpenCalendar`` — 24/7. **Défaut de toute venue** : une venue crypto
    qui ne déclare pas de calendrier garde exactement le comportement
    historique (aucun gating, aucune fermeture de session).
  - ``SessionCalendar`` — moteur générique : fuseau, sessions par jour de
    semaine (plusieurs plages possibles → pause déjeuner), jours fériés
    déclaratifs (date fixe ou décalage par rapport à Pâques), demi-séances.
  - ``BUILTIN_CALENDARS`` — un seul calendrier livré en dur : ``XPAR``
    (Euronext Paris). Tout autre place se déclare **en configuration**
    (``config.yaml › calendars``) sans toucher au code.
  - ``exchange_calendars`` est utilisé **s'il est installé** (source de vérité
    officielle, recommandée par le plan directeur) ; sinon repli automatique
    sur le moteur interne — aucune dépendance obligatoire ajoutée.

Le calendrier d'une venue se déclare dans ``venues.defs.<nom>.calendar``
(vide/absent = 24/7).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

logger = logging.getLogger(__name__)

try:                                     # stdlib depuis 3.9
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:                      # pragma: no cover — Python < 3.9
    ZoneInfo = None                      # type: ignore[misc, assignment]
    _HAS_ZONEINFO = False

try:
    import exchange_calendars as _xcals
    _HAS_XCALS = True
except Exception:                         # pragma: no cover — dépendance optionnelle
    _xcals = None
    _HAS_XCALS = False

# Marge de sécurité (en durées de bougie) au-delà de laquelle des données sont
# considérées périmées, une fois le marché rouvert.
_STALE_TF_MULTIPLIER = 3


# ── Protocole ───────────────────────────────────────────────────────────────
@runtime_checkable
class MarketCalendar(Protocol):
    """Ce que le moteur attend d'un calendrier — rien de plus."""

    name: str

    def is_open(self, ts: datetime) -> bool: ...

    def session_end(self, ts: datetime) -> Optional[datetime]: ...

    def next_open(self, ts: datetime) -> Optional[datetime]: ...

    def max_gap_seconds(self, ts: datetime, tf_seconds: int) -> float: ...

    def expected_bars_between(self, a: datetime, b: datetime,
                              tf_seconds: int) -> int: ...


def _as_utc(ts: Optional[datetime]) -> datetime:
    """Normalise en datetime **aware UTC** (naïf = supposé UTC)."""
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


# ── Barres attendues entre deux horodatages ─────────────────────────────────
# Primitive commune aux calendriers à séances. La détection de trous OHLCV
# posait jusqu'ici la question à l'envers : « quel écart tolérer ? », auquel on
# répondait par une pile d'heuristiques (marge de fraîcheur, prochaine
# ouverture, échantillonnage de l'intérieur du span). La bonne question est
# « combien de barres DEVRAIENT exister entre ces deux-là ? », et le calendrier
# sait y répondre exactement.
_WALK_LIMITE = 400          # borne de sécurité : ~1 an de séances en journalier


def _sessions_ouvertes_entre(cal, a: datetime, b: datetime) -> int:
    """Nombre d'ouvertures de séance dans l'intervalle **ouvert** ``(a, b)``.

    ``next_open(ts)`` rend ``ts`` lui-même quand le marché est déjà ouvert : il
    faut donc sortir de la séance courante (``session_end``) avant de demander
    la suivante, sinon on recompte indéfiniment la même.
    """
    n, t = 0, a
    for _ in range(_WALK_LIMITE):
        nxt = cal.next_open(t)
        if nxt is None or nxt >= b:
            return n
        if nxt > a:                      # une ouverture strictement après `a`
            n += 1
        fin = cal.session_end(nxt)
        t = fin if (fin is not None and fin > nxt) else nxt + timedelta(seconds=1)
    return n


def _secondes_ouvertes_entre(cal, a: datetime, b: datetime) -> float:
    """Secondes de séance dans ``(a, b)`` — base du compte intraday."""
    total, t = 0.0, a
    for _ in range(_WALK_LIMITE):
        if t >= b:
            break
        nxt = cal.next_open(t)
        if nxt is None or nxt >= b:
            break
        fin = cal.session_end(nxt)
        borne = min(fin, b) if fin is not None else b
        if borne > nxt:
            total += (borne - nxt).total_seconds()
        if fin is None or fin <= nxt:    # séance sans fin : rien de plus à cumuler
            break
        t = fin
    return total


def _barres_attendues(cal, a: datetime, b: datetime, tf_seconds: int) -> int:
    """Barres qui devraient exister STRICTEMENT entre ``a`` et ``b``.

    Deux régimes, parce qu'une barre journalière ne vaut pas 86 400 s de
    séance mais **une séance** :

    - ``tf >= 1 j`` → compter les séances ouvertes entre les deux barres ;
    - intraday      → temps de séance écoulé, divisé par le timeframe.
    """
    a, b = _as_utc(a), _as_utc(b)
    if b <= a or tf_seconds <= 0:
        return 0
    if tf_seconds >= 86400:
        return max(0, _sessions_ouvertes_entre(cal, a, b) - 1)
    # On compte à partir du créneau qui SUIT ``a``, pas depuis ``a`` : sinon le
    # reliquat de sa séance (``a`` à la clôture) ajoute une fraction de barre
    # qui fausse l'arrondi, et il faut ensuite corriger selon que ``a`` tombe
    # ou non dans une séance. En partant de ``a + tf``, la question posée est
    # directement la bonne — « combien de créneaux ouverts strictement après
    # ``a`` et avant ``b`` » — sans cas particulier.
    depart = a + timedelta(seconds=tf_seconds)
    if depart >= b:
        return 0
    return max(0, int(round(
        _secondes_ouvertes_entre(cal, depart, b) / tf_seconds)))


# ── 24/7 (crypto — défaut) ──────────────────────────────────────────────────
class AlwaysOpenCalendar:
    """Marché toujours ouvert. Comportement historique du bot (crypto)."""

    def __init__(self, name: str = "24x7"):
        self.name = name

    def is_open(self, ts: Optional[datetime] = None) -> bool:
        return True

    def session_end(self, ts: Optional[datetime] = None) -> Optional[datetime]:
        return None                      # pas de fin de séance : rien à fermer

    def next_open(self, ts: Optional[datetime] = None) -> Optional[datetime]:
        return _as_utc(ts)               # déjà ouvert

    def max_gap_seconds(self, ts: Optional[datetime] = None, tf_seconds: int = 3600) -> float:
        return float(max(tf_seconds, 1) * _STALE_TF_MULTIPLIER)

    def expected_bars_between(self, a: datetime, b: datetime,
                              tf_seconds: int) -> int:
        """Aucune fermeture : le compte est purement arithmétique."""
        a, b = _as_utc(a), _as_utc(b)
        if b <= a or tf_seconds <= 0:
            return 0
        return max(0, int(round((b - a).total_seconds() / tf_seconds)) - 1)

    def describe(self) -> str:
        return "24/7"


# ── Jours fériés déclaratifs ────────────────────────────────────────────────
def easter_date(year: int) -> date:
    """Dimanche de Pâques (algorithme grégorien anonyme, sans dépendance)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lu = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lu) // 451
    month, day = divmod(h + lu - 7 * m + 114, 31)
    return date(year, month, day + 1)


@dataclass(frozen=True)
class HolidayRule:
    """Règle de jour férié — soit une date fixe, soit un décalage sur Pâques.

    ``kind`` : ``"fixed"`` (``month``/``day``) ou ``"easter"`` (``offset``
    jours par rapport au dimanche de Pâques : -2 = Vendredi saint, +1 = lundi
    de Pâques).
    """
    kind: str
    month: int = 0
    day: int = 0
    offset: int = 0
    label: str = ""

    def resolve(self, year: int) -> Optional[date]:
        if self.kind == "fixed":
            try:
                return date(year, self.month, self.day)
            except ValueError:
                return None
        if self.kind == "easter":
            return easter_date(year) + timedelta(days=self.offset)
        logger.warning(f"[Calendar] Règle de férié inconnue : {self.kind}")
        return None

    @staticmethod
    def from_spec(spec) -> Optional["HolidayRule"]:
        """Construit une règle depuis la configuration YAML.

        Formes acceptées (toutes équivalentes côté moteur) :
          - ``"01-01"``                       → date fixe
          - ``{fixed: "12-25", label: Noël}`` → date fixe
          - ``{easter: -2, label: Vendredi saint}``
        """
        if isinstance(spec, str):
            parts = spec.split("-")
            if len(parts) != 2:
                logger.warning(f"[Calendar] Férié ignoré (format MM-DD attendu) : {spec}")
                return None
            try:
                return HolidayRule("fixed", month=int(parts[0]), day=int(parts[1]))
            except ValueError:
                logger.warning(f"[Calendar] Férié ignoré (non numérique) : {spec}")
                return None
        if isinstance(spec, dict):
            label = str(spec.get("label", ""))
            if "easter" in spec:
                return HolidayRule("easter", offset=int(spec["easter"]), label=label)
            if "fixed" in spec:
                base = HolidayRule.from_spec(str(spec["fixed"]))
                return HolidayRule("fixed", base.month, base.day, label=label) if base else None
        logger.warning(f"[Calendar] Spécification de férié non reconnue : {spec!r}")
        return None


# ── Moteur générique ────────────────────────────────────────────────────────
@dataclass
class SessionCalendar:
    """Calendrier à séances fixes, jours fériés et demi-séances.

    ``sessions`` : jour de semaine (0 = lundi … 6 = dimanche) → liste de plages
    ``(ouverture, fermeture)`` en heure **locale de la place**. Plusieurs
    plages par jour sont acceptées (marchés avec pause déjeuner).

    ``early_closes`` : ``(mois, jour)`` → heure de fermeture anticipée, qui
    remplace la fermeture de la **dernière** plage du jour (24 et 31 décembre
    sur Euronext, par exemple).
    """
    name: str
    tz_name: str = "UTC"
    sessions: Dict[int, List[Tuple[time, time]]] = field(default_factory=dict)
    holidays: List[HolidayRule] = field(default_factory=list)
    early_closes: Dict[Tuple[int, int], time] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._tz = _resolve_tz(self.tz_name)
        self._holiday_cache: Dict[int, set] = {}
        self._cache_lock = threading.Lock()

    # ── Séances du jour ────────────────────────────────────────────────────
    def _holidays_for(self, year: int) -> set:
        with self._cache_lock:
            cached = self._holiday_cache.get(year)
            if cached is not None:
                return cached
        resolved = {d for d in (r.resolve(year) for r in self.holidays) if d is not None}
        with self._cache_lock:
            self._holiday_cache[year] = resolved
        return resolved

    def _windows(self, local_day: date) -> List[Tuple[datetime, datetime]]:
        """Plages d'ouverture (aware, fuseau de la place) du jour donné."""
        if local_day in self._holidays_for(local_day.year):
            return []
        ranges = self.sessions.get(local_day.weekday()) or []
        if not ranges:
            return []
        early = self.early_closes.get((local_day.month, local_day.day))
        out: List[Tuple[datetime, datetime]] = []
        for idx, (start, end) in enumerate(ranges):
            if early is not None and idx == len(ranges) - 1:
                end = early
            if end <= start:             # plage vidée par la demi-séance
                continue
            out.append((
                datetime.combine(local_day, start, tzinfo=self._tz),
                datetime.combine(local_day, end,   tzinfo=self._tz),
            ))
        return out

    # ── API MarketCalendar ─────────────────────────────────────────────────
    def is_open(self, ts: Optional[datetime] = None) -> bool:
        local = _as_utc(ts).astimezone(self._tz)
        return any(start <= local < end for start, end in self._windows(local.date()))

    def session_end(self, ts: Optional[datetime] = None) -> Optional[datetime]:
        """Fin de la séance **en cours** (UTC), ou None si le marché est fermé."""
        local = _as_utc(ts).astimezone(self._tz)
        for start, end in self._windows(local.date()):
            if start <= local < end:
                return end.astimezone(timezone.utc)
        return None

    def next_open(self, ts: Optional[datetime] = None, max_days: int = 15) -> Optional[datetime]:
        """Prochaine ouverture (UTC) — ``ts`` lui-même si le marché est ouvert.

        ``max_days`` borne la recherche : au-delà (calendrier mal configuré,
        sessions vides), on renonce plutôt que de boucler.
        """
        now_utc = _as_utc(ts)
        local = now_utc.astimezone(self._tz)
        for offset in range(max_days + 1):
            day = (local + timedelta(days=offset)).date()
            for start, end in self._windows(day):
                if local < start:
                    return start.astimezone(timezone.utc)
                if start <= local < end:
                    return now_utc
        logger.warning(
            f"[Calendar] {self.name} — aucune ouverture trouvée sous {max_days} jours."
        )
        return None

    def max_gap_seconds(self, ts: Optional[datetime] = None, tf_seconds: int = 3600) -> float:
        """Ancienneté tolérée des données à l'instant ``ts``.

        Marché fermé : le temps restant jusqu'à la prochaine ouverture **plus**
        la marge habituelle. Sans cela, un week-end de 65 h ferait passer pour
        « données périmées » un cache parfaitement à jour.
        """
        now_utc = _as_utc(ts)
        margin = float(max(tf_seconds, 1) * _STALE_TF_MULTIPLIER)
        nxt = self.next_open(now_utc)
        if nxt is None or nxt <= now_utc:
            return margin
        return (nxt - now_utc).total_seconds() + margin

    def expected_bars_between(self, a: datetime, b: datetime,
                              tf_seconds: int) -> int:
        return _barres_attendues(self, a, b, tf_seconds)

    def describe(self) -> str:
        days = sorted(self.sessions)
        if not days:
            return f"{self.name} (aucune séance)"
        first = self.sessions[days[0]][0]
        last = self.sessions[days[0]][-1]
        return (f"{self.name} {self.tz_name} "
                f"{first[0].strftime('%H:%M')}-{last[1].strftime('%H:%M')} "
                f"j{days[0]}-j{days[-1]}")


def _resolve_tz(tz_name: str):
    if not tz_name or tz_name.upper() == "UTC":
        return timezone.utc
    if not _HAS_ZONEINFO:                # pragma: no cover
        logger.warning(f"[Calendar] zoneinfo indisponible — '{tz_name}' traité comme UTC.")
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception as e:
        logger.warning(f"[Calendar] Fuseau '{tz_name}' inconnu ({e}) — repli sur UTC.")
        return timezone.utc


# ── Adaptateur exchange_calendars (si installé) ─────────────────────────────
class ExchangeCalendarsAdapter:
    """Enveloppe un calendrier ``exchange_calendars`` derrière le protocole.

    Source de vérité officielle (fériés, demi-séances, changements
    historiques d'horaires) quand la librairie est présente. Toute erreur de
    la librairie est traitée comme « je ne sais pas » et remonte au moteur
    interne via :func:`get_calendar`, jamais comme un marché fermé.
    """

    def __init__(self, code: str, fallback: "SessionCalendar | AlwaysOpenCalendar"):
        self.name = code
        self._fallback = fallback
        self._cal = _xcals.get_calendar(code)   # peut lever → géré par l'appelant

    def _minute_is_open(self, ts: datetime) -> Optional[bool]:
        try:
            return bool(self._cal.is_open_on_minute(_as_utc(ts)))
        except Exception as e:
            logger.debug(f"[Calendar] {self.name} is_open_on_minute KO : {e}")
            return None

    def is_open(self, ts: Optional[datetime] = None) -> bool:
        res = self._minute_is_open(_as_utc(ts))
        return self._fallback.is_open(ts) if res is None else res

    def session_end(self, ts: Optional[datetime] = None) -> Optional[datetime]:
        now = _as_utc(ts)
        if not self.is_open(now):
            return None
        try:
            close = self._cal.previous_close(now + timedelta(minutes=1))
            end = close.to_pydatetime() if hasattr(close, "to_pydatetime") else close
            end = _as_utc(end)
            if end > now:
                return end
            nxt = self._cal.next_close(now)
            nxt = nxt.to_pydatetime() if hasattr(nxt, "to_pydatetime") else nxt
            return _as_utc(nxt)
        except Exception as e:
            logger.debug(f"[Calendar] {self.name} session_end KO : {e}")
            return self._fallback.session_end(ts)

    def next_open(self, ts: Optional[datetime] = None) -> Optional[datetime]:
        now = _as_utc(ts)
        if self.is_open(now):
            return now
        try:
            nxt = self._cal.next_open(now)
            nxt = nxt.to_pydatetime() if hasattr(nxt, "to_pydatetime") else nxt
            return _as_utc(nxt)
        except Exception as e:
            logger.debug(f"[Calendar] {self.name} next_open KO : {e}")
            return self._fallback.next_open(ts)

    def max_gap_seconds(self, ts: Optional[datetime] = None, tf_seconds: int = 3600) -> float:
        now = _as_utc(ts)
        margin = float(max(tf_seconds, 1) * _STALE_TF_MULTIPLIER)
        nxt = self.next_open(now)
        if nxt is None or nxt <= now:
            return margin
        return (nxt - now).total_seconds() + margin

    def expected_bars_between(self, a: datetime, b: datetime,
                              tf_seconds: int) -> int:
        return _barres_attendues(self, a, b, tf_seconds)

    def describe(self) -> str:
        return f"{self.name} (exchange_calendars)"


# ── Calendriers livrés ──────────────────────────────────────────────────────
def _weekday_sessions(open_h: int, open_m: int, close_h: int, close_m: int
                      ) -> Dict[int, List[Tuple[time, time]]]:
    """Séance unique du lundi au vendredi (cas de l'écrasante majorité)."""
    window = [(time(open_h, open_m), time(close_h, close_m))]
    return {d: list(window) for d in range(0, 5)}


# Euronext Paris — 09:00-17:30 CET/CEST, lundi-vendredi.
# Fériés Euronext (liste officielle, volontairement plus courte que les fériés
# français : la Bourse cote le 8 mai, le 14 juillet, le 15 août, les 1er et
# 11 novembre). Demi-séances les 24 et 31 décembre (clôture 14:05).
XPAR_CALENDAR = SessionCalendar(
    name="XPAR",
    tz_name="Europe/Paris",
    sessions=_weekday_sessions(9, 0, 17, 30),
    holidays=[
        HolidayRule("fixed", 1, 1, label="Jour de l'an"),
        HolidayRule("easter", offset=-2, label="Vendredi saint"),
        HolidayRule("easter", offset=1, label="Lundi de Pâques"),
        HolidayRule("fixed", 5, 1, label="Fête du travail"),
        HolidayRule("fixed", 12, 25, label="Noël"),
        HolidayRule("fixed", 12, 26, label="Saint-Étienne"),
    ],
    early_closes={(12, 24): time(14, 5), (12, 31): time(14, 5)},
)

BUILTIN_CALENDARS: Dict[str, "SessionCalendar | AlwaysOpenCalendar"] = {
    "XPAR": XPAR_CALENDAR,
    "24X7": AlwaysOpenCalendar(),
}

ALWAYS_OPEN = AlwaysOpenCalendar()

_registry: Dict[str, object] = {}
_registry_lock = threading.Lock()


def calendar_from_spec(name: str, spec: dict) -> SessionCalendar:
    """Construit un calendrier depuis ``config.yaml › calendars.<name>``.

    Format (tout est optionnel sauf ``sessions``) ::

        calendars:
          XAMS:
            timezone: Europe/Amsterdam
            days: [0, 1, 2, 3, 4]          # 0 = lundi
            sessions: ["09:00-17:30"]      # plusieurs plages autorisées
            holidays: ["01-01", {easter: -2}, {fixed: "12-25"}]
            early_closes: {"12-24": "14:05"}
    """
    tz_name = str(spec.get("timezone") or spec.get("tz") or "UTC")
    days: Sequence[int] = spec.get("days") or list(range(0, 5))
    windows: List[Tuple[time, time]] = []
    for rng in (spec.get("sessions") or ["09:00-17:30"]):
        try:
            start_s, end_s = str(rng).split("-", 1)
            windows.append((_parse_hhmm(start_s), _parse_hhmm(end_s)))
        except Exception as e:
            logger.warning(f"[Calendar] {name} — plage '{rng}' ignorée : {e}")
    sessions = {int(d): list(windows) for d in days if windows}

    holidays = [r for r in (HolidayRule.from_spec(h) for h in (spec.get("holidays") or []))
                if r is not None]

    early: Dict[Tuple[int, int], time] = {}
    for key, value in (spec.get("early_closes") or {}).items():
        try:
            month, day = str(key).split("-", 1)
            early[(int(month), int(day))] = _parse_hhmm(str(value))
        except Exception as e:
            logger.warning(f"[Calendar] {name} — demi-séance '{key}' ignorée : {e}")

    return SessionCalendar(name=name, tz_name=tz_name, sessions=sessions,
                           holidays=holidays, early_closes=early)


def _parse_hhmm(raw: str) -> time:
    hh, mm = str(raw).strip().split(":", 1)
    return time(int(hh), int(mm))


def get_calendar(name: Optional[str], cfg: Optional[dict] = None) -> MarketCalendar:
    """Résout un calendrier par son code. Vide/inconnu → 24/7 (crypto).

    Précédence : ``config.yaml › calendars`` (déclaratif, sans code) >
    ``exchange_calendars`` s'il est installé > calendrier livré en dur > 24/7.
    Les instances sont mémorisées : un calendrier est immuable et son coût de
    construction (fériés, fuseau) n'a pas à être payé à chaque cycle.
    """
    code = (name or "").strip().upper()
    if not code or code in ("24X7", "24/7", "NONE", "ALWAYS"):
        return ALWAYS_OPEN

    with _registry_lock:
        cached = _registry.get(code)
    if cached is not None:
        return cached                                          # type: ignore[return-value]

    declared = ((cfg or {}).get("calendars") or {}).get(code) \
        or ((cfg or {}).get("calendars") or {}).get(name or "")
    if declared:
        cal: object = calendar_from_spec(code, declared)
    else:
        builtin = BUILTIN_CALENDARS.get(code)
        cal = builtin if builtin is not None else None
        if _HAS_XCALS:
            try:
                cal = ExchangeCalendarsAdapter(code, builtin or ALWAYS_OPEN)
            except Exception as e:
                logger.info(
                    f"[Calendar] exchange_calendars ne connaît pas '{code}' ({e}) "
                    f"— repli sur le calendrier interne."
                )
        if cal is None:
            logger.warning(
                f"[Calendar] Calendrier '{code}' inconnu — marché traité comme "
                f"24/7. Déclarez-le dans config.yaml › calendars pour le corriger."
            )
            return ALWAYS_OPEN

    with _registry_lock:
        _registry.setdefault(code, cal)
        cal = _registry[code]
    logger.info(f"[Calendar] '{code}' résolu → {getattr(cal, 'describe', lambda: code)()}")
    return cal                                                 # type: ignore[return-value]


def clear_cache() -> None:
    """Vide le registre mémoïsé (tests, rechargement de configuration)."""
    with _registry_lock:
        _registry.clear()
