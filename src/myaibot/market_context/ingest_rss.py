"""Generic RSS/Atom ingestion for public market-context sources.

RSS feeds usually expose rolling windows rather than complete historical archives.
This connector therefore backfills from `--since` only as far as the feed itself
returns items, and records that limitation in metadata.
"""

from __future__ import annotations

import email.utils
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from myaibot.core.time import ensure_utc, utc_now
from myaibot.market_context.canonical import canonical_document_keys
from myaibot.market_context.schema import MarketContextDocument, stable_document_id, stable_hash
from myaibot.market_context.sources import SourceIngestResult, MarketContextSource

logger = logging.getLogger(__name__)
SPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class RssFetchConfig:
    feed_url: str
    rate_limit_seconds: float = 1.0
    timeout_seconds: int = 45
    max_retries: int = 3
    backoff_factor: float = 2.0
    max_items_per_source: int | None = None
    user_agent: str = "FINANCEBOT market_context RSS ingestion; public low-cost research"
    historical_coverage_note: str = "RSS/Atom feed rolling-window coverage; full archive backfill unavailable unless feed exposes old items."


@dataclass(frozen=True)
class ParsedFeedEntry:
    entry_id: str | None
    title: str
    link: str | None
    author: str | None
    published_at: datetime | None
    updated_at: datetime | None
    summary: str
    content: str
    categories: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedFeed:
    title: str | None
    link: str | None
    entries: list[ParsedFeedEntry]
    raw_root_tag: str


def ingest_rss_source(
    source: MarketContextSource,
    *,
    since: datetime,
    until: datetime | None = None,
    raw_root: str | Path = "data/market_context/raw",
) -> SourceIngestResult:
    result = SourceIngestResult(source_id=source.source_id, source_name=source.source_name, source_type=source.source_type)
    try:
        config = rss_fetch_config(source)
    except Exception as exc:
        result.errors.append(f"invalid_rss_config: {type(exc).__name__}: {exc}")
        return result

    since_dt = ensure_utc(since)
    until_dt = ensure_utc(until) if until else None
    fetched_at = utc_now()

    try:
        feed_text, response_meta = fetch_feed(config)
        parsed = parse_feed(feed_text)
    except Exception as exc:
        result.errors.append(f"rss_fetch_or_parse_failed: {type(exc).__name__}: {exc}")
        logger.exception("RSS ingestion failed for %s", source.source_name)
        return result

    raw_root_path = Path(raw_root)
    documents: list[MarketContextDocument] = []
    skipped_before_since = 0
    skipped_after_until = 0
    skipped_no_text = 0

    for entry in parsed.entries:
        effective_time = entry.published_at or entry.updated_at or fetched_at
        available_at = resolve_available_at(source, entry.published_at, entry.updated_at, fetched_at)
        if effective_time < since_dt:
            skipped_before_since += 1
            continue
        if until_dt and effective_time > until_dt:
            skipped_after_until += 1
            continue
        text = combine_text(entry.summary, entry.content)
        if not entry.title and not text:
            skipped_no_text += 1
            continue
        doc = document_from_feed_entry(
            source,
            config=config,
            parsed_feed=parsed,
            entry=entry,
            fetched_at=fetched_at,
            available_at=available_at,
            response_meta=response_meta,
            text=text,
            raw_root=raw_root_path,
        )
        documents.append(doc)
        if config.max_items_per_source and len(documents) >= config.max_items_per_source:
            result.skipped.append(f"max_items_per_source_reached:{config.max_items_per_source}")
            break

    result.documents = documents
    result.skipped.extend(
        [
            f"feed_entries_returned:{len(parsed.entries)}",
            f"skipped_before_since:{skipped_before_since}",
            f"skipped_after_until:{skipped_after_until}",
            f"skipped_no_text:{skipped_no_text}",
            f"coverage_note:{config.historical_coverage_note}",
        ]
    )
    return result


def rss_fetch_config(source: MarketContextSource) -> RssFetchConfig:
    feed_url = str(source.config.get("feed_url") or source.config.get("url") or "").strip()
    if not feed_url:
        raise ValueError("missing feed_url")
    return RssFetchConfig(
        feed_url=feed_url,
        rate_limit_seconds=float(source.config.get("rate_limit_seconds", 1.0)),
        timeout_seconds=int(source.config.get("timeout_seconds", 45)),
        max_retries=int(source.config.get("max_retries", 3)),
        backoff_factor=float(source.config.get("backoff_factor", 2.0)),
        max_items_per_source=int(source.config["max_items_per_source"]) if source.config.get("max_items_per_source") else None,
        user_agent=str(source.config.get("user_agent") or "FINANCEBOT market_context RSS ingestion; public low-cost research"),
        historical_coverage_note=str(
            source.config.get("historical_coverage_note")
            or "RSS/Atom feed rolling-window coverage; full archive backfill unavailable unless feed exposes old items."
        ),
    )


def fetch_feed(config: RssFetchConfig) -> tuple[str, dict[str, Any]]:
    headers = {"User-Agent": config.user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}
    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 1):
        try:
            response = requests.get(config.feed_url, headers=headers, timeout=config.timeout_seconds)
            if response.status_code == 429:
                wait = rate_limit_wait(response)
                logger.warning("RSS feed rate limited for %s; sleeping %ss", config.feed_url, wait)
                time.sleep(wait)
                continue
            if 500 <= response.status_code < 600 and attempt < config.max_retries:
                wait = config.backoff_factor**attempt
                logger.warning("RSS feed server error %s for %s; sleeping %.1fs", response.status_code, config.feed_url, wait)
                time.sleep(wait)
                continue
            response.raise_for_status()
            if config.rate_limit_seconds > 0:
                time.sleep(config.rate_limit_seconds)
            meta = {
                "feed_url": config.feed_url,
                "final_url": response.url,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
            }
            return response.text, meta
        except requests.RequestException as exc:
            last_error = exc
            if attempt < config.max_retries:
                wait = config.backoff_factor**attempt
                logger.warning("RSS fetch failed attempt %s/%s for %s; sleeping %.1fs", attempt, config.max_retries, config.feed_url, wait)
                time.sleep(wait)
    raise RuntimeError(f"RSS fetch failed for {config.feed_url}: {last_error}")


def parse_feed(feed_text: str) -> ParsedFeed:
    root = ET.fromstring(feed_text.lstrip())
    root_name = local_name(root.tag)
    if root_name == "feed":
        return parse_atom_feed(root)
    if root_name in {"rss", "rdf", "RDF"} or find_child(root, "channel") is not None:
        return parse_rss_feed(root)
    raise ValueError(f"unsupported feed root tag: {root.tag}")


def parse_atom_feed(root: ET.Element) -> ParsedFeed:
    title = text_of_child(root, "title")
    link = atom_link(root)
    entries: list[ParsedFeedEntry] = []
    for entry in children(root, "entry"):
        raw = element_to_dict(entry)
        title_text = clean_text(text_of_child(entry, "title"))
        link_text = atom_link(entry)
        published_text = text_of_child(entry, "published")
        updated_text = text_of_child(entry, "updated")
        summary = first_text(entry, ["summary", "description"])
        content = first_text(entry, ["content", "encoded"])
        author = atom_author(entry)
        categories = [clean_text(cat.attrib.get("term") or cat.attrib.get("label") or text_content(cat)) for cat in children(entry, "category")]
        entries.append(
            ParsedFeedEntry(
                entry_id=clean_text(text_of_child(entry, "id")) or link_text,
                title=title_text,
                link=link_text,
                author=author,
                published_at=parse_feed_timestamp(published_text),
                updated_at=parse_feed_timestamp(updated_text),
                summary=strip_html(summary),
                content=strip_html(content),
                categories=[c for c in categories if c],
                raw=raw,
            )
        )
    return ParsedFeed(title=clean_text(title), link=link, entries=entries, raw_root_tag=root.tag)


def parse_rss_feed(root: ET.Element) -> ParsedFeed:
    channel = find_child(root, "channel") or root
    title = clean_text(text_of_child(channel, "title"))
    link = clean_text(text_of_child(channel, "link")) or None
    entries: list[ParsedFeedEntry] = []
    item_nodes = children(channel, "item") or children(root, "item")
    for item in item_nodes:
        raw = element_to_dict(item)
        title_text = clean_text(text_of_child(item, "title"))
        link_text = clean_text(text_of_child(item, "link")) or None
        guid = clean_text(text_of_child(item, "guid")) or link_text
        published_text = first_text(item, ["pubDate", "published", "date", "updated"])
        updated_text = first_text(item, ["updated", "modified"])
        summary = first_text(item, ["description", "summary"])
        content = first_text(item, ["encoded", "content"])
        author = clean_text(first_text(item, ["creator", "author", "managingEditor"])) or None
        categories = [clean_text(text_content(cat)) for cat in children(item, "category")]
        entries.append(
            ParsedFeedEntry(
                entry_id=guid,
                title=title_text,
                link=link_text,
                author=author,
                published_at=parse_feed_timestamp(published_text),
                updated_at=parse_feed_timestamp(updated_text),
                summary=strip_html(summary),
                content=strip_html(content),
                categories=[c for c in categories if c],
                raw=raw,
            )
        )
    return ParsedFeed(title=title, link=link, entries=entries, raw_root_tag=root.tag)


def document_from_feed_entry(
    source: MarketContextSource,
    *,
    config: RssFetchConfig,
    parsed_feed: ParsedFeed,
    entry: ParsedFeedEntry,
    fetched_at: datetime,
    available_at: datetime,
    response_meta: dict[str, Any],
    text: str,
    raw_root: Path,
) -> MarketContextDocument:
    canonical = canonical_document_keys(url=entry.link, title=entry.title, published_at=entry.published_at or entry.updated_at)
    stable_key = canonical["canonical_url"] or entry.entry_id or entry.title or stable_hash((source.source_id, fetched_at.isoformat(), text[:200]))
    document_id = stable_document_id(source.source_id, stable_key)
    metadata = {
        "provider": "rss",
        "feed_url": config.feed_url,
        **canonical,
        "feed_title": parsed_feed.title,
        "feed_link": parsed_feed.link,
        "entry_id": entry.entry_id,
        "categories": entry.categories,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        "response": response_meta,
        "available_at_policy": "published_or_updated_or_fetched",
        "historical_coverage_note": config.historical_coverage_note,
    }
    doc = MarketContextDocument(
        document_id=document_id,
        source_id=source.source_id,
        source_type=source.source_type,
        source_name=source.source_name,
        url=entry.link,
        author=entry.author,
        title=entry.title,
        text=text,
        published_at=entry.published_at or entry.updated_at,
        fetched_at=fetched_at,
        available_at=available_at,
        raw_path=None,
        metadata_json=metadata,
    )
    raw_path = write_raw_rss(source, raw_root, entry, doc, config=config, response_meta=response_meta)
    return doc.model_copy(update={"raw_path": str(raw_path)})


def write_raw_rss(
    source: MarketContextSource,
    raw_root: Path,
    entry: ParsedFeedEntry,
    doc: MarketContextDocument,
    *,
    config: RssFetchConfig,
    response_meta: dict[str, Any],
) -> Path:
    date_part = (doc.published_at or doc.fetched_at).date().isoformat()
    entry_key = stable_hash((doc.document_id, entry.entry_id, entry.link), length=16)
    path = raw_root / "rss" / safe_path_part(source.source_id) / date_part / f"{entry_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source.registry_entry().model_dump(mode="json"),
        "provider": "rss",
        "feed_url": config.feed_url,
        "response": response_meta,
        "raw_entry": entry.raw,
        "parsed_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "link": entry.link,
            "author": entry.author,
            "published_at": entry.published_at.isoformat() if entry.published_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "categories": entry.categories,
            "summary": entry.summary,
            "content": entry.content,
        },
        "document": doc.model_dump(mode="json"),
        "fetched_at": doc.fetched_at.isoformat(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def resolve_available_at(source: MarketContextSource, published_at: datetime | None, updated_at: datetime | None, fetched_at: datetime) -> datetime:
    policy = str(source.config.get("available_at_policy") or "published_at_or_fetched")
    if policy == "fetched_at":
        return fetched_at
    candidate = published_at or updated_at
    if candidate and candidate <= fetched_at:
        return candidate
    return fetched_at


def combine_text(summary: str, content: str) -> str:
    summary = clean_text(summary)
    content = clean_text(content)
    if summary and content and summary != content:
        if content.startswith(summary) or summary in content:
            return content
        return f"{summary}\n\n{content}"
    return content or summary


def parse_feed_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    value = clean_text(value)
    if not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        pass
    try:
        return ensure_utc(email.utils.parsedate_to_datetime(value))
    except Exception:
        return None


def atom_link(element: ET.Element) -> str | None:
    links = children(element, "link")
    if not links:
        return None
    for link in links:
        rel = link.attrib.get("rel", "alternate")
        href = link.attrib.get("href")
        if href and rel in {"alternate", ""}:
            return href.strip()
    for link in links:
        href = link.attrib.get("href")
        if href:
            return href.strip()
    text = clean_text(text_content(links[0]))
    return text or None


def atom_author(element: ET.Element) -> str | None:
    author = find_child(element, "author")
    if author is not None:
        return clean_text(text_of_child(author, "name") or text_content(author)) or None
    return clean_text(text_of_child(element, "creator")) or None


def first_text(element: ET.Element, names: list[str]) -> str:
    for name in names:
        value = text_of_child(element, name)
        if value:
            return value
    return ""


def text_of_child(element: ET.Element, name: str) -> str:
    child = find_child(element, name)
    return text_content(child) if child is not None else ""


def find_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in list(element):
        if local_name(child.tag) == name:
            return child
    return None


def children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if local_name(child.tag) == name]


def text_content(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext())


def element_to_dict(element: ET.Element) -> dict[str, Any]:
    out: dict[str, Any] = {
        "tag": local_name(element.tag),
        "attributes": dict(element.attrib),
        "text": clean_text(element.text or ""),
        "children": [],
    }
    for child in list(element):
        out["children"].append(element_to_dict(child))
    return out


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    if ":" in tag:
        return tag.rsplit(":", 1)[1]
    return tag


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = TAG_RE.sub(" ", value)
    return clean_text(value)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return SPACE_RE.sub(" ", html.unescape(str(value))).strip()


def rate_limit_wait(response: requests.Response) -> int:
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return max(1, int(retry_after))
    return 30


def safe_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "unknown"
