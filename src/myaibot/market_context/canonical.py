"""Canonicalization helpers for market-context documents.

These functions are intentionally conservative. They are used for duplicate
inspection and metadata hints, not for deleting or rewriting records.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
    "cmpid",
    "source",
}
TITLE_SUFFIXES = [
    "muddy waters research",
    "hindenburg research",
    "spruce point capital",
    "fuzzy panda research",
    "grizzly reports",
    "blue orca capital",
    "kerrisdale capital",
]
SPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    url = html.unescape(str(url)).strip()
    if not url:
        return ""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    kept_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_KEYS or any(key_lower.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        kept_params.append((key, value))
    query = urlencode(sorted(kept_params))
    return urlunparse((scheme, netloc, path, "", query, ""))


def canonical_title(title: str | None) -> str:
    if not title:
        return ""
    text = html.unescape(str(title))
    text = TAG_RE.sub(" ", text)
    text = text.lower()
    for suffix in TITLE_SUFFIXES:
        text = re.sub(rf"(?:\s*[-|–—:]\s*)?{re.escape(suffix)}\s*$", "", text).strip()
    text = NON_ALNUM_RE.sub(" ", text)
    return SPACE_RE.sub(" ", text).strip()


def canonical_date(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def canonical_document_keys(*, url: str | None, title: str | None, published_at: Any = None) -> dict[str, str]:
    url_key = canonical_url(url)
    title_key = canonical_title(title)
    date_key = canonical_date(published_at)
    title_date_key = "|".join(part for part in [title_key, date_key] if part)
    return {
        "canonical_url": url_key,
        "canonical_title": title_key,
        "canonical_date": date_key,
        "canonical_title_date_key": title_date_key,
    }
