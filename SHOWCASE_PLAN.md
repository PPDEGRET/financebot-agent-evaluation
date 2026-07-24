# FINANCEBOT Showcase Plan

**Closure type:** Package — preserve the working research substrate and aggregate experiment evidence, then add a safe deterministic replay and a clearer public-facing results interface.

**Completion status (2026-07-23):** Implemented and validated in the destination. Apache-2.0 applies, public repository publication is authorized, and 132 release files passed staged review in a standalone `main` repository. See [`docs/verification.md`](docs/verification.md).

## Core promise

FINANCEBOT demonstrates a disciplined way to evaluate model-driven trading decisions without giving a model control of execution: timestamp-bounded context enters a model or deterministic decision policy, structured intents pass through deterministic long-only risk checks, and approved orders fill only in a paper ledger. The portfolio case focuses on agent-invocation frequency as an experimental variable, not on presenting an investable strategy.

## Intended audience

- Applied-AI and agent-system builders evaluating where deterministic controls belong.
- Product and technical leaders interested in reproducible experimentation, stateful workflows, and honest failure evidence.
- Quantitative-research reviewers who can assess replay assumptions, selection bias, benchmarks, and limitations.

## Problem and thesis

**Problem:** More agent calls can look like more intelligence while actually adding cost, churn, and noisy decisions. In financial simulations, that effect is easy to hide behind a single headline return.

**Thesis:** Agent cadence should be treated as a controlled experimental variable. Model judgment may propose trades, but timestamp handling, risk validation, execution, accounting, and measurement should remain deterministic and inspectable.

**Observed result to explain, not generalize:** In this simulator, open + close review produced a higher simulated return and a shallower maximum drawdown than hourly review during the Feb–Jun comparison while using far fewer estimated model calls. This is evidence about this experiment and configuration—not evidence of a general market edge.

## Current implementation state

The read-only source contains:

- daily and market-hour replay engines;
- structured signal, intent, order, fill, audit, and portfolio contracts;
- deterministic long-only risk validation;
- a paper ledger with costs/slippage accounting;
- benchmark, drawdown, return, and reported Sharpe calculations;
- Pi/model-agent adapters and deterministic policy fallbacks;
- tournament orchestration with per-variant checkpoints and usage-limit heartbeat handling;
- aggregate January selection and Feb–Jun comparison results;
- 47 statically identified tests across replay, risk, ledger, agent adapters, strategy search, market-context ingestion, and supporting utilities.

At initial inspection, tournament progress could resume only at a coarse variant boundary and the in-memory paper ledger had no crash-safe journal. The completed extension now proves isolated SQLite paper-fill restoration; full pending-decision, model-session, and tournament-state recovery remains explicitly out of scope.

## Existing evidence

### January 2026 selection arena

Five market-data-only Pi/model variants were compared from 2026-01-02 through 2026-01-30. This was a selection arena, not a pristine final test. Aggregate source artifacts report:

- open+close: +25.07%, 40 model calls, 44 fills;
- daily open: +25.00%, 20 model calls, 23 fills;
- three times daily: +22.51%, 60 model calls, 36 fills;
- hourly: +17.43%, 140 model calls, 76 fills;
- event/candidate-triggered: -6.58%, 140 model calls, 9 fills.

January benchmarks were SPY +1.29% and QQQ +1.43%. These figures are selection-period simulation evidence and must not be blended with the later window.

### Feb–Jun promoted comparison

Aggregate source artifacts report for 2026-02-02 through 2026-06-17:

- open+close: **+26.34% simulated return**, **-21.65% maximum drawdown**, **reported Sharpe 0.47**, 94 fills, 190 model calls;
- daily open: +11.26%, -35.31% maximum drawdown, reported Sharpe 0.29, 80 fills, 95 calls;
- three times daily: -8.42%, -26.85% maximum drawdown, reported Sharpe -0.03, 147 fills, 285 calls;
- hourly: +2.60%, -29.58% maximum drawdown, reported Sharpe 0.16, 86 fills, 665 calls;
- QQQ: **+15.54%**; SPY: +6.84%.

Whenever the +26.34% result is displayed, the interface and documentation will also display: current-listing-universe bias; paper execution assumptions; January selection/repeated-testing risk; the short historical regime; non-deterministic model decisions; and no live capital.

## Missing closure work

1. Curate only safe authored source, tests, schemas, variant definitions, and aggregate evidence into the destination.
2. Replace private/proprietary market data with a clearly labeled generated sample dataset.
3. Add a fast deterministic replay that needs no paid API, model login, network, or broker.
4. Add an enforced showcase mode in which broker/live-order code is inaccessible, not merely discouraged.
5. Build an accessible, responsive results dashboard with the required schedule, return, benchmark, drawdown, Sharpe, fills, calls, status, and caveats.
6. Add a calls-versus-performance chart for open, open+close, three-times-daily, and hourly variants.
7. Add architecture, data-flow, tournament-selection, and risk-control diagrams.
8. Add a frozen prospective paper-only protocol that cannot be retroactively tuned without creating a new version.
9. Document methodology, state-recovery limits, evidence boundaries, and negative results.
10. Preserve and run the 47-test suite in the destination, then add focused showcase-mode/demo tests without misreporting the resulting total.
11. Capture polished screenshots from the local dashboard.

## Methodological and safety risks

- **Current-listing-universe bias / survivorship risk:** the broad universe was built from current listings rather than a point-in-time historical membership source.
- **Selection and repeated-testing risk:** January selected/promoted variants; subsequent inspection and iteration reduce claims of clean out-of-sample evidence.
- **Short regime:** February through mid-June is too short to establish robustness across market conditions.
- **Model non-determinism:** model decisions can vary across reruns, even with a frozen prompt/configuration.
- **Execution model:** fills, costs, liquidity, and timing are simulated; they are not broker-confirmed executions.
- **Benchmark interpretation:** benchmark return is a simple same-window comparison and does not match exposure, turnover, concentration, or risk.
- **Concentration and drawdown:** the highlighted run experienced a -21.65% maximum drawdown and may contain concentrated positions.
- **Data timing:** code carries timestamp boundaries, but full source-data lineage and every historical availability assumption cannot be independently proven from the curated package.
- **State recovery:** the isolated SQLite drill proves exactly-once paper-fill restoration, but coarse tournament checkpoints are still not equivalent to full process recovery.
- **No live evidence:** no live capital, customer usage, or prospective paper result supports the historical simulation.

## Privacy, provenance, and attribution risks

- Do not inspect or copy `.env`, `.pi*`, auth state, model sessions, raw prompts/responses, broker/account records, live orders, raw/private/proprietary datasets, caches, logs, virtual environments, generated dependencies, or source `.git`.
- Copy only clearly authored code, focused tests, safe schemas/docs, frozen showcase configuration, and aggregate result rows.
- Record source commit, branch, and dirty working-tree state without implying uncommitted work belongs to the recorded commit.
- Attribute Pi, model providers, pandas, NumPy, Pydantic, PyYAML, pytest, and any other upstream packages. Apache-2.0 applies to the curated copy; third-party licenses and terms remain separate.
- Describe my contribution as system design, experiment design, integration, implementation, risk boundaries, evaluation, and documentation—not authorship of third-party runtimes or models.

## Definition of done

- [x] Curated package lives directly in `03-financebot/` with no nested project copy.
- [x] `README.md`, `PROVENANCE.md`, methodology/limitations, architecture, protocol, and demo-script documentation are complete.
- [x] A generated sample dataset is explicitly labeled **Synthetic demonstration** and has a deterministic generation recipe.
- [x] One command runs a local replay without network, paid APIs, model credentials, or broker access.
- [x] Showcase mode makes all broker submission/connect paths fail closed.
- [x] Dashboard presents every required metric and caveat, with a clear selection-vs-comparison distinction.
- [x] Calls-versus-performance chart includes open, open+close, three-times-daily, and hourly schedules.
- [x] Four required diagrams are present and referenced.
- [x] Prospective protocol is paper-only and frozen with a recorded digest/version.
- [x] The original 47 tests are present and pass in the destination; added showcase tests also pass.
- [x] Dashboard is checked locally for responsive layout, keyboard/focus behavior, broken links, and console errors.
- [x] Screenshots and a 60–90 second demo script are included.
- [x] No prohibited data, credentials, live-order capability, or inflated financial language is present.

## Planned validation commands

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/generate_sample_data.py --check
python scripts/run_showcase_replay.py --check
python scripts/run_risk_failure_lab.py --check
python scripts/run_recovery_drill.py --check
python scripts/freeze_protocol.py --check
python scripts/check_showcase_safety.py
python scripts/validate_package.py
python -m http.server 8000
```

Browser validation completed against `http://127.0.0.1:8000/dashboard/` at desktop and mobile widths; see `docs/verification.md`.

## Competitive extension — decision trace, failure lab, and recovery drill

Authorized on 2026-07-14 as a focused credibility pass. This extension must remain synthetic, offline, deterministic, and standard-library-first.

### Added scope

1. **Decision-trace explorer:** emit and display one sanitized synthetic path from data cutoff through candidate, deterministic policy decision, risk validation, next-bar paper fill, and before/after ledger state. Strip random IDs so the committed artifact remains reproducible.
2. **Risk failure lab:** inject fixed invalid intents into `RiskValidator` and show exact deterministic rejection output for sell-to-open, missing/low price, low liquidity, weak edge, oversized order, insufficient cash, maximum positions, sector concentration, and single-name concentration; include an approved control.
3. **Crash-recovery drill:** add a SQLite fill journal with unique idempotency keys and a verifiable hash chain; simulate interruption/restart, replay duplicate deliveries, restore the paper ledger, and prove equality with an uninterrupted baseline.
4. **Documentation closure:** update the interface, architecture, methodology, provenance, demo script, screenshots, tests, and verification record without strengthening historical-performance claims.

### Extension definition of done

- [x] Trace artifact is deterministic, timestamp-ordered, contains no model session or random identifier, and is rendered accessibly in the dashboard.
- [x] Failure-lab artifact is deterministic and every displayed rejection is produced by the real validator.
- [x] SQLite journal rejects conflicting duplicate keys, suppresses identical redelivery, verifies its hash chain, and restores fills exactly once.
- [x] Recovery drill demonstrates interrupted and uninterrupted ledgers have identical cash, positions, fills, and final marked equity.
- [x] Tests cover trace invariants, failure cases, idempotency, tamper detection, and restore behavior.
- [x] Documentation distinguishes transaction-level paper-fill recovery from still-unresolved full tournament/model-state recovery.
- [x] Browser checks and screenshots cover the new inspectability and recovery views.

## Publication status and next validation gate

Status: **published public-source portfolio case** at <https://github.com/PPDEGRET/financebot-agent-evaluation>, not deployed and not connected to any account.

Next external validation gate: first integrate the verified fill journal into the prospective runner and add durable pending-decision/tournament checkpoints. Only then, after a separate written activation decision, run the frozen configuration once in its declared paper-only window and evaluate it without mid-window tuning.
