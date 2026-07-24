"""Durable, idempotent SQLite journal for paper-fill recovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from myaibot.core.models import Fill
from myaibot.execution.ledger import PaperLedger

_SCHEMA_VERSION = "1"
_GENESIS_HASH = hashlib.sha256(b"FINANCEBOT_SQLITE_FILL_JOURNAL_V1").hexdigest()


class JournalConflictError(ValueError):
    """Raised when a known fill/idempotency key arrives with different content."""


class JournalIntegrityError(RuntimeError):
    """Raised when the stored hash chain or metadata does not verify."""


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    idempotency_key: str
    fill_id: str
    payload_sha256: str
    previous_hash: str
    event_hash: str
    fill: Fill


class SQLiteFillJournal:
    """Append-only fill journal with exact-once restore semantics.

    SQLite is the durable source of truth. Re-delivering an identical fill with
    the same fill ID or idempotency key is a no-op. Reusing either identifier
    for different content fails closed.
    """

    def __init__(self, path: str | Path, *, initial_cash: float) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initial_cash = float(initial_cash)
        if self.initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fill_events (
                    sequence INTEGER PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    fill_id TEXT NOT NULL UNIQUE,
                    fill_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    committed_at TEXT NOT NULL
                );
                """
            )
            expected_cash = format(self.initial_cash, ".17g")
            connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)", (_SCHEMA_VERSION,))
            connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('initial_cash', ?)", (expected_cash,))
            connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('journal_head', ?)", (_GENESIS_HASH,))
            connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('event_count', '0')")
            metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
            if metadata.get("schema_version") != _SCHEMA_VERSION:
                raise JournalIntegrityError("Unsupported fill-journal schema version.")
            if metadata.get("initial_cash") != expected_cash:
                raise JournalConflictError("Journal initial_cash does not match the requested ledger.")
            connection.commit()

    @staticmethod
    def _fill_json(fill: Fill) -> str:
        return json.dumps(fill.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _event_hash(previous_hash: str, sequence: int, idempotency_key: str, payload_sha256: str) -> str:
        body = f"{previous_hash}:{sequence}:{idempotency_key}:{payload_sha256}".encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    def append_fill(self, fill: Fill, *, idempotency_key: str | None = None) -> bool:
        """Append a fill once; return False for identical redelivery."""

        key = (idempotency_key or fill.fill_id).strip()
        if not key:
            raise ValueError("idempotency_key cannot be empty")
        fill_json = self._fill_json(fill)
        payload_sha256 = hashlib.sha256(fill_json.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT idempotency_key, fill_id, payload_sha256 FROM fill_events WHERE idempotency_key = ? OR fill_id = ?",
                (key, fill.fill_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] != payload_sha256:
                    raise JournalConflictError(
                        f"Conflicting fill redelivery for key={key!r} or fill_id={fill.fill_id!r}."
                    )
                connection.rollback()
                return False

            metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
            sequence = int(metadata["event_count"]) + 1
            previous_hash = metadata["journal_head"]
            event_hash = self._event_hash(previous_hash, sequence, key, payload_sha256)
            connection.execute(
                """
                INSERT INTO fill_events(
                    sequence, idempotency_key, fill_id, fill_json, payload_sha256,
                    previous_hash, event_hash, committed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    key,
                    fill.fill_id,
                    fill_json,
                    payload_sha256,
                    previous_hash,
                    event_hash,
                    fill.filled_at.isoformat(),
                ),
            )
            connection.execute("UPDATE metadata SET value = ? WHERE key = 'journal_head'", (event_hash,))
            connection.execute("UPDATE metadata SET value = ? WHERE key = 'event_count'", (str(sequence),))
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def events(self, *, verify: bool = True) -> list[JournalEvent]:
        if verify:
            self.verify()
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM fill_events ORDER BY sequence").fetchall()
        return [
            JournalEvent(
                sequence=int(row["sequence"]),
                idempotency_key=str(row["idempotency_key"]),
                fill_id=str(row["fill_id"]),
                payload_sha256=str(row["payload_sha256"]),
                previous_hash=str(row["previous_hash"]),
                event_hash=str(row["event_hash"]),
                fill=Fill.model_validate_json(row["fill_json"]),
            )
            for row in rows
        ]

    def verify(self) -> dict[str, Any]:
        """Verify payload hashes, sequence continuity, chain links, and head."""

        with self._connection() as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())
            rows = connection.execute("SELECT * FROM fill_events ORDER BY sequence").fetchall()

        expected_previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            if int(row["sequence"]) != expected_sequence:
                raise JournalIntegrityError("Fill-journal sequence is not contiguous.")
            payload_hash = hashlib.sha256(str(row["fill_json"]).encode("utf-8")).hexdigest()
            if payload_hash != row["payload_sha256"]:
                raise JournalIntegrityError(f"Fill payload hash mismatch at sequence {expected_sequence}.")
            if row["previous_hash"] != expected_previous:
                raise JournalIntegrityError(f"Hash-chain link mismatch at sequence {expected_sequence}.")
            event_hash = self._event_hash(
                expected_previous,
                expected_sequence,
                str(row["idempotency_key"]),
                payload_hash,
            )
            if event_hash != row["event_hash"]:
                raise JournalIntegrityError(f"Event hash mismatch at sequence {expected_sequence}.")
            expected_previous = event_hash

        if int(metadata.get("event_count", "-1")) != len(rows):
            raise JournalIntegrityError("Journal event_count metadata does not match stored events.")
        if metadata.get("journal_head") != expected_previous:
            raise JournalIntegrityError("Journal head does not match the verified hash chain.")
        return {
            "schema_version": metadata.get("schema_version"),
            "event_count": len(rows),
            "journal_head": expected_previous,
            "verified": True,
        }

    def restore_ledger(self) -> PaperLedger:
        """Rebuild an in-memory paper ledger from verified durable fills."""

        ledger = PaperLedger(initial_cash=self.initial_cash)
        for event in self.events(verify=True):
            ledger.apply_fill(event.fill)
        return ledger
