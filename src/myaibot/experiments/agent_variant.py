"""Agent variant registry/snapshot utilities."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentVariant:
    variant_id: str
    description: str
    summon_policy: str
    brief_path: str
    runner_args: dict[str, Any] = field(default_factory=dict)
    data_access: list[str] = field(default_factory=lambda: ["hourly_prices", "daily_prices", "wsb_counts_pre_today"])
    model: str = "gpt-5.5"
    thinking: str = "high"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def write_variant(root: str | Path, variant: AgentVariant, brief: str) -> Path:
    path = Path(root) / variant.variant_id
    path.mkdir(parents=True, exist_ok=True)
    brief_path = path / "brief.md"
    config_path = path / "variant.json"
    brief_path.write_text(brief, encoding="utf-8")
    data = asdict(variant)
    data["brief_path"] = str(brief_path)
    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    (path / "README.md").write_text(
        f"# {variant.variant_id}\n\n{variant.description}\n\nSummon policy: `{variant.summon_policy}`\n\nBrief: `brief.md`\n",
        encoding="utf-8",
    )
    return path


def load_variants(root: str | Path) -> list[AgentVariant]:
    variants: list[AgentVariant] = []
    for config in sorted(Path(root).glob("*/variant.json")):
        variants.append(AgentVariant(**json.loads(config.read_text(encoding="utf-8"))))
    return variants
