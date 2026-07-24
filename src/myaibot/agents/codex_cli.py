"""Codex/GPT-5.5 CLI runtime adapter.

The project assumes the local operator will authenticate the Codex CLI with their
own subscription/OAuth. This adapter never stores secrets. It only shells out to
a configured `codex` command after login has been completed by the user.

Because Codex CLI flags can evolve, the command is template-driven. The default
is conservative and can be overridden in config once the local CLI is confirmed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from myaibot.agents.contracts import AgentDecision, AgentInvocation, ReasoningEffort
from myaibot.core.showcase import require_external_runtime


@dataclass(frozen=True)
class CodexCliConfig:
    executable: str = "codex"
    model: str = "gpt-5.5"
    reasoning_effort: ReasoningEffort = "high"
    timeout_seconds: int = 900
    cwd: str | None = None
    # Template variables: {executable}, {model}, {reasoning_effort}, {prompt}
    # If your local Codex CLI differs, update this in config instead of editing code.
    command_template: list[str] = field(
        default_factory=lambda: [
            "{executable}",
            "exec",
            "--model",
            "{model}",
            "-c",
            "model_reasoning_effort={reasoning_effort}",
            "{prompt}",
        ]
    )
    extra_env: dict[str, str] = field(default_factory=dict)


class CodexCliUnavailable(RuntimeError):
    pass


class CodexCliRunner:
    """Run GPT-5.5/Codex as an external authenticated agent."""

    def __init__(self, config: CodexCliConfig | None = None) -> None:
        self.config = config or CodexCliConfig()

    def check_available(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [self.config.executable, "--version"],
                cwd=self.config.cwd,
                text=True,
                capture_output=True,
                timeout=20,
            )
        except FileNotFoundError:
            return False, f"{self.config.executable!r} not found on PATH. Install/login to Codex CLI first."
        except Exception as exc:  # pragma: no cover - platform/CLI dependent
            return False, f"Codex CLI check failed: {exc}"
        text = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, text or f"Codex CLI returned {proc.returncode}"
        return True, text

    def login_hint(self) -> str:
        return (
            "Authenticate outside Python using the local Codex CLI, e.g. run `codex login` "
            "or the login command required by your installed Codex version. Then run "
            "`python scripts/codex_login_check.py`. No OAuth token should be committed."
        )

    def run_text(self, prompt: str, *, timeout_seconds: int | None = None) -> str:
        require_external_runtime("Codex/model invocation")
        ok, detail = self.check_available()
        if not ok:
            raise CodexCliUnavailable(f"{detail}\n{self.login_hint()}")

        args = self._render_args(prompt)
        env = os.environ.copy()
        env.update(self.config.extra_env)
        proc = subprocess.run(
            args,
            cwd=self.config.cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds or self.config.timeout_seconds,
            env=env,
        )
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if proc.returncode != 0:
            raise RuntimeError(f"Codex CLI failed with {proc.returncode}:\n{output}")
        return output.strip()

    def run_decision(self, invocation: AgentInvocation, context_markdown: str) -> AgentDecision:
        prompt = self._decision_prompt(invocation, context_markdown)
        text = self.run_text(prompt)
        payload = extract_first_json_object(text)
        if payload is None:
            return AgentDecision(
                invocation_id=invocation.invocation_id,
                summary=text[:4000],
                trade_intents=[],
                no_trade_reason="Codex output was not structured JSON; captured as summary for audit.",
                confidence=0.0,
                metadata={"raw_output": text},
            )
        payload.setdefault("invocation_id", invocation.invocation_id)
        return AgentDecision.model_validate(payload)

    def _render_args(self, prompt: str) -> list[str]:
        values = {
            "executable": self.config.executable,
            "model": self.config.model,
            "reasoning_effort": self.config.reasoning_effort,
            "prompt": prompt,
        }
        return [part.format(**values) for part in self.config.command_template]

    def _decision_prompt(self, invocation: AgentInvocation, context_markdown: str) -> str:
        return f"""
# Agent Invocation

Agent: {invocation.agent.agent_id}
Role: {invocation.agent.role}
Model: {invocation.agent.model}
Reasoning effort: {invocation.agent.reasoning_effort}
Data cutoff: {invocation.data_cutoff.isoformat()}
Mandate: {invocation.agent.mandate}
Hard rules: {json.dumps(invocation.agent.hard_rules)}

# Task

{invocation.task}

# Context

{context_markdown}

# Required Output

Return a single JSON object compatible with `AgentDecision`:

{{
  "invocation_id": "{invocation.invocation_id}",
  "summary": "...",
  "trade_intents": [],
  "no_trade_reason": "... or null",
  "confidence": 0.0,
  "cited_refs": [],
  "self_critique": "...",
  "metadata": {{}}
}}
""".strip()


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from plain/markdown model output."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def load_codex_cli_config(path: str | Path) -> CodexCliConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = data.get("codex", data)
    allowed = {field.name for field in CodexCliConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return CodexCliConfig(**{k: v for k, v in cfg.items() if k in allowed})


def shell_quote_command(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)
