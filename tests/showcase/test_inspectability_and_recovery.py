import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from myaibot.core.models import Fill, MarketSnapshot, PortfolioState, RiskLimits, TradeIntent
from myaibot.execution.journal import JournalConflictError, JournalIntegrityError, SQLiteFillJournal
from myaibot.risk.validator import RiskValidator

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 2, 17, 10, 30, tzinfo=UTC)


def _fill(suffix: str = "001", *, price: float = 10.0, side: str = "buy", quantity: int = 10) -> Fill:
    return Fill(
        fill_id=f"fill_test_{suffix}",
        order_id=f"order_test_{suffix}",
        intent_id=f"intent_test_{suffix}",
        ticker="SYN_TEST",
        side=side,
        quantity=quantity,
        price=price,
        commission=0.25,
        filled_at=NOW,
        venue="paper_test",
    )


def test_decision_trace_is_timestamp_safe_hashed_and_identifier_free():
    report = json.loads((ROOT / "artifacts" / "sample-replay.json").read_text(encoding="utf-8"))
    trace = report["decision_trace"]
    assert datetime.fromisoformat(trace["data_cutoff"]) < datetime.fromisoformat(trace["decision_time"]) < datetime.fromisoformat(trace["execution_time"])
    assert trace["strict_timestamp_ordering"] is True
    assert trace["external_model_calls"] == 0
    assert trace["ledger_delta"]["fills"] == len(trace["fills"]) == 2
    assert all(item["approved"] for item in trace["validations"])
    digest = trace.pop("trace_sha256")
    canonical = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == digest
    trace["trace_sha256"] = digest
    assert not re.search(r"(?:intent|order|fill|decision)_[0-9a-f]{8,}", json.dumps(trace))


def test_failure_lab_records_real_rejections_and_safe_control():
    report = json.loads((ROOT / "artifacts" / "risk-failure-lab.json").read_text(encoding="utf-8"))
    expected = {
        "WOULD_SHORT",
        "MISSING_PRICE",
        "PRICE_TOO_LOW",
        "LIQUIDITY_TOO_LOW",
        "EDGE_TOO_LOW",
        "ORDER_CAP",
        "INSUFFICIENT_CASH",
        "MAX_POSITIONS",
        "SECTOR_CAP",
        "POSITION_CAP",
    }
    rejected = [case for case in report["cases"] if not case["approved"]]
    approved = [case for case in report["cases"] if case["approved"]]
    assert report["blocked_cases"] == len(rejected) == 10
    assert {case["issue_codes"][0] for case in rejected} == expected
    assert all(case["order_created"] is False for case in rejected)
    assert len(approved) == 1 and approved[0]["issue_codes"] == ["APPROVED"]


def test_order_notional_cap_is_enforced_by_real_validator():
    limits = RiskLimits(
        max_position_weight=0.5,
        max_order_notional_weight=0.2,
        max_sector_weight=1.0,
        max_positions=10,
        cash_buffer_weight=0.0,
        min_price=1.0,
        min_adv_dollars=0.0,
        min_expected_net_edge_bps=0.0,
    )
    intent = TradeIntent(
        intent_id="intent_order_cap_test",
        lab_id="test",
        originator="test",
        created_at=NOW,
        data_cutoff=NOW,
        ticker="SYN_TEST",
        side="buy",
        action="buy",
        quantity=25,
        horizon_days=1,
        confidence=1.0,
        expected_net_edge_bps=100.0,
        thesis="Direct order-cap regression test.",
    )
    result = RiskValidator(limits).validate(
        intent,
        PortfolioState(as_of=NOW, cash=1_000.0),
        MarketSnapshot(as_of=NOW, prices={"SYN_TEST": 10.0}),
    )
    assert result.approved is False
    assert [issue.code for issue in result.errors] == ["ORDER_CAP"]


def test_sqlite_journal_suppresses_identical_redelivery(tmp_path):
    journal = SQLiteFillJournal(tmp_path / "fills.sqlite3", initial_cash=1_000.0)
    fill = _fill()
    assert journal.append_fill(fill, idempotency_key="stable-001") is True
    assert journal.append_fill(fill, idempotency_key="stable-001") is False
    assert journal.append_fill(fill, idempotency_key="different-key-same-fill") is False
    assert journal.verify()["event_count"] == 1


def test_sqlite_journal_rejects_conflicting_duplicate(tmp_path):
    journal = SQLiteFillJournal(tmp_path / "fills.sqlite3", initial_cash=1_000.0)
    fill = _fill()
    assert journal.append_fill(fill, idempotency_key="stable-001") is True
    with pytest.raises(JournalConflictError):
        journal.append_fill(fill.model_copy(update={"price": 11.0}), idempotency_key="stable-001")


def test_sqlite_journal_restores_fills_exactly_once(tmp_path):
    path = tmp_path / "fills.sqlite3"
    journal = SQLiteFillJournal(path, initial_cash=1_000.0)
    buy = _fill("001", price=10.0, quantity=10)
    sell = _fill("002", price=12.0, side="sell", quantity=4)
    journal.append_fill(buy)
    journal.append_fill(sell)

    restored = SQLiteFillJournal(path, initial_cash=1_000.0).restore_ledger()
    snapshot = restored.snapshot(NOW, prices={"SYN_TEST": 12.0})
    assert len(restored.fills) == 2
    assert snapshot.positions["SYN_TEST"].quantity == 6
    assert snapshot.positions["SYN_TEST"].realized_pnl == pytest.approx(7.75)
    assert SQLiteFillJournal(path, initial_cash=1_000.0).verify()["verified"] is True


def test_sqlite_journal_detects_payload_tampering(tmp_path):
    path = tmp_path / "fills.sqlite3"
    journal = SQLiteFillJournal(path, initial_cash=1_000.0)
    journal.append_fill(_fill())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE fill_events SET fill_json = fill_json || ' '")
    with pytest.raises(JournalIntegrityError, match="payload hash"):
        journal.verify()


def test_recovery_drill_matches_uninterrupted_state_and_states_scope():
    report = json.loads((ROOT / "artifacts" / "recovery-drill.json").read_text(encoding="utf-8"))
    assert report["journal_verified"] is True
    assert report["exact_state_match"] is True
    assert report["duplicate_deliveries_suppressed"] == report["duplicate_deliveries_attempted"] == 3
    assert report["durable_events"] == len(report["events"]) == 4
    assert report["uninterrupted_state"] == report["recovered_state"]
    assert report["uninterrupted_state_sha256"] == report["recovered_state_sha256"]
    assert "does not recover model sessions" in report["scope_boundary"]
