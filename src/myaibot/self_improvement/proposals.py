"""Bounded self-improvement loop.

Agents may propose prompt/code/threshold changes, but promotion requires an
explicit experiment, allowed paths, and no frozen-test peeking.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from myaibot.core.models import ValidationIssue, new_id, now_utc


ProposalStatus = Literal["draft", "approved_for_experiment", "rejected", "promoted", "quarantined"]


class ChangeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: new_id("proposal"))
    proposer_agent_id: str
    created_at: datetime = Field(default_factory=now_utc)
    affected_paths: list[str]
    hypothesis: str
    validation_plan: str
    expected_metric: str
    train_window: str = "through_2024"
    validation_window: str = "2025_only"
    forbidden_test_window: str = "2026-01-01_to_2026-06-17"
    status: ProposalStatus = "draft"
    notes: str = ""


class SelfImprovementGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_prefixes: list[str] = Field(
        default_factory=lambda: [
            "labs/",
            "configs/experiments/",
            "research/experiments/",
            "src/myaibot/labs/",
        ]
    )
    forbidden_prefixes: list[str] = Field(
        default_factory=lambda: [
            "data/raw/",
            "data/manifests/",
            "shared/audit/immutable/",
            "fills/",
            "broker_statements/",
        ]
    )
    frozen_test_labels_forbidden: bool = True

    def review(self, proposal: ChangeProposal) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for raw_path in proposal.affected_paths:
            path = str(PurePosixPath(raw_path.replace("\\", "/")))
            if any(path.startswith(prefix) for prefix in self.forbidden_prefixes):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="FORBIDDEN_PATH",
                        message=f"Self-improvement proposal may not mutate immutable path: {raw_path}",
                    )
                )
            if not any(path.startswith(prefix) for prefix in self.allowed_prefixes):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="UNUSUAL_PATH",
                        message=f"Path is outside normal self-improvement surface: {raw_path}",
                    )
                )
        text = " ".join([proposal.hypothesis, proposal.validation_plan, proposal.notes]).lower()
        if self.frozen_test_labels_forbidden and any(marker in text for marker in ["2026 oos", "jan 2026", "jun 2026", "frozen test"]):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="FROZEN_TEST_PEEKING",
                    message="Proposal appears to use frozen OOS test results for selection. Treat as future hypothesis, not promotion evidence.",
                )
            )
        if not issues:
            issues.append(ValidationIssue(severity="info", code="GATE_PASSED", message="Proposal may become a frozen experiment."))
        return issues
