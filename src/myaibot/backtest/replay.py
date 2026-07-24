"""Event-driven daily replay harness.

This is the source-of-truth evaluator. Vectorized notebooks can explore ideas,
but promotion requires this replay loop so data cutoffs, fills, costs, and
position state are explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from myaibot.core.models import MarketSnapshot, RiskLimits, TradeIntent, TradeSignal, ValidationResult
from myaibot.execution.ledger import PaperLedger
from myaibot.labs.atlas_regime import AtlasRegimeLab
from myaibot.labs.base import BaseLab, LabContext
from myaibot.portfolio.optimizer import EnsemblePortfolioManager
from myaibot.risk.validator import RiskValidator


class ReplayEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    decision_time: datetime
    execution_time: datetime | None = None
    lab_id: str | None = None
    ticker: str | None = None
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ReplayResult:
    events: list[ReplayEvent] = field(default_factory=list)
    signals: list[TradeSignal] = field(default_factory=list)
    intents: list[TradeIntent] = field(default_factory=list)
    validations: list[ValidationResult] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fills(self) -> list[dict[str, Any]]:
        return [event.payload for event in self.events if event.kind == "fill"]


@dataclass
class DailyReplayEngine:
    price_frame: pd.DataFrame
    labs: list[BaseLab]
    ledger: PaperLedger
    limits: RiskLimits
    sectors: dict[str, str] = field(default_factory=dict)
    adv_dollars: dict[str, float] = field(default_factory=dict)
    atlas_lab: AtlasRegimeLab | None = None
    alt_data: dict[str, Any] = field(default_factory=dict)
    portfolio_manager: EnsemblePortfolioManager | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.price_frame.index, pd.DatetimeIndex):
            self.price_frame.index = pd.to_datetime(self.price_frame.index)
        self.price_frame = self.price_frame.sort_index()
        self.price_frame.columns = [str(col).upper() for col in self.price_frame.columns]
        self.validator = RiskValidator(self.limits)
        if self.atlas_lab is None:
            self.atlas_lab = AtlasRegimeLab()

    def run(self, start: str | datetime, end: str | datetime) -> ReplayResult:
        result = ReplayResult()
        dates = self.price_frame.loc[pd.Timestamp(start) : pd.Timestamp(end)].index
        if len(dates) < 2:
            return result

        for idx in range(len(dates) - 1):
            decision_ts = dates[idx].to_pydatetime()
            execution_ts = dates[idx + 1].to_pydatetime()
            history = self.price_frame.loc[: dates[idx]]
            decision_prices = self._row_prices(dates[idx])
            execution_prices = self._row_prices(dates[idx + 1])
            portfolio = self.ledger.snapshot(decision_ts, decision_prices)
            context = LabContext(
                as_of=decision_ts,
                data_cutoff=decision_ts,
                portfolio=portfolio,
                price_history=history,
                alt_data=self.alt_data,
            )
            regime_multiplier = self.atlas_lab.regime_multiplier(context) if self.atlas_lab else 1.0
            market = MarketSnapshot(
                as_of=decision_ts,
                prices=decision_prices,
                adv_dollars={k.upper(): v for k, v in self.adv_dollars.items()},
                sectors={k.upper(): v for k, v in self.sectors.items()},
                regime_multiplier=regime_multiplier,
            )

            result.events.append(
                ReplayEvent(
                    decision_time=decision_ts,
                    execution_time=execution_ts,
                    kind="heartbeat",
                    payload={"regime_multiplier": regime_multiplier, "equity": portfolio.equity},
                )
            )

            all_day_signals: list[TradeSignal] = []
            direct_intents: list[TradeIntent] = []
            for lab in self.labs:
                signals = lab.generate_signals(context)
                all_day_signals.extend(signals)
                result.signals.extend(signals)
                if self.portfolio_manager is None:
                    direct_intents.extend(lab.propose_intents(context, signals))

            if self.portfolio_manager is not None:
                intents = self.portfolio_manager.propose_intents(
                    signals=all_day_signals,
                    portfolio=self.ledger.snapshot(decision_ts, decision_prices),
                    market=market,
                    data_cutoff=decision_ts,
                )
            else:
                intents = direct_intents
            result.intents.extend(intents)
            for intent in intents:
                self._process_intent(result, intent, decision_ts, execution_ts, decision_prices, execution_prices, market)

            close_snapshot = self.ledger.snapshot(execution_ts, execution_prices)
            result.snapshots.append(
                {
                    "as_of": execution_ts.isoformat(),
                    "cash": close_snapshot.cash,
                    "equity": close_snapshot.equity,
                    "market_value": close_snapshot.market_value,
                    "gross_exposure": close_snapshot.gross_exposure,
                    "positions": {k: v.model_dump() for k, v in close_snapshot.positions.items()},
                }
            )
        return result

    def _process_intent(
        self,
        result: ReplayResult,
        intent: TradeIntent,
        decision_ts: datetime,
        execution_ts: datetime,
        decision_prices: dict[str, float],
        execution_prices: dict[str, float],
        market: MarketSnapshot,
    ) -> None:
        validation = self.validator.validate(intent, self.ledger.snapshot(decision_ts, decision_prices), market)
        result.validations.append(validation)
        result.events.append(
            ReplayEvent(
                decision_time=decision_ts,
                execution_time=execution_ts,
                lab_id=intent.lab_id,
                ticker=intent.ticker,
                kind="validation",
                payload=validation.model_dump(mode="json"),
            )
        )
        if not (validation.approved and validation.order is not None):
            return
        fill_price = execution_prices.get(validation.order.ticker)
        if fill_price is None or fill_price <= 0:
            result.events.append(
                ReplayEvent(
                    decision_time=decision_ts,
                    execution_time=execution_ts,
                    lab_id=intent.lab_id,
                    ticker=validation.order.ticker,
                    kind="missed_fill",
                    payload={"reason": "missing execution price", "order_id": validation.order.order_id},
                )
            )
            return
        try:
            fill = self.ledger.fill_order(
                validation.order,
                fill_price,
                execution_ts,
                cost_bps=self.limits.cost_bps_per_side,
                slippage_bps=self.limits.slippage_bps,
            )
            result.events.append(
                ReplayEvent(
                    decision_time=decision_ts,
                    execution_time=execution_ts,
                    lab_id=intent.lab_id,
                    ticker=fill.ticker,
                    kind="fill",
                    payload=fill.model_dump(mode="json"),
                )
            )
        except ValueError as exc:
            result.events.append(
                ReplayEvent(
                    decision_time=decision_ts,
                    execution_time=execution_ts,
                    lab_id=intent.lab_id,
                    ticker=validation.order.ticker,
                    kind="fill_rejected_by_ledger",
                    payload={"reason": str(exc), "order_id": validation.order.order_id},
                )
            )

    def _row_prices(self, ts: pd.Timestamp) -> dict[str, float]:
        row = self.price_frame.loc[ts].dropna()
        return {str(k).upper(): float(v) for k, v in row.items() if float(v) > 0}
