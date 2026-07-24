"""Public short-seller / research report page ingestion.

This module targets low-volume public report archives that do not expose reliable
RSS feeds. It avoids credentials and preserves page provenance. Parsers are
conservative and source-configurable because report archive HTML varies widely.
"""

from __future__ import annotations

import email.utils
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests

from myaibot.core.time import ensure_utc, utc_now
from myaibot.market_context.canonical import canonical_document_keys, canonical_url
from myaibot.market_context.schema import MarketContextDocument, stable_document_id, stable_hash
from myaibot.market_context.sources import SourceIngestResult, MarketContextSource

logger = logging.getLogger(__name__)
SPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", re.I | re.S)
ANCHOR_RE = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
META_RE = re.compile(r"<meta\s+[^>]*(?:property|name)=[\"']([^\"']+)[\"'][^>]*content=[\"']([^\"']*)[\"'][^>]*>", re.I | re.S)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
TIME_RE = re.compile(r"<time\b[^>]*datetime=[\"']([^\"']+)[\"'][^>]*>(.*?)</time>", re.I | re.S)
MONTH_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+20\d{2}\b",
    re.I,
)
ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")

DEFAULT_EXCLUDE_PATTERNS = [
    r"#",
    r"/about",
    r"/contact",
    r"/privacy",
    r"/cookie",
    r"/legal",
    r"/terms",
    r"/subscribe",
    r"/author/",
    r"/category/",
    r"/tag/",
    r"/page/\d+/?$",
    r"/wp-content/",
    r"javascript:",
    r"mailto:",
]


@dataclass(frozen=True)
class ReportFetchConfig:
    url: str
    parser: str = "generic"
    rate_limit_seconds: float = 1.0
    timeout_seconds: int = 45
    max_retries: int = 2
    backoff_factor: float = 2.0
    max_items_per_source: int = 50
    include_url_patterns: tuple[str, ...] = ()
    exclude_url_patterns: tuple[str, ...] = tuple(DEFAULT_EXCLUDE_PATTERNS)
    additional_listing_urls: tuple[str, ...] = ()
    user_agent: str = "FINANCEBOT market-context public report ingestion"
    include_unknown_dates: bool = True


@dataclass(frozen=True)
class ReportCandidate:
    url: str
    title: str = ""
    listing_text: str = ""
    published_at: datetime | None = None
    metadata: dict[str, Any] | None = None


def ingest_report_source(
    source: MarketContextSource,
    *,
    since: datetime,
    until: datetime | None = None,
    raw_root: str | Path = "data/market_context/raw",
) -> SourceIngestResult:
    result = SourceIngestResult(source_id=source.source_id, source_name=source.source_name, source_type=source.source_type)
    try:
        config = report_fetch_config(source)
    except Exception as exc:
        result.errors.append(f"invalid_report_config: {type(exc).__name__}: {exc}")
        return result

    since_dt = ensure_utc(since)
    until_dt = ensure_utc(until) if until else None
    fetched_at = utc_now()

    try:
        candidates = discover_report_candidates(source, config)
    except Exception as exc:
        result.errors.append(f"report_discovery_failed: {type(exc).__name__}: {exc}")
        logger.exception("Report discovery failed for %s", source.source_name)
        return result

    raw_root_path = Path(raw_root)
    seen: set[str] = set()
    skipped_before = 0
    skipped_after = 0
    skipped_unknown = 0
    skipped_duplicate = 0
    documents: list[MarketContextDocument] = []

    for candidate in candidates:
        if candidate.url in seen:
            skipped_duplicate += 1
            continue
        seen.add(candidate.url)
        try:
            doc = build_report_document(source, config, candidate, fetched_at=fetched_at, raw_root=raw_root_path)
        except Exception as exc:
            result.skipped.append(f"candidate_failed:{candidate.url}:{type(exc).__name__}:{exc}"[:500])
            continue
        effective_time = doc.published_at
        if effective_time is None and not config.include_unknown_dates:
            skipped_unknown += 1
            continue
        if effective_time and effective_time < since_dt:
            skipped_before += 1
            continue
        if effective_time and until_dt and effective_time > until_dt:
            skipped_after += 1
            continue
        documents.append(doc)
        if len(documents) >= config.max_items_per_source:
            result.skipped.append(f"max_items_per_source_reached:{config.max_items_per_source}")
            break

    result.documents = documents
    result.skipped.extend(
        [
            f"candidates_discovered:{len(candidates)}",
            f"skipped_duplicate:{skipped_duplicate}",
            f"skipped_before_since:{skipped_before}",
            f"skipped_after_until:{skipped_after}",
            f"skipped_unknown_date:{skipped_unknown}",
            "coverage_note:public report archive pages; historical completeness depends on source archive and parser visibility",
        ]
    )
    return result


def report_fetch_config(source: MarketContextSource) -> ReportFetchConfig:
    cfg = source.config
    url = str(cfg.get("url") or cfg.get("listing_url") or "").strip()
    if not url:
        raise ValueError("missing url")
    return ReportFetchConfig(
        url=url,
        parser=str(cfg.get("parser") or "generic"),
        rate_limit_seconds=float(cfg.get("rate_limit_seconds", 1.0)),
        timeout_seconds=int(cfg.get("timeout_seconds", 45)),
        max_retries=int(cfg.get("max_retries", 2)),
        backoff_factor=float(cfg.get("backoff_factor", 2.0)),
        max_items_per_source=int(cfg.get("max_items_per_source", 50)),
        include_url_patterns=tuple(str(x) for x in cfg.get("include_url_patterns", []) or []),
        exclude_url_patterns=tuple(str(x) for x in cfg.get("exclude_url_patterns", DEFAULT_EXCLUDE_PATTERNS) or []),
        additional_listing_urls=tuple(str(x) for x in cfg.get("additional_listing_urls", []) or []),
        user_agent=str(cfg.get("user_agent") or "FINANCEBOT market-context public report ingestion"),
        include_unknown_dates=as_bool(cfg.get("include_unknown_dates", True)),
    )


def discover_report_candidates(source: MarketContextSource, config: ReportFetchConfig) -> list[ReportCandidate]:
    urls = [config.url, *config.additional_listing_urls]
    candidates: list[ReportCandidate] = manual_report_candidates(source)
    listing_errors: list[str] = []
    for listing_url in urls:
        try:
            listing_html, response_meta = fetch_html(listing_url, config)
        except Exception as exc:
            listing_errors.append(f"{listing_url}: {type(exc).__name__}: {exc}")
            continue
        if config.parser == "kerrisdale_listing":
            candidates.extend(parse_kerrisdale_listing(listing_html, listing_url, response_meta=response_meta))
        else:
            candidates.extend(parse_generic_listing(listing_html, listing_url, config=config, response_meta=response_meta))
    if not candidates and listing_errors:
        raise RuntimeError("; ".join(listing_errors))
    return dedupe_candidates(candidates)


def manual_report_candidates(source: MarketContextSource) -> list[ReportCandidate]:
    manual = source.config.get("manual_urls") or source.config.get("manual_reports") or []
    candidates: list[ReportCandidate] = []
    for item in manual:
        if isinstance(item, str):
            url = item.strip()
            if not url:
                continue
            candidates.append(ReportCandidate(url=url, metadata={"manual_url": True}))
            continue
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("report_url") or "").strip()
        if not url:
            continue
        published_at = parse_report_date(str(item.get("published_at") or item.get("date") or ""))
        candidates.append(
            ReportCandidate(
                url=url,
                title=str(item.get("title") or ""),
                listing_text=str(item.get("text") or item.get("summary") or item.get("notes") or ""),
                published_at=published_at,
                metadata={"manual_url": True, **{k: v for k, v in item.items() if k not in {"url", "report_url", "published_at", "date", "title", "text", "summary"}}},
            )
        )
    return candidates


def parse_generic_listing(
    listing_html: str,
    listing_url: str,
    *,
    config: ReportFetchConfig,
    response_meta: dict[str, Any],
) -> list[ReportCandidate]:
    base_netloc = normalize_netloc(urlparse(listing_url).netloc)
    candidates: list[ReportCandidate] = []
    for match in ANCHOR_RE.finditer(listing_html):
        href = urljoin(listing_url, html.unescape(match.group(1)))
        label = strip_html(match.group(2))
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        same_site = normalize_netloc(parsed.netloc) == base_netloc
        if not same_site and not config.include_url_patterns:
            continue
        if not url_allowed(href, label, config):
            continue
        candidates.append(
            ReportCandidate(
                url=href,
                title=label,
                listing_text=label,
                published_at=parse_report_date(label),
                metadata={"listing_url": listing_url, "listing_response": response_meta, "listing_label": label},
            )
        )
    return candidates


def parse_kerrisdale_listing(listing_html: str, listing_url: str, *, response_meta: dict[str, Any]) -> list[ReportCandidate]:
    blocks = re.split(r'<div><div class="each-post">', listing_html)
    candidates: list[ReportCandidate] = []
    for block in blocks[1:]:
        company = clean_text(first_match(block, r'<h2[^>]*class="post-heading[^>]*>\s*<a[^>]*>(.*?)</a>'))
        subtitle = clean_text(first_match(block, r'<p[^>]*class="post-desc[^>]*>(.*?)</p>'))
        month = clean_text(first_match(block, r'<div[^>]*class="post-month"[^>]*>(.*?)</div>'))
        day = clean_text(first_match(block, r'<div[^>]*class="post-day"[^>]*>(.*?)</div>'))
        year = clean_text(first_match(block, r'<div[^>]*class="post-year"[^>]*>(.*?)</div>'))
        detail_url = first_match(block, r"toggleExcerpt\([^)]*,'([^']+)'", flags=re.S)
        report_url = first_match(block, r'<a\s+href=["\']([^"\']+)["\'][^>]*>\s*Read Full Report\s*</a>', flags=re.I | re.S)
        excerpt = strip_html(first_match(block, r'<div class="single-blog-post-description-all">(.*?)</div>', flags=re.I | re.S))
        title = clean_text(" - ".join(part for part in [company, subtitle] if part))
        if not title and not detail_url and not report_url:
            continue
        published = parse_report_date(f"{month} {day}, {year}") if month and day and year else None
        url = detail_url or report_url
        if not url:
            continue
        candidates.append(
            ReportCandidate(
                url=urljoin(listing_url, url),
                title=title,
                listing_text=excerpt,
                published_at=published,
                metadata={
                    "listing_url": listing_url,
                    "listing_response": response_meta,
                    "report_url": urljoin(listing_url, report_url) if report_url else None,
                    "company": company,
                    "subtitle": subtitle,
                    "parser": "kerrisdale_listing",
                },
            )
        )
    return candidates


def build_report_document(
    source: MarketContextSource,
    config: ReportFetchConfig,
    candidate: ReportCandidate,
    *,
    fetched_at: datetime,
    raw_root: Path,
) -> MarketContextDocument:
    detail_html = ""
    response_meta: dict[str, Any] = {}
    if should_fetch_detail(candidate.url):
        try:
            detail_html, response_meta = fetch_html(candidate.url, config)
        except Exception as exc:
            response_meta = {"detail_fetch_error": f"{type(exc).__name__}: {exc}", "url": candidate.url}
    page_meta = extract_page_metadata(detail_html, candidate.url) if detail_html else {}
    title = strip_html(page_meta.get("title") or candidate.title)
    published_at = page_meta.get("published_at") or candidate.published_at
    text = clean_text(page_meta.get("text") or candidate.listing_text or title)
    available_at = published_at if published_at and published_at <= fetched_at else fetched_at
    document_id = stable_document_id(source.source_id, candidate.url)
    final_url = str(response_meta.get("final_url") or candidate.url)
    report_url = (candidate.metadata or {}).get("report_url")
    canonical = canonical_document_keys(url=report_url or final_url, title=title, published_at=published_at)
    metadata = {
        "provider": "short_report_page",
        "source_url": config.url,
        **canonical,
        "canonical_source_url": canonical_url(config.url),
        "candidate_url": candidate.url,
        "detail_response": response_meta,
        "listing_metadata": candidate.metadata or {},
        "report_url": report_url,
        "parser": config.parser,
        "available_at_policy": "published_at_or_fetched",
        "date_confidence": "detected" if published_at else "unknown_fetched_at_only",
        "historical_coverage_note": "Public report archive page ingestion; completeness depends on visible archive links and source accessibility.",
    }
    doc = MarketContextDocument(
        document_id=document_id,
        source_id=source.source_id,
        source_type="short_report",
        source_name=source.source_name,
        url=candidate.url,
        author=source.source_name,
        title=title,
        text=text,
        published_at=published_at,
        fetched_at=fetched_at,
        available_at=available_at,
        raw_path=None,
        metadata_json=metadata,
    )
    raw_path = write_raw_report(source, raw_root, candidate, doc, detail_html=detail_html, metadata=metadata)
    return doc.model_copy(update={"raw_path": str(raw_path)})


def fetch_html(url: str, config: ReportFetchConfig) -> tuple[str, dict[str, Any]]:
    headers = {"User-Agent": config.user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=config.timeout_seconds, allow_redirects=True)
            if response.status_code == 429:
                time.sleep(30)
                continue
            if 500 <= response.status_code < 600 and attempt < config.max_retries:
                time.sleep(config.backoff_factor**attempt)
                continue
            response.raise_for_status()
            if config.rate_limit_seconds > 0:
                time.sleep(config.rate_limit_seconds)
            meta = {
                "url": url,
                "final_url": response.url,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
                "last_modified": response.headers.get("last-modified"),
            }
            return response.text, meta
        except requests.RequestException as exc:
            last_error = exc
            if attempt < config.max_retries:
                time.sleep(config.backoff_factor**attempt)
    raise RuntimeError(f"fetch failed for {url}: {last_error}")


def extract_page_metadata(page_html: str, url: str) -> dict[str, Any]:
    meta = {clean_text(k).lower(): clean_text(v) for k, v in META_RE.findall(page_html)}
    title = (
        meta.get("og:title")
        or meta.get("twitter:title")
        or clean_text(first_match(page_html, H1_RE))
        or clean_text(first_match(page_html, TITLE_RE))
    )
    for suffix in [" - Muddy Waters Research", " | Hindenburg Research", " - Spruce Point Capital", " - Fuzzy Panda Research", " - Grizzly Reports"]:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    published_at = None
    for key in ["article:published_time", "datepublished", "date", "pubdate", "publish_date", "sailthru.date"]:
        if meta.get(key):
            published_at = parse_report_date(meta[key])
            if published_at:
                break
    if not published_at:
        for dt, label in TIME_RE.findall(page_html):
            published_at = parse_report_date(dt) or parse_report_date(label)
            if published_at:
                break
    if not published_at:
        for date_html in re.findall(r'<[^>]*class=["\'][^"\']*(?:date|published|time)[^"\']*["\'][^>]*>(.*?)</[^>]+>', page_html, re.I | re.S):
            published_at = parse_report_date(strip_html(date_html))
            if published_at:
                break
    if not published_at:
        published_at = parse_report_date(strip_html(page_html[:15000]))
    text = readable_text(page_html)
    return {"title": title, "published_at": published_at, "text": text}


def readable_text(page_html: str) -> str:
    page_html = SCRIPT_STYLE_RE.sub(" ", page_html)
    text = strip_html(page_html)
    # Drop very long whitespace-normalized boilerplate tails.
    return text[:100_000]


def write_raw_report(
    source: MarketContextSource,
    raw_root: Path,
    candidate: ReportCandidate,
    doc: MarketContextDocument,
    *,
    detail_html: str,
    metadata: dict[str, Any],
) -> Path:
    date_part = (doc.published_at or doc.fetched_at).date().isoformat()
    entry_key = stable_hash((doc.document_id, candidate.url), length=16)
    path = raw_root / "short_report" / safe_path_part(source.source_id) / date_part / f"{entry_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source.registry_entry().model_dump(mode="json"),
        "provider": "short_report_page",
        "candidate": {
            "url": candidate.url,
            "title": candidate.title,
            "listing_text": candidate.listing_text,
            "published_at": candidate.published_at.isoformat() if candidate.published_at else None,
            "metadata": candidate.metadata or {},
        },
        "metadata": metadata,
        "detail_html_excerpt": detail_html[:250_000] if detail_html else "",
        "document": doc.model_dump(mode="json"),
        "fetched_at": doc.fetched_at.isoformat(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def url_allowed(url: str, label: str, config: ReportFetchConfig) -> bool:
    url_text = url.lower()
    haystack = f"{url} {label}".lower()
    parsed = urlparse(url)
    if parsed.path in {"", "/"}:
        return False
    for pattern in config.exclude_url_patterns:
        if re.search(pattern, haystack, re.I):
            return False
    if config.include_url_patterns:
        return any(re.search(pattern, url_text, re.I) for pattern in config.include_url_patterns)
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path or "." in path.rsplit("/", 1)[-1] and not path.endswith(".pdf"):
        return False
    return True


def dedupe_candidates(candidates: Iterable[ReportCandidate]) -> list[ReportCandidate]:
    out: list[ReportCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        clean_url = candidate.url.rstrip("/") + ("/" if candidate.url.endswith("/") else "")
        if clean_url in seen:
            continue
        seen.add(clean_url)
        out.append(candidate)
    return out


def should_fetch_detail(url: str) -> bool:
    return not urlparse(url).path.lower().endswith(".pdf")


def parse_report_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = clean_text(value)
    candidates = [text]
    candidates.extend(MONTH_DATE_RE.findall(text))
    candidates.extend(ISO_DATE_RE.findall(text))
    for candidate in candidates:
        try:
            return ensure_utc(datetime.fromisoformat(candidate.replace("Z", "+00:00")))
        except Exception:
            pass
        try:
            return ensure_utc(email.utils.parsedate_to_datetime(candidate))
        except Exception:
            pass
        for fmt in ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"]:
            try:
                return ensure_utc(datetime.strptime(candidate, fmt))
            except Exception:
                pass
    return None


def first_match(text: str, pattern: str | re.Pattern[str], *, flags: int = re.I | re.S) -> str:
    if isinstance(pattern, re.Pattern):
        match = pattern.search(text)
    else:
        match = re.search(pattern, text, flags)
    return match.group(1) if match else ""


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


def normalize_netloc(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")


def safe_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "unknown"


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
