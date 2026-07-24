"""RSS/news snapshot ingestion.

RSS and article pages mutate, so we store observed timestamps and raw-ish fields.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd


def fetch_rss_feeds(feed_urls: Iterable[str], out_path: str | Path | None = None) -> pd.DataFrame:
    try:
        import feedparser
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install optional dependency with `pip install -e .[data]` for RSS ingestion.") from exc
    observed_at = datetime.now(UTC)
    rows = []
    for url in feed_urls:
        parsed = feedparser.parse(url)
        for entry in parsed.entries:
            published = getattr(entry, "published", None) or getattr(entry, "updated", None)
            rows.append(
                {
                    "source": url,
                    "title": getattr(entry, "title", ""),
                    "link": getattr(entry, "link", ""),
                    "summary": getattr(entry, "summary", ""),
                    "published_at": published,
                    "available_at": observed_at,
                    "observed_at_utc": observed_at,
                    "feed_status": getattr(parsed, "status", None),
                    "etag": getattr(parsed, "etag", None),
                    "modified": getattr(parsed, "modified", None),
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
        df["available_at"] = pd.to_datetime(df["available_at"], utc=True)
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return df


def extract_article_text(url: str) -> dict[str, str | None]:
    try:
        import trafilatura
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install optional dependency with `pip install -e .[data]` for article extraction.") from exc
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return {"url": url, "text": None, "html": None}
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    return {"url": url, "text": text, "html": downloaded}
