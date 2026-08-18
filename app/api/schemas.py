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


class RiskEnvelopesBody(BaseModel):
    """Payload racine de ``POST /api/risk/envelopes`` : ``{venue: {…}}``.

    Utilise ``extra="allow"`` car les clés sont dynamiques (noms de venues).
    La validation métier (bornes, venues connues) reste dans la route.
    """

    model_config = ConfigDict(extra="allow")

    def as_envelopes(self) -> Dict[str, Any]:
        data = self.model_dump()
        if len(data) > 64:
            raise ValueError("Trop d'enveloppes (max 64)")
        return data


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
