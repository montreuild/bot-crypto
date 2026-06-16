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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_GEN_PATH = os.path.join("data", "bot_generations.json")
_lock = threading.Lock()

MARKET_TYPES = ("spot", "margin", "perp")


# ── Venue ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Venue:
    """Modèle de marché + exchange. Attribut par bot (cf. doc §4)."""
    name: str
    market_type: str = "spot"          # spot | margin | perp
    exchange: str = "binance"
    margin_mode: Optional[str] = None  # isolated | cross | None (spot)
    max_leverage: float = 1.0
    hedge_mode: bool = False           # perp hedge mode → netting natif (Phase 5)

    def describe(self) -> str:
        lev = f"×{self.max_leverage:g}" if self.max_leverage and self.max_leverage > 1 else "1×"
        mm = f"/{self.margin_mode}" if self.margin_mode else ""
        hm = " hedge" if self.hedge_mode else ""
        return f"{self.exchange}:{self.market_type}{mm} {lev}{hm}"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "market_type": self.market_type,
            "exchange": self.exchange, "margin_mode": self.margin_mode,
            "max_leverage": self.max_leverage, "hedge_mode": self.hedge_mode,
        }


def default_venue_from_cfg(cfg: dict) -> Venue:
    """Venue dérivée des globales historiques (rétro-compatibilité)."""
    t = cfg.get("trading", {}) or {}
    mm = t.get("margin_mode")
    exch = (cfg.get("exchange", {}) or {}).get("name", "binance")
    is_margin = bool(mm) or bool((cfg.get("exchange", {}) or {}).get("margin"))
    return Venue(
        name=("margin-" + str(mm)) if is_margin else "spot",
        market_type="margin" if is_margin else "spot",
        exchange=exch,
        margin_mode=mm,
        max_leverage=float(t.get("max_leverage", 1) or 1),
    )


def resolve_venue(cfg: dict, strategy: Optional[str] = None,
                  tf: Optional[str] = None) -> Venue:
    """Résout la venue d'un bot.

    Précédence : ``venues.assign["strategy::tf"]`` > ``venues.assign[strategy]``
    > ``venues.default`` > venue dérivée des globales (``default_venue_from_cfg``).
    """
    venues = cfg.get("venues") or {}
    defs = venues.get("defs") or {}
    assign = venues.get("assign") or {}
    slot_key = f"{strategy}::{tf}" if strategy and tf else None

    vname = None
    if slot_key and slot_key in assign:
        vname = assign[slot_key]
    elif strategy and strategy in assign:
        vname = assign[strategy]
    elif venues.get("default"):
        vname = venues["default"]

    if vname and vname in defs:
        d = defs[vname] or {}
        mt = d.get("market_type", "spot")
        return Venue(
            name=vname,
            market_type=mt if mt in MARKET_TYPES else "spot",
            exchange=d.get("exchange", (cfg.get("exchange", {}) or {}).get("name", "binance")),
            margin_mode=d.get("margin_mode"),
            max_leverage=float(d.get("max_leverage", 1) or 1),
            hedge_mode=bool(d.get("hedge_mode", False)),
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

    @property
    def slot_key(self) -> str:
        return f"{self.strategy}::{self.timeframe}"

    @property
    def bot_id(self) -> str:
        """Identité unique anti-collision : strat::tf::hash8::gN@venue."""
        return (f"{self.strategy}::{self.timeframe}::"
                f"{self.params_hash[:8]}::g{self.generation}@{self.venue.name}")

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "slot_key": self.slot_key,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "params_hash": self.params_hash,
            "generation": self.generation,
            "venue": self.venue.to_dict(),
            "created_at": self.created_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def register_identity(strategy: str, timeframe: str, params: dict,
                      cfg: dict) -> BotIdentity:
    """Identité du bot, en incrémentant la **génération** si les params ont changé.

    À appeler quand un bot est (re)créé/redoté (typiquement après application
    d'une optimisation). Si le hash des params diffère du dernier enregistré pour
    ce slot, la génération monotone est incrémentée et persistée (anti-collision).
    """
    ph = params_hash(params)
    slot_key = f"{strategy}::{timeframe}"
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
                       resolve_venue(cfg, strategy, timeframe), created_at=_now())


def peek_identity(strategy: str, timeframe: str, params: dict,
                  cfg: dict) -> BotIdentity:
    """Identité **sans** effet de bord (lecture seule, ne touche pas la génération).

    Utile pour l'affichage : la génération courante est celle persistée (ou 1 par
    défaut si jamais enregistrée).
    """
    ph = params_hash(params)
    slot_key = f"{strategy}::{timeframe}"
    rec = _load_generations().get(slot_key) or {}
    gen = int(rec.get("generation", 1)) if rec.get("params_hash") == ph else \
        int(rec.get("generation", 0)) + 1
    return BotIdentity(strategy, timeframe, ph, gen,
                       resolve_venue(cfg, strategy, timeframe), created_at="")
