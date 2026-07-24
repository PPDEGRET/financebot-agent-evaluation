# Prospective paper-only protocol

The machine-readable source of truth is [`../protocol/frozen-paper-v1.json`](../protocol/frozen-paper-v1.json). Its decision brief is [`../protocol/open-close-brief.md`](../protocol/open-close-brief.md). `scripts/freeze_protocol.py --check` verifies both through a configuration digest.

## Status

**Proposed · not started · not authorized for live capital**

- Planned paper window: 2026-08-03 09:30 ET through 2026-10-30 15:30 ET.
- Activation requires a separate dated authorization record.
- No broker connection or live order route is permitted.
- Missing the first review expires this version; a replacement requires a newly frozen future window before its outcomes are observed.

## What is frozen

- open + close review only (09:30 and 15:30 ET);
- maximum two calls per session and no candidate-triggered extra calls;
- a fixed 17-ETF universe;
- Pi / `openai/gpt-5.5`, high thinking, read-only tool declaration, no session continuation;
- static-momentum and relay candidate parameters;
- maximum names and weight caps;
- long-only/no-margin/no-options risk limits;
- next-bar paper fills, 20 bps transaction cost per side, and 5 bps slippage;
- starting paper cash, metrics, passive references, logging fields, and change-control rules;
- malformed/unavailable model output becomes logged no trade.

The ETF universe is intentionally different from the historical broad-stock universe. It tests a forward workflow and cadence on a fixed list; it cannot validate the historical return directly.

## Activation gates

Before the first review:

1. A separate dated record authorizes the paper-only run.
2. An authorized data provider/retrieval method is named and hashed in a separate activation record.
3. `available_at` handling is checked against sample timestamps.
4. The verified SQLite fill journal is integrated into the prospective runner.
5. Durable pending-decision and tournament checkpoints pass interruption/restore tests.
6. The protocol and brief digests verify.
7. No planned-window outcomes have been observed or used to alter configuration.

Provider declaration may not change the strategy, cadence, universe, risk, execution, prompt, or metric fields. If it requires such a change, freeze version 2 before observation.

## Reporting rule

Run once and report after the window. Do not stop, tune, or extend based on performance. Report:

- simulated return;
- maximum drawdown;
- repository-reported and frequency-corrected Sharpe;
- calls, failures, candidate intents, validations, and paper fills;
- SPY and QQQ passive references;
- protocol deviations and recovery incidents;
- data gaps and model/runtime availability.

Success means protocol adherence and interpretable evidence, not a positive return or benchmark comparison.

## Why the protocol is still a gate, not an active experiment

The current package now proves isolated paper-fill recovery: four stable fills survive two interruptions, duplicate delivery is suppressed, the SQLite hash chain verifies, and the restored `PaperLedger` matches an uninterrupted baseline exactly. This is necessary but insufficient.

The replay/tournament runner still uses in-memory decision flow and coarse variant checkpoints. It does not durably recover pending decisions, model session/runtime state, schedule position, or partially built context. The protocol is frozen to make the next decision explicit; it does not authorize running before journal integration and the remaining checkpoint gates are closed.
