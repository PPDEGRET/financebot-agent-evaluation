"""IBKR paper/live adapter boundary built around `ib_async`.

Labs and GPT agents never call this directly. They emit `TradeIntent`s; the risk
layer converts approved intents to `OrderRequest`s; this adapter submits those
orders only when explicitly configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from myaibot.core.models import Fill, OrderRequest
from myaibot.core.showcase import require_external_runtime


class BrokerAdapter(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def submit_order(self, order: OrderRequest) -> str: ...
    def reconcile(self) -> list[Fill]: ...


@dataclass(frozen=True)
class IbkrConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 55
    account: str | None = None
    readonly: bool = False
    timeout: float = 20.0
    transmit_orders: bool = False
    default_exchange: str = "SMART"
    default_currency: str = "USD"
    order_type_default: str = "MKT"
    tif_default: str = "DAY"


@dataclass
class DisabledIbkrAdapter:
    """Safety placeholder until IB Gateway paper credentials are configured."""

    reason: str = "IBKR adapter disabled until explicit paper configuration and dry-run tests pass."

    def connect(self) -> None:
        raise RuntimeError(self.reason)

    def disconnect(self) -> None:
        return None

    def submit_order(self, order: OrderRequest) -> str:
        raise RuntimeError(self.reason)

    def reconcile(self) -> list[Fill]:
        return []


@dataclass
class IbAsyncBrokerAdapter:
    """IBKR adapter using `ib_async`.

    Typical paper setup:
    - run IB Gateway Stable, paper account;
    - enable API connections;
    - use port 4002 for paper Gateway unless locally configured otherwise;
    - keep `transmit_orders=false` until dry-run reconciliation is confirmed.
    """

    config: IbkrConnectionConfig = field(default_factory=IbkrConnectionConfig)
    ib: Any | None = None
    _submitted: dict[str, Any] = field(default_factory=dict)

    def connect(self) -> None:
        require_external_runtime("Broker connection")
        try:
            from ib_async import IB
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install optional dependency with `pip install -e .[ibkr]` to use IBKR.") from exc
        if self.ib is None:
            self.ib = IB()
        if not self.ib.isConnected():
            self.ib.connect(
                self.config.host,
                self.config.port,
                clientId=self.config.client_id,
                readonly=self.config.readonly,
                timeout=self.config.timeout,
            )

    def disconnect(self) -> None:
        require_external_runtime("Broker disconnection")
        if self.ib is not None and self.ib.isConnected():
            self.ib.disconnect()

    def submit_order(self, order: OrderRequest) -> str:
        require_external_runtime("Broker order submission")
        if self.ib is None or not self.ib.isConnected():
            self.connect()
        assert self.ib is not None
        contract = self._stock_contract(order.ticker)
        qualified = self.ib.qualifyContracts(contract)
        contract = qualified[0] if qualified else contract
        ib_order = self._ib_order(order)
        trade = self.ib.placeOrder(contract, ib_order)
        self._submitted[order.order_id] = trade
        return str(getattr(trade.order, "orderId", order.order_id))

    def reconcile(self) -> list[Fill]:
        """Return known fills from IBKR.

        IB orderStatus messages are duplicated/incomplete for fast fills; executions
        and commission reports are the durable truth. This method converts the
        currently known `ib.fills()` objects into project Fill records.
        """
        require_external_runtime("Broker reconciliation")
        if self.ib is None or not self.ib.isConnected():
            self.connect()
        assert self.ib is not None
        fills: list[Fill] = []
        for raw in self.ib.fills():
            converted = self._convert_fill(raw)
            if converted is not None:
                fills.append(converted)
        return fills

    def open_order_ids(self) -> list[str]:
        require_external_runtime("Broker open-order lookup")
        if self.ib is None or not self.ib.isConnected():
            self.connect()
        assert self.ib is not None
        return [str(getattr(trade.order, "orderId", "")) for trade in self.ib.openTrades()]

    def _stock_contract(self, ticker: str) -> Any:
        from ib_async import Stock

        return Stock(ticker.upper(), self.config.default_exchange, self.config.default_currency)

    def _ib_order(self, order: OrderRequest) -> Any:
        from ib_async import LimitOrder, MarketOrder

        action = "BUY" if order.side == "buy" else "SELL"
        if order.order_type == "limit" or order.limit_price is not None:
            ib_order = LimitOrder(action, order.quantity, order.limit_price or order.expected_price)
        else:
            ib_order = MarketOrder(action, order.quantity)
        ib_order.tif = self.config.tif_default
        ib_order.transmit = self.config.transmit_orders
        if self.config.account:
            ib_order.account = self.config.account
        return ib_order

    def _convert_fill(self, raw_fill: Any) -> Fill | None:
        try:
            contract = raw_fill.contract
            execution = raw_fill.execution
        except AttributeError:
            return None
        side_raw = str(getattr(execution, "side", "")).upper()
        side = "buy" if side_raw in {"BOT", "BUY"} else "sell" if side_raw in {"SLD", "SELL"} else None
        if side is None:
            return None
        commission = 0.0
        report = getattr(raw_fill, "commissionReport", None)
        if report is not None:
            commission = float(getattr(report, "commission", 0.0) or 0.0)
        time_value = getattr(execution, "time", None)
        if isinstance(time_value, datetime):
            filled_at = time_value if time_value.tzinfo else time_value.replace(tzinfo=UTC)
        else:
            filled_at = datetime.now(UTC)
        order_id = str(getattr(execution, "orderId", "ibkr_unknown"))
        ticker = str(getattr(contract, "symbol", "UNKNOWN")).upper()
        return Fill(
            order_id=order_id,
            intent_id=f"ibkr_reconcile_{order_id}",
            ticker=ticker,
            side=side,  # type: ignore[arg-type]
            quantity=int(float(getattr(execution, "shares", 0) or 0)),
            price=float(getattr(execution, "price", 0.0) or 0.0),
            commission=commission,
            filled_at=filled_at,
            venue="ibkr",
            metadata={
                "execId": getattr(execution, "execId", None),
                "permId": getattr(execution, "permId", None),
                "account": getattr(execution, "acctNumber", None),
            },
        )
