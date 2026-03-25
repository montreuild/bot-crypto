"""État partagé de l'API — variables initialisées par init_app(), accédées via `state.cfg`."""
import threading

# ── Runtime state ──────────────────────────────────────────────────────────
cfg          = None   # dict config chargé depuis config.yaml
trader       = None   # instance LiveTrader (ou None si bot arrêté)
SessionLocal = None   # factory SQLAlchemy session

# ── Sémaphores & verrous ───────────────────────────────────────────────────
_bt_exchange       = None
_bt_exchange_lock  = threading.Lock()
_bt_semaphore      = threading.Semaphore(1)   # un seul backtest à la fois
_opt_semaphore     = threading.Semaphore(1)   # un seul démarrage optimizer à la fois
_config_write_lock = threading.Lock()         # écritures concurrentes sur config.yaml
_bt_cancel_event   = threading.Event()        # signal d'arrêt pour le backtest en cours
_rp_semaphore      = threading.Semaphore(1)   # un seul replay à la fois
_rp_cancel_event   = threading.Event()        # signal d'arrêt pour le replay en cours

# ── Cache découverte stratégies ────────────────────────────────────────────
_strategies_cache:    frozenset | None = None
_strategies_cache_ts: float = 0.0
_STRATEGIES_CACHE_TTL: float = 60.0  # secondes
