"""Schémas Pydantic partagés pour la validation des payloads API (SEC-03).

Les routes d'écriture critiques importent ces modèles plutôt que des
``dict`` / paramètres non bornés. Les schémas déjà locaux (ex. ``ml.py``,
``universe.py``) restent en place ; ce module centralise les types communs
(timeframe, symbole, corps de config / risk).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.timeframes import TF_SECONDS

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:\-]{0,31}$")
_STRATEGY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def validate_timeframe(tf: str) -> str:
    if tf not in TF_SECONDS:
        raise ValueError(
            f"Timeframe invalide : {tf!r}. "
            f"Autorisés : {', '.join(sorted(TF_SECONDS))}"
        )
    return tf


def validate_symbol(symbol: str) -> str:
    if not symbol or not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError(f"Symbole invalide : {symbol!r}")
    if ".." in symbol:
        raise ValueError(f"Symbole invalide : {symbol!r}")
    return symbol


def validate_strategy_name(name: str) -> str:
    if not name or not _STRATEGY_RE.fullmatch(name):
        raise ValueError(f"Nom de stratégie invalide : {name!r}")
    return name


class TimeframeQuery(BaseModel):
    timeframe: str = Field(..., description="Timeframe canonique (ex. 1h)")

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        return validate_timeframe(v)


class SymbolQuery(BaseModel):
    symbol: str = Field(..., description="Paire / ticker (ex. BTC/USDC)")

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        return validate_symbol(v)


class StrategyParamsBody(BaseModel):
    """Corps de ``POST /api/config/strategy-params``."""

    model_config = ConfigDict(extra="forbid")

    strategy: str
    params: Dict[str, Any] = Field(default_factory=dict)
    timeframe: Optional[str] = None
    symbol: Optional[str] = None

    @field_validator("strategy")
    @classmethod
    def _strat(cls, v: str) -> str:
        return validate_strategy_name(v)

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: Optional[str]) -> Optional[str]:
        return validate_timeframe(v) if v is not None else None

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: Optional[str]) -> Optional[str]:
        return validate_symbol(v) if v is not None else None


class StrategyTimeframeBody(BaseModel):
    """Corps de ``POST /api/config/strategy-timeframe``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    strategy: str
    timeframe: str = Field(
        ...,
        validation_alias=AliasChoices("timeframe", "tf"),
    )
    enabled: bool = Field(
        True,
        validation_alias=AliasChoices("enabled", "enable"),
    )
    symbol: Optional[str] = None

    @field_validator("strategy")
    @classmethod
    def _strat(cls, v: str) -> str:
        return validate_strategy_name(v)

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        return validate_timeframe(v)

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: Optional[str]) -> Optional[str]:
        return validate_symbol(v) if v is not None else None


class TradingParamsBody(BaseModel):
    """Corps de ``POST /api/config/trading``."""

    model_config = ConfigDict(extra="forbid")

    score_threshold: Optional[float] = Field(None, gt=0.0, lt=1.0)
    paper_mode: Optional[bool] = None
    paper_slippage: Optional[float] = Field(None, ge=0.0, le=0.05)
    daily_drawdown_limit: Optional[float] = Field(None, gt=0.0, le=0.5)
    max_drawdown_global: Optional[float] = Field(None, gt=0.0, le=0.8)


class MarginConfigBody(BaseModel):
    """Corps de ``POST /api/config/margin``."""

    model_config = ConfigDict(extra="forbid")

    margin: Optional[bool] = None
    margin_mode: Optional[str] = Field(None, pattern=r"^(cross|isolated)$")
    max_leverage: Optional[int] = Field(None, ge=1, le=125)


class RiskConfigBody(BaseModel):
    """Corps de ``POST /api/config/risk`` (circuit breakers par slot)."""

    model_config = ConfigDict(extra="forbid")

    consecutive_loss_limit: Optional[int] = Field(None, ge=1, le=20)
    slot_daily_dd_limit: Optional[float] = Field(None, gt=0.0, le=0.5)
    win_rate_floor: Optional[float] = Field(None, ge=0.0, le=1.0)
    volatility_threshold: Optional[float] = Field(None, gt=0.0, le=1.0)
    consecutive_pause_secs: Optional[int] = Field(None, ge=60, le=86400)
    min_slot_weight: Optional[float] = Field(None, gt=0.0, le=1.0)


class VenueEnvelopeBody(BaseModel):
    """Une enveloppe venue — bornes alignées sur ``risk.py`` / ``_validate_risk_envelopes``."""

    model_config = ConfigDict(extra="forbid")

    capital: Optional[float] = Field(None, gt=0)
    max_symbol_exposure_pct: Optional[float] = Field(None, gt=0.0, le=1.0)
    symbol_risk_pct: Optional[float] = Field(None, gt=0.0, le=0.10)
    venue_risk_pct: Optional[float] = Field(None, gt=0.0, le=0.20)


class RiskEnvelopesBody(BaseModel):
    """Payload racine de ``POST /api/risk/envelopes`` : ``{venue: {…}}``.

    Clés dynamiques (noms de venues) ; chaque valeur est validée par
    ``VenueEnvelopeBody`` (API-03).
    """

    model_config = ConfigDict(extra="allow")

    def as_envelopes(self) -> Dict[str, Any]:
        data = self.model_dump()
        if len(data) > 64:
            raise ValueError("Trop d'enveloppes (max 64)")
        out: Dict[str, Any] = {}
        for venue, env in data.items():
            if not isinstance(env, dict):
                raise ValueError(f"risk.envelopes.{venue} doit être un objet")
            out[venue] = VenueEnvelopeBody.model_validate(env).model_dump(exclude_none=True)
        return out


class StrategiesEnabledBody(BaseModel):
    """Corps de ``POST /api/config/strategies``."""

    model_config = ConfigDict(extra="forbid")

    enabled: List[str] = Field(default_factory=list, max_length=64)

    @field_validator("enabled")
    @classmethod
    def _names(cls, v: List[str]) -> List[str]:
        return [validate_strategy_name(s) for s in v]


class TimeframesBody(BaseModel):
    """Corps de ``POST /api/config/timeframes``."""

    model_config = ConfigDict(extra="forbid")

    timeframes: List[str] = Field(default_factory=list, max_length=16)

    @field_validator("timeframes")
    @classmethod
    def _tfs(cls, v: List[str]) -> List[str]:
        return [validate_timeframe(t) for t in v]


class AutoOptimizerBody(BaseModel):
    """Corps de ``POST /api/config/auto-optimizer``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_h: int = Field(24, ge=1, le=168)


class NotificationsConfigBody(BaseModel):
    """Corps de ``POST /api/config/notifications``."""

    model_config = ConfigDict(extra="forbid")

    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = Field(None, max_length=256)
    telegram_chat_id: Optional[str] = Field(None, max_length=64)
    whatsapp_enabled: Optional[bool] = None
    whatsapp_number: Optional[str] = Field(None, max_length=32)
    whatsapp_token: Optional[str] = Field(None, max_length=256)
    email_enabled: Optional[bool] = None
    email_smtp: Optional[str] = Field(None, max_length=255)
    email_port: Optional[int] = Field(None, ge=1, le=65535)
    email_user: Optional[str] = Field(None, max_length=255)
    email_password: Optional[str] = Field(None, max_length=256)
    email_to: Optional[str] = Field(None, max_length=255)
    min_pnl_to_notify: Optional[float] = Field(None, ge=0.0)
    position_loss_warn_pct: Optional[float] = Field(None, ge=0.0, le=100.0)


# ── Contrats de sortie (API-01 / ARCH-01) ──────────────────────────────────
# extra="allow" : on déclare le contrat sans casser un champ ajouté côté
# serveur avant que le modèle ne soit mis à jour.


class BacktestResultModel(BaseModel):
    """Miroir de ``BacktestPayload`` — 45 clés du moteur."""

    model_config = ConfigDict(extra="allow")

    initial_capital: float
    final_equity: float
    total_pnl: float
    net_profit: float
    total_trades: int
    win_rate: float
    max_drawdown: float
    sharpe: Optional[float] = None
    profit_factor: Optional[float] = None
    realistic_risk: bool = False
    fallback_to_inline: bool = False
    trades: List[Any] = Field(default_factory=list)
    cost_model: Optional[Dict[str, Any]] = None


class BacktestRunResponse(BaseModel):
    """``POST /api/backtest`` — payload assemblé par la route."""

    model_config = ConfigDict(extra="allow")

    symbol: str
    timeframe: str
    n_bars: int = 0
    realistic_risk: bool = False
    cost_model: Optional[Dict[str, Any]] = None
    by_strategy: Dict[str, Any] = Field(default_factory=dict)


class PortfolioResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    running: bool
    capital: Optional[float] = None
    paper_mode: Optional[bool] = None
    allocation: List[Any] = Field(default_factory=list)
    risk: Dict[str, Any] = Field(default_factory=dict)
    activity: List[Any] = Field(default_factory=list)


class TradeRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Any = None
    time: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    strategy: Optional[str] = None
    pnl: Optional[float] = None
    quote_currency: Optional[str] = None


class TradesListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    total: int
    offset: int
    limit: int
    trades: List[TradeRow] = Field(default_factory=list)


class RiskOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    venues: List[Dict[str, Any]] = Field(default_factory=list)
    symbols: List[Dict[str, Any]] = Field(default_factory=list)
    slots: List[Dict[str, Any]] = Field(default_factory=list)
    total_risk_engaged: float = 0.0
    rejections: Dict[str, Any] = Field(default_factory=dict)
    envelopes_config: Dict[str, Any] = Field(default_factory=dict)


class OptimizeResultsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    by_strategy_tf: Dict[str, Any] = Field(default_factory=dict)
    active_per_tf: Dict[str, Any] = Field(default_factory=dict)


# ── Contrats UI/API encore manuscrits dans types/index.ts (FE-03) ───────────


class CostModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    venue: str
    market_type: str
    margin_mode: Optional[str] = None
    max_leverage: float
    asset_class: str
    quote_currency: str
    exchange: Optional[str] = None
    calendar: str = "24/7"
    can_execute: bool = True
    allow_short: bool = True
    fee_rate_taker: float
    fee_rate_maker: float
    fee_pct_override: Optional[float] = None
    fee_fixed: float
    fee_min: float
    transaction_tax_pct: float
    tax_on_buy_only: bool
    borrows: bool
    borrow_rate_daily: float
    borrow_periods_per_day: int
    borrow_rate_annual: float
    spread_pct: float
    slippage_model: str
    slippage_k: float
    partial_fill_pct: float
    fractional: bool
    lot_size: float
    tick_size: float
    min_notional: float
    max_notional_pct: float


class Position(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    symbol: str
    side: str
    strategy: str
    timeframe: str
    score: float
    entry: float
    stop: float
    size: float
    notional: float
    fees: float
    upnl: float
    open_time: float
    reason: str
    quote_currency: str = ""


class EquityPoint(BaseModel):
    time: str
    equity: float


class BacktestRecommendation(BaseModel):
    model_config = ConfigDict(extra="allow")

    severity: str
    code: str
    title: str
    message: str
    action: str
    action_link: str = ""
    metrics: Dict[str, Any] = Field(default_factory=dict)


class BacktestRecoCounts(BaseModel):
    critical: int
    warning: int
    info: int
    positive: int


class BacktestRecommendationsSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    counts: BacktestRecoCounts
    verdict: str
    verdict_label: str
    total: int


class StrategyStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    trades: int
    wins: int
    pnl: float
    fees: float
    win_rate: float
    total_pnl: float
    total_fees: float
    total_trades: int
    profit_factor: float
    sharpe: Optional[float]
    max_drawdown: float
    expectancy: float = 0.0
    total_borrow_cost: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    initial_capital: float = 0.0
    buy_and_hold_pnl: float = 0.0
    alpha: float = 0.0
    #: Alpha en POINTS DE POURCENTAGE vs buy & hold (`alpha` est en devise).
    #: Produit par `performance_metrics` et porté par `BacktestResult`, consommé
    #: par `strategy-comparison-table` et `backtest-results` — mais absent de ce
    #: schéma, si bien que `generated.ts` avait dû être complété à la main
    #: (`b42d200`). C'est exactement la dérive que le contrat doit empêcher.
    alpha_vs_bh: Optional[float] = None
    equity_curve: List[EquityPoint] = Field(default_factory=list)
    recommendations: List[BacktestRecommendation] = Field(default_factory=list)
    recommendations_summary: BacktestRecommendationsSummary | None = None
    monte_carlo: Dict[str, Any] = Field(default_factory=dict)


class SignalLogEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    time: str
    symbol: str
    strategy: str
    side: str
    score: float
    threshold: float
    timeframe: str
    status: str
    reason: str
    entry: Optional[float] = None
    exit: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None


class SlotBudget(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot_key: str
    strategy: str
    tf: str
    symbol: Optional[str] = None
    enabled: bool
    budget_pct: float
    budget_usdc: float
    used_notional: float
    used_pct: float
    weekly_pnl: float
    weekly_trades: int
    weekly_wins: int
    next_rebalance: str
    paused: bool
    pause_reason: str
    consecutive_losses: int
    win_rate_15t: float
    daily_pnl: float
    excluded_by_optimizer: bool = False


class RiskVenue(BaseModel):
    model_config = ConfigDict(extra="allow")

    venue: str
    currency: str
    envelope: float
    notional_engaged: float
    risk_budget: float
    risk_engaged: float
    risk_pct_used: float


class RiskSymbol(BaseModel):
    model_config = ConfigDict(extra="allow")

    venue: str
    symbol: str
    currency: str
    envelope: float
    notional_engaged: float
    risk_budget: float
    risk_engaged: float


class RiskSlot(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot_key: str
    venue: str
    symbol: str
    currency: str
    weight: float
    envelope: float
    max_notional: float
    risk_amount: float
    risk_engaged: float
    notional_engaged: float
    edge_ci_low: Optional[float] = None


class RiskRejections(BaseModel):
    model_config = ConfigDict(extra="allow")

    total: Optional[int] = None
    par_motif: Dict[str, int] = Field(default_factory=dict)
    par_slot: Dict[str, int] = Field(default_factory=dict)
    par_symbole: Dict[str, int] = Field(default_factory=dict)


class VenueEnvelopeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    capital: Optional[float] = None
    max_symbol_exposure_pct: Optional[float] = None
    symbol_risk_pct: Optional[float] = None
    venue_risk_pct: Optional[float] = None


class RiskOverview(BaseModel):
    model_config = ConfigDict(extra="allow")

    venues: List[RiskVenue] = Field(default_factory=list)
    symbols: List[RiskSymbol] = Field(default_factory=list)
    slots: List[RiskSlot] = Field(default_factory=list)
    total_risk_engaged: float = 0.0
    rejections: RiskRejections = Field(default_factory=RiskRejections)
    envelopes_config: Dict[str, VenueEnvelopeConfig] = Field(default_factory=dict)


class RiskDiagnostic(BaseModel):
    model_config = ConfigDict(extra="allow")

    severity: str
    code: str
    scope: str
    message: str
    values: Dict[str, Any]


class RiskDiagnostics(BaseModel):
    model_config = ConfigDict(extra="allow")

    diagnostics: List[RiskDiagnostic]
    errors: int
    warnings: int


class CircuitBreakerStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot_key: str
    paused: bool
    pause_reason: str
    consecutive_losses: int
    win_rate_15t: float
    daily_pnl: float


class SlotState(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot_key: str
    consecutive_losses: int
    last_trades: List[bool]
    daily_pnl: float
    daily_trades: int
    day_key: str
    paused_until: float
    pause_reason: str
    win_rate: float


class BalanceDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    free: float
    used: float
    total: float
    borrowed: float


class BotIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot_key: str
    strategy: str
    timeframe: str
    symbol: str
    generation: int
    born_at: str
    parent_slot: Optional[str] = None
    lineage: List[str]


class LifecycleSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    states: Dict[str, str]
    counts: Dict[str, int]
    reopt_queue: List[str]


class BotStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    paper_mode: bool
    timeframe: str
    timeframes: List[str]
    strategies: List[str]
    capital: float = 0.0
    cycle: int = 0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_fees: float = 0.0
    best_trade: float = 0.0
    positions: List[Position] = Field(default_factory=list)
    by_strategy: Dict[str, StrategyStats] = Field(default_factory=dict)
    signal_log: List[SignalLogEntry] = Field(default_factory=list)
    active_per_tf: Dict[str, List[str]] = Field(default_factory=dict)
    circuit_breaker_active: bool = False
    circuit_breaker_reason: str = ""
    daily_pnl_pct: float = 0.0
    global_dd_pct: float = 0.0
    current_risk: float = 0.0
    daily_dd_limit: float = 0.0
    global_dd_limit: float = 0.0
    capital_allocation: List[SlotBudget] = Field(default_factory=list)
    circuit_breakers: List[CircuitBreakerStatus] = Field(default_factory=list)
    slot_states: List[SlotState] = Field(default_factory=list)
    volatility_brake: bool = False
    margin_enabled: bool = False
    margin_level: Optional[float] = None
    margin_interest: float = 0.0
    margin_mode: Optional[str] = None
    balance_detail: Optional[BalanceDetail] = None
    bots: List[BotIdentity] = Field(default_factory=list)
    lifecycle: Optional[LifecycleSnapshot] = None
    shadow_allocation: Dict[str, Any] = Field(default_factory=dict)
    last_scan_time: str = ""
    last_symbols_scanned: List[str] = Field(default_factory=list)
    git_commit: str = ""


class Trade(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Any
    time: str
    symbol: str
    side: str
    strategy: str
    timeframe: Optional[str] = None
    entry: float
    exit: float
    pnl: float
    pnl_pct: float
    fees: float
    status: str
    score: float
    reason: str
    quote_currency: str = ""
