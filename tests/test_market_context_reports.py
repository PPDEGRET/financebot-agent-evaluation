from datetime import UTC, datetime

from myaibot.market_context.ingest_reports import (
    extract_page_metadata,
    ingest_report_source,
    parse_generic_listing,
    parse_kerrisdale_listing,
    parse_report_date,
    report_fetch_config,
)
from myaibot.market_context.sources import MarketContextSource


def test_parse_report_date_month_and_iso():
    assert parse_report_date("May 19, 2026") == datetime(2026, 5, 19, tzinfo=UTC)
    assert parse_report_date("2025-01-15") == datetime(2025, 1, 15, tzinfo=UTC)


def test_extract_page_metadata_report_date_class_and_html_title():
    html = '<html><head><title>&lt;span&gt;Blue Orca is Short ABC&lt;/span&gt;</title></head><body><p class="report-date">February 26, 2026</p><p>Report text</p></body></html>'
    meta = extract_page_metadata(html, "https://example.com/report")
    assert meta["title"] == "<span>Blue Orca is Short ABC</span>"
    assert parse_report_date(meta["title"]) is None
    assert meta["published_at"] == datetime(2026, 2, 26, tzinfo=UTC)


def test_parse_generic_listing_with_include_patterns():
    html = '''<html><body>
      <a href="/about/">About</a>
      <a href="/research/2026/mw-is-short-abc/">MW is Short ABC</a>
      <a href="/company/abc/">ABC</a>
    </body></html>'''
    source = MarketContextSource(
        source_id="short_test",
        source_name="Short Test",
        source_type="short_report",
        config={
            "url": "https://example.com/research/",
            "include_url_patterns": ["example\\.com/research/2026/"],
        },
    )
    config = report_fetch_config(source)
    candidates = parse_generic_listing(html, "https://example.com/research/", config=config, response_meta={})
    assert len(candidates) == 1
    assert candidates[0].url == "https://example.com/research/2026/mw-is-short-abc/"
    assert candidates[0].title == "MW is Short ABC"


def test_parse_kerrisdale_listing_block():
    html = '''<div><div class="each-post"><h2 class="post-heading ">
      <a onclick="toggleExcerpt(1,1,jQuery(this),'https://www.kerrisdalecap.com/investments/everspin-mram/','2');">Everspin Technologies</a></h2>
      <a><p class="post-desc "> Memory Error</p><div class="post-date"><div class="post-month">May</div><div class="post-day">19</div><div class="post-year">2026</div></div></a></div>
      <div class="excerpt-data"><div class="single-blog-post-body-all"><div class="disclosure-report-all">
      <a href="https://kerr.co/mram" target="_blank" class="css3-button">Read Full Report</a></div>
      <div class="single-blog-post-description-all"><p>We are short shares.</p></div></div></div></div>'''
    candidates = parse_kerrisdale_listing(html, "https://www.kerrisdalecap.com/blog/", response_meta={})
    assert len(candidates) == 1
    assert candidates[0].title == "Everspin Technologies - Memory Error"
    assert candidates[0].published_at == datetime(2026, 5, 19, tzinfo=UTC)
    assert candidates[0].metadata["report_url"] == "https://kerr.co/mram"


def test_ingest_report_source_with_monkeypatched_fetch(monkeypatch, tmp_path):
    listing = '<a href="/research/2025/abc/">ABC Report</a>'
    detail = '<html><head><meta property="article:published_time" content="2025-01-15T12:00:00Z"><title>ABC Report</title></head><body><h1>ABC Report</h1><p>We are short $ABC.</p></body></html>'

    def fake_fetch(url, config):
        if url.endswith('/research/'):
            return listing, {"url": url, "status_code": 200}
        return detail, {"url": url, "status_code": 200}

    monkeypatch.setattr("myaibot.market_context.ingest_reports.fetch_html", fake_fetch)
    source = MarketContextSource(
        source_id="short_test",
        source_name="Short Test",
        source_type="short_report",
        config={
            "url": "https://example.com/research/",
            "include_url_patterns": ["example\\.com/research/2025/"],
        },
    )
    result = ingest_report_source(source, since=datetime(2025, 1, 1, tzinfo=UTC), raw_root=tmp_path)
    assert result.errors == []
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.title == "ABC Report"
    assert doc.published_at == datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
    assert doc.available_at == doc.published_at
    assert doc.raw_path and doc.raw_path.endswith(".json")
    assert doc.metadata_json["canonical_url"] == "https://example.com/research/2025/abc"


def test_manual_report_urls_survive_listing_fetch_failure(monkeypatch, tmp_path):
    detail = '<html><head><title>Manual ABC Report</title><meta property="article:published_time" content="2025-02-01T00:00:00Z"></head><body><p>$ABC report text.</p></body></html>'

    def fake_fetch(url, config):
        if "example.invalid" in url:
            raise RuntimeError("listing unavailable")
        return detail, {"url": url, "final_url": url, "status_code": 200}

    monkeypatch.setattr("myaibot.market_context.ingest_reports.fetch_html", fake_fetch)
    source = MarketContextSource(
        source_id="short_manual",
        source_name="Manual Reports",
        source_type="short_report",
        config={
            "url": "https://example.invalid/manual/",
            "manual_urls": [{"url": "https://reports.example.com/abc?utm_source=x", "title": "Manual ABC", "published_at": "2025-02-01"}],
        },
    )
    result = ingest_report_source(source, since=datetime(2025, 1, 1, tzinfo=UTC), raw_root=tmp_path)
    assert result.errors == []
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.title == "Manual ABC Report"
    assert doc.metadata_json["listing_metadata"]["manual_url"] is True
    assert doc.metadata_json["canonical_url"] == "https://reports.example.com/abc"
