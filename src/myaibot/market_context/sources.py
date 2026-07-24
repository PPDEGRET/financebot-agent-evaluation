"""Config loading for market-context source registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from myaibot.core.time import ensure_utc
from myaibot.market_context.schema import MarketContextDocument, MarketContextSourceRegistryEntry, stable_source_id


@dataclass(frozen=True)
class MarketContextSource:
    source_id: str
    source_name: str
    source_type: str
    enabled: bool = True
    locator: str | None = None
    notes: str = ""
    expected_coverage_start: Any = None
    legal_notes: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def registry_entry(self) -> MarketContextSourceRegistryEntry:
        return MarketContextSourceRegistryEntry(
            source_id=self.source_id,
            source_name=self.source_name,
            source_type=self.source_type,
            locator=self.locator,
            enabled=self.enabled,
            notes=self.notes,
            expected_coverage_start=self.expected_coverage_start,
            legal_notes=self.legal_notes,
            metadata_json={k: v for k, v in self.config.items() if k not in {"videos"}},
        )


@dataclass
class SourceIngestResult:
    source_id: str
    source_name: str
    source_type: str
    documents: list[MarketContextDocument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_source_file(path: str | Path) -> tuple[dict[str, Any], list[MarketContextSource]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {}) or {}
    sources: list[MarketContextSource] = []
    raw_sources = data.get("sources", {}) or {}

    if isinstance(raw_sources, list):
        for entry in raw_sources:
            if isinstance(entry, dict):
                sources.append(_source_from_entry(entry.get("source_type", "unknown"), entry, defaults))
        return defaults, sources

    if not isinstance(raw_sources, dict):
        raise ValueError("sources.yaml must contain a mapping or list under 'sources'")

    for source_type, entries in raw_sources.items():
        if entries is None:
            continue
        if isinstance(entries, dict):
            entries = entries.get("items", [entries] if "source_name" in entries else [])
        for entry in entries or []:
            if not isinstance(entry, dict):
                raise ValueError(f"source entry for {source_type!r} must be a mapping")
            sources.append(_source_from_entry(str(source_type), entry, defaults))
    return defaults, sources


def filter_sources(
    sources: Iterable[MarketContextSource],
    *,
    source_types: set[str] | None = None,
    include_disabled: bool = False,
    source_ids: set[str] | None = None,
) -> list[MarketContextSource]:
    out: list[MarketContextSource] = []
    for source in sources:
        if source_types and source.source_type not in source_types:
            continue
        if source_ids and source.source_id not in source_ids:
            continue
        if not include_disabled and not source.enabled:
            continue
        out.append(source)
    return out


def _source_from_entry(source_type: str, entry: dict[str, Any], defaults: dict[str, Any]) -> MarketContextSource:
    merged = {**defaults, **entry}
    actual_type = str(merged.get("source_type") or source_type).strip()
    source_name = str(merged.get("source_name") or merged.get("name") or merged.get("title") or actual_type).strip()
    locator = _locator_from_entry(merged)
    source_id = str(merged.get("source_id") or stable_source_id(actual_type, locator or source_name, source_name))
    expected = merged.get("expected_coverage_start")
    if expected not in (None, ""):
        expected = ensure_utc(expected)
    reserved = {
        "source_id",
        "source_name",
        "name",
        "title",
        "source_type",
        "enabled",
        "notes",
        "expected_coverage_start",
        "legal_notes",
    }
    config = {k: v for k, v in merged.items() if k not in reserved}
    return MarketContextSource(
        source_id=source_id,
        source_name=source_name,
        source_type=actual_type,
        enabled=bool(merged.get("enabled", True)),
        locator=locator,
        notes=str(merged.get("notes") or ""),
        expected_coverage_start=expected,
        legal_notes=str(merged.get("legal_notes") or ""),
        config=config,
    )


def _locator_from_entry(entry: dict[str, Any]) -> str | None:
    for key in ("feed_url", "url", "channel_url", "playlist_url", "subreddit", "repo", "endpoint"):
        value = entry.get(key)
        if value:
            return str(value)
    videos = entry.get("videos") or []
    if videos:
        first = videos[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return str(first.get("url") or first.get("video_id") or "") or None
    return None
