"""Contrats des mixins live — ARCH-04.

Les mixins lisent ``self.exchange``, ``self.cfg``, ``self.ledger``… fournis
par ``LiveTrader.__init__``. Sans annotation, mypy compte ~340 erreurs
« has no attribute ». Un Protocol par famille rend le commentaire de
module vérifiable.
"""
from __future__ import annotations

from typing import Any


class LiveHost:
    """Mixin d'annotations — pas un Protocol (évite de transformer
    les mixins en Protocol via le MRO)."""
    exchange: Any
    cfg: dict
    risk: Any
    notif: Any
    scanner: Any
    capital_display: float
    _capital_lock: Any
    _paper_base: float
    open_positions: dict
    signal_log: list
    _positions_lock: Any
    _trailing_cfg: dict
    _strat_thresholds: dict
    threshold: float
    ohlcv_cache: Any
    ledger: Any
    rejections: Any
    envelopes: dict
    tf: str
    interval: Any
    SessionLocal: Any
    _loaded_strategies: dict
    strat_params: dict
    _margin_interest: dict
    _loss_notified: set
    _cooldown: dict
    _pre_execution_check: Any
    _safe_ticker: Any
    _get_ohlcv: Any
    _paper_slippage_fraction: Any
    _close_position: Any
    _exchange_stops_enabled: Any
    _adopt_or_place_exchange_stop: Any
    _sync_spot_balance: Any
    _cancel_exchange_stop: Any
