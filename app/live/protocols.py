"""Contrats des mixins live — ARCH-04.

Les mixins lisent ``self.exchange``, ``self.cfg``, ``self.ledger``… fournis
par ``LiveTrader.__init__``. Sans annotation, mypy compte ~340 erreurs
« has no attribute ». Un Protocol par famille rend le commentaire de
module vérifiable.
"""
from __future__ import annotations

from collections import deque
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
    signal_log: deque
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
    _margin_interest: float
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
    _place_exchange_stop: Any
    _execute_order: Any
    _serialize_position: Any
    _envelope_for: Any
    _venue_for: Any
    # Boucle de scan
    engine: Any
    pipeline: Any
    timeframes: list
    running: bool
    cycle_count: int
    last_scan_time: Any
    last_symbols_scanned: Any
    _active_per_tf: dict
    # Portefeuille de stratégies et cycle de vie des bots (AutoOptMixin)
    _ml_trainer: Any
    _lifecycle: Any
    _lifecycle_enabled: bool
    _lifecycle_interval: float
    _lifecycle_auto_reopt: bool
    _lifecycle_snapshot: Any
    _auto_opt_next_run: float
    _fwd_test_next_run: float
    _lifecycle_next_run: float
    _shadow_alloc: Any
    _fwd_test_enabled: bool
    _fwd_test_symbol: str
    _fwd_test_interval: float
    _fwd_test_lookback_days: int
    _fwd_test_edge_lookback: int
    # Marge
    _margin_enabled: bool
    _margin_level: Any
    _margin_next_sync: float
    _balance_detail: Any
    # Caches de statut — déclarés ici, sinon mypy infère `None` depuis la seule
    # affectation visible dans le mixin et refuse toute indexation ensuite.
    _bots_cache: list | None
    _status_db_cache: dict | None
    _status_db_cache_ts: float
    _status_db_cache_ttl: float


class OptimizerHost:
    """Attributs de ``OptimizerSearchEngine`` pour les mixins bayésien / budget."""
    param_space: dict
    strategy_name: str
    cfg: dict
    results: list
    progress_callback: Any
    _cancel_event: Any
    _fixed_ml_hp: Any
    _MIN_SCREEN_PER_PARAM: int
    _impact_scores: Any
    _restore_param_space: Any
    _with_hp: Any
    _should_reduce_space: Any
    _safe_worker_count: Any
    _best_result: Any
    _freeze_params: Any
    _eval: Any
    _penalized_score: Any
    _should_early_stop: Any
    _serialize_pool_inputs: Any
    _worker_args: Any
    _open_pool: Any
    _run_parallel: Any
    _freeze_from_results: Any
    _perturb: Any


class LifecycleHost:
    """Attributs de ``Backtester`` pour ``PositionLifecycleMixin``."""
    spread_pct: float
    taker_fee: float
    maker_fee: float
    borrow_rate: float
    borrow_periods: int
    partial_fill: float
    exit_mode: str
    exit_mode_params: dict
    _venue: Any
    rejections: Any
    cfg: dict
    _impact_cost: Any
    _funding_cost: Any
    _find_strategy: Any
    _min_notional: Any
    _sizing_base: Any
    _leverage: Any
    _fees: Any
    _make_trailing: Any
