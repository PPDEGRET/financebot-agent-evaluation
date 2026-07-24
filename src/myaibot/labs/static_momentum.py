"""Static/slow momentum allocation lab.

This lab is a clean baseline: buy the strongest liquid symbols by trailing return
using only data before the decision date. It is intentionally simple and useful as
a benchmark against more complex GPT/social/news strategies.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from myaibot.core.models import TradeIntent, TradeSignal
from myaibot.labs.base import BaseLab, LabContext


@dataclass(frozen=True)
class StaticMomentumConfig:
    lookback_bars: int = 252
    top_n: int = 5
    min_return: float = 0.0
    target_weight: float = 0.20
    horizon_days: int = 63
    exclude_symbols: tuple[str, ...] = ()
    freeze_selection: bool = False


class StaticMomentumLab(BaseLab):
    def __init__(self, config: StaticMomentumConfig | None = None) -> None:
        super().__init__(lab_id="static_momentum", strategy_id="slow_topk_momentum")
        self.config = config or StaticMomentumConfig()
        self._frozen_selection: list[str] | None = None
        self._frozen_scores: dict[str, float] | None = None

    def generate_signals(self, context: LabContext) -> list[TradeSignal]:
        prices = context.price_history.dropna(axis=1, how="all")
        if len(prices) <= self.config.lookback_bars:
            return []
        latest = prices.iloc[-1]
        past = prices.iloc[-self.config.lookback_bars]
        returns = (latest / past - 1.0).replace([float("inf"), float("-inf")], pd.NA).dropna()
        returns = returns.drop(labels=list(self.config.exclude_symbols), errors="ignore")
        returns = returns[returns >= self.config.min_return].sort_values(ascending=False)
        if self.config.freeze_selection and self._frozen_selection is not None:
            returns = pd.Series(self._frozen_scores or {}, dtype=float).reindex(self._frozen_selection).dropna()
        else:
            returns = returns.head(self.config.top_n)
            if self.config.freeze_selection:
                self._frozen_selection = [str(t).upper() for t in returns.index]
                self._frozen_scores = {str(t).upper(): float(v) for t, v in returns.items()}
        signals: list[TradeSignal] = []
        for ticker, ret in returns.items():
            score = float(ret)
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
                    score=score,
                    confidence=max(0.05, min(0.95, 0.40 + score / 2)),
                    expected_gross_edge_bps=score * 10_000,
                    expected_net_edge_bps=score * 10_000 - 25.0,
                    features={"lookback_return": score, "lookback_bars": self.config.lookback_bars},
                    notes="Slow trailing-momentum top-K baseline.",
                )
            )
        return signals

    def propose_intents(self, context: LabContext, signals: list[TradeSignal]) -> list[TradeIntent]:
        return [
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
                thesis=f"{signal.ticker} is in the top {self.config.top_n} by trailing {self.config.lookback_bars}-bar return.",
                invalidators=["falls out of top momentum book", "trend turns negative", "risk caps reject position"],
                metadata={"signal_id": signal.signal_id, **signal.features},
            )
            for signal in signals
        ]
