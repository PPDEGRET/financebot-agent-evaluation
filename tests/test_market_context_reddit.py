from datetime import UTC, datetime

from myaibot.market_context.ingest_reddit import (
    document_from_comment,
    document_from_post,
    ingest_reddit_source,
    normalize_response,
    parse_provider_order,
    parse_reddit_rss_items,
    reddit_fetch_config,
    reddit_url,
    split_time_range,
)
from myaibot.market_context.sources import MarketContextSource


def reddit_source() -> MarketContextSource:
    return MarketContextSource(
        source_id="reddit_wsb",
        source_name="r/wallstreetbets",
        source_type="reddit",
        locator="wallstreetbets",
        config={"subreddit": "wallstreetbets", "provider": "arctic_shift"},
    )


def test_normalize_response_shapes():
    assert normalize_response(None) == []
    assert normalize_response([{"id": "a"}, "bad"]) == [{"id": "a"}]
    assert normalize_response({"data": [{"id": "b"}]}) == [{"id": "b"}]
    assert normalize_response({"id": "c"}) == [{"id": "c"}]


def test_split_time_range_utc_windows():
    windows = split_time_range(
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 2, tzinfo=UTC),
        hours=6,
    )
    assert len(windows) == 4
    assert windows[0][0].isoformat() == "2025-01-01T00:00:00+00:00"


def test_document_from_post_and_comment(tmp_path):
    source = reddit_source()
    post = document_from_post(
        source,
        {
            "id": "abc123",
            "subreddit": "wallstreetbets",
            "author": "user1",
            "created_utc": 1735689600,
            "title": "$GME and TSLA",
            "selftext": "discussion text",
            "score": 10,
            "num_comments": 2,
            "permalink": "/r/wallstreetbets/comments/abc123/title/",
        },
        tmp_path,
    )
    assert post is not None
    assert post.source_type == "reddit_post"
    assert post.available_at == post.published_at
    assert post.url == "https://www.reddit.com/r/wallstreetbets/comments/abc123/title/"
    assert post.raw_path and post.raw_path.endswith("abc123.json")

    comment = document_from_comment(
        source,
        {
            "id": "def456",
            "subreddit": "wallstreetbets",
            "author": "user2",
            "created_utc": 1735689700,
            "body": "NVDA comment",
            "score": 5,
            "link_id": "t3_abc123",
            "parent_id": "t3_abc123",
        },
        tmp_path,
    )
    assert comment is not None
    assert comment.source_type == "reddit_comment"
    assert comment.url == "https://www.reddit.com/r/wallstreetbets/comments/abc123/_/def456/"


def test_reddit_url_external_permalink():
    assert reddit_url({"permalink": "https://reddit.com/x"}, kind="post") == "https://reddit.com/x"


def test_provider_order_parsing_and_config_aliases():
    assert parse_provider_order(["arctic", "rss", "pull_push"]) == ("arctic_shift", "reddit_rss", "pullpush")
    source = MarketContextSource(
        source_id="reddit_test",
        source_name="r/test",
        source_type="reddit",
        config={"subreddit": "test", "provider_order": ["rss"], "allow_large_backfill": "true"},
    )
    config = reddit_fetch_config(source)
    assert config.provider_order == ("reddit_rss",)
    assert config.allow_large_backfill is True


def test_parse_reddit_rss_items_atom():
    feed = '''<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.reddit.com/r/wallstreetbets/comments/abc123/title/</id>
        <title>$NVDA to the moon</title>
        <author><name>/u/test_user</name></author>
        <link href="https://www.reddit.com/r/wallstreetbets/comments/abc123/title/" />
        <published>2026-06-23T10:00:00+00:00</published>
        <content type="html">&lt;p&gt;body text&lt;/p&gt;</content>
      </entry>
    </feed>'''
    items = parse_reddit_rss_items(feed, subreddit="wallstreetbets")
    assert len(items) == 1
    assert items[0]["id"] == "abc123"
    assert items[0]["author"] == "test_user"
    assert items[0]["created_utc"] == 1782208800
    assert items[0]["selftext"] == "body text"


def test_reddit_large_window_guardrail(tmp_path):
    source = reddit_source()
    result = ingest_reddit_source(
        source,
        since=datetime(2025, 1, 1, tzinfo=UTC),
        until=datetime(2025, 1, 10, tzinfo=UTC),
        raw_root=tmp_path,
    )
    assert result.documents == []
    assert result.errors
    assert "reddit_window_too_large" in result.errors[0]
