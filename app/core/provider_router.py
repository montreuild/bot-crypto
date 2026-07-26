"""Routage multi-provider par venue (G2).

Le bot ne connaissait qu'un seul « exchange » : l'objet ccxt passé au
``LiveTrader``, au ``MarketScanner`` et au ``CandleStore``. Faire cohabiter des
cryptos OKX et des actions Euronext demandait donc soit de propager un provider
partout (des dizaines de sites d'appel), soit de router au point d'entrée.

C'est ce que fait ``ProviderRouter`` : il **implémente la même interface** que
``RobustExchange`` et, pour chaque appel, résout la venue du symbole
(``bot_identity.resolve_venue``) puis délègue au provider déclaré par cette
venue (``venues.defs.<nom>.data_provider``). Aucun site d'appel ne change.

Deux garde-fous délibérés :

* :func:`build_market_provider` retourne l'exchange **inchangé** quand aucune
  venue ne déclare de provider alternatif — une configuration crypto ne paie
  ni indirection ni risque de régression ;
* une venue ``can_execute: false`` (data-only, cas des actions tant que G3
  n'est pas livré) ne lève pas à l'ouverture : elle retourne un ordre
  **simulé** marqué ``simulated: True``, que le chemin d'ouverture traduit en
  *notification de trade* à exécuter à la main. Le bot suit alors la position
  comme en paper, sans jamais prétendre qu'un ordre est parti.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from app.core.bot_identity import Venue, resolve_venue

logger = logging.getLogger(__name__)

#: Constructeurs de providers, par nom déclaré dans ``data_provider``.
_FACTORIES: Dict[str, str] = {
    "yfinance": "app.core.yfinance_provider:YFinanceProvider",
    "yahoo":    "app.core.yfinance_provider:YFinanceProvider",
}


def register_provider(name: str, dotted_path: str) -> None:
    """Déclare un provider supplémentaire (``"module.chemin:Classe"``).

    Permet de brancher un fournisseur maison ou payant (EOD Historical Data,
    IBKR en G3) sans modifier ce module.
    """
    _FACTORIES[name.strip().lower()] = dotted_path


def _instantiate(name: str, cfg: dict):
    path = _FACTORIES.get(name.strip().lower())
    if path is None:
        raise KeyError(
            f"Provider '{name}' inconnu — providers disponibles : "
            f"{', '.join(sorted(_FACTORIES))}. Utilisez register_provider() "
            f"pour en déclarer un autre."
        )
    module_name, _, class_name = path.partition(":")
    import importlib
    module = importlib.import_module(module_name)
    return getattr(module, class_name)(cfg)


def declared_providers(cfg: dict) -> set:
    """Noms de providers alternatifs déclarés par les venues de ``cfg``."""
    defs = ((cfg or {}).get("venues") or {}).get("defs") or {}
    return {str(d.get("data_provider")).strip()
            for d in defs.values() if isinstance(d, dict) and d.get("data_provider")}


class ProviderRouter:
    """Aiguille les appels marché vers le provider de la venue du symbole."""

    def __init__(self, cfg: dict, default_provider):
        self._cfg = cfg
        self._default = default_provider
        self._providers: Dict[str, object] = {}
        self._lock = threading.Lock()
        self._venue_cache: Dict[str, Venue] = {}

    # ── Résolution ─────────────────────────────────────────────────────────

    def venue_for(self, symbol: str) -> Venue:
        """Venue d'un symbole (mémoïsée — appelée à chaque bougie de chaque cycle)."""
        cached = self._venue_cache.get(symbol)
        if cached is None:
            cached = resolve_venue(self._cfg, symbol=symbol)
            self._venue_cache[symbol] = cached
        return cached

    def provider_for(self, symbol: str):
        """Provider de données du symbole — l'exchange par défaut si non déclaré."""
        name = self.venue_for(symbol).data_provider
        if not name:
            return self._default
        with self._lock:
            provider = self._providers.get(name)
            if provider is None:
                try:
                    provider = _instantiate(name, self._cfg)
                except Exception as e:
                    logger.error(
                        f"[Router] Provider '{name}' indisponible ({e}) — "
                        f"repli sur le provider par défaut pour {symbol}."
                    )
                    provider = self._default
                self._providers[name] = provider
        return provider

    # ── Données de marché ──────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        return self.provider_for(symbol).fetch_ohlcv(symbol, timeframe,
                                                      limit=limit, since=since)

    def fetch_ticker(self, symbol):
        return self.provider_for(symbol).fetch_ticker(symbol)

    def fetch_tickers(self, symbols=None):
        """Regroupe les symboles par provider avant d'interroger chacun d'eux."""
        if not symbols:
            return self._default.fetch_tickers()
        grouped: Dict[int, list] = {}
        providers: Dict[int, object] = {}
        for sym in symbols:
            provider = self.provider_for(sym)
            grouped.setdefault(id(provider), []).append(sym)
            providers[id(provider)] = provider
        out: dict = {}
        for pid, syms in grouped.items():
            try:
                out.update(providers[pid].fetch_tickers(syms) or {})
            except Exception as e:
                logger.warning(f"[Router] fetch_tickers KO sur {syms[:3]}… : {e}")
        return out

    def load_markets(self):
        return self._default.load_markets()

    # ── Exécution ──────────────────────────────────────────────────────────

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        """Passe l'ordre, ou le **simule** si la venue n'a pas d'exécution branchée.

        Un ordre simulé est explicitement marqué (``simulated: True``) pour que
        l'appelant notifie l'utilisateur au lieu de croire à un fill réel.
        """
        venue = self.venue_for(symbol)
        if not venue.can_execute:
            logger.warning(
                f"[Router] {symbol} — venue '{venue.name}' sans exécution branchée : "
                f"ordre {side} {amount} NON transmis, notification de trade émise."
            )
            return {
                "id": f"signal_{int(time.time() * 1000)}", "status": "closed",
                "symbol": symbol, "side": side, "amount": amount,
                "price": price or 0, "filled": amount,
                "simulated": True, "venue": venue.name,
            }
        provider = self.provider_for(symbol)
        return provider.create_order(symbol, order_type, side, amount, price, params)

    def cancel_order(self, order_id, symbol):
        if not self.venue_for(symbol).can_execute:
            return {"id": order_id, "status": "canceled", "simulated": True}
        return self.provider_for(symbol).cancel_order(order_id, symbol)

    def fetch_order(self, order_id, symbol):
        if not self.venue_for(symbol).can_execute:
            return {"id": order_id, "status": "closed", "simulated": True}
        return self.provider_for(symbol).fetch_order(order_id, symbol)

    # ── Reste de la surface : l'exchange par défaut ────────────────────────
    #
    # ``parse_timeframe``, ``milliseconds``, ``rateLimit``, ``fetch_balance*``,
    # ``paper``, ``margin``… ne dépendent pas du symbole : le compte et le
    # capital restent portés par l'exchange principal.

    def __getattr__(self, name):
        return getattr(self._default, name)


def build_market_provider(cfg: dict, exchange):
    """Point d'entrée unique : exchange nu (crypto) ou routeur (multi-actifs).

    Retourner l'objet d'origine quand aucune venue ne déclare de provider
    alternatif garantit qu'une configuration crypto existante n'emprunte
    **aucun** code nouveau.
    """
    names = declared_providers(cfg)
    if not names:
        return exchange
    logger.info(f"[Router] Providers alternatifs déclarés : {', '.join(sorted(names))}")
    return ProviderRouter(cfg, exchange)


def market_calendar_for(cfg: dict, symbol: str, strategy: Optional[str] = None,
                        tf: Optional[str] = None):
    """Calendrier de marché applicable à un symbole (24/7 par défaut)."""
    from app.core.market_calendar import get_calendar
    venue = resolve_venue(cfg, strategy, tf, symbol)
    return get_calendar(venue.calendar, cfg)
