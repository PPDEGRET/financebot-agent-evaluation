"""Relay lab: simple relative-strength baseline.

This is not the final alpha engine. It is a clean, timestamp-safe baseline that
proves the lab -> intent -> risk -> ledger loop works before adding GPT-5.5
manager judgment and richer features.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from myaibot.core.models import TradeIntent, TradeSignal
from myaibot.labs.base import BaseLab, LabContext


@dataclass(frozen=True)
class RelayRelativeStrengthConfig:
    lookback_bars: int = 60
    top_n: int = 5
    min_momentum: float = 0.05
    exit_momentum: float = 0.0
    target_weight: float = 0.03
    horizon_days: int = 20
    benchmark: str = "SPY"


class RelayRelativeStrengthLab(BaseLab):
    def __init__(self, config: RelayRelativeStrengthConfig | None = None) -> None:
        super().__init__(lab_id="relay_relative_strength", strategy_id="price_relative_strength")
        self.config = config or RelayRelativeStrengthConfig()

    def generate_signals(self, context: LabContext) -> list[TradeSignal]:
        prices = context.price_history.dropna(axis=1, how="all")
        if len(prices) <= self.config.lookback_bars:
            return []
        latest = prices.iloc[-1]
        past = prices.iloc[-self.config.lookback_bars]
        momentum = (latest / past - 1.0).replace([float("inf"), float("-inf")], pd.NA).dropna()
        if self.config.benchmark in momentum.index:
            benchmark_mom = float(momentum[self.config.benchmark])
        else:
            benchmark_mom = 0.0
        candidates = momentum.drop(labels=[self.config.benchmark], errors="ignore")
        candidates = candidates[candidates >= self.config.min_momentum]
        candidates = candidates.sort_values(ascending=False).head(self.config.top_n)

        signals: list[TradeSignal] = []
        for ticker, mom in candidates.items():
            rel = float(mom) - benchmark_mom
            signals.append(
                TradeSignal(
                    lab_id=self.lab_id,
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    ticker=str(ticker),
                    as_of=context.as_of,
                    data_cutoff=context.data_cutoff,
                    horizon_days=self.config.horizon_days,
                    direction="long",
                    score=rel,
                    confidence=max(0.05, min(0.90, 0.50 + rel)),
                    expected_gross_edge_bps=rel * 10_000,
                    expected_net_edge_bps=rel * 10_000 - 25.0,
                    features={"momentum": float(mom), "benchmark_momentum": benchmark_mom, "relative_momentum": rel},
                    notes="Timestamp-safe price relative-strength baseline signal.",
                )
            )
        return signals

    def propose_intents(self, context: LabContext, signals: list[TradeSignal]) -> list[TradeIntent]:
        intents: list[TradeIntent] = []
        for signal in signals:
            intents.append(
                TradeIntent(
                    lab_id=self.lab_id,
                    originator=f"{self.lab_id}.manager.gpt-5.5-placeholder",
                    strategy_version=self.strategy_version,
                    data_cutoff=context.data_cutoff,
                    ticker=signal.ticker,
                    side="buy",
                    action="buy",
                    target_weight=self.config.target_weight,
                    horizon_days=self.config.horizon_days,
                    confidence=signal.confidence,
                    expected_gross_edge_bps=signal.expected_gross_edge_bps,
                    expected_net_edge_bps=signal.expected_net_edge_bps,
                    thesis=(
                        f"{signal.ticker} has stronger {self.config.lookback_bars}-bar momentum than the benchmark; "
                        "enter a small capped long if risk validates."
                    ),
                    invalidators=["relative momentum turns negative", "broad market regime deteriorates", "liquidity/risk cap fails"],
                    evidence_refs=signal.evidence_refs,
                    metadata={"signal_id": signal.signal_id, **signal.features},
                )
            )
        return intents
