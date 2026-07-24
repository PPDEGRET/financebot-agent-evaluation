"""Atlas lab: deterministic market-regime exposure governor."""

from __future__ import annotations

from dataclasses import dataclass

from myaibot.core.models import TradeIntent, TradeSignal
from myaibot.labs.base import BaseLab, LabContext


@dataclass(frozen=True)
class AtlasRegimeConfig:
    benchmark: str = "SPY"
    fast_bars: int = 20
    slow_bars: int = 100


class AtlasRegimeLab(BaseLab):
    """Emits a synthetic regime signal.

    The current replay engine uses `MarketSnapshot.regime_multiplier`; this lab
    exists so the same computation can be audited and later replaced/enriched by
    GPT-5.5 macro reasoning.
    """

    def __init__(self, config: AtlasRegimeConfig | None = None) -> None:
        super().__init__(lab_id="atlas_regime", strategy_id="spy_trend_regime")
        self.config = config or AtlasRegimeConfig()

    def regime_multiplier(self, context: LabContext) -> float:
        prices = context.price_history
        if self.config.benchmark not in prices.columns or len(prices) < self.config.slow_bars:
            return 0.5
        series = prices[self.config.benchmark].dropna()
        if len(series) < self.config.slow_bars:
            return 0.5
        latest = float(series.iloc[-1])
        fast = float(series.iloc[-self.config.fast_bars :].mean())
        slow = float(series.iloc[-self.config.slow_bars :].mean())
        if latest > fast > slow:
            return 1.0
        if latest > slow:
            return 0.7
        return 0.25

    def generate_signals(self, context: LabContext) -> list[TradeSignal]:
        mult = self.regime_multiplier(context)
        return [
            TradeSignal(
                lab_id=self.lab_id,
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                ticker=self.config.benchmark,
                as_of=context.as_of,
                data_cutoff=context.data_cutoff,
                horizon_days=20,
                direction="long" if mult >= 0.7 else "flat",
                score=mult,
                confidence=0.75,
                expected_gross_edge_bps=None,
                expected_net_edge_bps=None,
                features={"regime_multiplier": mult},
                notes="Portfolio-level regime signal; not a direct trade intent.",
            )
        ]

    def propose_intents(self, context: LabContext, signals: list[TradeSignal]) -> list[TradeIntent]:
        return []
