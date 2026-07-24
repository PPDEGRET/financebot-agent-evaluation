"""Shared Pydantic contracts for signals, intents, risk, and execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SignalDirection = Literal["long", "flat"]
TradeSide = Literal["buy", "sell", "hold", "no_trade"]
TradeAction = Literal["buy", "trim", "close", "hold", "no_trade"]
OrderType = Literal["market_on_close", "market_on_open", "limit"]
OrderStatus = Literal["created", "approved", "rejected", "submitted", "filled", "cancelled"]
IssueSeverity = Literal["info", "warning", "error"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def now_utc() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceRef(StrictModel):
    source: str
    uri: str | None = None
    summary: str = ""
    published_at: datetime | None = None
    available_at: datetime
    sha256: str | None = None


class TradeSignal(StrictModel):
    """A lab-level signal before portfolio/risk sizing."""

    signal_id: str = Field(default_factory=lambda: new_id("sig"))
    lab_id: str
    strategy_id: str
    strategy_version: str = "0.1.0"
    ticker: str
    as_of: datetime
    data_cutoff: datetime
    horizon_days: int = Field(gt=0)
    direction: SignalDirection
    score: float
    confidence: float = Field(ge=0.0, le=1.0)
    expected_gross_edge_bps: float | None = None
    expected_net_edge_bps: float | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    features: dict[str, float | int | str | None] = Field(default_factory=dict)
    notes: str = ""

    @field_validator("ticker")
    @classmethod
    def uppercase_ticker(cls, value: str) -> str:
        return value.upper().strip()


class TradeIntent(StrictModel):
    """Agent decision submitted to deterministic validation.

    This is the main boundary between GPT-5.5/Hermes-style reasoning and the
    deterministic trading substrate.
    """

    intent_id: str = Field(default_factory=lambda: new_id("intent"))
    lab_id: str
    originator: str
    strategy_version: str = "0.1.0"
    code_version: str = "unknown"
    created_at: datetime = Field(default_factory=now_utc)
    data_cutoff: datetime
    ticker: str
    side: TradeSide
    action: TradeAction
    quantity: int | None = Field(default=None, ge=1)
    target_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    limit_price: float | None = Field(default=None, gt=0)
    time_in_force: str = "DAY"
    horizon_days: int = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)
    expected_gross_edge_bps: float | None = None
    expected_net_edge_bps: float | None = None
    thesis: str
    invalidators: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def uppercase_ticker(cls, value: str) -> str:
        return value.upper().strip()

    @model_validator(mode="after")
    def enforce_long_only_actions(self) -> "TradeIntent":
        if self.side == "buy" and self.action != "buy":
            raise ValueError("buy side must use action='buy'")
        if self.side == "sell" and self.action not in {"trim", "close"}:
            raise ValueError("sell side is only allowed for trim/close, never sell-to-open")
        if self.side in {"hold", "no_trade"} and self.action not in {"hold", "no_trade"}:
            raise ValueError("hold/no_trade side must use hold/no_trade action")
        if self.side == "buy" and self.quantity is None and self.target_weight is None:
            raise ValueError("buy intents need quantity or target_weight")
        return self


class Position(StrictModel):
    ticker: str
    quantity: int = Field(ge=0)
    avg_cost: float = Field(ge=0.0)
    last_price: float = Field(default=0.0, ge=0.0)
    realized_pnl: float = 0.0
    sector: str | None = None

    @property
    def market_value(self) -> float:
        return float(self.quantity * self.last_price)


class PortfolioState(StrictModel):
    as_of: datetime
    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)

    @property
    def market_value(self) -> float:
        return sum(pos.market_value for pos in self.positions.values())

    @property
    def equity(self) -> float:
        return float(self.cash + self.market_value)

    @property
    def gross_exposure(self) -> float:
        if self.equity <= 0:
            return 0.0
        return self.market_value / self.equity

    def position_value(self, ticker: str) -> float:
        pos = self.positions.get(ticker.upper())
        return 0.0 if pos is None else pos.market_value

    def position_quantity(self, ticker: str) -> int:
        pos = self.positions.get(ticker.upper())
        return 0 if pos is None else pos.quantity


class MarketSnapshot(StrictModel):
    as_of: datetime
    prices: dict[str, float]
    adv_dollars: dict[str, float] = Field(default_factory=dict)
    sectors: dict[str, str] = Field(default_factory=dict)
    regime_multiplier: float = Field(default=1.0, ge=0.0, le=1.5)

    def price(self, ticker: str) -> float | None:
        return self.prices.get(ticker.upper())

    def adv(self, ticker: str) -> float | None:
        return self.adv_dollars.get(ticker.upper())

    def sector(self, ticker: str) -> str | None:
        return self.sectors.get(ticker.upper())


class RiskLimits(StrictModel):
    allow_shorts: bool = False
    allow_margin: bool = False
    max_position_weight: float = Field(default=0.05, gt=0.0, le=1.0)
    max_order_notional_weight: float = Field(default=0.05, gt=0.0, le=1.0)
    max_sector_weight: float = Field(default=0.30, gt=0.0, le=1.0)
    max_positions: int = Field(default=30, ge=1)
    cash_buffer_weight: float = Field(default=0.01, ge=0.0, le=0.5)
    min_price: float = Field(default=5.0, ge=0.0)
    min_adv_dollars: float = Field(default=20_000_000.0, ge=0.0)
    min_expected_net_edge_bps: float = 25.0
    cost_bps_per_side: float = Field(default=20.0, ge=0.0)
    slippage_bps: float = Field(default=5.0, ge=0.0)
    reject_social_only_above_weight: float = Field(default=0.01, ge=0.0, le=1.0)


class ValidationIssue(StrictModel):
    severity: IssueSeverity
    code: str
    message: str


class OrderRequest(StrictModel):
    order_id: str = Field(default_factory=lambda: new_id("order"))
    intent_id: str
    ticker: str
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    order_type: OrderType = "market_on_close"
    limit_price: float | None = None
    created_at: datetime = Field(default_factory=now_utc)
    expected_price: float = Field(gt=0)
    estimated_fees: float = Field(default=0.0, ge=0.0)
    estimated_slippage: float = Field(default=0.0, ge=0.0)
    reason: str = ""


class ValidationResult(StrictModel):
    intent_id: str
    approved: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    order: OrderRequest | None = None

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]


class Fill(StrictModel):
    fill_id: str = Field(default_factory=lambda: new_id("fill"))
    order_id: str
    intent_id: str
    ticker: str
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    commission: float = Field(default=0.0, ge=0.0)
    slippage_bps: float = 0.0
    filled_at: datetime
    venue: str = "paper"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TradeAudit(StrictModel):
    audit_id: str = Field(default_factory=lambda: new_id("audit"))
    intent_id: str
    created_at: datetime = Field(default_factory=now_utc)
    status: Literal["approved", "rejected", "filled", "cancelled", "paper_filled", "no_order"]
    validator_passed: bool
    reasons: list[str] = Field(default_factory=list)
    order: OrderRequest | None = None
    fill: Fill | None = None
    portfolio_snapshot: PortfolioState | None = None
