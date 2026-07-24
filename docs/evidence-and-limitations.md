# Methodology, evidence, and limitations

## Scope

FINANCEBOT is a historical simulation and agent-cadence experiment. It evaluates a research workflow, not an investable product. No result in this report came from live capital.

## Research question

Does more frequent model-agent review improve simulated decisions when the replay engine, candidate process, deterministic risk layer, paper execution, and accounting remain broadly fixed?

## Experimental stages

### 1. January selection arena

Five market-data-only cadence variants ran from 2026-01-02 through 2026-01-30:

| Variant | Estimated calls | Paper fills | Simulated return | Role |
|---|---:|---:|---:|---|
| Open + close | 40 | 44 | +25.07% | Promoted |
| Open only | 20 | 23 | +25.00% | Promoted |
| Three times daily | 60 | 36 | +22.51% | Promoted |
| Hourly | 140 | 76 | +17.43% | Comparison retained |
| Candidate / event | 140 | 9 | -6.58% | Not promoted |

January SPY was +1.29% and QQQ was +1.43%. This window selected variants. It is not final holdout evidence.

### 2. Feb–Jun comparison

The three promoted variants and hourly comparison ran from 2026-02-02 through 2026-06-17:

| Variant | Simulated return | Max drawdown | Reported Sharpe | Paper fills | Estimated calls |
|---|---:|---:|---:|---:|---:|
| Open + close | +26.34% | -21.65% | 0.47 | 94 | 190 |
| Open only | +11.26% | -35.31% | 0.29 | 80 | 95 |
| Three times daily | -8.42% | -26.85% | -0.03 | 147 | 285 |
| Hourly | +2.60% | -29.58% | 0.16 | 86 | 665 |

Same-window references: QQQ +15.54%; SPY +6.84%.

The implementation labels this source directory as a holdout, but the public case uses the more cautious description **post-selection comparison**. January selection, later inspection, and repeated testing mean the Feb–Jun evidence is not pristine out-of-sample validation.

## Common system components

- Current broad listing universe, daily prices/volume, and hourly prices from excluded source caches.
- Static 126-day momentum candidates, top-eight selection, relay inputs, and up to five portfolio names.
- Model review through Pi using a market-data-only brief and read-only tools.
- Schedule-specific invocation at open, close, three daily times, or every hourly bar.
- Structured decisions translated to `TradeIntent` records.
- Deterministic `RiskValidator` as final approval authority.
- Paper fills on a later bar, not the visible context bar.
- Explicit transaction costs and slippage.
- In-memory `PaperLedger` for cash, positions, fills, and marked equity.

Raw source data, prompts/responses, and event streams are excluded from the public copy, so full independent historical reproduction is not possible here.

## Metric definitions

### Simulated return

Ending paper-ledger equity divided by starting equity minus one.

### Maximum drawdown

The largest peak-to-trough decline across recorded simulated equity snapshots.

### Reported Sharpe

The repository calculates:

```text
sqrt(252) × mean(per-snapshot return) / population_std(per-snapshot return)
```

Snapshots are intraday. Applying √252 to intraday snapshot returns is not a conventional frequency-matched annualization. The value is preserved and labeled **reported Sharpe** for internal comparison only. A prospective report should add a frequency-corrected statistic.

### Model calls

Calls are estimates from configured review invocations in aggregate tournament artifacts. They are not model-provider billing records. A failed/limited invocation may still count as a review opportunity depending on the runner.

### Benchmark return

Simple first-to-last buy-and-hold return for SPY/QQQ over the same dates. It does not match the strategy’s exposure, concentration, turnover, timing, or risk and is not a causal control.

## Execution and risk assumptions

The replay separates decision data from execution by one bar. Approved orders fill at the next available replay price with configured costs and slippage. The simulator does not establish broker-confirmed liquidity, queue position, partial fills, market impact, borrow, or outage behavior.

The deterministic validator enforces long-only action semantics, price/liquidity checks when supplied, expected edge, cash buffer, position/order caps, maximum names, sector exposure, and no sell-to-open. These controls reduce action space; they do not make the resulting portfolio safe.

## Interpretation

The defensible finding is comparative and bounded:

> In this simulator and historical window, simulated portfolio outcomes did not improve monotonically as invocation frequency increased. Open + close review produced a higher simulated return and a shallower maximum drawdown than hourly review while making 475 fewer estimated calls.

Possible mechanisms—decision churn, noisy reassessment, context limitations, prompt behavior, or path dependence—were not isolated causally. The experiment does not prove that lower cadence is always better.

## Required limitations

1. **Current-listing-universe bias.** The broad universe was assembled from current listings rather than point-in-time historical membership, creating survivorship and availability risk.
2. **Paper execution assumptions.** Fills, costs, slippage, liquidity, and timing are simulated.
3. **January selection / repeated-testing risk.** Variants were selected in January and inspected later; multiple comparisons increase false-discovery risk.
4. **Short historical regime.** February through mid-June is insufficient to establish robustness across market conditions.
5. **Non-deterministic model decisions.** The model may produce different choices on rerun even with the same brief and data.
6. **No live capital.** There are no live brokerage fills, prospective returns, or operational usage results.

## Additional limitations

- Raw source data and model records are intentionally excluded, limiting third-party reproduction.
- Data-vintage and every `available_at` assumption cannot be independently verified from aggregate artifacts alone.
- Benchmark comparisons are unmatched for risk and exposure.
- The highlighted run’s -21.65% drawdown is substantial.
- Concentrated portfolios can make results sensitive to a small number of names.
- A single primary model/runtime family limits generalization.
- Calls, candidates, and fills are correlated with schedule and path; schedule is not the only possible causal difference.
- The reported Sharpe scaling is methodologically weak for intraday snapshots.
- No turnover, Sortino, calibration, profit factor, matched random control, or confidence interval is established in the aggregate report.
- Transaction-level paper fills can now be durably journaled and restored in isolation, but the historical tournament did not use that journal and full orchestration state remains non-durable.
- Broker reconciliation code existed in the source but is inaccessible in showcase mode and was not validated against an account.

## State recovery

Tournament orchestration writes coarse per-variant state and heartbeat/partial output. This can help avoid rerunning a completed variant, but it is not full process recovery.

The portfolio extension adds a standard-library SQLite fill journal and deterministic drill. Four stable synthetic fill IDs are appended through serialized transactions and a hash chain. Two simulated interruptions include a commit-before-memory failure. Three redeliveries are suppressed, the complete chain verifies, and the restored ledger exactly matches an uninterrupted baseline for cash, positions, fill count, realized P&L, and final marked equity.

That closes **paper-fill transaction recovery in the isolated drill only**. The historical tournament and current replay engine are not wired to the journal. Pending decisions, model runtime/session state, scheduling, context construction, and external broker reconciliation remain unrecovered. The prospective protocol therefore still requires runner integration and durable pending-decision/tournament checkpoints before activation.

## Synthetic replay evidence

The local demonstration deliberately does not reproduce historical performance. It proves that:

- four review schedules execute deterministically;
- invocation counts differ as expected;
- context and execution bars remain separated;
- deterministic validation and paper accounting run without a model provider;
- one sanitized decision trace preserves cutoff → candidate → review → validation → later fill → ledger ordering without random IDs;
- ten invalid intents are rejected by the real validator and one safe control is approved;
- a SQLite fill journal suppresses duplicates, detects tampering, and restores a matching paper ledger after interruption;
- broker/model/network-price capabilities are blocked in showcase mode;
- generated data, trace, failure, recovery, and replay summaries match committed artifacts.

Its smooth mathematical series are not a market model. Any synthetic return is operational output only.

## Next validation gate

Activate a pre-registered paper-only window only after:

1. activation is authorized in a separate dated record;
2. an authorized data source is named and hashed before observation;
3. the verified fill journal is integrated into the prospective runner;
4. durable pending-decision and tournament checkpoints pass interruption/restore tests;
5. the configuration/brief digest still verifies;
6. no outcome from the planned window has been used to alter the protocol.

Report protocol adherence, failures, all primary metrics, and both passive references once. A negative result remains valid evidence.
