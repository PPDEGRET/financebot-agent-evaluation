# Release checklist

**Target:** public portfolio repository for the curated `03-financebot/` copy only

**Status:** prepared and staged locally; public repository publication authorized on 2026-07-23

**License:** Apache-2.0

## Scope boundary

- [x] Read-only source repository remains separate and unchanged.
- [x] Public package contains curated authored code, focused tests, generated fixtures, sanitized aggregate evidence, and original portfolio assets only.
- [x] Raw source datasets, model sessions, prompts/responses, broker/account information, credentials, caches, logs, and generated dependencies are excluded by the staged allowlist and `.gitignore`.
- [x] Historical simulation claims retain their required metrics, caveats, and no-live-capital boundary.
- [x] Showcase mode blocks external model, broker, and network price-download paths by default.

## Engineering and evidence

- [x] 47-test curated source suite passes.
- [x] 61-test complete package passes.
- [x] Synthetic dataset, replay/trace, risk failure lab, recovery drill, protocol digest, safety, and package checks pass.
- [x] Desktop/mobile dashboard checks pass without console errors, external requests, broken anchors, or page overflow.
- [x] SQLite recovery scope is stated accurately: paper-fill transactions only, not full model/tournament recovery.
- [x] Exact commands and verification evidence are recorded in `docs/verification.md`.
- [x] A read-only GitHub Actions workflow mirrors the deterministic checks and 61-test suite on Python 3.11; its first hosted run necessarily waits for publication.

## Licensing and attribution

- [x] Canonical Apache License 2.0 text is included in `LICENSE`.
- [x] `pyproject.toml` declares `Apache-2.0` and includes the license file.
- [x] README links the license and keeps third-party licenses/terms separate.
- [x] `PROVENANCE.md` records the source snapshot, dirty status, curation, AI assistance, exclusions, and upstream attribution boundaries.

## Git release gate

- [x] Standalone repository initialized on `main` with no remote.
- [x] Release files staged; ignored tooling/caches are not part of the index.
- [x] Staged filenames, sizes, whitespace, high-confidence secrets, local paths, required artifacts, and license metadata checked by `scripts/check_release_staging.py`.
- [x] No commit, remote, push, deployment, public repository, or hosted CI run has been created.
- [x] Public repository publication authorized on 2026-07-23.

## Suggested public metadata

**Repository name:** `financebot-agent-evaluation`

**Description:** `Offline quantitative-research case study for evaluating agent cadence inside deterministic risk, paper-ledger, and recovery controls.`

**Topics:** `applied-ai`, `agent-evaluation`, `quantitative-research`, `simulation`, `risk-controls`, `python`, `sqlite`, `portfolio-project`

## Actions requiring separate authorization

1. Re-run `python scripts/check_release_staging.py`.
2. Create the initial local commit from the reviewed index.
3. Create or select the public remote.
4. Push `main` without adding ignored local artifacts.
5. Verify the rendered README, license detection, screenshots, and repository description on the hosting platform.

No deployment or prospective protocol activation is implied by repository publication.
