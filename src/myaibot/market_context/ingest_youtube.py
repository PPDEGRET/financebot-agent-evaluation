"""YouTube market-context ingestion.

Backfill standard for v1:
- Manual video URLs work with only ``youtube-transcript-api`` installed.
- Full channel/playlist backfill from ``--since`` uses public metadata via
  ``yt-dlp`` when available; no YouTube Data API credentials are required.
- If a transcript is unavailable, the video metadata/title is still stored and
  the transcript gap is recorded in metadata/raw provenance.
"""

from __future__ import annotations

import html
import importlib.util
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi

from myaibot.core.time import ensure_utc, utc_now
from myaibot.market_context.schema import MarketContextDocument, stable_document_id
from myaibot.market_context.sources import SourceIngestResult, MarketContextSource

logger = logging.getLogger(__name__)

YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"


@dataclass(frozen=True)
class VideoTarget:
    video_id: str
    url: str
    title: str = ""
    published_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    origin: str = "manual"


class YouTubeIngestor:
    def __init__(
        self,
        *,
        since: datetime,
        raw_root: str | Path = "data/market_context/raw",
        languages: Iterable[str] = ("en",),
        rate_limit_seconds: float = 1.0,
        fetch_metadata: bool = True,
        available_at_policy: str = "published_at_or_fetched",
        metadata_only_when_transcript_missing: bool = True,
    ) -> None:
        self.since = ensure_utc(since)
        self.raw_root = Path(raw_root)
        self.languages = tuple(languages) or ("en",)
        self.rate_limit_seconds = max(0.0, float(rate_limit_seconds))
        self.fetch_metadata = fetch_metadata
        self.available_at_policy = available_at_policy
        self.metadata_only_when_transcript_missing = metadata_only_when_transcript_missing
        self._last_request_at = 0.0
        self._transcript_api = YouTubeTranscriptApi()

    def ingest_source(self, source: MarketContextSource) -> SourceIngestResult:
        result = SourceIngestResult(
            source_id=source.source_id,
            source_name=source.source_name,
            source_type=source.source_type,
        )
        targets: list[VideoTarget] = []
        seen: set[str] = set()
        try:
            for target in self.video_targets_for_source(source, result=result):
                if target.video_id in seen:
                    continue
                seen.add(target.video_id)
                targets.append(target)
        except Exception as exc:  # source-level guard; per-video errors happen below
            result.errors.append(f"target_enumeration_failed: {type(exc).__name__}: {exc}")
            logger.exception("Failed to enumerate YouTube targets for %s", source.source_name)
            return result

        for target in targets:
            try:
                document = self.ingest_video(source, target)
                if document is None:
                    result.skipped.append(f"{target.video_id}: before since or no usable public data")
                    continue
                result.documents.append(document)
            except Exception as exc:
                msg = f"{target.video_id}: {type(exc).__name__}: {exc}"
                result.errors.append(msg)
                logger.exception("Failed to ingest YouTube video %s", target.video_id)
        return result

    def video_targets_for_source(self, source: MarketContextSource, *, result: SourceIngestResult | None = None) -> list[VideoTarget]:
        config = source.config
        targets: list[VideoTarget] = []

        for item in config.get("videos") or []:
            target = target_from_config_item(item)
            if target is None:
                if result:
                    result.skipped.append(f"invalid video config item: {item!r}")
                continue
            targets.append(target)

        collection_urls = []
        for key in ("channel_url", "playlist_url", "url"):
            url = config.get(key)
            if url and not extract_video_id(str(url)):
                collection_urls.append(str(url))
        for url in collection_urls:
            try:
                targets.extend(self.enumerate_collection(url, source))
            except RuntimeError as exc:
                if result:
                    result.errors.append(str(exc))
                else:
                    raise
            except Exception as exc:
                if result:
                    result.errors.append(f"collection {url}: {type(exc).__name__}: {exc}")
                else:
                    raise
        single_url = config.get("url")
        if single_url and extract_video_id(str(single_url)):
            target = target_from_config_item(str(single_url))
            if target:
                targets.append(target)
        return targets

    def enumerate_collection(self, url: str, source: MarketContextSource) -> list[VideoTarget]:
        yt_dlp = require_yt_dlp(
            "Full YouTube channel/playlist backfill requires yt-dlp. "
            "Install optional data dependencies or run `python -m pip install yt-dlp`."
        )
        config = source.config
        max_videos = config.get("max_videos")
        assume_reverse_chronological = bool(config.get("assume_reverse_chronological", True))
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
        }
        if max_videos:
            opts["playlistend"] = int(max_videos)
        self._rate_limit()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = list(iter_flat_entries(info))
        out: list[VideoTarget] = []
        for entry in entries:
            if not entry:
                continue
            video_id = str(entry.get("id") or extract_video_id(str(entry.get("url") or "")) or "").strip()
            if not video_id or not looks_like_youtube_video_id(video_id):
                continue
            published_at = published_at_from_info(entry)
            if published_at and published_at < self.since:
                if assume_reverse_chronological:
                    break
                continue
            entry_url = normalize_video_url(str(entry.get("webpage_url") or entry.get("url") or video_id))
            out.append(
                VideoTarget(
                    video_id=video_id,
                    url=entry_url,
                    title=str(entry.get("title") or ""),
                    published_at=published_at,
                    metadata=safe_jsonish(entry),
                    origin="yt_dlp_collection",
                )
            )
        return out

    def ingest_video(self, source: MarketContextSource, target: VideoTarget) -> MarketContextDocument | None:
        fetched_at = utc_now()
        metadata: dict[str, Any] = dict(target.metadata or {})
        metadata_error: str | None = None
        if self.fetch_metadata:
            try:
                fetched_metadata = self.fetch_video_metadata(target.video_id)
                metadata.update(fetched_metadata)
            except Exception as exc:
                metadata_error = f"{type(exc).__name__}: {exc}"
                metadata["metadata_error"] = metadata_error

        published_at = first_timestamp(target.published_at, published_at_from_info(metadata))
        if published_at and published_at < self.since:
            return None

        transcript_payload, transcript_error = self.fetch_transcript(target.video_id)
        if not transcript_payload["snippets"] and not self.metadata_only_when_transcript_missing:
            return None

        text = transcript_text(transcript_payload)
        if not text and source.config.get("include_description_when_no_transcript", True):
            text = str(metadata.get("description") or "")

        title = str(metadata.get("title") or target.title or "")
        url = normalize_video_url(str(metadata.get("webpage_url") or metadata.get("original_url") or target.url))
        author = str(metadata.get("uploader") or metadata.get("channel") or metadata.get("creator") or "") or None
        available_at = self.resolve_available_at(published_at, fetched_at)

        document = MarketContextDocument(
            document_id=stable_document_id(source.source_id, target.video_id),
            source_id=source.source_id,
            source_type="youtube",
            source_name=source.source_name,
            url=url,
            author=author,
            title=title,
            text=text,
            published_at=published_at,
            fetched_at=fetched_at,
            available_at=available_at,
            raw_path=None,
            metadata_json={
                "provider": "youtube",
                "video_id": target.video_id,
                "origin": target.origin,
                "channel": metadata.get("channel"),
                "channel_id": metadata.get("channel_id"),
                "uploader_id": metadata.get("uploader_id"),
                "duration": metadata.get("duration"),
                "view_count": metadata.get("view_count"),
                "tags": metadata.get("tags") or [],
                "categories": metadata.get("categories") or [],
                "language_requested": list(self.languages),
                "transcript_available": bool(transcript_payload["snippets"]),
                "transcript_source": transcript_payload.get("source"),
                "transcript_language": transcript_payload.get("language"),
                "transcript_language_code": transcript_payload.get("language_code"),
                "transcript_is_generated": transcript_payload.get("is_generated"),
                "transcript_primary_error": transcript_payload.get("primary_error"),
                "transcript_error": transcript_error,
                "metadata_error": metadata_error,
                "available_at_policy": self.available_at_policy,
                "availability_basis": availability_basis(self.available_at_policy, published_at),
                "historical_coverage_note": (
                    "video metadata backfilled from public YouTube pages via yt-dlp when available; "
                    "transcript historical edit/version timing is not provided by YouTube transcripts"
                ),
            },
        )
        raw_path = self.write_raw(
            source,
            target.video_id,
            {
                "source": source.registry_entry().model_dump(mode="json"),
                "target": target.__dict__ | {"published_at": target.published_at.isoformat() if target.published_at else None},
                "metadata": safe_jsonish(metadata),
                "transcript": safe_jsonish(transcript_payload),
                "transcript_error": transcript_error,
                "document": document.model_dump(mode="json"),
                "fetched_at": fetched_at.isoformat(),
            },
            published_at=published_at,
            fetched_at=fetched_at,
        )
        return document.model_copy(update={"raw_path": str(raw_path)})

    def fetch_video_metadata(self, video_id: str) -> dict[str, Any]:
        if importlib.util.find_spec("yt_dlp") is None:
            return {"metadata_status": "yt_dlp_not_installed"}
        yt_dlp = require_yt_dlp("yt-dlp missing")
        opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "skip_download": True,
        }
        self._rate_limit()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(YOUTUBE_WATCH_URL.format(video_id=video_id), download=False)
        keep_keys = {
            "id",
            "title",
            "description",
            "webpage_url",
            "original_url",
            "channel",
            "channel_id",
            "channel_url",
            "uploader",
            "uploader_id",
            "creator",
            "timestamp",
            "release_timestamp",
            "upload_date",
            "duration",
            "view_count",
            "like_count",
            "comment_count",
            "tags",
            "categories",
            "availability",
            "live_status",
        }
        return {k: safe_jsonish(v) for k, v in (info or {}).items() if k in keep_keys}

    def fetch_transcript(self, video_id: str) -> tuple[dict[str, Any], str | None]:
        self._rate_limit()
        try:
            transcript = self._transcript_api.fetch(video_id, languages=self.languages)
            snippets = [snippet_to_dict(snippet) for snippet in transcript.snippets]
            return (
                {
                    "video_id": transcript.video_id,
                    "language": transcript.language,
                    "language_code": transcript.language_code,
                    "is_generated": transcript.is_generated,
                    "source": "youtube_transcript_api",
                    "snippets": snippets,
                },
                None,
            )
        except Exception as exc:  # library exposes several request/transcript exceptions
            primary_error = f"{type(exc).__name__}: {exc}"

        fallback_payload, fallback_error = self.fetch_yt_dlp_caption_transcript(video_id)
        if fallback_payload["snippets"]:
            fallback_payload["primary_error"] = primary_error
            return fallback_payload, None
        combined_error = primary_error if not fallback_error else f"{primary_error}; yt_dlp_caption_fallback: {fallback_error}"
        return ({"video_id": video_id, "snippets": [], "source": None}, combined_error)

    def fetch_yt_dlp_caption_transcript(self, video_id: str) -> tuple[dict[str, Any], str | None]:
        if importlib.util.find_spec("yt_dlp") is None:
            return ({"video_id": video_id, "snippets": [], "source": None}, "yt_dlp_not_installed")
        yt_dlp = require_yt_dlp("yt-dlp missing")
        opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": list(self.languages) + [f"{language}.*" for language in self.languages],
        }
        self._rate_limit()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(YOUTUBE_WATCH_URL.format(video_id=video_id), download=False)
        except Exception as exc:
            return ({"video_id": video_id, "snippets": [], "source": None}, f"metadata_failed: {type(exc).__name__}: {exc}")

        tracks = choose_caption_tracks(info or {}, self.languages)
        if not tracks:
            return ({"video_id": video_id, "snippets": [], "source": None}, "no_public_caption_tracks")
        errors: list[str] = []
        for source_name, language_code, track in tracks:
            url = track.get("url")
            if not url:
                continue
            self._rate_limit()
            try:
                response = requests.get(str(url), timeout=30, headers={"User-Agent": "Mozilla/5.0 FINANCEBOT market_context ingestion"})
                response.raise_for_status()
                ext = str(track.get("ext") or "").lower()
                if ext == "json3":
                    snippets = parse_json3_transcript(response.text)
                elif ext == "vtt":
                    snippets = parse_vtt_transcript(response.text)
                else:
                    errors.append(f"unsupported_caption_ext:{ext or 'unknown'}")
                    continue
                if snippets:
                    return (
                        {
                            "video_id": video_id,
                            "language": track.get("name") or language_code,
                            "language_code": language_code,
                            "is_generated": source_name == "automatic_captions",
                            "source": f"yt_dlp_{source_name}",
                            "snippets": snippets,
                        },
                        None,
                    )
                errors.append(f"empty_caption_track:{language_code}:{ext}")
            except Exception as exc:
                errors.append(f"{language_code}:{type(exc).__name__}: {exc}")
        return ({"video_id": video_id, "snippets": [], "source": None}, "; ".join(errors) or "caption_fetch_failed")

    def resolve_available_at(self, published_at: datetime | None, fetched_at: datetime) -> datetime:
        if self.available_at_policy == "fetched_at":
            return fetched_at
        if self.available_at_policy == "published_at_or_fetched" and published_at and published_at <= fetched_at:
            return published_at
        return fetched_at

    def write_raw(
        self,
        source: MarketContextSource,
        video_id: str,
        payload: dict[str, Any],
        *,
        published_at: datetime | None,
        fetched_at: datetime,
    ) -> Path:
        date_part = (published_at or fetched_at).date().isoformat()
        path = self.raw_root / "youtube" / safe_path_part(source.source_id) / date_part / f"{video_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(path)
        return path

    def _rate_limit(self) -> None:
        if self.rate_limit_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request_at = time.monotonic()


def ingest_youtube_source(
    source: MarketContextSource,
    *,
    since: datetime,
    raw_root: str | Path = "data/market_context/raw",
) -> SourceIngestResult:
    config = source.config
    ingestor = YouTubeIngestor(
        since=since,
        raw_root=raw_root,
        languages=config.get("languages") or ("en",),
        rate_limit_seconds=float(config.get("rate_limit_seconds", 1.0)),
        fetch_metadata=bool(config.get("fetch_metadata", True)),
        available_at_policy=str(config.get("available_at_policy", "published_at_or_fetched")),
        metadata_only_when_transcript_missing=bool(config.get("metadata_only_when_transcript_missing", True)),
    )
    return ingestor.ingest_source(source)


def target_from_config_item(item: Any) -> VideoTarget | None:
    if isinstance(item, str):
        video_id = extract_video_id(item) or (item if looks_like_youtube_video_id(item) else None)
        if not video_id:
            return None
        return VideoTarget(video_id=video_id, url=normalize_video_url(item), origin="manual")
    if isinstance(item, dict):
        raw_url = str(item.get("url") or item.get("video_url") or item.get("video_id") or "")
        video_id = str(item.get("video_id") or extract_video_id(raw_url) or "").strip()
        if not video_id or not looks_like_youtube_video_id(video_id):
            return None
        published_at = ensure_utc(item["published_at"]) if item.get("published_at") else None
        return VideoTarget(
            video_id=video_id,
            url=normalize_video_url(raw_url or video_id),
            title=str(item.get("title") or ""),
            published_at=published_at,
            metadata={k: v for k, v in item.items() if k not in {"url", "video_url", "video_id", "published_at", "title"}},
            origin="manual",
        )
    return None


def extract_video_id(value: str) -> str | None:
    value = value.strip()
    if looks_like_youtube_video_id(value):
        return value
    parsed = urlparse(value)
    host = parsed.netloc.lower().replace("www.", "")
    if host in {"youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if looks_like_youtube_video_id(candidate) else None
    if "youtube.com" in host or "youtube-nocookie.com" in host:
        qs = parse_qs(parsed.query)
        if qs.get("v"):
            candidate = qs["v"][0]
            return candidate if looks_like_youtube_video_id(candidate) else None
        parts = [p for p in parsed.path.split("/") if p]
        for marker in ("shorts", "embed", "live"):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    candidate = parts[idx + 1]
                    return candidate if looks_like_youtube_video_id(candidate) else None
    return None


def looks_like_youtube_video_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", value.strip()))


def normalize_video_url(value: str) -> str:
    video_id = extract_video_id(value)
    if video_id:
        return YOUTUBE_WATCH_URL.format(video_id=video_id)
    return value


def require_yt_dlp(message: str) -> Any:
    if importlib.util.find_spec("yt_dlp") is None:
        raise RuntimeError(message)
    import yt_dlp  # type: ignore[import-not-found]

    return yt_dlp


def iter_flat_entries(info: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(info, dict):
        return []
    entries = info.get("entries")
    if not entries:
        return [info]
    out: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("entries"):
            out.extend(iter_flat_entries(entry))
        elif isinstance(entry, dict):
            out.append(entry)
    return out


def published_at_from_info(info: dict[str, Any] | None) -> datetime | None:
    if not info:
        return None
    for key in ("release_timestamp", "timestamp"):
        value = info.get(key)
        if value:
            try:
                return ensure_utc(datetime.fromtimestamp(float(value)))
            except Exception:
                pass
    upload_date = info.get("upload_date")
    if upload_date:
        text = str(upload_date)
        try:
            if re.fullmatch(r"\d{8}", text):
                return ensure_utc(datetime.strptime(text, "%Y%m%d"))
            return ensure_utc(text)
        except Exception:
            return None
    return None


def first_timestamp(*values: datetime | None) -> datetime | None:
    for value in values:
        if value is not None:
            return ensure_utc(value)
    return None


def snippet_to_dict(snippet: Any) -> dict[str, Any]:
    if isinstance(snippet, dict):
        return {
            "text": str(snippet.get("text") or ""),
            "start": float(snippet.get("start") or 0.0),
            "duration": float(snippet.get("duration") or 0.0),
        }
    return {
        "text": str(getattr(snippet, "text", "")),
        "start": float(getattr(snippet, "start", 0.0)),
        "duration": float(getattr(snippet, "duration", 0.0)),
    }


def transcript_text(payload: dict[str, Any]) -> str:
    return "\n".join(snippet.get("text", "") for snippet in payload.get("snippets") or [] if snippet.get("text"))


def choose_caption_tracks(info: dict[str, Any], languages: Iterable[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return public caption tracks in preference order.

    Human subtitles are preferred over automatic captions; json3 is preferred
    because it preserves transcript offsets cleanly.
    """
    preferred_languages = list(languages) or ["en"]
    out: list[tuple[str, str, dict[str, Any]]] = []
    for source_name in ("subtitles", "automatic_captions"):
        collection = info.get(source_name) or {}
        if not isinstance(collection, dict):
            continue
        language_keys = rank_caption_languages(collection.keys(), preferred_languages)
        for language_code in language_keys:
            tracks = collection.get(language_code) or []
            if not isinstance(tracks, list):
                continue
            for track in sorted(tracks, key=lambda t: caption_ext_rank(str(t.get("ext") or ""))):
                if isinstance(track, dict):
                    out.append((source_name, language_code, track))
    return out


def rank_caption_languages(keys: Iterable[str], preferred: Iterable[str]) -> list[str]:
    keys = list(keys)
    ranked: list[str] = []
    for language in preferred:
        language = language.lower()
        for key in keys:
            key_lower = key.lower()
            if key_lower == language and key not in ranked:
                ranked.append(key)
        for key in keys:
            key_lower = key.lower()
            if (key_lower.startswith(language + "-") or key_lower.startswith(language + ".")) and key not in ranked:
                ranked.append(key)
    for key in keys:
        if key not in ranked and key.lower().startswith("en"):
            ranked.append(key)
    return ranked


def caption_ext_rank(ext: str) -> int:
    order = {"json3": 0, "vtt": 1, "srv3": 2, "ttml": 3}
    return order.get(ext.lower(), 99)


def parse_json3_transcript(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    snippets: list[dict[str, Any]] = []
    for event in data.get("events") or []:
        segs = event.get("segs") or []
        caption = "".join(str(seg.get("utf8") or "") for seg in segs if isinstance(seg, dict))
        caption = html.unescape(SPACE_RE.sub(" ", caption)).strip()
        if not caption:
            continue
        snippets.append(
            {
                "text": caption,
                "start": float(event.get("tStartMs") or 0.0) / 1000.0,
                "duration": float(event.get("dDurationMs") or 0.0) / 1000.0,
            }
        )
    return snippets


def parse_vtt_transcript(text: str) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if "-->" not in line:
            idx += 1
            continue
        start_text, end_text = [part.strip().split(" ", 1)[0] for part in line.split("-->", 1)]
        idx += 1
        caption_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip():
            caption_lines.append(re.sub(r"<[^>]+>", "", lines[idx]))
            idx += 1
        caption = html.unescape(SPACE_RE.sub(" ", " ".join(caption_lines))).strip()
        if caption:
            start = parse_vtt_timestamp(start_text)
            end = parse_vtt_timestamp(end_text)
            snippets.append({"text": caption, "start": start, "duration": max(0.0, end - start)})
        idx += 1
    return snippets


def parse_vtt_timestamp(value: str) -> float:
    pieces = value.replace(",", ".").split(":")
    try:
        if len(pieces) == 3:
            hours, minutes, seconds = pieces
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(pieces) == 2:
            minutes, seconds = pieces
            return int(minutes) * 60 + float(seconds)
        return float(pieces[0])
    except Exception:
        return 0.0


SPACE_RE = re.compile(r"\s+")


def safe_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe_jsonish(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_jsonish(v) for v in value]
    if isinstance(value, tuple):
        return [safe_jsonish(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def safe_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "unknown"


def availability_basis(policy: str, published_at: datetime | None) -> str:
    if policy == "fetched_at":
        return "fetched_at"
    if published_at:
        return "published_at_public_video_upload"
    return "fetched_at_no_published_at"
