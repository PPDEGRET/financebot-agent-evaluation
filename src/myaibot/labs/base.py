"""Base contracts for strategy labs.

Labs can be deterministic scanners, GPT-5.5/Hermes-style manager agents, or a
hybrid. Their only trading output is a structured `TradeIntent`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from myaibot.core.models import PortfolioState, TradeIntent, TradeSignal


class LabContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    as_of: datetime
    data_cutoff: datetime
    portfolio: PortfolioState
    price_history: pd.DataFrame
    alt_data: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)

    def prices_as_of(self) -> dict[str, float]:
        if self.price_history.empty:
            return {}
        last = self.price_history.iloc[-1].dropna()
        return {str(k).upper(): float(v) for k, v in last.items() if float(v) > 0}


class BaseLab(ABC):
    lab_id: str
    strategy_id: str
    strategy_version: str

    def __init__(self, *, lab_id: str, strategy_id: str, strategy_version: str = "0.1.0") -> None:
        self.lab_id = lab_id
        self.strategy_id = strategy_id
        self.strategy_version = strategy_version

    @abstractmethod
    def generate_signals(self, context: LabContext) -> list[TradeSignal]:
        """Generate timestamp-safe lab signals from the supplied context."""

    @abstractmethod
    def propose_intents(self, context: LabContext, signals: list[TradeSignal]) -> list[TradeIntent]:
        """Convert signals into trade intents for deterministic validation."""
