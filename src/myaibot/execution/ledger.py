"""Deterministic paper ledger.

The ledger is the execution truth in research and paper simulations. It does not
model a full exchange order book; it applies explicit fill prices, commissions,
and slippage so assumptions are visible and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from myaibot.core.models import Fill, OrderRequest, PortfolioState, Position


@dataclass
class PaperLedger:
    initial_cash: float
    cash: float | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cash is None:
            self.cash = float(self.initial_cash)

    def mark_to_market(self, prices: dict[str, float]) -> None:
        for ticker, position in list(self.positions.items()):
            if ticker in prices and prices[ticker] > 0:
                position.last_price = float(prices[ticker])

    def snapshot(self, as_of: datetime, prices: dict[str, float] | None = None) -> PortfolioState:
        if prices:
            self.mark_to_market({k.upper(): v for k, v in prices.items()})
        return PortfolioState(as_of=as_of, cash=float(self.cash or 0.0), positions={k: v.model_copy() for k, v in self.positions.items()})

    @property
    def equity(self) -> float:
        return float((self.cash or 0.0) + sum(pos.market_value for pos in self.positions.values()))

    def fill_order(
        self,
        order: OrderRequest,
        market_price: float,
        filled_at: datetime,
        *,
        cost_bps: float = 0.0,
        slippage_bps: float = 0.0,
    ) -> Fill:
        if market_price <= 0:
            raise ValueError("market_price must be positive")
        if order.side == "buy":
            fill_price = market_price * (1.0 + slippage_bps / 10_000)
        else:
            fill_price = market_price * (1.0 - slippage_bps / 10_000)
        commission = fill_price * order.quantity * cost_bps / 10_000
        fill = Fill(
            order_id=order.order_id,
            intent_id=order.intent_id,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=commission,
            slippage_bps=slippage_bps,
            filled_at=filled_at,
        )
        self.apply_fill(fill)
        return fill

    def apply_fill(self, fill: Fill) -> None:
        ticker = fill.ticker.upper()
        notional = fill.quantity * fill.price
        if fill.side == "buy":
            total_cost = notional + fill.commission
            if total_cost > (self.cash or 0.0) + 1e-9:
                raise ValueError(f"Insufficient cash for fill {fill.fill_id}: need {total_cost}, have {self.cash}")
            current = self.positions.get(ticker)
            if current is None:
                current = Position(ticker=ticker, quantity=0, avg_cost=0.0, last_price=fill.price)
                self.positions[ticker] = current
            new_qty = current.quantity + fill.quantity
            current.avg_cost = ((current.avg_cost * current.quantity) + notional) / new_qty
            current.quantity = new_qty
            current.last_price = fill.price
            self.cash = float((self.cash or 0.0) - total_cost)
        else:
            current = self.positions.get(ticker)
            if current is None or current.quantity < fill.quantity:
                raise ValueError(f"Paper ledger refuses sell-to-open for {ticker}")
            proceeds = notional - fill.commission
            current.realized_pnl += (fill.price - current.avg_cost) * fill.quantity - fill.commission
            current.quantity -= fill.quantity
            current.last_price = fill.price
            self.cash = float((self.cash or 0.0) + proceeds)
            if current.quantity == 0:
                del self.positions[ticker]
        self.fills.append(fill)
