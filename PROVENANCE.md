# Provenance

## Source snapshot

- **Read-only source:** `C:/Users/henri/Desktop/FINANCEBOT`
- **Inspection / curation date:** 2026-07-14
- **Git branch:** `master`
- **HEAD at inspection:** `b91cf942056e7001b045a5d21c5403598981048f`
- **Working tree:** dirty — 11 modified entries and 32 untracked entries (`git status --porcelain=v1` SHA-256: `a714be1d6532ce410362d9a4b87b54bebcaf06bbf291bc5cbabd9979d1270b34`)

The commit identifies the last committed base only. The curated package also draws from safe uncommitted source files. It must not be described as a clean export of the recorded commit.

The source repository was treated as permanently read-only. No tests, formatters, installs, data jobs, or cache-generating commands were run inside it. Its `.git` directory was not copied.

## Curated from the source

Only allowlisted, authored, non-secret material was copied into this destination:

- Python modules under `src/myaibot/` covering contracts, replay, labs, agent adapters, deterministic risk validation, paper accounting, metrics, search, and safe market-context parsers;
- 47 existing test functions across 21 test files;
- three JSON schemas under `shared/schemas/`;
- the point-in-time liquidity filtering script required by an existing test;
- aggregate values transcribed from the January and Feb–Jun tournament leaderboards into `evidence/tournament-results.json`.

Raw leaderboard files were not copied because they contain local absolute paths and implementation details unnecessary for public evidence. The sanitized aggregate values were checked against:

- `research/experiments/tournament/january_round_enhanced_market_5_pi/leaderboard.json`
- `research/experiments/tournament/feb_jun_holdout/enhanced_market_top3_pi/leaderboard.json`
- `research/experiments/tournament/feb_jun_holdout/enhanced_market_hourly_pi/leaderboard.json`
- `research/experiments/tournament/EXPERIMENT_LOG.md`

## Portfolio-specific modifications

The destination is not a byte-for-byte mirror. Changes made for the showcase include:

- default-on `FINANCEBOT_SHOWCASE_MODE` capability guard;
- broker connect/disconnect/submit/reconcile/open-order paths blocked before side effects in showcase mode;
- Pi/Codex model invocation and yfinance price-download paths blocked in showcase mode;
- hard-coded private sibling-repository paths removed;
- three conditional external-repository tests converted to explicit temporary synthetic fixtures while preserving their test intent and count;
- network data-builder entry point guarded while retaining its point-in-time liquidity helper;
- a generated, hash-checked sample dataset and deterministic four-schedule replay;
- a sanitized, hash-verified synthetic decision trace derived from the real replay event flow;
- an order-notional cap added to deterministic validation so the declared risk field is enforced;
- a real-validator failure lab with ten rejected synthetic intents and one approved control;
- a standard-library SQLite fill journal with stable-ID idempotency, conflicting-redelivery rejection, append-only hash verification, and ledger restore;
- an interrupted-versus-uninterrupted recovery drill with fixed synthetic fills;
- 14 added showcase, trace, failure, recovery, data, and safety tests;
- sanitized evidence JSON, dashboard, diagrams, screenshots, reports, and demo script;
- a proposed frozen prospective paper-only protocol with digest verification.

## Original generated material in this package

These assets were generated specifically for the portfolio copy and do not come from external datasets:

- `data/synthetic/*.csv` and `manifest.json` — deterministic mathematical series produced by `scripts/generate_sample_data.py`;
- `artifacts/sample-replay.json` — deterministic local replay summary and sanitized decision trace;
- `artifacts/risk-failure-lab.json` — fixed outcomes produced by the real deterministic validator;
- `artifacts/recovery-drill.json` — fixed SQLite interruption, redelivery, hash-chain, and restored-ledger evidence;
- `dashboard/` — local HTML/CSS/JavaScript interface;
- `docs/diagrams/*.svg` — original explanatory diagrams;
- `docs/screenshots/*.png` — local screenshots of the dashboard;
- portfolio documentation and the prospective protocol.

The synthetic dataset has no external data license because it contains no external observations. It is distributed as part of this repository under Apache-2.0.

## Explicit exclusions

The following source categories were neither inspected for content nor copied:

- `.env` files, API keys, credentials, tokens, cookies, private keys, or auth state;
- `.pi*` directories, raw model sessions, model histories, prompts/responses, or transcripts;
- broker/account records, live orders, fills tied to an account, or private financial records;
- source `data/`, raw/proprietary datasets, market caches, and unrelated sibling-repository data;
- tournament stdout/PID/runtime state, raw event streams, detailed audit trails, and generated logs;
- `node_modules`, virtual environments, package caches, bytecode, test caches, build output, and generated dependencies;
- source `.git`;
- unrelated personal content.

The historical raw data needed to reproduce the Feb–Jun result remains excluded. The package therefore demonstrates code, control boundaries, aggregate evidence, and a synthetic replay—not full independent reproduction of the historical headline.

## Authorship and attribution

I am the source-project owner and author of the system thesis, research architecture, integrations, tournament design, and implementation present in the supplied repository. Exact line-by-line human-versus-AI authorship in the dirty source snapshot was not independently reconstructed.

I directed an AI coding assistant during portfolio closure. That work includes curation, fail-closed showcase safeguards, synthetic fixtures, decision-trace sanitization, validator failure cases, SQLite recovery implementation, dashboard implementation, diagrams, documentation, and verification.

Third-party systems and packages include Pi, OpenAI model runtimes, pandas, NumPy, Pydantic, PyYAML, python-dateutil, pytest, setuptools, and optional source integrations such as yfinance and `ib_async`. SQLite is used through Python’s standard library; no new recovery dependency was added. I claim the configuration, integration, policies, prompts, evaluation, and project-specific code—not authorship of those upstream systems.

No collaborator contributions, upstream fork lineage, or third-party asset rights were established beyond package attribution. No external visual assets, webfonts, or stock imagery were added to the portfolio copy.

## License and publication status

The curated portfolio copy uses Apache-2.0. The canonical license text is included at `LICENSE`, and `pyproject.toml` declares `Apache-2.0`. This license applies to the curated FINANCEBOT copy; named third-party dependencies and services retain their own licenses and terms.

Public repository publication was authorized on 2026-07-23 and completed on 2026-07-24 at <https://github.com/PPDEGRET/financebot-agent-evaluation>. No deployment, broker connection, live order, prospective activation, or communication of financial expectations was authorized.

Status: **published public-source portfolio case**, with the historical result framed as aggregate, caveated simulation evidence and the next gate kept prospective and paper-only.
