# Problem and market thesis

## Who experiences the problem?

Applied-AI teams, agent-system builders, research leads, and quantitative experimenters increasingly use model calls inside decision workflows. They need to decide not only which model or prompt to use, but **when the model should be invoked** and which parts of the system must remain deterministic.

In financial research, the cost of getting that boundary wrong is unusually visible: extra calls can add latency, cost, turnover, unstable decisions, and more opportunities to overfit. A polished final equity number can hide all five.

## What current approaches get wrong

Common agent demos conflate activity with value:

- an “always-on” agent looks more capable because it emits more reasoning;
- model output is allowed to mix signal generation, sizing, risk, execution, and explanation;
- benchmark return, drawdown, calls, and failures are reported separately or not at all;
- a selected winner is shown without the selection process;
- simulated fills are described with language better suited to live outcomes.

That makes it hard to answer whether an agent improved a decision or merely increased system motion.

## My thesis

Agent cadence is a product and system-design variable that should be tested like any other intervention. A model may contribute judgment, but the replay clock, timestamp boundaries, schema, risk validation, execution assumptions, accounting, and evaluation must remain inspectable and deterministic.

The testable question is narrower than “can a model trade?”:

> Holding the surrounding simulator broadly fixed, how does review frequency change decisions, calls, fills, return, and drawdown?

## Designed intervention

FINANCEBOT implements:

1. timestamp-bounded daily/hourly replay;
2. deterministic candidate generation and portfolio construction;
3. a structured model/policy decision schema;
4. deterministic long-only risk checks;
5. next-bar paper execution with costs and slippage;
6. paper-ledger accounting;
7. cadence variants covering open, open + close, three times daily, hourly, and event/candidate review;
8. tournament selection and a later aggregate comparison.

The portfolio copy adds a fail-closed showcase mode and a generated offline replay so reviewers can test the control boundaries without private data, a model account, or a broker. It also exposes one sanitized decision trace, ten real-validator failure paths, an approved control, and a SQLite interruption drill so the system can be judged on inspectability and recovery—not only aggregate output.

## Evidence

In the Feb–Jun simulator comparison:

- open + close used 190 estimated model calls and returned +26.34% with -21.65% maximum drawdown;
- hourly review used 665 calls and returned +2.60% with -29.58% maximum drawdown;
- three-times-daily review had a negative simulated return despite more calls than open + close;
- QQQ returned +15.54% over the same dates, but was not a matched-risk control.

The full highlighted context is **+26.34% simulated return, -21.65% maximum drawdown, reported Sharpe 0.47, and QQQ +15.54%**. It carries current-listing-universe bias, paper execution assumptions, January selection/repeated-testing risk, a short historical regime, non-deterministic model decisions, and no live capital.

This supports a limited conclusion: **simulated portfolio outcomes did not improve monotonically as invocation frequency increased in this experiment**. It does not establish a durable strategy, a causal mechanism, or a general result across models and regimes.

## Negative evidence and decisions changed

- Hourly review did not justify its additional call volume.
- Three-times-daily review did not sit monotonically between daily and hourly outcomes.
- Event/candidate triggering was negative in the January selection arena.
- The highlighted run still experienced a -21.65% drawdown.
- Current-listing-universe bias and January selection prevent a clean final-validation claim.
- The historical tournament’s in-memory ledger and coarse checkpoints are insufficient for resilient prospective operation.
- A new isolated SQLite drill proves exactly-once paper-fill restore, but pending decisions, model sessions, and tournament state remain outside that recovery boundary.

Those observations shift the next step away from adding features or more frequent calls. The next step is a frozen, paper-only prospective protocol with stronger state durability and no mid-window tuning.

## Commercial/product relevance

The reusable product lesson is broader than finance: expensive, non-deterministic judgment should be invoked at the minimum cadence that improves a measured decision. Deterministic layers should own policy constraints and irreversible effects. FINANCEBOT makes that architecture and evaluation trade-off concrete.

## Safe positioning

FINANCEBOT can credibly demonstrate:

- applied-AI workflow and evaluation design;
- timestamp-safe replay and structured decisions;
- deterministic risk and paper accounting boundaries;
- experimental comparison of invocation schedules;
- preservation of negative evidence and limitations.

It should not be positioned as an investment product, live trading system, customer-validated platform, or evidence that a strategy will perform prospectively.
