from datetime import datetime, timezone

from myaibot.market_context.canonical import canonical_document_keys, canonical_title, canonical_url


def test_canonical_url_strips_tracking_and_www():
    assert (
        canonical_url("https://www.Example.com/reports/ABC/?utm_source=x&keep=1#frag")
        == "https://example.com/reports/ABC?keep=1"
    )


def test_canonical_title_strips_html_suffix_and_punctuation():
    assert canonical_title("<b>Blue Orca is Short ABC</b> - Blue Orca Capital") == "blue orca is short abc"


def test_canonical_document_keys_title_date():
    keys = canonical_document_keys(
        url="https://www.example.com/a/?utm_campaign=x",
        title="ABC Report",
        published_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    assert keys["canonical_url"] == "https://example.com/a"
    assert keys["canonical_title"] == "abc report"
    assert keys["canonical_date"] == "2025-01-02"
    assert keys["canonical_title_date_key"] == "abc report|2025-01-02"
