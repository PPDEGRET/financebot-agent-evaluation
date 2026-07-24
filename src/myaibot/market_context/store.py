"""Storage layer for market-context documents and mentions.

DuckDB is used when installed. Otherwise the store falls back to compact JSONL
files next to the requested output path, which keeps v1 usable without adding a
hard dependency to existing tournament environments.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from myaibot.market_context.schema import MarketContextDocument, MarketContextMention, MarketContextSourceRegistryEntry


DOCUMENT_COLUMNS = [
    "document_id",
    "source_id",
    "source_type",
    "source_name",
    "url",
    "author",
    "title",
    "text",
    "published_at",
    "fetched_at",
    "available_at",
    "symbols_mentioned",
    "companies_mentioned",
    "themes",
    "raw_path",
    "metadata_json",
]

MENTION_COLUMNS = [
    "mention_id",
    "document_id",
    "symbol",
    "company_name",
    "confidence",
    "context_window",
    "sentiment_hint",
    "available_at",
    "metadata_json",
]

REGISTRY_COLUMNS = [
    "source_id",
    "source_name",
    "source_type",
    "locator",
    "enabled",
    "notes",
    "expected_coverage_start",
    "legal_notes",
    "metadata_json",
]


@dataclass(frozen=True)
class UpsertResult:
    table: str
    total: int
    inserted: int
    updated: int


class MarketContextStore:
    """Idempotent event store for market_context ingestion outputs."""

    def __init__(self, out_path: str | Path, *, backend: str = "auto") -> None:
        self.out_path = Path(out_path)
        self.backend = backend
        self._conn: Any = None
        self._duckdb: Any = None
        self.jsonl_root: Path | None = None
        if backend not in {"auto", "duckdb", "jsonl"}:
            raise ValueError("backend must be 'auto', 'duckdb', or 'jsonl'")

    @property
    def backend_label(self) -> str:
        if self._conn is not None:
            return "duckdb"
        return "jsonl"

    @property
    def location(self) -> str:
        if self._conn is not None:
            return str(self.out_path)
        assert self.jsonl_root is not None
        return str(self.jsonl_root)

    def initialize(self) -> "MarketContextStore":
        if self.backend in {"auto", "duckdb"}:
            try:
                import duckdb  # type: ignore[import-not-found]

                self._duckdb = duckdb
                self.out_path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = duckdb.connect(str(self.out_path))
                self._init_duckdb()
                return self
            except ImportError:
                if self.backend == "duckdb":
                    raise RuntimeError("duckdb is not installed; use backend='jsonl' or install optional data dependencies")
        self.jsonl_root = self._jsonl_root_for(self.out_path)
        self.jsonl_root.mkdir(parents=True, exist_ok=True)
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "MarketContextStore":
        return self.initialize()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def upsert_source_registry(self, sources: Iterable[MarketContextSourceRegistryEntry]) -> UpsertResult:
        rows = [_registry_row(source) for source in sources]
        return self._upsert("market_context_source_registry", "source_id", REGISTRY_COLUMNS, rows)

    def upsert_documents(self, documents: Iterable[MarketContextDocument]) -> UpsertResult:
        rows = [_document_row(doc) for doc in documents]
        return self._upsert("market_context_documents", "document_id", DOCUMENT_COLUMNS, rows)

    def upsert_mentions(self, mentions: Iterable[MarketContextMention]) -> UpsertResult:
        rows = [_mention_row(mention) for mention in mentions]
        return self._upsert("market_context_mentions", "mention_id", MENTION_COLUMNS, rows)

    def replace_mentions_for_documents(self, document_ids: Iterable[str], mentions: Iterable[MarketContextMention]) -> UpsertResult:
        """Replace all mentions for a set of documents.

        This keeps ingestion idempotent even when the extractor improves and now
        emits fewer/different mentions for an already-stored document.
        """
        ids = sorted({doc_id for doc_id in document_ids if doc_id})
        rows = [_mention_row(mention) for mention in mentions]
        if not ids:
            return UpsertResult(table="market_context_mentions", total=0, inserted=0, updated=0)
        if self._conn is not None:
            for doc_id in ids:
                self._conn.execute("DELETE FROM market_context_mentions WHERE document_id = ?", [doc_id])
            if rows:
                placeholders = ", ".join(["?"] * len(MENTION_COLUMNS))
                column_sql = ", ".join(MENTION_COLUMNS)
                values = [[row.get(col) for col in MENTION_COLUMNS] for row in rows]
                self._conn.executemany(f"INSERT INTO market_context_mentions ({column_sql}) VALUES ({placeholders})", values)
            return UpsertResult(table="market_context_mentions", total=len(rows), inserted=len(rows), updated=0)
        assert self.jsonl_root is not None
        path = self._jsonl_path("market_context_mentions")
        id_set = set(ids)
        kept = [row for row in self._read_jsonl("market_context_mentions") if row.get("document_id") not in id_set]
        by_id = {row["mention_id"]: row for row in kept if row.get("mention_id")}
        for row in rows:
            by_id[row["mention_id"]] = row
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row_id in sorted(by_id):
                handle.write(json.dumps(by_id[row_id], sort_keys=True, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        return UpsertResult(table="market_context_mentions", total=len(rows), inserted=len(rows), updated=0)

    def counts_by_source(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if self._conn is not None:
            return _query_dicts(
                self._conn,
                """
                SELECT source_id, source_name, source_type, COUNT(*) AS document_count,
                       MIN(available_at) AS first_available_at, MAX(available_at) AS last_available_at
                FROM market_context_documents
                GROUP BY 1, 2, 3
                ORDER BY document_count DESC, source_id
                LIMIT ?
                """,
                [limit],
            )
        rows = self._read_jsonl("market_context_documents")
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(row.get("source_id") or "", row.get("source_name") or "", row.get("source_type") or "")].append(row)
        out = []
        for (source_id, source_name, source_type), docs in grouped.items():
            times = sorted([d.get("available_at") for d in docs if d.get("available_at")])
            out.append(
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "source_type": source_type,
                    "document_count": len(docs),
                    "first_available_at": times[0] if times else None,
                    "last_available_at": times[-1] if times else None,
                }
            )
        return sorted(out, key=lambda r: (-int(r["document_count"]), str(r["source_id"])))[:limit]

    def counts_by_date(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if self._conn is not None:
            return _query_dicts(
                self._conn,
                """
                SELECT CAST(available_at AS DATE) AS available_date, COUNT(*) AS document_count
                FROM market_context_documents
                GROUP BY 1
                ORDER BY available_date DESC
                LIMIT ?
                """,
                [limit],
            )
        counter: Counter[str] = Counter()
        for row in self._read_jsonl("market_context_documents"):
            available_at = str(row.get("available_at") or "")
            if available_at:
                counter[available_at[:10]] += 1
        return [
            {"available_date": date, "document_count": count}
            for date, count in sorted(counter.items(), reverse=True)[:limit]
        ]

    def counts_by_symbol(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if self._conn is not None:
            return _query_dicts(
                self._conn,
                """
                SELECT symbol, COUNT(*) AS mention_count, AVG(confidence) AS avg_confidence,
                       MIN(available_at) AS first_available_at, MAX(available_at) AS last_available_at
                FROM market_context_mentions
                GROUP BY 1
                ORDER BY mention_count DESC, symbol
                LIMIT ?
                """,
                [limit],
            )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._read_jsonl("market_context_mentions"):
            symbol = row.get("symbol")
            if symbol:
                grouped[str(symbol)].append(row)
        out = []
        for symbol, rows in grouped.items():
            times = sorted([r.get("available_at") for r in rows if r.get("available_at")])
            confidences = [float(r.get("confidence") or 0.0) for r in rows]
            out.append(
                {
                    "symbol": symbol,
                    "mention_count": len(rows),
                    "avg_confidence": sum(confidences) / len(confidences) if confidences else None,
                    "first_available_at": times[0] if times else None,
                    "last_available_at": times[-1] if times else None,
                }
            )
        return sorted(out, key=lambda r: (-int(r["mention_count"]), str(r["symbol"])))[:limit]

    def table_counts(self) -> dict[str, int]:
        if self._conn is not None:
            return {
                name: int(self._conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
                for name in ("market_context_source_registry", "market_context_documents", "market_context_mentions")
            }
        return {name: len(self._read_jsonl(name)) for name in ("market_context_source_registry", "market_context_documents", "market_context_mentions")}

    def _init_duckdb(self) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_context_documents (
                document_id VARCHAR PRIMARY KEY,
                source_id VARCHAR,
                source_type VARCHAR,
                source_name VARCHAR,
                url VARCHAR,
                author VARCHAR,
                title VARCHAR,
                text VARCHAR,
                published_at TIMESTAMPTZ,
                fetched_at TIMESTAMPTZ,
                available_at TIMESTAMPTZ,
                symbols_mentioned VARCHAR,
                companies_mentioned VARCHAR,
                themes VARCHAR,
                raw_path VARCHAR,
                metadata_json VARCHAR
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_context_mentions (
                mention_id VARCHAR PRIMARY KEY,
                document_id VARCHAR,
                symbol VARCHAR,
                company_name VARCHAR,
                confidence DOUBLE,
                context_window VARCHAR,
                sentiment_hint VARCHAR,
                available_at TIMESTAMPTZ,
                metadata_json VARCHAR
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_context_source_registry (
                source_id VARCHAR PRIMARY KEY,
                source_name VARCHAR,
                source_type VARCHAR,
                locator VARCHAR,
                enabled BOOLEAN,
                notes VARCHAR,
                expected_coverage_start TIMESTAMPTZ,
                legal_notes VARCHAR,
                metadata_json VARCHAR
            )
            """
        )

    def _upsert(self, table: str, id_column: str, columns: list[str], rows: list[dict[str, Any]]) -> UpsertResult:
        if not rows:
            return UpsertResult(table=table, total=0, inserted=0, updated=0)
        if self._conn is not None:
            return self._duckdb_upsert(table, id_column, columns, rows)
        return self._jsonl_upsert(table, id_column, rows)

    def _duckdb_upsert(self, table: str, id_column: str, columns: list[str], rows: list[dict[str, Any]]) -> UpsertResult:
        assert self._conn is not None
        ids = [row[id_column] for row in rows]
        existing = set()
        for row_id in ids:
            found = self._conn.execute(f"SELECT 1 FROM {table} WHERE {id_column} = ? LIMIT 1", [row_id]).fetchone()
            if found:
                existing.add(row_id)
        for row_id in ids:
            self._conn.execute(f"DELETE FROM {table} WHERE {id_column} = ?", [row_id])
        placeholders = ", ".join(["?"] * len(columns))
        column_sql = ", ".join(columns)
        values = [[row.get(col) for col in columns] for row in rows]
        self._conn.executemany(f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})", values)
        return UpsertResult(table=table, total=len(rows), inserted=len(rows) - len(existing), updated=len(existing))

    def _jsonl_upsert(self, table: str, id_column: str, rows: list[dict[str, Any]]) -> UpsertResult:
        assert self.jsonl_root is not None
        path = self._jsonl_path(table)
        existing_rows = self._read_jsonl(table)
        by_id = {row[id_column]: row for row in existing_rows if row.get(id_column)}
        existing_ids = set(by_id)
        for row in rows:
            by_id[row[id_column]] = row
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row_id in sorted(by_id):
                handle.write(json.dumps(by_id[row_id], sort_keys=True, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
        updated = sum(1 for row in rows if row[id_column] in existing_ids)
        return UpsertResult(table=table, total=len(rows), inserted=len(rows) - updated, updated=updated)

    def _read_jsonl(self, table: str) -> list[dict[str, Any]]:
        path = self._jsonl_path(table)
        if not path.exists():
            return []
        rows = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def _jsonl_path(self, table: str) -> Path:
        assert self.jsonl_root is not None
        return self.jsonl_root / f"{table}.jsonl"

    @staticmethod
    def _jsonl_root_for(path: Path) -> Path:
        if path.suffix:
            return Path(str(path) + ".jsonl")
        return path


def _document_row(doc: MarketContextDocument) -> dict[str, Any]:
    row = doc.model_dump(mode="json")
    for col in ("symbols_mentioned", "companies_mentioned", "themes", "metadata_json"):
        row[col] = json.dumps(row.get(col) or ([] if col != "metadata_json" else {}), sort_keys=True, ensure_ascii=False)
    return row


def _mention_row(mention: MarketContextMention) -> dict[str, Any]:
    row = mention.model_dump(mode="json")
    row["metadata_json"] = json.dumps(row.get("metadata_json") or {}, sort_keys=True, ensure_ascii=False)
    return row


def _registry_row(source: MarketContextSourceRegistryEntry) -> dict[str, Any]:
    row = source.model_dump(mode="json")
    row["metadata_json"] = json.dumps(row.get("metadata_json") or {}, sort_keys=True, ensure_ascii=False)
    return row


def _query_dicts(conn: Any, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    result = conn.execute(sql, params or [])
    cols = [desc[0] for desc in result.description]
    rows = []
    for values in result.fetchall():
        rows.append({col: _jsonish(value) for col, value in zip(cols, values, strict=False)})
    return rows


def _jsonish(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
