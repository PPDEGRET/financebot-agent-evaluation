#!/usr/bin/env python3
"""Validate portfolio evidence language, required files, and test-suite counts."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = [
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".gitignore",
    "LICENSE",
    "README.md",
    "RELEASE_CHECKLIST.md",
    "SHOWCASE_PLAN.md",
    "PROVENANCE.md",
    "artifacts/sample-replay.json",
    "artifacts/risk-failure-lab.json",
    "artifacts/recovery-drill.json",
    "dashboard/index.html",
    "data/synthetic/manifest.json",
    "docs/architecture.md",
    "docs/evidence-and-limitations.md",
    "docs/inspectability-and-recovery.md",
    "docs/problem-and-market.md",
    "docs/demo-script.md",
    "docs/prospective-paper-protocol.md",
    "docs/verification.md",
    "docs/diagrams/architecture.svg",
    "docs/diagrams/data-flow.svg",
    "docs/diagrams/tournament-selection.svg",
    "docs/diagrams/risk-control.svg",
    "docs/diagrams/recovery-drill.svg",
    "docs/screenshots/hero-desktop.png",
    "docs/screenshots/results-desktop.png",
    "docs/screenshots/trace-desktop.png",
    "docs/screenshots/controls-desktop.png",
    "evidence/tournament-results.json",
    "protocol/frozen-paper-v1.json",
    "scripts/run_risk_failure_lab.py",
    "scripts/run_recovery_drill.py",
    "scripts/check_release_staging.py",
    "src/myaibot/execution/journal.py",
]
BANNED_CLAIMS = ("ai " + "beat " + "the " + "market", "profitable " + "stra" + "tegy")
CONTEXT_STEMS = (
    "21.65",
    "0.47",
    "15.54",
    "current-listing-universe bias",
    "paper execution assumptions",
    "repeated-testing risk",
    "short historical regime",
    "non-deterministic model decisions",
    "no live capital",
)


def test_function_count(path: Path) -> int:
    count = 0
    for file in path.rglob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        count += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
    return count


def main() -> None:
    problems: list[str] = []
    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).is_file():
            problems.append(f"missing required file: {relative}")

    original_tests = test_function_count(ROOT / "tests") - test_function_count(ROOT / "tests" / "showcase")
    showcase_tests = test_function_count(ROOT / "tests" / "showcase")
    if original_tests != 47:
        problems.append(f"expected 47 curated original tests, found {original_tests}")
    if showcase_tests != 14:
        problems.append(f"expected 14 showcase tests, found {showcase_tests}")

    public_files = [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "docs" / "diagrams").glob("*.svg"),
        *(ROOT / "dashboard").glob("*.html"),
    ]
    for path in public_files:
        text = path.read_text(encoding="utf-8").lower()
        for claim in BANNED_CLAIMS:
            if claim in text:
                problems.append(f"banned claim in {path.relative_to(ROOT)}: {claim}")
        if "+26.34%" in text:
            missing = [stem for stem in CONTEXT_STEMS if stem not in text]
            if missing:
                problems.append(f"incomplete Feb–Jun context in {path.relative_to(ROOT)}: {', '.join(missing)}")

    evidence = json.loads((ROOT / "evidence" / "tournament-results.json").read_text(encoding="utf-8"))
    comparison = evidence["historical_evidence"]["comparison_window"]
    highlight = next(row for row in comparison["variants"] if row["id"] == "open_close")
    expected = {
        "simulated_return": 0.2634153532,
        "max_drawdown": -0.2165398536,
        "reported_sharpe": 0.4666382563,
        "benchmark": 0.1553662349,
        "fills": 94,
        "model_calls": 190,
    }
    for key, value in expected.items():
        if highlight[key] != value:
            problems.append(f"historical evidence mismatch: open_close.{key}")
    if len(evidence["required_caveats"]) != 6:
        problems.append("historical evidence must carry exactly six required caveat entries")

    replay = json.loads((ROOT / "artifacts" / "sample-replay.json").read_text(encoding="utf-8"))
    if replay.get("label") != "Synthetic demonstration" or replay.get("external_model_calls") != 0 or replay.get("broker_connections") != 0:
        problems.append("sample replay is not clearly offline/synthetic")
    if replay.get("showcase_config_sha256") != hashlib.sha256((ROOT / "configs" / "showcase.yaml").read_bytes()).hexdigest():
        problems.append("sample replay is not bound to the committed showcase configuration")
    trace = replay.get("decision_trace", {})
    trace_digest = trace.pop("trace_sha256", None)
    canonical_trace = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if not trace.get("strict_timestamp_ordering") or trace_digest != hashlib.sha256(canonical_trace).hexdigest():
        problems.append("sample decision trace is missing or does not verify")
    if trace_digest is not None:
        trace["trace_sha256"] = trace_digest

    failure_lab = json.loads((ROOT / "artifacts" / "risk-failure-lab.json").read_text(encoding="utf-8"))
    if failure_lab.get("blocked_cases") != 10 or failure_lab.get("approved_controls") != 1:
        problems.append("risk failure-lab artifact does not contain the expected real-validator outcomes")

    recovery = json.loads((ROOT / "artifacts" / "recovery-drill.json").read_text(encoding="utf-8"))
    if not recovery.get("journal_verified") or not recovery.get("exact_state_match"):
        problems.append("recovery drill does not prove a verified exact state match")
    if recovery.get("duplicate_deliveries_suppressed") != 3 or recovery.get("durable_events") != 4:
        problems.append("recovery drill idempotency counts changed")

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "Apache License" not in license_text or "Version 2.0, January 2004" not in license_text:
        problems.append("LICENSE is not recognizable as Apache License 2.0")
    if 'license = "Apache-2.0"' not in pyproject_text:
        problems.append("pyproject.toml does not declare Apache-2.0")

    protocol = json.loads((ROOT / "protocol" / "frozen-paper-v1.json").read_text(encoding="utf-8"))
    if protocol.get("status") != "proposed_not_started" or protocol["authorization"].get("live_capital") is not False:
        problems.append("prospective protocol status or live-capital boundary changed")

    if problems:
        raise SystemExit("Package validation failed:\n- " + "\n- ".join(problems))
    print(f"Package validation passed: {original_tests} curated tests + {showcase_tests} showcase tests; evidence and claims checked.")


if __name__ == "__main__":
    main()
