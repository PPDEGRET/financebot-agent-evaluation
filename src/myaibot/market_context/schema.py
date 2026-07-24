"""Contracts for timestamp-safe market-context source ingestion.

Every record carries both source timestamps and `available_at`; downstream replay
code must only use records where `available_at <= decision_time`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from myaibot.core.models import StrictModel, new_id
from myaibot.core.time import ensure_utc, utc_now


class MarketContextSourceRegistryEntry(StrictModel):
    source_id: str
    source_name: str
    source_type: str
    locator: str | None = None
    enabled: bool = True
    notes: str = ""
    expected_coverage_start: datetime | None = None
    legal_notes: str = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id", "source_name", "source_type")
    @classmethod
    def non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field may not be empty")
        return value

    @field_validator("expected_coverage_start", mode="before")
    @classmethod
    def parse_optional_time(cls, value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        return ensure_utc(value)


class MarketContextDocument(StrictModel):
    document_id: str = Field(default_factory=lambda: new_id("mctx_doc"))
    source_id: str
    source_type: str
    source_name: str
    url: str | None = None
    author: str | None = None
    title: str = ""
    text: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utc_now)
    available_at: datetime = Field(default_factory=utc_now)
    symbols_mentioned: list[str] = Field(default_factory=list)
    companies_mentioned: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    raw_path: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id", "source_type", "source_name")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field may not be empty")
        return value

    @field_validator("published_at", "fetched_at", "available_at", mode="before")
    @classmethod
    def parse_time(cls, value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        return ensure_utc(value)

    @field_validator("symbols_mentioned")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return sorted({v.upper().strip() for v in values if v and v.strip()})

    @field_validator("companies_mentioned", "themes")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return sorted({v.strip() for v in values if v and v.strip()})


class MarketContextMention(StrictModel):
    mention_id: str = Field(default_factory=lambda: new_id("mctx_mention"))
    document_id: str
    symbol: str
    company_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    context_window: str = ""
    sentiment_hint: str | None = None
    available_at: datetime
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        value = value.upper().strip()
        if not value:
            raise ValueError("symbol may not be empty")
        return value

    @field_validator("available_at", mode="before")
    @classmethod
    def parse_available_at(cls, value: Any) -> datetime:
        return ensure_utc(value)


def stable_hash(parts: list[Any] | tuple[Any, ...], *, length: int = 20) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def stable_document_id(source_id: str, *parts: Any) -> str:
    return f"mctx_doc_{stable_hash((source_id, *parts))}"


def stable_mention_id(document_id: str, symbol: str, context_window: str) -> str:
    return f"mctx_mention_{stable_hash((document_id, symbol.upper(), context_window))}"


def stable_source_id(source_type: str, locator: str, source_name: str = "") -> str:
    return f"mctx_src_{stable_hash((source_type, locator, source_name), length=14)}"
