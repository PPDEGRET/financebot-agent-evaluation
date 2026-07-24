# Verification record

**Date:** 2026-07-14

**Destination:** `03-financebot/` portfolio copy

**Package:** `financebot-showcase 1.1.0`

**Python:** 3.11.9

All project commands ran from the portfolio destination. No test, install, formatter, or data-generation command ran inside the read-only source repository.

## Installation

```bash
python -m pip install -e ".[dev]"
```

Result: editable wheel built and `financebot-showcase 1.1.0` installed successfully. Declared dependencies were already available. Installed metadata reports `License-Expression: Apache-2.0` and `License-File: LICENSE`.

```bash
python -m pip check
```

Result: `No broken requirements found.`

## Deterministic dataset and replay

```bash
python scripts/generate_sample_data.py --check
```

Result: three generated CSV files and the manifest matched deterministic bytes and hashes.

```bash
python scripts/run_showcase_replay.py --check
```

Result: the fresh four-schedule replay, sanitized decision trace, trace digest, and configuration digest matched `artifacts/sample-replay.json` byte-for-byte.

| Synthetic schedule | Policy calls | Paper fills | Synthetic return | Drawdown |
|---|---:|---:|---:|---:|
| Open only | 15 | 11 | +0.36% | -0.08% |
| Open + close | 29 | 17 | +0.41% | -0.08% |
| Three times daily | 44 | 20 | +0.34% | -0.11% |
| Hourly | 104 | 49 | +0.36% | -0.11% |

These generated-fixture values have no financial meaning.

The trace check additionally verified:

- strict `data_cutoff < decision_time < execution_time` ordering;
- two deterministic candidates, approvals, later fills, and ledger updates;
- zero external model calls;
- no random intent/order/fill/decision identifiers in the sanitized trace;
- canonical SHA-256 trace integrity.

## Risk failure lab

```bash
python scripts/run_risk_failure_lab.py --check
```

Result: ten committed rejections matched fresh output from the real `RiskValidator`; one valid long-only control was approved. Covered codes:

```text
WOULD_SHORT · MISSING_PRICE · PRICE_TOO_LOW · LIQUIDITY_TOO_LOW
EDGE_TOO_LOW · ORDER_CAP · INSUFFICIENT_CASH · MAX_POSITIONS
SECTOR_CAP · POSITION_CAP
```

The order-cap regression also directly verifies enforcement of `max_order_notional_weight`.

## SQLite interruption and recovery

```bash
python scripts/run_recovery_drill.py --check
```

Result:

- 2 simulated interruptions;
- 4 durable fill events;
- 3 duplicate deliveries attempted and suppressed;
- append-only payload/link/head hash chain verified;
- interrupted and uninterrupted cash, positions, fill count, realized P&L, market value, and equity matched exactly;
- state digest: `91d49429603d7b820edfd50542e858a75503a9e8675dc771b61c59fc07b69bca`.

The test suite also verifies conflicting redelivery rejection and direct SQLite payload-tampering detection.

Scope: transaction-level paper-fill recovery only. The runner still lacks durable pending-decision, model-session, schedule, and full tournament-state recovery.

## Safety, frozen protocol, and package claims

```bash
python scripts/check_showcase_safety.py
```

Result: eight external/live capability paths blocked before side effects: broker connect, disconnect, submission, reconciliation, open-order lookup; Pi/model invocation; Codex/model invocation; and network price download.

```bash
python scripts/freeze_protocol.py --check
```

Result: protocol and brief verified at configuration digest:

```text
4624bd6ad15eb6895e533543398cb91e31185ac2f1ede13f50f15af111933509
```

```bash
python scripts/validate_package.py
```

Result: required artifacts present; 47 curated source tests and 14 showcase tests counted; historical constants, required caveat co-display, synthetic labels, trace hash, failure outcomes, recovery invariants, protocol status, and prohibited claims checked.

## Tests

Curated source suite only:

```bash
python -m pytest -q --ignore=tests/showcase
```

Result: **47 passed in 3.10s**.

Complete package:

```bash
python -m pytest -q
```

Result:

```text
.............................................................            [100%]
61 passed in 3.68s
```

The total comprises the 47-test curated suite plus 14 showcase, trace, risk, recovery, data, and safety tests. Three source tests that conditionally referenced private sibling repositories were adapted to explicit temporary synthetic fixtures; their count and adapter behaviors were preserved without touching private data.

## Browser validation

Served locally with:

```bash
python -m http.server 8000
```

Checked `http://127.0.0.1:8000/dashboard/` with Playwright at:

- desktop: 1440 × 1250;
- mobile: 390 × 844.

Verified:

- five local JSON requests returned 200: aggregate evidence, replay/trace, failure lab, recovery drill, and protocol;
- no external network request;
- zero console errors or warnings;
- 4 historical rows, 4 synthetic schedule rows, 6 trace stages, 11 failure/control cases, and 6 recovery steps rendered;
- required highlighted-result metrics and all six caveats remained co-displayed in the results view;
- no page-level horizontal overflow at desktop or mobile width;
- skip link was the first keyboard focus target;
- all in-page hash links resolved, and the dashboard Apache-2.0 link returned the staged `LICENSE` file;
- SVG chart and native failure-case disclosures retained accessible names/keyboard behavior.

Preferred screenshots:

- `docs/screenshots/hero-desktop.png`
- `docs/screenshots/results-desktop.png`
- `docs/screenshots/trace-desktop.png`
- `docs/screenshots/controls-desktop.png`

## Git and release staging

A standalone repository was initialized on `main`. No remote is configured and no commit was created. `.github/workflows/ci.yml` mirrors the deterministic checks and 61-test suite on Python 3.11 with read-only repository permissions; its first hosted run is intentionally pending publication.

`.gitattributes` enforces LF text in clones so deterministic byte comparisons remain portable. `.gitignore` excludes caches, editable-install metadata, local Pi/Playwright artifacts, raw-data file types, SQLite files, logs, environments, and the superseded fifth screenshot.

```bash
python scripts/check_release_staging.py
```

Result:

```text
Release staging check passed: 132 files, 1.30 MiB, Apache-2.0, no forbidden paths or high-confidence secrets.
```

The staged review additionally verified:

- required release, evidence, protocol, diagram, test, and licensing files are present;
- no untracked nonignored files;
- no staged `.pi*`, cache, environment, dependency, database, log, private-key, or raw-data path;
- high-confidence credential patterns absent from every staged blob;
- local user paths appear only in `PROVENANCE.md`, where the read-only source path is intentionally recorded;
- exactly four screenshots are staged;
- largest staged file is below 250 KiB and no staged file exceeds 5 MiB;
- `git diff --cached --check` passes;
- ignored `.pi-subagents`, pytest cache, bytecode, editable metadata, and superseded screenshot are not part of the index.

### Clean staged-export rehearsal

The staged tree was exported with `git archive` into an empty temporary directory—not copied from the working tree. The export contained exactly 132 files and no `.git`, `.pi-subagents`, pytest cache, or editable-install metadata. From that isolated export:

- every deterministic artifact/safety/protocol/package check passed;
- the complete suite passed: **61 passed**;
- no ignored local tooling file was required for setup or verification.

This is the closest local rehearsal of the first public clone without creating a commit or remote.

## Intentionally not run

- Historical Feb–Jun replay: raw/proprietary source data remains excluded.
- Pi/OpenAI/Codex model calls: blocked in showcase mode and unnecessary for the deterministic proofs.
- Broker connection, account reconciliation, or order submission: prohibited and blocked.
- Prospective paper protocol: proposed but not activated; runner integration and durable pending-decision/tournament recovery remain open gates.
- Deployment, publication, Git push, outreach, or external communication: not authorized.
