# 90-second demo script

## 0–10 seconds — frame the experiment

Open `http://127.0.0.1:8000/dashboard/`.

> “FINANCEBOT is an agent-evaluation and quantitative-research system, not an investment product. The question is narrow: does invoking the model more often improve simulated decisions?”

Point to **No live capital**.

## 10–25 seconds — show the result with its boundary

> “In the Feb–Jun simulator, open + close review made 190 estimated calls and returned 26.34%. Hourly review made 665 calls and returned 2.60%. The highlighted result includes a -21.65% maximum drawdown, reported Sharpe 0.47, and QQQ at +15.54%.”

> “It also carries current-listing-universe bias, paper execution assumptions, January selection and repeated-testing risk, a short historical regime, non-deterministic model decisions, and no live capital.”

## 25–38 seconds — make cadence measurable

Scroll to **Calls versus performance**.

> “More calls did not map to higher return here: three-times-daily was negative, and hourly used 3.5 times the open + close call count. QQQ is an unmatched same-window reference, and the reported-Sharpe scaling is disclosed.”

## 38–55 seconds — inspect one decision

Scroll to **Decision trace**.

> “This is a generated, deterministic trace—not a historical model transcript. The review sees data only through the prior bar. Two momentum candidates enter a structured policy decision, cross the real validator, fill one bar later with costs, and update the paper ledger. Random IDs are stripped and the trace is hash-verified.”

Point across the three timestamps and then to visible price versus later fill price.

## 55–72 seconds — show failures and recovery

Scroll to **Control proof**.

> “Ten fixed invalid intents are blocked for ten explicit reasons: shorting, missing or weak data, weak edge, order, cash, position, and sector limits. A safe control is approved, so this is not a blanket deny-list.”

> “The recovery drill interrupts SQLite twice, including after durable commit but before memory application. Three duplicate deliveries are suppressed, the hash chain verifies, and the restored ledger exactly matches the uninterrupted state.”

Point to the scope note: this is paper-fill recovery, not model-session or tournament-state recovery.

## 72–83 seconds — run the offline proof

From the repository root:

```bash
python scripts/run_showcase_replay.py --check
python scripts/run_risk_failure_lab.py --check
python scripts/run_recovery_drill.py --check
```

> “Every artifact regenerates without network access, a model provider, or a broker.”

## 83–90 seconds — close with the next gate

Scroll to **Next validation gate**.

> “The next step is one frozen paper-only window—after the journal is integrated into the runner and pending-decision recovery is durable. No mid-window tuning.”

Stop. Do not imply live readiness or communicate financial expectations.
