"""Schémas Pydantic partagés pour la validation des payloads API (SEC-03).

Les routes d'écriture critiques importent ces modèles plutôt que des
``dict`` / paramètres non bornés. Les schémas déjà locaux (ex. ``ml.py``,
``universe.py``) restent en place ; ce module centralise les types communs
(timeframe, symbole, corps de config / risk).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    """Corps de ``POST /api/config/strategies/{strategy}/params``."""

    model_config = ConfigDict(extra="forbid")

    params: Dict[str, Any] = Field(default_factory=dict)
    timeframe: Optional[str] = None
    symbol: Optional[str] = None

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: Optional[str]) -> Optional[str]:
        return validate_timeframe(v) if v is not None else None

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
    model_config = ConfigDict(extra="forbid")

    margin: Optional[bool] = None
    margin_mode: Optional[str] = Field(None, pattern=r"^(cross|isolated)?$")
    max_leverage: Optional[int] = Field(None, ge=1, le=125)


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
    model_config = ConfigDict(extra="forbid")

    enabled: List[str] = Field(default_factory=list, max_length=64)

    @field_validator("enabled")
    @classmethod
    def _names(cls, v: List[str]) -> List[str]:
        return [validate_strategy_name(s) for s in v]


class TimeframesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeframes: List[str] = Field(default_factory=list, max_length=16)

    @field_validator("timeframes")
    @classmethod
    def _tfs(cls, v: List[str]) -> List[str]:
        return [validate_timeframe(t) for t in v]
