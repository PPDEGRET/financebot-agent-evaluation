"""GPT-5.5 trading-manager orchestration.

This is the agentic decision layer: deterministic labs can produce candidate
signals, then GPT-5.5/Codex can approve, reject, or rewrite trade intents before
risk validation. It is disabled until Codex OAuth/login is available locally.
"""

from __future__ import annotations

import json
from datetime import datetime

from typing import Protocol

from myaibot.agents.contracts import AgentDecision, AgentIdentity, AgentInvocation, ReasoningEffort
from myaibot.agents.pi_cli import PiCliRunner
from myaibot.core.models import MarketSnapshot, PortfolioState, TradeSignal


class DecisionRunner(Protocol):
    def run_decision(self, invocation: AgentInvocation, context_markdown: str, *, continue_session: bool | None = None) -> AgentDecision: ...


class GptTradingManager:
    def __init__(
        self,
        *,
        lab_id: str = "ensemble",
        model: str = "gpt-5.5",
        reasoning_effort: ReasoningEffort = "high",
        runner: DecisionRunner | None = None,
    ) -> None:
        self.identity = AgentIdentity(
            agent_id=f"{lab_id}.manager.{model}",
            lab_id=lab_id,
            role="manager",
            model=model,
            reasoning_effort=reasoning_effort,
            mandate="Decide long-only stock/ETF trades from timestamp-safe signals and evidence.",
            hard_rules=[
                "Use only available evidence with available_at <= data_cutoff.",
                "Allowed actions: buy, hold, trim, close, no_trade.",
                "No shorts, options, leverage, or margin.",
                "Every buy must include thesis, invalidators, confidence, horizon, expected edge, and evidence refs.",
                "Prefer no_trade when evidence is weak.",
            ],
        )
        self.runner = runner or PiCliRunner()

    def decide(
        self,
        *,
        signals: list[TradeSignal],
        portfolio: PortfolioState,
        market: MarketSnapshot,
        data_cutoff: datetime,
        task: str | None = None,
    ) -> AgentDecision:
        invocation = AgentInvocation(
            agent=self.identity,
            data_cutoff=data_cutoff,
            task=task or "Review candidate signals and return approved long-only trade intents or a no_trade reason.",
        )
        context = self._context(signals=signals, portfolio=portfolio, market=market)
        return self.runner.run_decision(invocation, context, continue_session=True)

    def _context(self, *, signals: list[TradeSignal], portfolio: PortfolioState, market: MarketSnapshot) -> str:
        payload = {
            "portfolio": portfolio.model_dump(mode="json"),
            "market": market.model_dump(mode="json"),
            "signals": [signal.model_dump(mode="json") for signal in signals],
        }
        return "# Timestamp-safe candidate context\n\n```json\n" + json.dumps(payload, indent=2, default=str) + "\n```"
