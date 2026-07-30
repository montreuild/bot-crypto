"""Identité de bot versionnée + venue (Phase 1 — le bot comme unité).

Un **bot** est l'unité de tout : ``(stratégie, timeframe, params figés, génération,
venue)``. Ce module fournit cette identité, stable et anti-collision :

- ``params_hash``  : empreinte des params figés (deux paramétrages différents →
  deux bots différents, même stratégie/timeframe).
- ``generation``   : compteur **monotone** par slot ``strategy::tf``, incrémenté
  **uniquement** quand les params changent (re-optimisation → nouveau bot).
  Persisté dans ``data/bot_generations.json`` pour survivre aux redémarrages.
- ``venue``        : modèle de marché (spot / margin / perp) **+** exchange,
  attribut **par bot**. Remplace les globales ``trading.margin_mode`` /
  ``trading.max_leverage`` de ``config.yaml`` : par défaut on dérive la venue de
  ces globales (rétro-compatibilité), mais chaque bot peut être assigné à une
  venue déclarée dans ``config.yaml › venues`` (ex. « perp hedge OKX » en Phase 5).

Aucune logique de trading ici : c'est de l'identité et de la métadonnée.
"""
import hashlib
import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_GEN_PATH = os.path.join("data", "bot_generations.json")
_lock = threading.Lock()

MARKET_TYPES = ("spot", "margin", "perp")


# ── Venue ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Venue:
    """Modèle de marché + exchange. Attribut par bot (cf. doc §4).

    Champs S2-02 (généralisation multi-actifs) : ``asset_class``,
    ``quote_currency``, ``tick_size``, ``lot_size``, ``fractional``,
    ``allow_short`` — tous par défaut alignés sur le comportement crypto
    historique (rétro-compatibilité totale des ``venues.defs`` existants qui
    ne renseignent pas ces clés).

    Champs G2 (actions en paper) : ``calendar`` (code de calendrier de marché,
    vide = 24/7), ``data_provider`` (fournisseur de données, vide = l'exchange
    ccxt par défaut), ``can_execute`` (False = venue **data-only** : le bot
    calcule le trade et **notifie** au lieu d'envoyer un ordre),
    ``close_at_session_end`` + ``close_before_close_min`` (fermer avant la
    clôture plutôt que de porter le risque overnight), et le modèle de coûts
    (``fee_pct``, ``fee_fixed``, ``fee_min``, ``transaction_tax_pct``,
    ``tax_on_buy_only``, ``min_notional``). Tous neutres par défaut : une
    venue crypto se comporte exactement comme avant.
    """
    name: str
    market_type: str = "spot"          # spot | margin | perp
    exchange: str = "okx"
    margin_mode: Optional[str] = None  # isolated | cross | None (spot)
    max_leverage: float = 1.0
    hedge_mode: bool = False           # perp hedge mode → netting natif (Phase 5)
    asset_class: str = "crypto"        # crypto | equity
    quote_currency: str = "USDC"
    tick_size: float = 0.0             # 0.0 = pas de contrainte (crypto quasi-continu)
    lot_size: float = 0.0              # 0.0 = pas de contrainte (fractionnable)
    fractional: bool = True            # False = quantité entière requise (actions)
    allow_short: bool = True
    # ── G2 : horaires, provider, exécution, coûts ──────────────────────────
    calendar: str = ""                 # "" = 24/7 ; ex. "XPAR" (Euronext Paris)
    data_provider: str = ""            # "" = exchange ccxt ; ex. "yfinance"
    can_execute: bool = True           # False = data-only → notification de trade
    close_at_session_end: bool = False # fermer les positions avant la clôture
    close_before_close_min: float = 5.0
    fee_pct: Optional[float] = None    # None = repli sur trading.taker_fee
    fee_fixed: float = 0.0             # commission fixe par ordre (courtiers actions)
    fee_min: float = 0.0               # plancher de commission par ordre
    transaction_tax_pct: float = 0.0   # ex. TTF française sur les achats d'actions
    tax_on_buy_only: bool = True       # la TTF ne frappe que les acquisitions
    min_notional: float = 0.0          # notionnel minimum accepté par la venue

    def describe(self) -> str:
        lev = f"×{self.max_leverage:g}" if self.max_leverage and self.max_leverage > 1 else "1×"
        mm = f"/{self.margin_mode}" if self.margin_mode else ""
        hm = " hedge" if self.hedge_mode else ""
        cal = f" [{self.calendar}]" if self.calendar else ""
        exe = "" if self.can_execute else " (data-only)"
        return f"{self.exchange}:{self.market_type}{mm} {lev}{hm}{cal}{exe}"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "market_type": self.market_type,
            "exchange": self.exchange, "margin_mode": self.margin_mode,
            "max_leverage": self.max_leverage, "hedge_mode": self.hedge_mode,
            "asset_class": self.asset_class, "quote_currency": self.quote_currency,
            "tick_size": self.tick_size, "lot_size": self.lot_size,
            "fractional": self.fractional, "allow_short": self.allow_short,
            "calendar": self.calendar, "data_provider": self.data_provider,
            "can_execute": self.can_execute,
            "close_at_session_end": self.close_at_session_end,
            "close_before_close_min": self.close_before_close_min,
            "fee_pct": self.fee_pct, "fee_fixed": self.fee_fixed,
            "fee_min": self.fee_min,
            "transaction_tax_pct": self.transaction_tax_pct,
            "tax_on_buy_only": self.tax_on_buy_only,
            "min_notional": self.min_notional,
        }


def default_venue_from_cfg(cfg: dict) -> Venue:
    """Venue dérivée des globales historiques (rétro-compatibilité)."""
    t = cfg.get("trading", {}) or {}
    mm = t.get("margin_mode")
    exch = (cfg.get("exchange", {}) or {}).get("name", "okx")
    is_margin = bool(mm) or bool((cfg.get("exchange", {}) or {}).get("margin"))
    return Venue(
        name=("margin-" + str(mm)) if is_margin else "spot",
        market_type="margin" if is_margin else "spot",
        exchange=exch,
        margin_mode=mm,
        max_leverage=float(t.get("max_leverage", 1) or 1),
    )


def _universe_venue_for(cfg: dict, symbol: str) -> Optional[str]:
    """Venue déclarée par l'univers qui liste ``symbol``, ou ``None``.

    Ne consulte que les univers effectivement activés dans
    ``scanner.universe`` : un fichier présent dans ``data/universe/`` mais non
    référencé ne doit router aucun symbole. Best-effort — une erreur de
    lecture d'univers ne doit jamais empêcher de résoudre une venue crypto.
    """
    names = ((cfg.get("scanner") or {}).get("universe")) or []
    if not names:
        return None
    try:
        from app.core.universe import symbol_venue_index
        return symbol_venue_index(names).get(symbol)
    except Exception as e:  # pragma: no cover — défensif
        logger.debug(f"[Venue] index d'univers illisible : {e}")
        return None


def resolve_venue(cfg: dict, strategy: Optional[str] = None,
                  tf: Optional[str] = None, symbol: Optional[str] = None) -> Venue:
    """Résout la venue d'un bot.

    Précédence : ``venues.assign["strategy::tf::symbol"]`` >
    ``venues.assign["strategy::tf"]`` > ``venues.assign[symbol]`` (S2-02 —
    un instrument porte sa venue indépendamment de la stratégie qui le
    trade, ex. assigner "AIR.PA" à une venue actions sans lister chaque
    stratégie) > **clé ``venue:`` de l'univers qui liste le symbole** >
    ``venues.assign[strategy]`` > ``venues.default`` > venue dérivée des
    globales (``default_venue_from_cfg``).

    L'échelon « univers » (G2) évite d'écrire 120 lignes d'``assign`` pour
    activer le SBF 120 : le fichier ``data/universe/sbf120.yaml`` déclare déjà
    ``venue: euronext-paper``, cette information était lue (``universe_venue``)
    mais n'atteignait pas la résolution. Sans elle, activer ``scanner.universe``
    ajoutait bien les 120 instruments au scanner — mais tous résolus sur la
    venue crypto par défaut, donc cherchés chez OKX et jamais chez le provider
    actions. Un ``assign`` explicite continue de primer : le déclaratif ne
    reprend jamais la main sur ce qu'un opérateur a écrit à la main.
    """
    venues = cfg.get("venues") or {}
    defs = venues.get("defs") or {}
    assign = venues.get("assign") or {}
    slot_sym = (f"{strategy}::{tf}::{symbol}"
                if strategy and tf and symbol else None)
    slot_key = f"{strategy}::{tf}" if strategy and tf else None

    uni_venue = _universe_venue_for(cfg, symbol) if symbol else None

    vname = None
    if slot_sym and slot_sym in assign:
        vname = assign[slot_sym]
    elif slot_key and slot_key in assign:
        vname = assign[slot_key]
    elif symbol and symbol in assign:
        vname = assign[symbol]
    elif uni_venue:
        vname = uni_venue
    elif strategy and strategy in assign:
        vname = assign[strategy]
    elif venues.get("default"):
        vname = venues["default"]

    if vname and vname in defs:
        d = defs[vname] or {}
        mt = d.get("market_type", "spot")
        _fee_pct = d.get("fee_pct")
        return Venue(
            name=vname,
            market_type=mt if mt in MARKET_TYPES else "spot",
            exchange=d.get("exchange", (cfg.get("exchange", {}) or {}).get("name", "okx")),
            margin_mode=d.get("margin_mode"),
            max_leverage=float(d.get("max_leverage", 1) or 1),
            hedge_mode=bool(d.get("hedge_mode", False)),
            asset_class=d.get("asset_class", "crypto"),
            quote_currency=d.get("quote_currency", "USDC"),
            tick_size=float(d.get("tick_size", 0.0) or 0.0),
            lot_size=float(d.get("lot_size", 0.0) or 0.0),
            fractional=bool(d.get("fractional", True)),
            allow_short=bool(d.get("allow_short", True)),
            calendar=str(d.get("calendar", "") or ""),
            data_provider=str(d.get("data_provider", "") or ""),
            can_execute=bool(d.get("can_execute", True)),
            close_at_session_end=bool(d.get("close_at_session_end", False)),
            close_before_close_min=float(d.get("close_before_close_min", 5.0) or 0.0),
            fee_pct=(None if _fee_pct is None else float(_fee_pct)),
            fee_fixed=float(d.get("fee_fixed", 0.0) or 0.0),
            fee_min=float(d.get("fee_min", 0.0) or 0.0),
            transaction_tax_pct=float(d.get("transaction_tax_pct", 0.0) or 0.0),
            tax_on_buy_only=bool(d.get("tax_on_buy_only", True)),
            min_notional=float(d.get("min_notional", 0.0) or 0.0),
        )
    return default_venue_from_cfg(cfg)


# ── Empreinte des params ─────────────────────────────────────────────────────
def _clean(params: dict) -> dict:
    """Normalise les params pour un hash stable : retire None/NaN, trie les clés."""
    out = {}
    for k, v in (params or {}).items():
        if v is None:
            continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            continue
        if isinstance(v, dict):
            v = _clean(v)
        out[k] = v
    return out


def params_hash(params: dict) -> str:
    """Empreinte SHA-1 stable des params figés (insensible à l'ordre des clés)."""
    payload = json.dumps(_clean(params or {}), sort_keys=True, separators=(",", ":"),
                         default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# ── Génération monotone (persistée) ──────────────────────────────────────────
def _load_generations() -> dict:
    try:
        with open(_GEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning(f"[bot_identity] lecture générations KO : {e}")
        return {}


def _save_generations(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_GEN_PATH), exist_ok=True)
        with open(_GEN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[bot_identity] écriture générations KO : {e}")


# ── BotIdentity ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BotIdentity:
    strategy: str
    timeframe: str
    params_hash: str
    generation: int
    venue: Venue
    created_at: str = ""
    symbol: str = ""

    @property
    def slot_key(self) -> str:
        base = f"{self.strategy}::{self.timeframe}"
        return f"{base}::{self.symbol}" if self.symbol else base

    @property
    def bot_id(self) -> str:
        """Identité unique anti-collision : strat::tf[::symbol]::hash8::gN@venue."""
        return (f"{self.slot_key}::"
                f"{self.params_hash[:8]}::g{self.generation}@{self.venue.name}")

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "slot_key": self.slot_key,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "symbol": self.symbol,
            "params_hash": self.params_hash,
            "generation": self.generation,
            "venue": self.venue.to_dict(),
            "created_at": self.created_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _slot_key(strategy: str, timeframe: str, symbol: str = "") -> str:
    base = f"{strategy}::{timeframe}"
    return f"{base}::{symbol}" if symbol else base


# Alias publics (V4-C : ARCH-05) — SEULS constructeurs autorisés pour les clés
# de slot/position ; ne jamais reconstruire ces formats en f-string ad hoc.
def build_slot_key(strategy: str, timeframe: str, symbol: str = "") -> str:
    """Clé de slot ``strategy::tf[::symbol]`` (symbole vide = clé héritée)."""
    return _slot_key(strategy, timeframe, symbol)


def build_pos_key(symbol: str, strategy: str, timeframe: str) -> str:
    """Clé de position ouverte ``symbol::strategy::tf`` (ordre HISTORIQUE,
    distinct du slot_key — les deux formats coexistent volontairement)."""
    return f"{symbol}::{strategy}::{timeframe}"


def parse_slot_key(slot_key: str) -> tuple:
    """Décompose ``strategy::tf[::symbol]`` → ``(strategy, tf, symbol)``.

    ``symbol`` vaut "" pour un slot hérité sans dimension symbole. Robuste aux
    symboles contenant "/" (ex. BTC/USDC) — seul "::" sépare les composantes."""
    parts = (slot_key or "").split("::")
    strategy = parts[0] if parts else ""
    tf = parts[1] if len(parts) > 1 else ""
    symbol = parts[2] if len(parts) > 2 else ""
    return strategy, tf, symbol


def register_identity(strategy: str, timeframe: str, params: dict,
                      cfg: dict, symbol: str = "") -> BotIdentity:
    """Identité du bot, en incrémentant la **génération** si les params ont changé.

    À appeler quand un bot est (re)créé/redoté (typiquement après application
    d'une optimisation). Si le hash des params diffère du dernier enregistré pour
    ce slot ``strategy::tf::symbol``, la génération monotone est incrémentée et
    persistée (anti-collision).
    """
    ph = params_hash(params)
    slot_key = _slot_key(strategy, timeframe, symbol)
    with _lock:
        data = _load_generations()
        rec = data.get(slot_key) or {}
        if rec.get("params_hash") != ph:
            gen = int(rec.get("generation", 0)) + 1
            data[slot_key] = {"generation": gen, "params_hash": ph, "updated": _now()}
            _save_generations(data)
        else:
            gen = int(rec.get("generation", 1))
    return BotIdentity(strategy, timeframe, ph, gen,
                       resolve_venue(cfg, strategy, timeframe, symbol),
                       created_at=_now(), symbol=symbol)


def peek_identity(strategy: str, timeframe: str, params: dict,
                  cfg: dict, gens: dict = None, symbol: str = "") -> BotIdentity:
    """Identité **sans** effet de bord (lecture seule, ne touche pas la génération).

    Utile pour l'affichage : la génération courante est celle persistée (ou 1 par
    défaut si jamais enregistrée). ``gens`` (dict des générations déjà chargé)
    évite une lecture disque par appel quand on itère plusieurs bots.
    """
    ph = params_hash(params)
    slot_key = _slot_key(strategy, timeframe, symbol)
    rec = (gens if gens is not None else _load_generations()).get(slot_key) or {}
    gen = int(rec.get("generation", 1)) if rec.get("params_hash") == ph else \
        int(rec.get("generation", 0)) + 1
    return BotIdentity(strategy, timeframe, ph, gen,
                       resolve_venue(cfg, strategy, timeframe, symbol),
                       created_at="", symbol=symbol)
