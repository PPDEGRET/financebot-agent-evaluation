#!/usr/bin/env python3
"""Fail closed if the staged FINANCEBOT release contains unsafe or unintended files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 5 * 1024 * 1024
REQUIRED = {
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".gitignore",
    "LICENSE",
    "README.md",
    "SHOWCASE_PLAN.md",
    "PROVENANCE.md",
    "pyproject.toml",
    "artifacts/sample-replay.json",
    "artifacts/risk-failure-lab.json",
    "artifacts/recovery-drill.json",
    "dashboard/index.html",
    "docs/evidence-and-limitations.md",
    "docs/inspectability-and-recovery.md",
    "docs/verification.md",
    "evidence/tournament-results.json",
    "protocol/frozen-paper-v1.json",
    "scripts/validate_package.py",
    "src/myaibot/execution/journal.py",
}
FORBIDDEN_PARTS = {
    ".git",
    ".pi",
    ".pi-subagents",
    ".playwright-mcp",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".env",
    ".key",
    ".log",
    ".parquet",
    ".pem",
    ".pickle",
    ".pkl",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}
LOCAL_USER_PATHS = (b"C:/" + b"Users/" + b"henri", b"C:\\" + b"Users\\" + b"henri")
SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "OpenAI-style secret": re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(rb"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    "Slack token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
}


def git(*args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=text)


def staged_blob(path: str) -> bytes:
    return git("show", f":{path}", text=False)  # type: ignore[return-value]


def main() -> None:
    problems: list[str] = []
    try:
        inside = git("rev-parse", "--is-inside-work-tree").strip()
    except subprocess.CalledProcessError as exc:
        raise SystemExit("Release staging check requires an initialized Git repository.") from exc
    if inside != "true":
        problems.append("destination is not a Git working tree")

    staged = [
        line.strip()
        for line in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines()
        if line.strip()
    ]
    if not staged:
        problems.append("no files are staged")
    missing = sorted(REQUIRED - set(staged))
    problems.extend(f"required release file is not staged: {path}" for path in missing)

    untracked = [line for line in git("ls-files", "--others", "--exclude-standard").splitlines() if line]
    problems.extend(f"untracked and not ignored: {path}" for path in untracked)

    total_bytes = 0
    for path in staged:
        normalized = Path(path)
        lowered_parts = {part.lower() for part in normalized.parts}
        if lowered_parts & FORBIDDEN_PARTS:
            problems.append(f"forbidden staged path: {path}")
            continue
        lowered_name = normalized.name.lower()
        if lowered_name.startswith(".env") or any(lowered_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            problems.append(f"forbidden staged file type/name: {path}")
            continue
        if lowered_name.endswith(".egg-info") or any(part.lower().endswith(".egg-info") for part in normalized.parts):
            problems.append(f"generated package metadata staged: {path}")
            continue

        blob = staged_blob(path)
        total_bytes += len(blob)
        if len(blob) > MAX_FILE_BYTES:
            problems.append(f"staged file exceeds 5 MiB: {path}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(blob):
                problems.append(f"possible {label} in staged file: {path}")
        if path != "PROVENANCE.md" and any(local_path in blob for local_path in LOCAL_USER_PATHS):
            problems.append(f"local user path outside PROVENANCE.md: {path}")

    license_text = staged_blob("LICENSE") if "LICENSE" in staged else b""
    if b"Apache License" not in license_text or b"Version 2.0, January 2004" not in license_text:
        problems.append("LICENSE is not recognizable as Apache License 2.0")
    pyproject = staged_blob("pyproject.toml") if "pyproject.toml" in staged else b""
    if b'license = "Apache-2.0"' not in pyproject:
        problems.append("pyproject.toml does not declare Apache-2.0")

    whitespace = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--check"],
        text=True,
        capture_output=True,
        check=False,
    )
    if whitespace.returncode:
        problems.append("staged diff whitespace check failed:\n" + whitespace.stdout.strip())

    if problems:
        raise SystemExit("Release staging check failed:\n- " + "\n- ".join(problems))
    print(
        f"Release staging check passed: {len(staged)} files, "
        f"{total_bytes / (1024 * 1024):.2f} MiB, Apache-2.0, no forbidden paths or high-confidence secrets."
    )


if __name__ == "__main__":
    main()
