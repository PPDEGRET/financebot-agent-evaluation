from datetime import UTC, datetime

from myaibot.market_context.ingest_rss import ingest_rss_source, parse_feed, parse_feed_timestamp
from myaibot.market_context.sources import MarketContextSource


def test_parse_atom_feed():
    feed = '''<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Example Atom</title>
      <link href="https://example.com/" />
      <entry>
        <id>tag:example.com,2025:1</id>
        <title>$TSLA Atom Title</title>
        <author><name>Alice</name></author>
        <link href="https://example.com/a" />
        <published>2025-01-02T03:04:05Z</published>
        <summary>&lt;p&gt;Atom summary&lt;/p&gt;</summary>
        <category term="macro" />
      </entry>
    </feed>'''
    parsed = parse_feed(feed)
    assert parsed.title == "Example Atom"
    assert len(parsed.entries) == 1
    entry = parsed.entries[0]
    assert entry.title == "$TSLA Atom Title"
    assert entry.author == "Alice"
    assert entry.link == "https://example.com/a"
    assert entry.summary == "Atom summary"
    assert entry.categories == ["macro"]
    assert entry.published_at == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_rss_feed_content_encoded():
    feed = '''<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel>
        <title>Example RSS</title>
        <link>https://example.com</link>
        <item>
          <guid>abc</guid>
          <title>NVDA RSS Title</title>
          <link>https://example.com/rss</link>
          <pubDate>Thu, 02 Jan 2025 03:04:05 GMT</pubDate>
          <dc:creator>Bob</dc:creator>
          <description>&lt;p&gt;Short summary&lt;/p&gt;</description>
          <content:encoded>&lt;p&gt;Full content&lt;/p&gt;</content:encoded>
          <category>AI</category>
        </item>
      </channel>
    </rss>'''
    parsed = parse_feed(feed)
    entry = parsed.entries[0]
    assert parsed.title == "Example RSS"
    assert entry.entry_id == "abc"
    assert entry.author == "Bob"
    assert entry.summary == "Short summary"
    assert entry.content == "Full content"
    assert entry.categories == ["AI"]


def test_parse_feed_timestamp_email_date():
    assert parse_feed_timestamp("Thu, 02 Jan 2025 03:04:05 GMT") == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_ingest_rss_source_with_monkeypatched_fetch(monkeypatch, tmp_path):
    feed = '''<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Example RSS</title>
      <item><guid>old</guid><title>Old</title><pubDate>Thu, 01 Jan 2024 00:00:00 GMT</pubDate><description>old</description></item>
      <item><guid>new</guid><title>$MSFT New</title><link>https://example.com/new</link><pubDate>Thu, 02 Jan 2025 00:00:00 GMT</pubDate><description>new text</description></item>
    </channel></rss>'''

    def fake_fetch(config):
        return feed, {"feed_url": config.feed_url, "status_code": 200}

    monkeypatch.setattr("myaibot.market_context.ingest_rss.fetch_feed", fake_fetch)
    source = MarketContextSource(
        source_id="rss_test",
        source_name="RSS Test",
        source_type="rss",
        config={"feed_url": "https://example.com/feed.xml"},
    )
    result = ingest_rss_source(source, since=datetime(2025, 1, 1, tzinfo=UTC), raw_root=tmp_path)
    assert result.errors == []
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.title == "$MSFT New"
    assert doc.available_at == datetime(2025, 1, 2, tzinfo=UTC)
    assert doc.raw_path and doc.raw_path.endswith(".json")
    assert "skipped_before_since:1" in result.skipped
