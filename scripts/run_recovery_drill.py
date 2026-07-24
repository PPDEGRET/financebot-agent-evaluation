#!/usr/bin/env python3
"""Prove exactly-once paper-fill recovery across simulated interruptions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("FINANCEBOT_SHOWCASE_MODE", "1")

from myaibot.core.models import Fill
from myaibot.core.showcase import showcase_mode_enabled
from myaibot.execution.journal import SQLiteFillJournal
from myaibot.execution.ledger import PaperLedger

DEFAULT_OUTPUT = ROOT / "artifacts" / "recovery-drill.json"
INITIAL_CASH = 100_000.0
FINAL_PRICES = {"SYN_ORBIT": 56.0, "SYN_CEDAR": 72.0, "SYN_HARBOR": 37.0}
FINAL_TIME = datetime(2026, 2, 20, 15, 30, tzinfo=UTC)


def _fills() -> list[Fill]:
    specifications = [
        ("001", "SYN_ORBIT", "buy", 120, 50.0, 1.00, "2026-02-17T10:30:00+00:00"),
        ("002", "SYN_CEDAR", "buy", 80, 70.0, 1.12, "2026-02-17T15:30:00+00:00"),
        ("003", "SYN_ORBIT", "sell", 20, 55.0, 0.22, "2026-02-18T10:30:00+00:00"),
        ("004", "SYN_HARBOR", "buy", 100, 36.0, 0.72, "2026-02-18T15:30:00+00:00"),
    ]
    return [
        Fill(
            fill_id=f"fill_recovery_{suffix}",
            order_id=f"order_recovery_{suffix}",
            intent_id=f"intent_recovery_{suffix}",
            ticker=ticker,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            slippage_bps=0.0,
            filled_at=datetime.fromisoformat(filled_at),
            venue="paper_recovery_drill",
            metadata={"synthetic": True, "sequence": int(suffix)},
        )
        for suffix, ticker, side, quantity, price, commission, filled_at in specifications
    ]


def _state(ledger: PaperLedger) -> dict[str, Any]:
    snapshot = ledger.snapshot(as_of=FINAL_TIME, prices=FINAL_PRICES)
    return {
        "cash": round(snapshot.cash, 8),
        "market_value": round(snapshot.market_value, 8),
        "equity": round(snapshot.equity, 8),
        "fill_count": len(ledger.fills),
        "positions": [
            {
                "ticker": ticker,
                "quantity": position.quantity,
                "avg_cost": round(position.avg_cost, 8),
                "last_price": round(position.last_price, 8),
                "market_value": round(position.market_value, 8),
                "realized_pnl": round(position.realized_pnl, 8),
            }
            for ticker, position in sorted(snapshot.positions.items())
        ],
    }


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_report() -> dict[str, Any]:
    if not showcase_mode_enabled():
        raise RuntimeError("Refusing to run recovery drill with showcase mode disabled.")
    fills = _fills()

    uninterrupted = PaperLedger(initial_cash=INITIAL_CASH)
    for fill in fills:
        uninterrupted.apply_fill(fill)
    baseline_state = _state(uninterrupted)

    with tempfile.TemporaryDirectory(prefix="financebot-recovery-") as temp_dir:
        db_path = Path(temp_dir) / "paper-fill-journal.sqlite3"

        phase_one = SQLiteFillJournal(db_path, initial_cash=INITIAL_CASH)
        assert phase_one.append_fill(fills[0], idempotency_key="paper-fill-001")
        assert phase_one.append_fill(fills[1], idempotency_key="paper-fill-002")
        committed_before_first_interrupt = phase_one.verify()["event_count"]

        after_first_restart = SQLiteFillJournal(db_path, initial_cash=INITIAL_CASH)
        restored_after_first_interrupt = _state(after_first_restart.restore_ledger())
        duplicate_deliveries = 0
        duplicate_deliveries += int(not after_first_restart.append_fill(fills[0], idempotency_key="paper-fill-001"))
        duplicate_deliveries += int(not after_first_restart.append_fill(fills[1], idempotency_key="paper-fill-002"))

        assert after_first_restart.append_fill(fills[2], idempotency_key="paper-fill-003")
        committed_before_second_interrupt = after_first_restart.verify()["event_count"]

        after_second_restart = SQLiteFillJournal(db_path, initial_cash=INITIAL_CASH)
        restored_after_second_interrupt = _state(after_second_restart.restore_ledger())
        duplicate_deliveries += int(not after_second_restart.append_fill(fills[2], idempotency_key="paper-fill-003"))
        assert after_second_restart.append_fill(fills[3], idempotency_key="paper-fill-004")

        integrity = after_second_restart.verify()
        recovered = after_second_restart.restore_ledger()
        recovered_state = _state(recovered)
        events = after_second_restart.events()

    exact_match = recovered_state == baseline_state
    if not exact_match or duplicate_deliveries != 3 or integrity["event_count"] != len(fills):
        raise RuntimeError("Recovery drill failed its exactly-once invariants.")

    return {
        "schema_version": 1,
        "label": "Synthetic demonstration",
        "mode": "sqlite_fill_journal_recovery_drill",
        "initial_cash": INITIAL_CASH,
        "simulated_interruptions": 2,
        "duplicate_deliveries_attempted": 3,
        "duplicate_deliveries_suppressed": duplicate_deliveries,
        "durable_events": integrity["event_count"],
        "journal_verified": integrity["verified"],
        "journal_head_sha256": integrity["journal_head"],
        "uninterrupted_state": baseline_state,
        "recovered_state": recovered_state,
        "uninterrupted_state_sha256": _digest(baseline_state),
        "recovered_state_sha256": _digest(recovered_state),
        "exact_state_match": exact_match,
        "timeline": [
            {
                "step": 1,
                "label": "Commit first two fills",
                "detail": f"{committed_before_first_interrupt} durable events before interruption.",
            },
            {
                "step": 2,
                "label": "Restart from SQLite",
                "detail": f"Restored {restored_after_first_interrupt['fill_count']} fills before any redelivery.",
            },
            {
                "step": 3,
                "label": "Suppress duplicate delivery",
                "detail": "The first two stable fill IDs were redelivered and produced no new ledger events.",
            },
            {
                "step": 4,
                "label": "Interrupt after durable commit",
                "detail": f"Fill 3 reached SQLite ({committed_before_second_interrupt} events) before in-memory application.",
            },
            {
                "step": 5,
                "label": "Restore and finish",
                "detail": f"Restart recovered {restored_after_second_interrupt['fill_count']} fills, suppressed fill 3 redelivery, then committed fill 4.",
            },
            {
                "step": 6,
                "label": "Compare with uninterrupted baseline",
                "detail": "Cash, positions, fill count, realized P&L, and marked equity matched exactly.",
            },
        ],
        "events": [
            {
                "sequence": event.sequence,
                "idempotency_key": event.idempotency_key,
                "fill_id": event.fill_id,
                "ticker": event.fill.ticker,
                "side": event.fill.side,
                "quantity": event.fill.quantity,
                "filled_at": event.fill.filled_at.isoformat(),
                "payload_sha256": event.payload_sha256,
                "previous_hash": event.previous_hash,
                "event_hash": event.event_hash,
            }
            for event in events
        ],
        "scope_boundary": "This proves transaction-level paper-fill recovery only. It does not recover model sessions, pending decisions, tournament scheduling, or external broker state.",
        "external_model_calls": 0,
        "broker_connections": 0,
    }


def _serialized(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Compare a fresh crash drill with the committed artifact.")
    args = parser.parse_args()

    rendered = _serialized(build_report())
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"Recovery drill check failed: regenerate {args.output}")
        print("Recovery drill check passed: interrupted and uninterrupted ledger states match exactly.")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
