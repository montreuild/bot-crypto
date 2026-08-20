"""MarketHoursMixin — la boucle live cesse de supposer un marché 24/7 (G2).

Le point de couplage le plus structurant du plan directeur : ``_cycle()``
scanne, score et ouvre sans jamais se demander si la place est ouverte. C'est
sans conséquence en crypto ; sur Euronext, cela veut dire scorer toute la nuit
sur la même bougie de clôture, et porter en fin de séance un risque overnight
que ni le backtest ni le trailing stop ne modélisent.

Trois comportements, tous **inertes par défaut** (une venue sans ``calendar``
utilise ``AlwaysOpenCalendar`` et rien ne change) :

* ``_tradable_symbols`` — filtre les symboles dont le marché est fermé, avec
  un log throttlé (sinon un cycle de 60 s inonderait le journal la nuit) ;
* ``_market_open`` — garde-fou par signal, au cas où le filtre serait contourné
  (ouverture directe par ``_scan_symbol_strategy``, appel API…) ;
* ``_close_positions_at_session_end`` — solde les positions des venues
  déclarant ``close_at_session_end`` avant la clôture.

Les positions déjà ouvertes restent **gérées** marché fermé : le trailing doit
pouvoir se recalculer et un stop touché sur un gap d'ouverture doit être
constaté. Seules les *entrées* sont bloquées.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.bot_identity import resolve_venue
from app.core.market_calendar import get_calendar
from app.core.provider_router import market_calendar_for
from app.live.protocols import LiveHost

logger = logging.getLogger(__name__)

#: Un log « marché fermé » par symbole toutes les N secondes.
_CLOSED_LOG_INTERVAL = 900.0


class MarketHoursMixin(LiveHost):
    """Gating horaire de la boucle live. Requiert ``self.cfg`` (et, pour la
    clôture de séance, ``self.open_positions`` / ``self._close_position`` /
    ``self._safe_ticker``)."""

    # ── Résolution du calendrier ───────────────────────────────────────────

    def _market_calendar(self, symbol: str, strategy: str | None = None, tf: str | None = None):
        """Calendrier applicable au symbole, mémoïsé **par venue**.

        La résolution elle-même vit dans ``provider_router.market_calendar_for``
        (source unique) ; le cache est ici parce que c'est la boucle live qui
        appelle, une fois par symbole et par cycle.
        """
        cache: Dict[str, object] = self.__dict__.setdefault("_calendar_cache", {})
        venue_name = resolve_venue(self.cfg, strategy, tf, symbol).name
        cached = cache.get(venue_name)
        if cached is None:
            cached = market_calendar_for(self.cfg, symbol, strategy, tf)
            cache[venue_name] = cached
        return cached

    def _market_open(self, symbol: str, strategy: str | None = None, tf: str | None = None,
                     now: Optional[datetime] = None) -> bool:
        """Le marché du symbole accepte-t-il des ordres maintenant ?

        Toute erreur de calendrier est traitée comme « ouvert » : un
        calendrier cassé ne doit pas geler silencieusement le trading.
        """
        try:
            return bool(self._market_calendar(symbol, strategy, tf).is_open(now))
        except Exception as e:
            logger.warning(f"[MarketHours] {symbol} — calendrier KO ({e}), marché supposé ouvert.")
            return True

    # ── Filtrage du scan ───────────────────────────────────────────────────

    def _tradable_symbols(self, symbols: List[str],
                          now: Optional[datetime] = None) -> List[str]:
        """Retire les symboles dont la place est fermée (log throttlé)."""
        if not symbols:
            return []
        open_syms: list[str] = []
        closed: list[str] = []
        for sym in symbols:
            (open_syms if self._market_open(sym, now=now) else closed).append(sym)
        if closed:
            self._log_closed(closed)
        return open_syms

    def _log_closed(self, closed: List[str]) -> None:
        seen: Dict[str, float] = self.__dict__.setdefault("_closed_logged_at", {})
        now = time.time()
        due = [s for s in closed if now - seen.get(s, 0.0) > _CLOSED_LOG_INTERVAL]
        if not due:
            return
        for sym in due:
            seen[sym] = now
        sample = ", ".join(due[:5]) + ("…" if len(due) > 5 else "")
        nxt = ""
        try:
            reopen = self._market_calendar(due[0]).next_open(datetime.now(timezone.utc))
            if reopen:
                nxt = f" — réouverture {reopen.strftime('%Y-%m-%d %H:%M UTC')}"
        except Exception as e:
            logger.debug(f"[MarketHours] next_open indisponible : {e}")
        logger.info(f"[MarketHours] {len(due)} symbole(s) hors séance : {sample}{nxt}")

    # ── Clôture avant fin de séance ────────────────────────────────────────

    def _close_positions_at_session_end(self, now: Optional[datetime] = None) -> int:
        """Solde les positions des venues qui refusent le risque overnight.

        Déclenché ``close_before_close_min`` minutes avant la fin de la séance
        en cours. Retourne le nombre de positions clôturées (0 en crypto : la
        venue par défaut n'a pas de fin de séance).
        """
        positions = list(getattr(self, "open_positions", {}).items())
        if not positions:
            return 0
        now = now or datetime.now(timezone.utc)
        closed = 0
        for pos_id, pos in positions:
            if not isinstance(pos, dict) or pos.get("_reserved"):
                continue
            symbol = pos.get("symbol")
            if not symbol:
                continue
            venue = resolve_venue(self.cfg, pos.get("strategy"),
                                  pos.get("timeframe"), symbol)
            if not venue.close_at_session_end:
                continue
            try:
                end = get_calendar(venue.calendar, self.cfg).session_end(now)
            except Exception as e:
                logger.warning(f"[MarketHours] {symbol} — session_end KO : {e}")
                continue
            if end is None:
                continue          # hors séance : le gap est déjà passé, rien à couper
            remaining_min = (end - now).total_seconds() / 60.0
            if remaining_min > venue.close_before_close_min:
                continue
            price = self._session_close_price(pos)
            if price is None:
                continue
            logger.info(
                f"[MarketHours] {symbol} — fin de séance dans "
                f"{max(remaining_min, 0):.1f} min : clôture (venue '{venue.name}')."
            )
            try:
                self._close_position(pos_id, price, exit_reason="session_end")
                closed += 1
            except Exception as e:
                logger.error(f"[MarketHours] Clôture de séance {symbol} KO : {e}",
                             exc_info=True)
        return closed

    def _session_close_price(self, pos: dict) -> Optional[float]:
        """Prix de référence pour une clôture de séance (ticker, sinon entrée)."""
        try:
            ticker = self._safe_ticker(pos["symbol"])
            price = float((ticker or {}).get("last") or 0)
            if price > 0:
                return price
        except Exception as e:
            logger.debug(f"[MarketHours] ticker {pos.get('symbol')} : {e}")
        entry = float(pos.get("entry", 0) or 0)
        return entry if entry > 0 else None
