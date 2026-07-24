"""Reddit market-context ingestion from public/no-credential sources.

Provider stack:
1. Arctic Shift: primary historical/daily archive provider.
2. Reddit RSS/Atom: current/latest fallback for daily collection.
3. PullPush: Pushshift-style historical fallback; not reliable for fresh data.

The connector is deliberately guarded. Large Reddit backfills must be explicit;
daily collection should use bounded overlapping windows such as 24-36 hours.
"""

from __future__ import annotations

import email.utils
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests

from myaibot.core.time import ensure_utc, utc_now
from myaibot.market_context.schema import MarketContextDocument, stable_document_id, stable_hash
from myaibot.market_context.sources import SourceIngestResult, MarketContextSource

logger = logging.getLogger(__name__)

ARCTIC_SHIFT_BASE_URL = "https://arctic-shift.photon-reddit.com"
PULLPUSH_BASE_URL = "https://api.pullpush.io"
REDDIT_RSS_URLS = (
    "https://www.reddit.com/r/{subreddit}/new/.rss?limit={limit}",
    "https://old.reddit.com/r/{subreddit}/new/.rss?limit={limit}",
)

# Keep Arctic Shift field sets conservative. The API returns HTTP 400 for some
# unsupported field names; this post list matches the proven WSB repo pattern.
POST_FIELDS = ["id", "subreddit", "author", "created_utc", "title", "selftext", "score", "num_comments"]
COMMENT_FIELDS = ["id", "subreddit", "author", "created_utc", "body", "score", "link_id", "parent_id"]

PROVIDER_ALIASES = {
    "arctic": "arctic_shift",
    "arctic-shift": "arctic_shift",
    "arctic_shift": "arctic_shift",
    "rss": "reddit_rss",
    "reddit-rss": "reddit_rss",
    "reddit_rss": "reddit_rss",
    "pull-push": "pullpush",
    "pull_push": "pullpush",
    "pullpush": "pullpush",
}


@dataclass(frozen=True)
class RedditFetchConfig:
    provider_order: tuple[str, ...] = ("arctic_shift", "reddit_rss", "pullpush")
    include_posts: bool = True
    include_comments: bool = False
    window_hours_posts: int = 1
    window_hours_comments: int = 1
    sleep_seconds: float = 0.5
    timeout_seconds: int = 60
    max_retries: int = 3
    backoff_factor: float = 2.0
    request_limit_posts: int = 500
    request_limit_comments: int = 500
    pullpush_size_posts: int = 100
    pullpush_size_comments: int = 100
    reddit_rss_limit: int = 100
    max_windows: int | None = None
    max_items_per_source: int | None = None
    max_backfill_days: float = 3.0
    allow_large_backfill: bool = False
    fallback_on_empty: bool = True


class ArcticShiftClient:
    def __init__(
        self,
        *,
        base_url: str = ARCTIC_SHIFT_BASE_URL,
        timeout_seconds: int = 60,
        sleep_seconds: float = 0.5,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.sleep_seconds = max(0.0, float(sleep_seconds))
        self.max_retries = max(1, int(max_retries))
        self.backoff_factor = float(backoff_factor)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FINANCEBOT market_context public Reddit archive ingestion"})

    def search_posts(self, **params: Any) -> list[dict[str, Any]]:
        return normalize_response(self.request("/api/posts/search", params))

    def search_comments(self, **params: Any) -> list[dict[str, Any]]:
        return normalize_response(self.request("/api/comments/search", params))

    def request(self, endpoint: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{endpoint}"
        clean_params = {k: join_param(v) for k, v in params.items() if v is not None}
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, params=clean_params, timeout=self.timeout_seconds)
                if response.status_code == 429:
                    wait = rate_limit_wait(response)
                    logger.warning("Arctic Shift rate limited; sleeping %ss", wait)
                    time.sleep(wait)
                    continue
                if response.status_code == 504 or "Query timed out" in response.text:
                    if attempt >= self.max_retries:
                        raise RuntimeError(f"Arctic Shift query timed out: endpoint={endpoint} params={clean_params}")
                    wait = self.backoff_factor**attempt
                    logger.warning("Arctic Shift timeout attempt %s/%s; sleeping %.1fs", attempt, self.max_retries, wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as exc:
                    raise RuntimeError(
                        f"Arctic Shift returned non-JSON: status={response.status_code} text={response.text[:500]}"
                    ) from exc
                if self.sleep_seconds:
                    time.sleep(self.sleep_seconds)
                return data
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Arctic Shift request failed: endpoint={endpoint} params={clean_params}") from exc
                wait = self.backoff_factor**attempt
                logger.warning("Arctic Shift request failed attempt %s/%s; sleeping %.1fs", attempt, self.max_retries, wait)
                time.sleep(wait)
        raise RuntimeError(f"Unexpected Arctic Shift failure: endpoint={endpoint}")


class PullPushClient:
    def __init__(self, *, base_url: str = PULLPUSH_BASE_URL, timeout_seconds: int = 60, sleep_seconds: float = 0.5) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.sleep_seconds = max(0.0, float(sleep_seconds))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FINANCEBOT market_context public Reddit PullPush fallback"})

    def search_submissions(self, **params: Any) -> list[dict[str, Any]]:
        return normalize_response(self.request("/reddit/search/submission/", params))

    def search_comments(self, **params: Any) -> list[dict[str, Any]]:
        return normalize_response(self.request("/reddit/search/comment/", params))

    def request(self, endpoint: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}{endpoint}"
        response = self.session.get(url, params={k: v for k, v in params.items() if v is not None}, timeout=self.timeout_seconds)
        if response.status_code == 429:
            raise RuntimeError(f"PullPush rate limited: {response.text[:300]}")
        response.raise_for_status()
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        return response.json()


def ingest_reddit_source(
    source: MarketContextSource,
    *,
    since: datetime,
    until: datetime | None = None,
    raw_root: str | Path = "data/market_context/raw",
) -> SourceIngestResult:
    result = SourceIngestResult(source_id=source.source_id, source_name=source.source_name, source_type=source.source_type)
    config = reddit_fetch_config(source)
    subreddit = str(source.config.get("subreddit") or "").strip().lstrip("r/")
    if not subreddit:
        result.errors.append("missing_subreddit")
        return result

    since_dt = ensure_utc(since)
    until_dt = ensure_utc(until or source.config.get("until") or utc_now())
    if until_dt <= since_dt:
        result.errors.append("until must be after since")
        return result

    if not config.allow_large_backfill:
        span_days = (until_dt - since_dt).total_seconds() / 86400.0
        if span_days > config.max_backfill_days:
            result.errors.append(
                "reddit_window_too_large: "
                f"{span_days:.2f} days requested for r/{subreddit}; max_backfill_days={config.max_backfill_days}. "
                "Use a smaller --since/--until window or set allow_large_backfill: true intentionally."
            )
            return result

    provider_notes: list[str] = []
    for provider in config.provider_order:
        try:
            docs = fetch_reddit_provider_documents(
                provider,
                source=source,
                subreddit=subreddit,
                since=since_dt,
                until=until_dt,
                raw_root=Path(raw_root),
                config=config,
            )
        except Exception as exc:
            note = f"{provider}: {type(exc).__name__}: {exc}"
            provider_notes.append(note)
            logger.warning("Reddit provider failed for r/%s: %s", subreddit, note)
            continue
        if docs:
            result.documents.extend(docs)
            result.skipped.extend(f"provider_note: {note}" for note in provider_notes)
            result.skipped.append(f"provider_used: {provider}")
            return result
        provider_notes.append(f"{provider}: no documents returned")
        if not config.fallback_on_empty:
            break

    result.skipped.extend(f"provider_note: {note}" for note in provider_notes)
    if provider_notes and all("no documents returned" not in note for note in provider_notes):
        result.errors.append("all_reddit_providers_failed: " + " | ".join(provider_notes))
    return result


def fetch_reddit_provider_documents(
    provider: str,
    *,
    source: MarketContextSource,
    subreddit: str,
    since: datetime,
    until: datetime,
    raw_root: Path,
    config: RedditFetchConfig,
) -> list[MarketContextDocument]:
    if provider == "arctic_shift":
        return fetch_arctic_shift_documents(source, subreddit, since, until, raw_root, config)
    if provider == "reddit_rss":
        return fetch_reddit_rss_documents(source, subreddit, since, until, raw_root, config)
    if provider == "pullpush":
        return fetch_pullpush_documents(source, subreddit, since, until, raw_root, config)
    raise ValueError(f"unsupported_reddit_provider:{provider}")


def fetch_arctic_shift_documents(
    source: MarketContextSource,
    subreddit: str,
    since: datetime,
    until: datetime,
    raw_root: Path,
    config: RedditFetchConfig,
) -> list[MarketContextDocument]:
    client = ArcticShiftClient(
        base_url=str(source.config.get("arctic_shift_base_url") or source.config.get("base_url") or ARCTIC_SHIFT_BASE_URL),
        timeout_seconds=config.timeout_seconds,
        sleep_seconds=config.sleep_seconds,
        max_retries=config.max_retries,
        backoff_factor=config.backoff_factor,
    )
    documents: list[MarketContextDocument] = []
    if config.include_posts:
        for item in iter_reddit_items(
            client,
            kind="post",
            subreddit=subreddit,
            start=since,
            end=until,
            window_hours=config.window_hours_posts,
            request_limit=config.request_limit_posts,
            max_windows=config.max_windows,
        ):
            doc = document_from_post(source, item, raw_root, provider="arctic_shift")
            if doc:
                documents.append(doc)
            if config.max_items_per_source and len(documents) >= config.max_items_per_source:
                return documents
    if config.include_comments:
        for item in iter_reddit_items(
            client,
            kind="comment",
            subreddit=subreddit,
            start=since,
            end=until,
            window_hours=config.window_hours_comments,
            request_limit=config.request_limit_comments,
            max_windows=config.max_windows,
        ):
            doc = document_from_comment(source, item, raw_root, provider="arctic_shift")
            if doc:
                documents.append(doc)
            if config.max_items_per_source and len(documents) >= config.max_items_per_source:
                return documents
    return documents


def fetch_pullpush_documents(
    source: MarketContextSource,
    subreddit: str,
    since: datetime,
    until: datetime,
    raw_root: Path,
    config: RedditFetchConfig,
) -> list[MarketContextDocument]:
    client = PullPushClient(
        base_url=str(source.config.get("pullpush_base_url") or PULLPUSH_BASE_URL),
        timeout_seconds=config.timeout_seconds,
        sleep_seconds=config.sleep_seconds,
    )
    documents: list[MarketContextDocument] = []
    if config.include_posts:
        for item in fetch_pullpush_paginated(
            client,
            kind="post",
            subreddit=subreddit,
            start=since,
            end=until,
            size=config.pullpush_size_posts,
        ):
            doc = document_from_post(source, item, raw_root, provider="pullpush")
            if doc:
                documents.append(doc)
            if config.max_items_per_source and len(documents) >= config.max_items_per_source:
                return documents
    if config.include_comments:
        for item in fetch_pullpush_paginated(
            client,
            kind="comment",
            subreddit=subreddit,
            start=since,
            end=until,
            size=config.pullpush_size_comments,
        ):
            doc = document_from_comment(source, item, raw_root, provider="pullpush")
            if doc:
                documents.append(doc)
            if config.max_items_per_source and len(documents) >= config.max_items_per_source:
                return documents
    return documents


def fetch_reddit_rss_documents(
    source: MarketContextSource,
    subreddit: str,
    since: datetime,
    until: datetime,
    raw_root: Path,
    config: RedditFetchConfig,
) -> list[MarketContextDocument]:
    if not config.include_posts:
        return []
    urls = source.config.get("reddit_rss_urls") or source.config.get("rss_urls") or REDDIT_RSS_URLS
    if isinstance(urls, str):
        urls = [urls]
    headers = {"User-Agent": "FINANCEBOT market_context Reddit RSS fallback; no credentials"}
    last_error: str | None = None
    for template in urls:
        url = str(template).format(subreddit=subreddit, limit=config.reddit_rss_limit)
        try:
            response = requests.get(url, headers=headers, timeout=config.timeout_seconds)
            if response.status_code == 429:
                raise RuntimeError("reddit_rss_rate_limited_429")
            response.raise_for_status()
            items = parse_reddit_rss_items(response.text, subreddit=subreddit)
            documents: list[MarketContextDocument] = []
            for item in items:
                created = created_utc_to_dt(item.get("created_utc"))
                if not created or created < since or created > until:
                    continue
                doc = document_from_post(source, item, raw_root, provider="reddit_rss")
                if doc:
                    documents.append(doc)
                if config.max_items_per_source and len(documents) >= config.max_items_per_source:
                    break
            if config.sleep_seconds:
                time.sleep(config.sleep_seconds)
            return documents
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Reddit RSS URL failed for r/%s: %s", subreddit, last_error)
            continue
    raise RuntimeError(last_error or "reddit_rss_failed")


def reddit_fetch_config(source: MarketContextSource) -> RedditFetchConfig:
    cfg = source.config
    return RedditFetchConfig(
        provider_order=parse_provider_order(cfg.get("provider_order") or cfg.get("providers") or cfg.get("provider")),
        include_posts=as_bool(cfg.get("include_posts", True)),
        include_comments=as_bool(cfg.get("include_comments", False)),
        window_hours_posts=int(cfg.get("window_hours_posts", cfg.get("window_hours", 1))),
        window_hours_comments=int(cfg.get("window_hours_comments", 1)),
        sleep_seconds=float(cfg.get("sleep_seconds", cfg.get("rate_limit_seconds", 0.5))),
        timeout_seconds=int(cfg.get("timeout_seconds", 60)),
        max_retries=int(cfg.get("max_retries", 3)),
        backoff_factor=float(cfg.get("backoff_factor", 2.0)),
        request_limit_posts=int(cfg.get("request_limit_posts", cfg.get("request_limit", 500))),
        request_limit_comments=int(cfg.get("request_limit_comments", cfg.get("request_limit", 500))),
        pullpush_size_posts=int(cfg.get("pullpush_size_posts", cfg.get("pullpush_size", 100))),
        pullpush_size_comments=int(cfg.get("pullpush_size_comments", cfg.get("pullpush_size", 100))),
        reddit_rss_limit=int(cfg.get("reddit_rss_limit", 100)),
        max_windows=int(cfg["max_windows"]) if cfg.get("max_windows") else None,
        max_items_per_source=int(cfg["max_items_per_source"]) if cfg.get("max_items_per_source") else None,
        max_backfill_days=float(cfg.get("max_backfill_days", 3.0)),
        allow_large_backfill=as_bool(cfg.get("allow_large_backfill", False)),
        fallback_on_empty=as_bool(cfg.get("fallback_on_empty", True)),
    )


def iter_reddit_items(
    client: ArcticShiftClient,
    *,
    kind: str,
    subreddit: str,
    start: datetime,
    end: datetime,
    window_hours: int,
    request_limit: int,
    max_windows: int | None = None,
) -> Iterable[dict[str, Any]]:
    seen: set[str] = set()
    windows_yielded = 0
    for window_start, window_end in split_time_range(start, end, hours=window_hours):
        if max_windows is not None and windows_yielded >= max_windows:
            break
        windows_yielded += 1
        for item in fetch_arctic_window_paginated(
            client,
            kind=kind,
            subreddit=subreddit,
            start=window_start,
            end=window_end,
            request_limit=request_limit,
        ):
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            yield item


def fetch_arctic_window_paginated(
    client: ArcticShiftClient,
    *,
    kind: str,
    subreddit: str,
    start: datetime,
    end: datetime,
    request_limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after = format_arctic_date(start)
    before = format_arctic_date(end)
    seen: set[str] = set()
    fields = POST_FIELDS if kind == "post" else COMMENT_FIELDS
    request_limit = max(1, min(int(request_limit), 1000))

    while True:
        if kind == "post":
            batch = client.search_posts(subreddit=subreddit, after=after, before=before, limit=request_limit, sort="asc", fields=fields)
        elif kind == "comment":
            batch = client.search_comments(subreddit=subreddit, after=after, before=before, limit=request_limit, sort="asc", fields=fields)
        else:
            raise ValueError(f"unsupported reddit kind: {kind}")
        if not batch:
            break

        new_rows = 0
        for item in batch:
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            rows.append(item)
            new_rows += 1
        if len(batch) < request_limit or not new_rows:
            break
        last_created = max((int(float(item.get("created_utc") or 0)) for item in batch), default=0)
        if not last_created:
            break
        next_after = ensure_utc(datetime.fromtimestamp(last_created + 1, tz=UTC))
        if next_after >= end:
            break
        after = format_arctic_date(next_after)
    return rows


def fetch_pullpush_paginated(
    client: PullPushClient,
    *,
    kind: str,
    subreddit: str,
    start: datetime,
    end: datetime,
    size: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    after = int(ensure_utc(start).timestamp())
    before = int(ensure_utc(end).timestamp())
    size = max(1, min(int(size), 100))
    while after < before:
        params = {"subreddit": subreddit, "after": after, "before": before, "size": size, "sort": "asc", "sort_type": "created_utc"}
        batch = client.search_submissions(**params) if kind == "post" else client.search_comments(**params)
        if not batch:
            break
        new_rows = 0
        last_created = after
        for item in batch:
            item_id = str(item.get("id") or "")
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            rows.append(item)
            new_rows += 1
            try:
                last_created = max(last_created, int(float(item.get("created_utc") or after)))
            except Exception:
                pass
        if len(batch) < size or not new_rows:
            break
        next_after = last_created + 1
        if next_after <= after:
            break
        after = next_after
    return rows


def document_from_post(source: MarketContextSource, item: dict[str, Any], raw_root: Path, *, provider: str = "arctic_shift") -> MarketContextDocument | None:
    reddit_id = str(item.get("id") or "").strip()
    published_at = created_utc_to_dt(item.get("created_utc"))
    if not reddit_id or not published_at:
        return None
    fetched_at = utc_now()
    title = str(item.get("title") or "")
    text = str(item.get("selftext") or item.get("body") or "")
    url = reddit_url(item, kind="post")
    doc = MarketContextDocument(
        document_id=stable_document_id(source.source_id, "post", reddit_id),
        source_id=source.source_id,
        source_type="reddit_post",
        source_name=source.source_name,
        url=url,
        author=str(item.get("author") or "") or None,
        title=title,
        text=text,
        published_at=published_at,
        fetched_at=fetched_at,
        available_at=published_at,
        raw_path=None,
        metadata_json={
            "provider": provider,
            "subreddit": item.get("subreddit") or source.config.get("subreddit"),
            "reddit_kind": "post",
            "reddit_id": reddit_id,
            "score": item.get("score"),
            "num_comments": item.get("num_comments"),
            "link_flair_text": item.get("link_flair_text"),
            "author_flair_text": item.get("author_flair_text"),
            "over_18": item.get("over_18"),
            "spoiler": item.get("spoiler"),
            "external_url": item.get("url"),
            "available_at_policy": "reddit_created_utc",
            "historical_coverage_note": reddit_coverage_note(provider),
        },
    )
    raw_path = write_raw_reddit(source, raw_root, "post", reddit_id, item, doc, provider=provider)
    return doc.model_copy(update={"raw_path": str(raw_path)})


def document_from_comment(source: MarketContextSource, item: dict[str, Any], raw_root: Path, *, provider: str = "arctic_shift") -> MarketContextDocument | None:
    reddit_id = str(item.get("id") or "").strip()
    published_at = created_utc_to_dt(item.get("created_utc"))
    if not reddit_id or not published_at:
        return None
    fetched_at = utc_now()
    text = str(item.get("body") or "")
    doc = MarketContextDocument(
        document_id=stable_document_id(source.source_id, "comment", reddit_id),
        source_id=source.source_id,
        source_type="reddit_comment",
        source_name=source.source_name,
        url=reddit_url(item, kind="comment"),
        author=str(item.get("author") or "") or None,
        title="",
        text=text,
        published_at=published_at,
        fetched_at=fetched_at,
        available_at=published_at,
        raw_path=None,
        metadata_json={
            "provider": provider,
            "subreddit": item.get("subreddit") or source.config.get("subreddit"),
            "reddit_kind": "comment",
            "reddit_id": reddit_id,
            "score": item.get("score"),
            "link_id": item.get("link_id"),
            "parent_id": item.get("parent_id"),
            "author_flair_text": item.get("author_flair_text"),
            "available_at_policy": "reddit_created_utc",
            "historical_coverage_note": reddit_coverage_note(provider),
        },
    )
    raw_path = write_raw_reddit(source, raw_root, "comment", reddit_id, item, doc, provider=provider)
    return doc.model_copy(update={"raw_path": str(raw_path)})


def write_raw_reddit(
    source: MarketContextSource,
    raw_root: Path,
    kind: str,
    reddit_id: str,
    item: dict[str, Any],
    doc: MarketContextDocument,
    *,
    provider: str,
) -> Path:
    date_part = doc.published_at.date().isoformat() if doc.published_at else doc.fetched_at.date().isoformat()
    path = raw_root / "reddit" / safe_path_part(source.source_id) / provider / kind / date_part / f"{reddit_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source.registry_entry().model_dump(mode="json"),
        "provider": provider,
        "kind": kind,
        "raw_item": item,
        "document": doc.model_dump(mode="json"),
        "fetched_at": doc.fetched_at.isoformat(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def parse_reddit_rss_items(feed_text: str, *, subreddit: str) -> list[dict[str, Any]]:
    root = ET.fromstring(feed_text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", ns):
        title = entry.findtext("a:title", default="", namespaces=ns)
        author = entry.findtext("a:author/a:name", default="", namespaces=ns)
        link_el = entry.find("a:link", ns)
        link = link_el.attrib.get("href") if link_el is not None else ""
        entry_id = entry.findtext("a:id", default="", namespaces=ns)
        published_text = entry.findtext("a:published", default="", namespaces=ns) or entry.findtext("a:updated", default="", namespaces=ns)
        content_text = entry.findtext("a:content", default="", namespaces=ns)
        published_at = parse_feed_timestamp(published_text)
        reddit_id = extract_reddit_post_id(link) or extract_reddit_post_id(entry_id) or f"rss_{stable_hash((link, title, published_text), length=12)}"
        items.append(
            {
                "id": reddit_id,
                "subreddit": subreddit,
                "author": clean_reddit_author(author),
                "created_utc": int(published_at.timestamp()) if published_at else None,
                "title": html.unescape(title or ""),
                "selftext": strip_html(content_text),
                "permalink": link,
                "url": link,
                "score": None,
                "num_comments": None,
            }
        )
    return items


def parse_provider_order(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        raw = ["arctic_shift", "reddit_rss", "pullpush"]
    elif isinstance(value, str):
        raw = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, Iterable):
        raw = [str(part).strip() for part in value if str(part).strip()]
    else:
        raw = [str(value).strip()]
    providers: list[str] = []
    for item in raw:
        normalized = PROVIDER_ALIASES.get(item.lower().replace(" ", "_"), item.lower().replace(" ", "_"))
        if normalized not in providers:
            providers.append(normalized)
    return tuple(providers or ["arctic_shift", "reddit_rss", "pullpush"])


def normalize_response(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [data]
    return []


def split_time_range(start: datetime, end: datetime, *, hours: int) -> list[tuple[datetime, datetime]]:
    start = ensure_utc(start)
    end = ensure_utc(end)
    if start >= end:
        raise ValueError("start must be earlier than end")
    hours = max(1, int(hours))
    windows: list[tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(hours=hours), end)
        windows.append((cur, nxt))
        cur = nxt
    return windows


def format_arctic_date(dt: datetime) -> str:
    return ensure_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def created_utc_to_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return ensure_utc(datetime.fromtimestamp(float(value), tz=UTC))
    except Exception:
        try:
            return ensure_utc(value)
        except Exception:
            return None


def parse_feed_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        try:
            return ensure_utc(email.utils.parsedate_to_datetime(value))
        except Exception:
            return None


def reddit_url(item: dict[str, Any], *, kind: str) -> str | None:
    permalink = item.get("permalink")
    if permalink:
        permalink = str(permalink)
        return permalink if permalink.startswith("http") else f"https://www.reddit.com{permalink}"
    subreddit = str(item.get("subreddit") or "")
    reddit_id = str(item.get("id") or "")
    if kind == "post" and subreddit and reddit_id:
        return f"https://www.reddit.com/r/{subreddit}/comments/{reddit_id}/"
    link_id = str(item.get("link_id") or "").removeprefix("t3_")
    if kind == "comment" and subreddit and link_id and reddit_id:
        return f"https://www.reddit.com/r/{subreddit}/comments/{link_id}/_/{reddit_id}/"
    return None


def extract_reddit_post_id(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"/comments/([A-Za-z0-9_]+)/", str(value))
    return match.group(1) if match else None


def clean_reddit_author(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    return value.removeprefix("/u/").removeprefix("u/") or None


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def join_param(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v) for v in value)
    return value


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def rate_limit_wait(response: requests.Response) -> int:
    reset = response.headers.get("X-RateLimit-Reset")
    if reset and str(reset).isdigit():
        value = int(reset)
        now = int(time.time())
        if value > now:
            return max(value - now, 1)
        return max(value, 1)
    return 30


def reddit_coverage_note(provider: str) -> str:
    if provider == "arctic_shift":
        return "Arctic Shift public archive backfill; Reddit edit/delete history may be incomplete."
    if provider == "reddit_rss":
        return "Reddit public RSS latest-feed fallback; rolling current coverage only, not reliable deep backfill."
    if provider == "pullpush":
        return "PullPush public Pushshift-style fallback; useful for historical gaps but observed stale for current WSB."
    return "Public Reddit provider; coverage limitations unknown."


def safe_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "unknown"
