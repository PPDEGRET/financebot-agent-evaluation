"""Contracts for GPT-5.5/Hermes-style trading agents.

These objects are intentionally model-provider neutral. A Hermes/OpenClaw runner
can execute the prompts/tools; this package records what was asked, what model
setting was used, and what structured decision came back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from myaibot.core.models import TradeIntent, new_id, now_utc


ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
AgentRole = Literal["manager", "skeptic", "coder", "researcher", "execution_worker"]


class AgentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    lab_id: str | None = None
    role: AgentRole
    model: str = "gpt-5.5"
    reasoning_effort: ReasoningEffort = "high"
    mandate: str
    hard_rules: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class AgentInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str = Field(default_factory=lambda: new_id("invoke"))
    agent: AgentIdentity
    created_at: datetime = Field(default_factory=now_utc)
    data_cutoff: datetime
    task: str
    context_refs: list[str] = Field(default_factory=list)
    prompt_hash: str | None = None
    tool_budget: dict[str, int] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(default_factory=lambda: new_id("decision"))
    invocation_id: str
    created_at: datetime = Field(default_factory=now_utc)
    summary: str
    trade_intents: list[TradeIntent] = Field(default_factory=list)
    no_trade_reason: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    cited_refs: list[str] = Field(default_factory=list)
    self_critique: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningEffortExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(default_factory=lambda: new_id("effort_exp"))
    lab_id: str
    task_class: str
    efforts: list[ReasoningEffort] = Field(default_factory=lambda: ["medium", "high", "xhigh"])
    metric: str = "oos_net_return_after_costs_and_review_quality"
    notes: str = "Compare cost/latency/decision quality; keep cheapest setting that preserves OOS performance."
