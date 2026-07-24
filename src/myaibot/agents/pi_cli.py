"""Pi CLI runtime adapter for the trading agent.

Pi is the preferred runtime for the true agentic tests because it has a minimal,
controllable prompt surface and can run with our project docs/tools/memory rather
than Codex CLI's built-in coding-agent prompt conventions.

Authentication is performed outside Python with `pi` + `/login`. This adapter
stores no credentials.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from myaibot.agents.codex_cli import extract_first_json_object
from myaibot.agents.contracts import AgentDecision, AgentInvocation, ReasoningEffort
from myaibot.core.showcase import require_external_runtime


@dataclass(frozen=True)
class PiCliConfig:
    executable: str = "pi"
    model: str | None = None
    thinking: ReasoningEffort = "high"
    timeout_seconds: int = 1800
    cwd: str | None = None
    agent_dir: str | None = ".pi-agent-home"
    session_dir: str | None = ".pi/sessions"
    continue_session: bool = False
    print_mode: bool = True
    allow_tools: list[str] = field(default_factory=lambda: ["read", "write", "edit", "bash", "grep", "find", "ls"])
    no_context_files: bool = False
    system_prompt: str | None = None
    extra_args: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)


class PiCliUnavailable(RuntimeError):
    pass


class PiCliRunner:
    def __init__(self, config: PiCliConfig | None = None) -> None:
        self.config = config or PiCliConfig()

    def check_available(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [self._executable(), "--version"],
                cwd=self.config.cwd,
                text=True,
                capture_output=True,
                timeout=20,
            )
        except FileNotFoundError:
            return False, f"{self.config.executable!r} not found on PATH. Install Pi first."
        except Exception as exc:  # pragma: no cover - platform dependent
            return False, f"Pi CLI check failed: {exc}"
        text = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, text or f"Pi CLI returned {proc.returncode}"
        return True, text

    def login_hint(self) -> str:
        return (
            "Run `pi`, then type `/login` and choose the OpenAI ChatGPT/Codex subscription provider. "
            "Then select the target model with `/model`, or run this adapter with `--model openai/<model-id>`."
        )

    def run_text(self, prompt: str, *, continue_session: bool | None = None, timeout_seconds: int | None = None) -> str:
        require_external_runtime("Pi/model invocation")
        ok, detail = self.check_available()
        if not ok:
            raise PiCliUnavailable(f"{detail}\n{self.login_hint()}")
        args = self._build_args(prompt, continue_session=continue_session)
        env = self._env()
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
            raise RuntimeError(f"Pi CLI failed with {proc.returncode}:\n{output}")
        return output.strip()

    def run_decision(self, invocation: AgentInvocation, context_markdown: str, *, continue_session: bool | None = None) -> AgentDecision:
        text = self.run_text(self._decision_prompt(invocation, context_markdown), continue_session=continue_session)
        payload = extract_first_json_object(text)
        if payload is None:
            return AgentDecision(
                invocation_id=invocation.invocation_id,
                summary=text[:4000],
                trade_intents=[],
                no_trade_reason="Pi output was not structured JSON; captured as summary for audit.",
                confidence=0.0,
                metadata={"raw_output": text},
            )
        payload.setdefault("invocation_id", invocation.invocation_id)
        return AgentDecision.model_validate(payload)

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("PI_SKIP_VERSION_CHECK", "1")
        base = Path(self.config.cwd or os.getcwd())
        if self.config.agent_dir:
            agent_dir = Path(self.config.agent_dir)
            if not agent_dir.is_absolute():
                agent_dir = base / agent_dir
            env.setdefault("PI_CODING_AGENT_DIR", str(agent_dir))
        if self.config.session_dir:
            session_dir = Path(self.config.session_dir)
            if not session_dir.is_absolute():
                session_dir = base / session_dir
            env.setdefault("PI_CODING_AGENT_SESSION_DIR", str(session_dir))
        env.update(self.config.extra_env)
        return env

    def _executable(self) -> str:
        found = shutil.which(self.config.executable) or shutil.which(f"{self.config.executable}.cmd")
        if found:
            return found
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidate = Path(appdata) / "npm" / f"{self.config.executable}.cmd"
            if candidate.exists():
                return str(candidate)
        return self.config.executable

    def _build_args(self, prompt: str, *, continue_session: bool | None = None) -> list[str]:
        cfg = self.config
        args = [self._executable()]
        if cfg.print_mode:
            args.append("-p")
        if continue_session if continue_session is not None else cfg.continue_session:
            args.append("-c")
        if cfg.model:
            args.extend(["--model", cfg.model])
        if cfg.thinking:
            args.extend(["--thinking", cfg.thinking])
        if cfg.session_dir:
            args.extend(["--session-dir", cfg.session_dir])
        if cfg.no_context_files:
            args.append("--no-context-files")
        if cfg.allow_tools:
            args.extend(["--tools", ",".join(cfg.allow_tools)])
        if cfg.system_prompt:
            args.extend(["--system-prompt", cfg.system_prompt])
        args.extend(cfg.extra_args)
        args.append(prompt)
        return args

    def _decision_prompt(self, invocation: AgentInvocation, context_markdown: str) -> str:
        return f"""
You are running as a controlled Pi trading agent inside `myaibot`.

Agent: {invocation.agent.agent_id}
Role: {invocation.agent.role}
Model: {invocation.agent.model}
Thinking: {invocation.agent.reasoning_effort}
Data cutoff: {invocation.data_cutoff.isoformat()}
Mandate: {invocation.agent.mandate}
Hard rules: {json.dumps(invocation.agent.hard_rules)}

Task:
{invocation.task}

Context:
{context_markdown}

Return exactly one JSON object compatible with AgentDecision. Do not wrap it in prose.
""".strip()


def load_pi_cli_config(path: str | Path) -> PiCliConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = data.get("pi", data)
    allowed = {field.name for field in PiCliConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return PiCliConfig(**{k: v for k, v in cfg.items() if k in allowed})
