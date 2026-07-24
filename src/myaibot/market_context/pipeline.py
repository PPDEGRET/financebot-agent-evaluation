"""Composable market-context ingestion pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from myaibot.core.time import ensure_utc
from myaibot.market_context.entity_extract import EntityIndex, annotate_document_entities, extract_mentions_from_document, load_entity_index
from myaibot.market_context.ingest_reddit import ingest_reddit_source
from myaibot.market_context.ingest_reports import ingest_report_source
from myaibot.market_context.ingest_rss import ingest_rss_source
from myaibot.market_context.ingest_youtube import ingest_youtube_source
from myaibot.market_context.schema import MarketContextDocument, MarketContextMention
from myaibot.market_context.sources import SourceIngestResult, MarketContextSource, filter_sources, load_source_file
from myaibot.market_context.store import UpsertResult, MarketContextStore

logger = logging.getLogger(__name__)

SUPPORTED_SOURCE_TYPES = {"youtube", "reddit", "rss", "short_report"}


@dataclass
class PipelineResult:
    backend: str
    location: str
    source_results: list[SourceIngestResult] = field(default_factory=list)
    registry_upsert: UpsertResult | None = None
    documents_upsert: UpsertResult | None = None
    mentions_upsert: UpsertResult | None = None
    skipped_sources: list[str] = field(default_factory=list)

    @property
    def documents(self) -> int:
        return sum(len(result.documents) for result in self.source_results)

    @property
    def errors(self) -> list[str]:
        out: list[str] = []
        for result in self.source_results:
            out.extend(f"{result.source_name}: {err}" for err in result.errors)
        return out


def run_market_context_ingestion(
    *,
    sources_path: str | Path,
    since: str | datetime,
    out: str | Path,
    until: str | datetime | None = None,
    raw_root: str | Path = "data/market_context/raw",
    source_types: Iterable[str] | None = None,
    source_ids: Iterable[str] | None = None,
    include_disabled: bool = False,
    backend: str = "auto",
    universe_paths: Iterable[str | Path] | None = None,
) -> PipelineResult:
    since_dt = ensure_utc(since)
    until_dt = ensure_utc(until) if until else None
    defaults, all_sources = load_source_file(sources_path)
    selected = filter_sources(
        all_sources,
        source_types=set(source_types) if source_types else None,
        source_ids=set(source_ids) if source_ids else None,
        include_disabled=include_disabled,
    )
    index = load_entity_index(universe_paths)
    logger.info("Loaded %d source(s), selected %d", len(all_sources), len(selected))

    with MarketContextStore(out, backend=backend) as store:
        result = PipelineResult(backend=store.backend_label, location=store.location)
        result.registry_upsert = store.upsert_source_registry([source.registry_entry() for source in selected])

        documents: list[MarketContextDocument] = []
        mentions: list[MarketContextMention] = []
        for source in selected:
            if source.source_type not in SUPPORTED_SOURCE_TYPES:
                msg = f"{source.source_id} ({source.source_type}) not implemented in this phase"
                result.skipped_sources.append(msg)
                logger.warning("Skipping unsupported market_context source: %s", msg)
                continue
            source_result = _ingest_source(source, since=since_dt, until=until_dt, raw_root=raw_root)
            annotated_docs = []
            for document in source_result.documents:
                doc_mentions = extract_mentions_from_document(document, index)
                annotated = annotate_document_entities(document, doc_mentions)
                annotated_docs.append(annotated)
                mentions.extend(doc_mentions)
            source_result.documents = annotated_docs
            documents.extend(annotated_docs)
            result.source_results.append(source_result)
            logger.info(
                "Source %s produced %d document(s), %d error(s), %d skipped",
                source.source_name,
                len(source_result.documents),
                len(source_result.errors),
                len(source_result.skipped),
            )

        result.documents_upsert = store.upsert_documents(documents)
        result.mentions_upsert = store.replace_mentions_for_documents([doc.document_id for doc in documents], mentions)
        return result


def _ingest_source(source: MarketContextSource, *, since: datetime, until: datetime | None, raw_root: str | Path) -> SourceIngestResult:
    if source.source_type == "youtube":
        return ingest_youtube_source(source, since=since, raw_root=raw_root)
    if source.source_type == "reddit":
        return ingest_reddit_source(source, since=since, until=until, raw_root=raw_root)
    if source.source_type == "rss":
        return ingest_rss_source(source, since=since, until=until, raw_root=raw_root)
    if source.source_type == "short_report":
        return ingest_report_source(source, since=since, until=until, raw_root=raw_root)
    raise ValueError(f"unsupported source type: {source.source_type}")
